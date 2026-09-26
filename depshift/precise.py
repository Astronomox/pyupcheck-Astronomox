"""Match precise API surface changes against actual code usages.

This complements the changelog-based analyzer with facts derived from the
real API diff. It knows not just that you call a function, but which keyword
arguments you pass, so it can flag a removed parameter you actually use.
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Union

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


def match_precise(usages: List[Usage], changes: List[APIChange],
                  package: Union[str, Iterable[str]]) -> List[PreciseRisk]:
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

    for ch in changes:
        short = _short_name(ch.api, package)
        if ch.kind in ("removed_function", "removed_class"):
            removed_callables.setdefault(short, []).append(ch)
        elif ch.kind == "removed_param":
            removed_params.setdefault(short, []).append((ch.param, ch))

    def pick(candidates, usage):
        """Prefer an exact path match, else any same-package match."""
        exact = [c for c in candidates if c.api == usage.attr_chain]
        if exact:
            return exact[0]
        for c in candidates:
            if same_package(usage, c):
                return c
        return None

    for usage in usages:
        chain = usage.attr_chain
        callable_name = _usage_callable(usage, package)

        # removed module the user imports or reaches into
        mod = next((m for m in removed_modules
                    if chain == m.api or chain.startswith(m.api + ".")), None)
        if mod:
            risks.append(PreciseRisk(usage=usage, change=mod, severity="breaking",
                                     reason=mod.detail))
            continue

        # removed function/class the user references
        ch = pick(removed_callables.get(callable_name, []), usage)
        if ch:
            risks.append(PreciseRisk(usage=usage, change=ch, severity="breaking",
                                     reason=ch.detail))
            continue

        # removed parameter that the user actually passes as a kwarg
        if usage.kwargs and callable_name in removed_params:
            flagged = set()
            for param, ch in removed_params[callable_name]:
                if param in usage.kwargs and param not in flagged and same_package(usage, ch):
                    flagged.add(param)
                    risks.append(PreciseRisk(
                        usage=usage, change=ch, severity="breaking",
                        reason=f"You pass '{param}=' but it was removed: {ch.detail}",
                    ))

    return risks
