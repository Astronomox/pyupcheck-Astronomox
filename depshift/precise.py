"""Match precise API surface changes against actual code usages.

This complements the changelog-based analyzer with facts derived from the
real API diff. It knows not just that you call a function, but which keyword
arguments you pass, so it can flag a removed parameter you actually use.
"""

from dataclasses import dataclass
from typing import List

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


def match_precise(usages: List[Usage], changes: List[APIChange], package: str) -> List[PreciseRisk]:
    """Cross-reference real API changes against usages, including kwargs."""
    risks: List[PreciseRisk] = []

    # index changes by the short name they affect
    removed_callables = {}   # short_name -> change
    removed_params = {}      # short_name -> [(param, change)]
    removed_classes = {}     # short_name -> change

    for ch in changes:
        short = _short_name(ch.api, package)
        if ch.kind in ("removed_function",):
            removed_callables[short] = ch
        elif ch.kind == "removed_class":
            removed_classes[short] = ch
        elif ch.kind == "removed_param":
            removed_params.setdefault(short, []).append((ch.param, ch))
        elif ch.kind == "removed_module":
            removed_callables[short] = ch

    for usage in usages:
        callable_name = _usage_callable(usage, package)

        # removed function/module/class the user references
        if callable_name in removed_callables:
            ch = removed_callables[callable_name]
            risks.append(PreciseRisk(
                usage=usage, change=ch, severity="breaking",
                reason=ch.detail,
            ))
            continue
        if callable_name in removed_classes:
            ch = removed_classes[callable_name]
            risks.append(PreciseRisk(
                usage=usage, change=ch, severity="breaking",
                reason=ch.detail,
            ))
            continue

        # removed parameter that the user actually passes as a kwarg
        if usage.kwargs and callable_name in removed_params:
            for param, ch in removed_params[callable_name]:
                if param in usage.kwargs:
                    risks.append(PreciseRisk(
                        usage=usage, change=ch, severity="breaking",
                        reason=f"You pass '{param}=' but it was removed: {ch.detail}",
                    ))

    return risks
