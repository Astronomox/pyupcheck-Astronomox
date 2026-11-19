"""Match precise API surface changes against actual code usages.

This complements the changelog-based analyzer with facts derived from the
real API diff. It knows not just that you call a function, but which keyword
arguments you pass, so it can flag a removed parameter you actually use.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union

from depshift.scanner import Usage
from depshift.apisurface import APIChange


@dataclass
class PreciseRisk:
    usage: Usage
    change: APIChange
    severity: str  # "breaking" always for surface diffs (they're facts)
    reason: str


def _short_name(api: str, package: str) -> str:
    """Strip the module prefix to get the callable/attribute name."""
    # api is dotted module path + name; usage attr_chain is package-rooted
    parts = api.split(".")
    return parts[-1]


def _usage_callable(usage: Usage, package: str) -> str:
    """Last segment of the usage's attribute chain."""
    return usage.attr_chain.split(".")[-1]


def _roots(package: Union[str, Iterable[str]]) -> List[str]:
    names = [package] if isinstance(package, str) else list(package)
    return sorted((n for n in names if n), key=len, reverse=True)


def _root_of(dotted: str, roots: List[str]) -> Optional[str]:
    """The import root (longest match) a dotted path belongs to.

    Uses the real import names rather than the first segment, so for namespace
    packages ``google.protobuf.X`` and ``google.cloud.X`` are different packages.
    """
    for r in roots:
        if dotted == r or dotted.startswith(r + "."):
            return r
    return None


def _prefixes(dotted: str) -> List[str]:
    parts = dotted.split(".")
    return [".".join(parts[:i]) for i in range(len(parts), 1, -1)]


def match_precise(usages: List[Usage], changes: List[APIChange],
                  package: Union[str, Iterable[str]],
                  returns: Optional[Dict[str, str]] = None) -> List[PreciseRisk]:
    """Cross-reference real API changes against usages, including kwargs.

    A usage matches a change when the full dotted path agrees, or, because
    packages re-export names (``requests.get`` is defined in
    ``requests.api``), when the final name agrees within the same top-level
    package. Removed modules only match by path prefix.
    """
    risks: List[PreciseRisk] = []
    roots = _roots(package)

    def same_package(usage: Usage, change: APIChange) -> bool:
        root = _root_of(usage.attr_chain, roots)
        return root is not None and _root_of(change.api, [root]) is not None

    removed_modules = [ch for ch in changes if ch.kind == "removed_module"]
    removed_callables = {}   # short_name -> [change]
    removed_params = {}      # short_name -> [(param, change)]
    required_params = {}     # short_name -> [change]
    kwonly_params = {}       # short_name -> [change]
    removed_attrs = {}       # short_name -> [change]
    removed_exports = {}     # exact dotted path -> change (re-exports only break that path)

    for ch in changes:
        short = _short_name(ch.api, package)
        if short == "__init__" and ch.api.count(".") >= 1:
            # constructor changes surface as calls to the class: pkg.Cls(...)
            short = ch.api.split(".")[-2]
        if ch.kind in ("removed_function", "removed_class"):
            removed_callables.setdefault(short, []).append(ch)
        elif ch.kind == "removed_param":
            removed_params.setdefault(short, []).append((ch.param, ch))
        elif ch.kind == "added_required_param":
            required_params.setdefault(short, []).append(ch)
        elif ch.kind in ("became_keyword_only",):
            kwonly_params.setdefault(short, []).append(ch)
        elif ch.kind == "removed_export":
            removed_exports.setdefault(ch.api, ch)
        elif ch.kind == "removed_attribute":
            removed_attrs.setdefault(short, []).append(ch)

    def pick(candidates, usage, loose=True):
        """Prefer an exact path match, else any same-package match.

        A candidate is skipped when the path the user wrote still resolves in
        the new version (re-exported or moved behind a compatible import).
        """
        chain = usage.attr_chain
        exact = [c for c in candidates if c.api == chain]
        if exact:
            return exact[0]
        if not loose:
            return None
        for c in candidates:
            if chain in getattr(c, "still_at", ()):
                continue
            if not same_class(usage, c):
                continue
            if same_package(usage, c):
                return c
        return None

    def same_class(usage, c) -> bool:
        """A method change only matches a usage reached through that class
        (``pkg.Session.get`` or an instance of it), never a module-level
        function of the same name (``click.command`` vs ``Context.command``)."""
        owner = getattr(c, "member_of", None)
        parts = usage.attr_chain.split(".")
        if owner is None:
            # module-level API: the usage must not be a method call on a class
            return True
        if len(parts) < 2:
            return False
        # pkg.Engine.execute, or e.execute() where e = create_engine() -> Engine
        return parts[-2] == owner or (returns or {}).get(parts[-2]) == owner

    def strict_pick(candidates, usage):
        """Exact path, or the package-root re-export (``pkg.NAME``)."""
        chain = usage.attr_chain
        for c in candidates:
            if c.api == chain:
                return c
            if chain in getattr(c, "still_at", ()):
                continue  # the name the user wrote still exists
            root = _root_of(chain, roots)
            if root and chain == f"{root}.{chain.rsplit('.', 1)[-1]}" and same_package(usage, c):
                return c
        return None

    def positional_count(usage) -> int:
        for a in usage.call_args or []:
            if a.startswith("pos:"):
                try:
                    return int(a[4:])
                except ValueError:
                    return 0
        return 0

    seen = set()

    def add(usage, ch, reason):
        key = (usage.file, usage.line, usage.attr_chain, ch.kind, ch.param)
        if key in seen:
            return
        seen.add(key)
        risks.append(PreciseRisk(usage=usage, change=ch, severity="breaking", reason=reason))

    for usage in usages:
        chain = usage.attr_chain
        callable_name = _usage_callable(usage, package)

        # removed module the user imports or reaches into
        mod = next((m for m in removed_modules
                    if chain == m.api or chain.startswith(m.api + ".")), None)
        if mod:
            add(usage, mod, mod.detail)
            continue

        # removed function/class the user references
        ch = pick(removed_callables.get(callable_name, []), usage)
        if ch:
            add(usage, ch, ch.detail)
            continue

        # an import path that no longer works (dropped re-export); also covers
        # pkg.Name.attr reached through the dropped name
        ch = next((removed_exports[p] for p in _prefixes(chain) if p in removed_exports), None)
        if ch:
            add(usage, ch, ch.detail)
            continue

        # removed constant/attribute
        ch = strict_pick(removed_attrs.get(callable_name, []), usage)
        if ch:
            add(usage, ch, ch.detail)
            continue

        if usage.usage_type != "function_call":
            continue

        # removed parameter that the user actually passes as a kwarg
        if usage.kwargs and callable_name in removed_params:
            for param, ch in removed_params[callable_name]:
                if param in usage.kwargs and (ch.api == usage.attr_chain or
                                              (same_package(usage, ch) and same_class(usage, ch))):
                    add(usage, ch, f"You pass '{param}=' but it was removed: {ch.detail}")

        # a new required argument: the call as written no longer works
        for ch in required_params.get(callable_name, []):
            if ch.param in (usage.kwargs or []):
                continue
            if pick([ch], usage) is ch:
                add(usage, ch, ch.detail)

        # positional argument that must now be passed by keyword
        npos = positional_count(usage)
        for ch in kwonly_params.get(callable_name, []):
            if ch.position is not None and npos > ch.position and pick([ch], usage) is ch:
                add(usage, ch, f"You pass '{ch.param}' positionally: {ch.detail}")

    return risks
