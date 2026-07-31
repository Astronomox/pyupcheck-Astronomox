"""Cross-reference code usages against changelog changes."""

from dataclasses import dataclass
from typing import List, Optional

from depshift.scanner import Usage
from depshift.changelog import ChangeEntry


@dataclass
class Risk:
    """A usage that may be affected by a change."""
    usage: Usage
    change: ChangeEntry
    severity: str  # "breaking", "deprecated", "warning"


def normalize_api(api: str, package: str) -> str:
    """Normalize an API string for comparison."""
    api = api.strip().rstrip("()")
    # ensure it starts with package name
    if not api.startswith(f"{package}.") and api != package:
        api = f"{package}.{api}"
    return api.lower()


def match_usage_to_change(usage: Usage, change: ChangeEntry, package: str) -> Optional[Risk]:
    """Check if a usage is affected by a change."""
    usage_api = normalize_api(usage.attr_chain, package)
    change_api = normalize_api(change.api, package)

    # direct match
    if usage_api == change_api:
        severity = "breaking" if change.kind in ("removed", "changed") else "deprecated"
        return Risk(usage=usage, change=change, severity=severity)

    # usage is a child of the changed API (e.g. change affects requests.Session,
    # usage is requests.Session.get)
    if usage_api.startswith(f"{change_api}."):
        severity = "breaking" if change.kind in ("removed", "changed") else "deprecated"
        return Risk(usage=usage, change=change, severity=severity)

    # change is a child of something the usage touches
    if change_api.startswith(f"{usage_api}."):
        severity = "warning"
        return Risk(usage=usage, change=change, severity=severity)

    return None


def analyze(usages: List[Usage], changes: List[ChangeEntry], package: str) -> tuple:
    """
    Cross-reference usages against changes.
    Returns (risks, safe_usages).
    """
    risks: List[Risk] = []
    matched_usage_keys = set()

    for usage in usages:
        for change in changes:
            risk = match_usage_to_change(usage, change, package)
            if risk:
                risks.append(risk)
                matched_usage_keys.add((usage.file, usage.line, usage.attr_chain))

    safe_usages = [
        u for u in usages
        if (u.file, u.line, u.attr_chain) not in matched_usage_keys
    ]

    # sort risks: breaking first, then deprecated, then warning
    severity_order = {"breaking": 0, "deprecated": 1, "warning": 2}
    risks.sort(key=lambda r: severity_order.get(r.severity, 99))

    return risks, safe_usages
