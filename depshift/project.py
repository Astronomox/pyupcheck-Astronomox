"""What pyupcheck knows about a project and its dependency upgrades: versions,
checks, the highest safe version, and migration plans. Nothing here prints:
the commands (cli.py) decide how results are shown (output.py)."""

import os
from dataclasses import dataclass
from datetime import datetime

from depshift.analyzer import analyze
from depshift.apisurface import get_api_changes, get_return_types
from depshift.changelog import collect_changes, get_current_version, get_pypi_info
from depshift.deps import discover_dependencies, normalize_name
from depshift.lockfiles import locked_version, locked_versions
from depshift.names import import_names_for
from depshift.precise import match_precise
from depshift.scanner import imported_top_modules, scan_directory


def get_installed_version(package: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return None


def filter_by_since(changes, since_str: str, package: str):
    """Filter changelog entries to only those introduced after since_str (YYYY-MM-DD)."""
    try:
        since_dt = datetime.strptime(since_str, "%Y-%m-%d")
    except ValueError:
        return changes  # bad date format, return unfiltered

    try:
        info = get_pypi_info(package)
        releases = info.get("releases", {})
    except Exception:
        return changes  # can't fetch, return unfiltered

    def version_date(ver: str) -> datetime | None:
        for f in releases.get(ver, []):
            upload = f.get("upload_time")
            if upload:
                try:
                    return datetime.strptime(upload[:10], "%Y-%m-%d")
                except Exception:
                    pass
        return None

    filtered = []
    for c in changes:
        vd = version_date(c.version)
        if vd is None:
            continue  # unknown date — exclude when filtering by date
        if vd >= since_dt:
            filtered.append(c)
    return filtered


def latest_allowed_version(package: str, spec: str | None = None, fresh: bool = False) -> str | None:
    """Newest non-yanked, non-prerelease version on PyPI that satisfies spec.

    With no spec this is the latest release. A constraint like ``>=2,<3`` means
    the project will never install 3.x, so checking against 3.x is noise.
    """
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
    info = get_pypi_info(package, fresh=True) if fresh else get_pypi_info(package)
    try:
        spec_set = SpecifierSet(spec or "")
    except InvalidSpecifier:
        spec_set = SpecifierSet("")
    best = None
    for ver, files in (info.get("releases") or {}).items():
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        if v.is_prerelease or v.is_devrelease:
            continue
        if files and all(f.get("yanked") for f in files):
            continue
        if spec_set and v not in spec_set:
            continue
        if best is None or v > best:
            best = v
    return str(best) if best is not None else None


def specifier_of(raw: str) -> str | None:
    """The version specifier of a requirement string, e.g. '>=2,<3'."""
    from packaging.requirements import InvalidRequirement, Requirement
    try:
        spec = str(Requirement(raw).specifier)
    except InvalidRequirement:
        return None
    return spec or None


def satisfies(version: str, spec: str) -> bool:
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
    try:
        return Version(version) in SpecifierSet(spec)
    except (InvalidSpecifier, InvalidVersion):
        return True


def is_exact_pin(spec: str | None) -> bool:
    if not spec:
        return False
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    return len(parts) == 1 and parts[0].startswith(("==", "===")) and "*" not in parts[0]


def not_newer(target: str, current: str) -> bool:
    """True when target is the same as or older than current (nothing to upgrade)."""
    from packaging.version import InvalidVersion, Version
    try:
        return Version(target) <= Version(current)
    except InvalidVersion:
        return target == current


def lower_bound(spec: str | None) -> str | None:
    """The minimum version a spec allows (>=, ~=, ==), used as 'current'
    when nothing is installed or pinned."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    if not spec:
        return None
    try:
        specs = SpecifierSet(spec)
    except InvalidSpecifier:
        return None
    for sp in specs:
        if sp.operator in (">=", "~=", "==", "===") and "*" not in sp.version:
            return sp.version
    return None


UNDECLARED = "(imported, not declared)"


def undeclared_imports(directory: str, deps, locks, ignored, exclude_dirs):
    """Locked packages the code imports directly without declaring them.

    They arrive transitively (``urllib3`` via ``requests``), so their version
    can change without you choosing to upgrade them, and they break the same.
    """
    from depshift.deps import Dependency
    if not locks:
        return []
    declared = {d.name for d in deps}
    imported = imported_top_modules(directory, exclude_dirs)
    out = []
    for name, lock in sorted(locks.items()):
        if name in declared or name in ignored:
            continue
        if any(n in imported for n in import_names_for(name)):
            out.append(Dependency(name=name, pinned_version=lock.version, raw=name,
                                  source=f"{lock.source} {UNDECLARED}"))
    return out


def exact_pin_of(directory: str, package: str) -> str | None:
    """The ==pinned version of package in the project's dependency files, if any."""
    target = normalize_name(package)
    for dep in discover_dependencies(directory):
        if dep.name == target and dep.pinned_version:
            return dep.pinned_version
    return None


def baseline_version(package: str, directory: str, pinned: str | None = None,
                      spec: str | None = None, locked: str | None = "auto"):
    """(version, source) the project is on now, most trustworthy first: the
    lockfile (what the project runs), an exact pin, what's installed here (if
    it fits the project's constraint), then the lowest version the constraint
    allows. (None, None) when there is no baseline."""
    if locked == "auto":
        locked = locked_version(directory, package)
    if locked:
        return locked, "lockfile"
    pin = pinned or exact_pin_of(directory, package)
    if pin:
        return pin, "pinned"
    here = get_installed_version(package)
    if here and not (spec and not satisfies(here, spec)):
        return here, "installed"
    if spec:
        # unpinned range: the oldest version it allows is the riskiest baseline
        low = lower_bound(spec)
        if low:
            return low, "constraint lower bound"
    return None, None


def cell_fields(usage) -> dict:
    """A notebook finding's cell and line within it (its "line" is the
    .ipynb file's); nothing for a .py file."""
    if getattr(usage, "cell", None) is None:
        return {}
    return {"cell": usage.cell, "cell_line": usage.cell_line}


def project_dependencies(directory: str, cfg):
    """Declared dependencies (minus ignored ones) plus packages the code
    imports that are only present through the lockfile; and the locks."""
    deps = discover_dependencies(directory)
    ignored = {normalize_name(p) for p in cfg.ignore_packages}
    deps = [d for d in deps if d.name not in ignored]
    locks = locked_versions(directory)
    deps += undeclared_imports(directory, deps, locks, ignored, cfg.exclude_dirs)
    return deps, locks


def check_dependency(dep, locks, directory: str, cfg, github_token: str | None = None,
                     deep: bool = False, since: str | None = None) -> dict | None:
    """One dependency of a project, checked against its latest allowed version."""
    lock = locks.get(dep.name)
    r = run_check(dep.name, None, directory, github_token, cfg, since=since, pinned=dep.pinned_version,
                          deep=deep, spec=specifier_of(dep.raw),
                          locked=lock.version if lock else None)
    if r and dep.source.endswith(UNDECLARED):
        r["undeclared"] = True
        r.setdefault("warnings", []).append(
            f"imported by your code but not declared as a dependency "
            f"(only present via {lock.source if lock else 'another package'})")
    return r


def run_check(package: str, target_version: str | None, directory: str,
              github_token: str | None, cfg, since: str | None = None, deep: bool = False,
              pinned: str | None = None, spec: str | None = None,
              locked: str | None = "auto") -> dict | None:
    """Check one package's upgrade against the code in ``directory``.

    The result dict is what reports are made from. None means the package
    isn't on PyPI (or PyPI couldn't be reached)."""
    installed, current_source = baseline_version(package, directory, pinned, spec, locked)

    if not target_version:
        try:
            # an exact pin says where you are, not where you may go
            range_spec = None if is_exact_pin(spec) else spec
            target_version = latest_allowed_version(package, range_spec) or get_current_version(package)
        except Exception:
            return None

    if installed and not_newer(target_version, installed):
        return {
            "package": package, "current_version": installed,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "up_to_date": True,
        }

    if not installed:
        # Comparing against 0.0.0 would report every change in the package's
        # history. Be honest instead.
        return {
            "package": package, "current_version": None,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "unchecked": True,
            "warnings": ["current version unknown: install the package, pin it with ==, "
                         "or give a lower bound (>=) so there is a baseline to compare"],
        }

    current = installed
    import_names = import_names_for(package)
    usages = scan_directory(directory, import_names, exclude_dirs=cfg.exclude_dirs)
    if not usages:
        return {
            "package": package, "current_version": current,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "no_usages": True, "import_names": import_names,
        }

    warnings: list[str] = []
    try:
        report = collect_changes(package, current, target_version, github_token=github_token)
        changes = report.entries
        if report.sources_failed:
            warnings.append("could not read: " + ", ".join(report.sources_failed))
        changelog_checked = report.checked
    except Exception as e:
        changes = []
        changelog_checked = False
        warnings.append(f"changelog lookup failed ({type(e).__name__})")

    if since and changes:
        changes = filter_by_since(changes, since, package)

    risks, safe = analyze(usages, changes, import_names)

    min_rank = SEVERITY_RANK.get(cfg.min_severity, 2)
    risks = [r for r in risks if SEVERITY_RANK.get(r.severity, 2) <= min_rank]

    # deep mode: real API surface diff, argument-level precision
    precise_findings = []
    if deep:
        try:
            api_changes = get_api_changes(package, current, target_version)
        except Exception as e:
            api_changes = None
            warnings.append(f"deep API diff failed ({type(e).__name__})")
        if api_changes is None and not any("deep" in w for w in warnings):
            warnings.append("deep API diff unavailable (no Python sources for one of the versions)")
        if api_changes:
            try:
                returns = get_return_types(package, current, target_version)
            except Exception:
                returns = {}
            precise = match_precise(usages, api_changes, import_names, returns)
            # dedupe against changelog risks by (file, line, api)
            existing_keys = {(r.usage.file, r.usage.line, r.usage.attr_chain) for r in risks}
            for pr in precise:
                key = (pr.usage.file, pr.usage.line, pr.usage.attr_chain)
                if key in existing_keys:
                    continue  # changelog already flagged this exact usage
                existing_keys.add(key)
                precise_findings.append({
                    "file": pr.usage.file, "line": pr.usage.line,
                    **cell_fields(pr.usage),
                    "code": pr.usage.code, "api": pr.usage.attr_chain,
                    "severity": "breaking", "change_kind": pr.change.kind,
                    "change_description": pr.reason,
                    "change_version": target_version,
                    "precise": True, "confidence": "high",
                    "replacement": getattr(pr.change, "replacement", None),
                })

    risk_dicts = [
        {
            "file": r.usage.file, "line": r.usage.line, "code": r.usage.code,
            **cell_fields(r.usage),
            "api": r.usage.attr_chain, "severity": r.severity,
            "change_kind": r.change.kind,
            "change_description": r.change.description,
            "change_version": r.change.version,
            "confidence": getattr(r.change, "confidence", "medium"),
            "replacement": getattr(r.change, "replacement", None),
        }
        for r in risks
    ]
    # prepend precise (fact-based) findings — they're more trustworthy
    all_risks = dedupe_risks(precise_findings + risk_dicts)

    return {
        "package": package,
        "current_version": current,
        "target_version": target_version,
        "risks": all_risks,
        "safe_count": len(safe),
        "breaking_count": sum(1 for r in all_risks if r["severity"] == "breaking"),
        "deprecated_count": sum(1 for r in all_risks if r["severity"] == "deprecated"),
        "deep": deep,
        "current_source": current_source,
        "warnings": warnings,
        # nothing could be read at all: "OK" would be a lie
        "unchecked": not changelog_checked and not (deep and api_changes is not None),
    }


def dedupe_risks(risks: list[dict]) -> list[dict]:
    """One finding per (file, line, API): the most severe, verified first.

    A removed API usually also has an earlier deprecation notice; once a
    line is known to break, repeating that it was deprecated is noise.
    """
    order = {"breaking": 0, "deprecated": 1, "warning": 2}
    best: dict[tuple, dict] = {}
    for r in risks:
        key = (r["file"], r["line"], r["api"])
        cur = best.get(key)
        rank = (order.get(r["severity"], 3), 0 if r.get("precise") else 1)
        if cur is None or rank < (order.get(cur["severity"], 3), 0 if cur.get("precise") else 1):
            best[key] = r
    kept = {id(r) for r in best.values()}
    return [r for r in risks if id(r) in kept]


SEVERITY_RANK = {"breaking": 0, "deprecated": 1, "warning": 2}


def should_fail(results: list[dict], fail_on: str) -> bool:
    breaking = sum(r["breaking_count"] for r in results)
    deprecated = sum(r["deprecated_count"] for r in results)
    any_risk = sum(len(r["risks"]) for r in results)
    if fail_on == "never":
        return False
    if fail_on == "breaking":
        return breaking > 0
    if fail_on == "deprecated":
        return breaking > 0 or deprecated > 0
    if fail_on == "any":
        return any_risk > 0
    return breaking > 0


def fixable_count(package: str, old: str, new: str, directory: str, cfg,
                   github_token: str | None) -> int | None:
    """How many breaking usages `migrate` would fix automatically."""
    from depshift.apisurface import extract_surface
    from depshift.rewrite import plan_migration
    names = import_names_for(package)
    usages = scan_directory(directory, names, exclude_dirs=cfg.exclude_dirs)
    changes = get_api_changes(package, old, new)
    surface = extract_surface(package, new)
    if changes is None or surface is None:
        return None
    try:
        history = collect_changes(package, old, new, github_token=github_token, lookback=True).history
    except Exception:
        history = []
    plan = plan_migration(usages, changes, surface, names, history,
                          get_return_types(package, old, new))
    return len(plan.fixes)


def check_change(change, directory: str, cfg, github_token: str | None, deep: bool) -> dict | None:
    """Check one version change (a review.VersionChange) from its old version
    to its new one, with what migrate could fix."""
    r = run_check(change.name, change.new, directory, github_token, cfg, deep=deep, spec=specifier_of(change.raw),
                          locked=change.old)
    if r is None:
        return None
    r["current_source"] = "previous"  # the version before the change, not a lockfile's
    if r.get("up_to_date"):
        # a downgrade or no-op: nothing new to break
        r.setdefault("warnings", []).append(f"{change.old} -> {change.new} is not an upgrade; not checked")
        r["current_version"] = change.old
    elif deep and r["breaking_count"]:
        try:
            r["fixable"] = fixable_count(change.name, change.old, change.new, directory, cfg,
                                          github_token)
        except Exception:
            r["fixable"] = None
    return r


def replace_file(path: str, text: str, newline: str = "") -> None:
    """Replace a file's contents in one step (a crash never leaves it half
    written), keeping its permissions. ``newline=""`` writes ``text`` exactly."""
    import shutil
    import tempfile
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".pyupcheck-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class FileChange:
    """One file's rewrite: its text before and after, and the fixes in it."""

    def __init__(self, path: str, rel: str, before: str, after: str, fixes: list, nb=None, error=None):
        self.path, self.rel, self.before, self.after = path, rel, before, after
        self.fixes, self.notebook, self.error = fixes, nb, error

    def write(self, root: str | None = None) -> None:
        """Replace the file in one step (a crash never leaves it half written),
        keeping its permissions. Refuses a symlink, or a file outside ``root``:
        a cloned repository must not get its rewrite written somewhere else."""
        from depshift import notebook
        if os.path.islink(self.path):
            raise PermissionError(f"{self.rel} is a symlink: not rewritten")
        if root is not None:
            real_root = os.path.realpath(root)
            if os.path.commonpath([real_root, os.path.realpath(self.path)]) != real_root:
                raise PermissionError(f"{self.rel} is outside {root}: not rewritten")
        text = (notebook.dumps(notebook.restore(self.notebook, self.after))
                if self.notebook is not None else self.after)
        with open(self.path, "rb") as f:
            newline = "\r\n" if b"\r\n" in f.read() else "\n"  # keep the file's line endings
        replace_file(self.path, text, newline)


class MigrationPlan:
    def __init__(self, package: str, directory: str):
        self.package, self.directory = package, directory
        self.current: str | None = None
        self.source: str | None = None
        self.target: str | None = None
        self.error: str | None = None
        self.up_to_date = False
        self.has_fixes = False
        self.files: list[FileChange] = []
        self.manual: list = []

    def where(self, item) -> str:
        rel = os.path.relpath(item.file, self.directory)
        return f"{rel} cell {item.cell}:{item.line}" if item.cell is not None else f"{rel}:{item.line}"


def plan_project_migration(package: str, target_version: str | None, directory: str, cfg,
                           github_token: str | None = None) -> MigrationPlan:
    """What ``migrate`` would change, without printing or writing anything:
    used by the migrate command and by ``pyupcheck ui``."""
    import ast as _ast

    from depshift import notebook
    from depshift.apisurface import extract_surface
    from depshift.rewrite import apply_edits, plan_migration

    mp = MigrationPlan(package, directory)
    dep = next((d for d in discover_dependencies(directory) if d.name == normalize_name(package)), None)
    spec = specifier_of(dep.raw) if dep else None
    current, source = baseline_version(package, directory, dep.pinned_version if dep else None, spec)
    mp.current, mp.source = current, source
    if not current:
        mp.error = ("current version unknown. Install it, pin it with ==, or lock it, "
                    "so there is a version to migrate from.")
        return mp
    if not target_version:
        try:
            target_version = latest_allowed_version(package, None if is_exact_pin(spec) else spec)
        except Exception as e:
            mp.error = f"not found on PyPI ({e})"
            return mp
    mp.target = target_version
    if not target_version or not_newer(target_version, current):
        mp.up_to_date = True
        return mp

    names = import_names_for(package)
    usages = scan_directory(directory, names, exclude_dirs=cfg.exclude_dirs)
    changes = get_api_changes(package, current, target_version)
    new_surface = extract_surface(package, target_version)
    try:
        entries = collect_changes(package, current, target_version, github_token=github_token,
                                  lookback=True).history
    except Exception:
        entries = []
    try:
        returns = get_return_types(package, current, target_version)
    except Exception:
        returns = {}
    if changes is None or new_surface is None:
        mp.error = (f"could not read the API of {current} or {target_version} (no Python sources "
                    "published), so nothing can be rewritten safely.")
        return mp

    plan = plan_migration(usages, changes, new_surface, names, entries, returns)
    mp.has_fixes = bool(plan.fixes)
    for path, fixes in sorted(plan.by_file().items()):
        nb = notebook.load(path) if path.endswith(".ipynb") else None
        if nb is not None:
            before = nb.source
        else:
            with open(path, encoding="utf-8") as f:
                before = f.read()
        after = apply_edits(before, [e for fx in fixes for e in fx.edits])
        if after == before:
            continue
        rel = os.path.relpath(path, directory)
        error = None
        try:
            _ast.parse(after)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            error = "the rewrite would not parse"
        mp.files.append(FileChange(path, rel, before, after,
                                   sorted(fixes, key=lambda x: (x.cell or 0, x.line)), nb, error))
    mp.manual = sorted(plan.manual, key=lambda m: (m.file, m.cell or 0, m.line))
    return mp


def candidate_versions(package: str, current: str, spec: str | None) -> list[str]:
    """The newest release of each minor series above ``current`` that the
    project's constraint allows (no pre-releases, no yanked), oldest first."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
    info = get_pypi_info(package)
    try:
        cur = Version(current)
    except InvalidVersion:
        return []
    try:
        allowed = SpecifierSet("" if is_exact_pin(spec) else (spec or ""))
    except InvalidSpecifier:
        allowed = SpecifierSet("")
    newest: dict = {}
    for ver, files in (info.get("releases") or {}).items():
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        if v <= cur or v.is_prerelease or v.is_devrelease:
            continue
        if files and all(f.get("yanked") for f in files):
            continue
        if allowed and v not in allowed:
            continue
        key = (v.major, v.minor)
        if key not in newest or v > newest[key]:
            newest[key] = v
    return [str(v) for v in sorted(newest.values())]


def highest_safe_version(package: str, current: str, directory: str, cfg,
                         github_token: str | None = None, spec: str | None = None,
                         deep: bool = True) -> dict:
    """The newest version that breaks none of your code.

    Breaking changes only pile up as versions go up, so the candidates (the
    newest release of each minor series) are binary-searched: the latest is
    checked first, then about log2(n) more. A version that couldn't be
    checked never counts as safe.
    """
    candidates = candidate_versions(package, current, spec)
    checked: list[tuple[str, int | None]] = []
    out = {"package": package, "current": current, "latest": candidates[-1] if candidates else current,
           "safe": current, "breaking_at_latest": 0, "checked": checked, "unchecked": False}
    if not candidates:
        return out

    cache: dict = {}

    def breaks(version: str) -> int | None:
        """Breaking usages at ``version``; None if it couldn't be checked."""
        if version not in cache:
            r = run_check(package, version, directory, github_token, cfg,
                                  deep=deep, spec=spec, locked=current)
            cache[version] = None if (r is None or r.get("unchecked")) else r["breaking_count"]
            checked.append((version, cache[version]))
        return cache[version]

    latest = breaks(candidates[-1])
    out["breaking_at_latest"] = latest if latest is not None else 0
    if latest == 0:
        out["safe"] = candidates[-1]
        return out
    if latest is None:
        out["unchecked"] = True
    lo, hi = 0, len(candidates) - 2  # the last one is known not to be safe
    while lo <= hi:
        mid = (lo + hi) // 2
        n = breaks(candidates[mid])
        if n == 0:
            out["safe"] = candidates[mid]
            lo = mid + 1
        else:
            if n is None:
                out["unchecked"] = True
            hi = mid - 1
    return out


@dataclass(frozen=True)
class Release:
    """One release of a package on PyPI."""
    version: str
    date: str          # YYYY-MM-DD of its first upload; "" when PyPI doesn't say
    yanked: bool       # every file was yanked
    prerelease: bool


def release_history(pypi_info: dict) -> list[Release]:
    """A package's releases from its PyPI JSON, newest first. Versions that
    aren't valid PEP 440 are left out."""
    from packaging.version import InvalidVersion, Version
    found = []
    for raw, files in (pypi_info.get("releases") or {}).items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        date = next((f["upload_time"][:10] for f in files if f.get("upload_time")), "")
        yanked = bool(files) and all(f.get("yanked") for f in files)
        found.append((version, Release(raw, date, yanked, version.is_prerelease)))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [release for _, release in found]


def project_version(package: str, directory: str) -> tuple[str | None, str | None]:
    """(version, where it comes from) the project in ``directory`` is on for
    ``package``, or (None, None) when it isn't a dependency."""
    dep = next((d for d in discover_dependencies(directory) if d.name == normalize_name(package)), None)
    if dep is None:
        return None, None
    return baseline_version(dep.name, directory, dep.pinned_version, specifier_of(dep.raw))


def outdated_status(dep, locks: dict, directory: str, cfg, github_token: str | None = None,
                    safe: bool = False) -> dict | None:
    """One row of ``outdated``: current and latest allowed version, whether
    it's outdated and by a major version, and with ``safe`` the plan from
    :func:`highest_safe_version`. None when PyPI couldn't be asked."""
    from packaging.version import InvalidVersion, Version
    lock = locks.get(dep.name)
    spec = specifier_of(dep.raw)
    current, _ = baseline_version(dep.name, directory, dep.pinned_version, spec, lock.version if lock else None)
    try:
        latest = latest_allowed_version(dep.name, None if is_exact_pin(spec) else spec) \
            or get_current_version(dep.name)
    except Exception:
        return None
    row: dict = {"name": dep.name, "current": current, "latest": latest}
    if not current:
        return row
    try:
        row["outdated"] = Version(current) < Version(latest)
        row["major"] = Version(current).major < Version(latest).major
    except InvalidVersion:
        row["outdated"] = current != latest
        row["major"] = False
    if safe and row["outdated"]:
        row["plan"] = highest_safe_version(dep.name, current, directory, cfg, github_token, spec)
    return row
