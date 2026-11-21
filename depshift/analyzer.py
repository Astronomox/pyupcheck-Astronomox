"""Cross-reference code usages against changelog changes."""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Union

from depshift.scanner import Usage
from depshift.changelog import ChangeEntry


@dataclass
class Risk:
    """A usage that may be affected by a change."""
    usage: Usage
    change: ChangeEntry
    severity: str  # "breaking", "deprecated", "warning"


# conventional import aliases used in changelogs: "np.NaN" means numpy.NaN
COMMON_ALIASES = {
    "np": "numpy", "pd": "pandas", "sa": "sqlalchemy", "tf": "tensorflow",
    "plt": "matplotlib.pyplot", "mpl": "matplotlib", "nx": "networkx",
    "xr": "xarray", "pl": "polars", "jnp": "jax.numpy", "sns": "seaborn",
    "dd": "dask.dataframe", "da": "dask.array", "sp": "scipy",
}


def normalize_api(api: str, package: str) -> str:
    """Normalize an API string for comparison."""
    api = api.strip().rstrip("()")
    head, _, rest = api.partition(".")
    full = COMMON_ALIASES.get(head)
    if full and (full == package or full.startswith(package + ".")):
        api = f"{full}.{rest}" if rest else full
    # ensure it starts with package name
    if not api.startswith(f"{package}.") and api != package:
        api = f"{package}.{api}"
    return api  # case-sensitive, like Python: numpy.NaN is not numpy.nan


def _root_for(usage: Usage, package: Union[str, Iterable[str]]) -> str:
    """Pick the import name this usage belongs to (longest match wins)."""
    if isinstance(package, str):
        return package
    names = sorted(package, key=len, reverse=True)
    for n in names:
        if usage.attr_chain == n or usage.attr_chain.startswith(f"{n}."):
            return n
    return names[0] if names else ""


def match_usage_to_change(usage: Usage, change: ChangeEntry,
                          package: Union[str, Iterable[str]]) -> Optional[Risk]:
    """Check if a usage is affected by a change.

    package is the import name (or names) the usage was scanned for.
    """
    package = _root_for(usage, package)
    usage_api = normalize_api(usage.attr_chain, package)
    change_api = normalize_api(change.api, package)
    if change_api == package or change.api == "unknown":
        # "Import X from flask instead" names the package itself, which is
        # not a change to every usage of it
        return None

    # removed/renamed keyword argument the usage actually passes:
    # change "Retry.method_whitelist" vs call Retry(method_whitelist=...)
    if usage.kwargs and change.kind in ("removed", "changed", "deprecated", "renamed"):
        parts = change.api.rstrip("()").split(".")
        param = parts[-1]
        owner = parts[-2] if len(parts) > 1 else None
        callee = usage.attr_chain.split(".")[-1]
        if param in usage.kwargs and owner is not None and owner == callee:
            severity = "breaking" if change.kind in ("removed", "changed") else "deprecated"
            return Risk(usage=usage, change=change, severity=severity)

    excepted = getattr(change, "exceptions", None) or []
    if excepted:
        # a module deprecated/removed except for some members: the module
        # itself stays, and so do those members
        if usage_api == change_api:
            return None
        if usage_api.startswith(change_api + "."):
            member = usage_api[len(change_api) + 1:].split(".")[0]
            if member in excepted:
                return None

    def sev() -> str:
        if getattr(change, "confidence", "medium") == "low":
            return "warning"  # mentioned, not shown to be removed
        return "breaking" if change.kind in ("removed", "changed") else "deprecated"

    # direct match
    if usage_api == change_api:
        return Risk(usage=usage, change=change, severity=sev())

    # usage is a child of the changed API (e.g. change affects requests.Session,
    # usage is requests.Session.get)
    if usage_api.startswith(f"{change_api}."):
        return Risk(usage=usage, change=change, severity=sev())

    if usage_api == package:
        # a bare `import numpy` touches every change in the package; listing
        # them all on the import line is noise
        return None

    # change is a child of something the usage touches
    if change_api.startswith(f"{usage_api}."):
        severity = "warning"
        return Risk(usage=usage, change=change, severity=severity)

    return None


def analyze(usages: List[Usage], changes: List[ChangeEntry],
            package: Union[str, Iterable[str]]) -> tuple:
    """
    Cross-reference usages against changes.
    Returns (risks, safe_usages).
    """
    if not isinstance(package, str):
        package = list(package)
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
