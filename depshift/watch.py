"""``pyupcheck watch``: re-check as dependencies change.

Two things are watched:

* the project's dependency files and lockfiles: when a version in them
  changes (a ``pip-compile``, ``uv lock``, ``poetry update`` or a hand edit),
  each package that moved is checked from its old version to its new one;
* optionally PyPI: every so often the newest release each direct dependency
  allows is looked up, and a release that appears while watching is checked
  against your code before you upgrade to it.

Files are polled (size and modification time), which needs no extra
dependencies and works on every platform and file system. A change is
handled once the files have stopped changing for one poll, so a lockfile
written in several steps is read once, complete.
"""

import os
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from depshift.review import VersionChange, is_dep_file, resolved_versions, version_changes

Snapshot = dict[str, tuple]  # name -> (version, requirement text)
Signature = dict[str, tuple[int, int]]  # relative path -> (mtime_ns, size)


def dep_file_signature(directory: str) -> Signature:
    """Size and modification time of each dependency file in the project."""
    out: Signature = {}
    try:
        entries = os.listdir(directory)
    except OSError:
        return out
    for name in entries:
        full = os.path.join(directory, name)
        if os.path.isdir(full):
            try:
                subs = [f"{name}/{s}" for s in os.listdir(full)]
            except OSError:
                continue
            candidates = [p for p in subs if is_dep_file(p)]
        else:
            candidates = [name] if is_dep_file(name) else []
        for rel in candidates:
            try:
                st = os.stat(os.path.join(directory, rel))
            except OSError:
                continue
            out[rel] = (st.st_mtime_ns, st.st_size)
    from depshift.deps import workspace_members
    for member in workspace_members(directory):
        rel = f"{member}/pyproject.toml"
        if rel not in out:
            try:
                st = os.stat(os.path.join(directory, rel))
            except OSError:
                continue
            out[rel] = (st.st_mtime_ns, st.st_size)
    return out


@dataclass
class Event:
    """Something the watcher noticed.

    kind: "files" (dependency files changed: ``changes`` were checked, and
    ``added``/``removed`` packages listed), "release" (a new release was
    checked), or "error" (``message`` says what could not be read)."""
    kind: str
    files: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    changes: list[VersionChange] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    message: str = ""


def _newer(a: str, b: str) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        return Version(a) > Version(b)
    except InvalidVersion:
        return False


class Watcher:
    """Polls a project; :meth:`poll` returns what happened since the last call.

    ``check(change)`` checks one VersionChange and returns a result dict (or
    None). ``latest(name, requirement)`` returns the newest release that
    requirement allows; ``direct()`` the names of direct dependencies. All
    are passed in so the loop can be driven without the network.
    """

    def __init__(self, directory: str, check: Callable[[VersionChange], dict | None],
                 latest: Callable[[str, str], str | None] | None = None,
                 direct: Callable[[], Iterable[str]] | None = None,
                 releases_every: float = 0, jobs: int = 8,
                 resolve: Callable[[str], Snapshot] = resolved_versions,
                 clock: Callable[[], float] = time.monotonic):
        self.directory = directory
        self.check = check
        self.latest = latest
        self.direct = direct
        self.releases_every = releases_every if latest and direct else 0
        self.jobs = max(1, jobs)
        self.resolve = resolve
        self.clock = clock
        self.snapshot: Snapshot = {}
        self.signature: Signature = {}
        self._pending: Signature | None = None
        self.known_latest: dict[str, str] = {}
        self._next_release_poll = 0.0

    # ── start ────────────────────────────────────────────────────────────

    def start(self) -> list[Event]:
        """Record where the project is now (and, when watching releases, the
        newest release of each dependency) without reporting it."""
        self.signature = dep_file_signature(self.directory)
        try:
            self.snapshot = self.resolve(self.directory)
        except Exception as e:
            self.snapshot = {}
            return [Event("error", message=f"could not read the dependency files: {e}")]
        if self.releases_every:
            self.known_latest = {n: v for n, v in self._latest_all().items() if v}
            self._next_release_poll = self.clock() + self.releases_every
        return []

    # ── one poll ─────────────────────────────────────────────────────────

    def poll(self) -> list[Event]:
        events = []
        ev = self._poll_files()
        if ev:
            events.append(ev)
        if self.releases_every and self.clock() >= self._next_release_poll:
            self._next_release_poll = self.clock() + self.releases_every
            events.extend(self._poll_releases())
        return events

    def _poll_files(self) -> Event | None:
        sig = dep_file_signature(self.directory)
        if sig == self.signature:
            self._pending = None
            return None
        if sig != self._pending:
            self._pending = sig  # still being written: look again next poll
            return None
        # unchanged for one poll: read it
        changed = sorted(p for p in set(sig) | set(self.signature) if sig.get(p) != self.signature.get(p))
        self._pending = None
        try:
            new = self.resolve(self.directory)
        except Exception as e:
            # keep the old signature, so the next good write is compared
            # with the last good state
            return Event("error", files=changed, message=f"could not read the dependency files: {e}")
        self.signature = sig
        old, self.snapshot = self.snapshot, new
        changes = version_changes(old, new)
        ev = Event("files", files=changed, changes=changes,
                   added=sorted(set(new) - set(old)), removed=sorted(set(old) - set(new)))
        ev.results = self._check_all(changes)
        # a release you've just moved to is no longer news
        for c in changes:
            if c.name in self.known_latest and _newer(c.new, self.known_latest[c.name]):
                self.known_latest[c.name] = c.new
        return ev

    def _latest_all(self) -> dict[str, str | None]:
        latest, direct = self.latest, self.direct
        if latest is None or direct is None:  # release polling is off
            return {}
        names = [n for n in direct() if n in self.snapshot]

        def one(name):
            try:
                return name, latest(name, self.snapshot[name][1])
            except Exception:
                return name, None

        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            return dict(pool.map(one, names))

    def _poll_releases(self) -> list[Event]:
        fresh = []
        for name, version in self._latest_all().items():
            if not version:
                continue
            seen = self.known_latest.get(name)
            self.known_latest[name] = version if not seen or _newer(version, seen) else seen
            current = self.snapshot.get(name, (None,))[0]
            if seen and _newer(version, seen) and current and _newer(version, current):
                fresh.append(VersionChange(name, current, version, self.snapshot[name][1]))
        return [Event("release", changes=[c], results=r)
                for c, r in zip(fresh, (self._check_all([c]) for c in fresh), strict=True)]

    def _check_all(self, changes: list[VersionChange]) -> list[dict]:
        if not changes:
            return []

        def one(c):
            try:
                return self.check(c)
            except Exception as e:  # one package failing must not stop the watch
                return {"package": c.name, "current_version": c.old, "target_version": c.new,
                        "risks": [], "breaking_count": 0, "deprecated_count": 0, "safe_count": 0,
                        "unchecked": True, "warnings": [f"could not check: {e}"]}

        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            return [r for r in pool.map(one, changes) if r]
