"""Example: use the API surface differ directly for fact-based detection."""

import sys
from depshift.apisurface import extract_surface, diff_surfaces
from depshift.scanner import scan_directory
from depshift.precise import match_precise


def deep_check(package: str, from_version: str, to_version: str, directory: str = "."):
    """Fact-based upgrade safety check using real API surface diff."""
    print(f"Extracting API surface for {package} {from_version}...")
    old = extract_surface(package, from_version)
    if not old:
        print(f"Could not extract surface for {package} {from_version}")
        return None

    print(f"Extracting API surface for {package} {to_version}...")
    new = extract_surface(package, to_version)
    if not new:
        print(f"Could not extract surface for {package} {to_version}")
        return None

    print(f"\n{package} {from_version}: {len(old.functions)} functions, {len(old.classes)} classes")
    print(f"{package} {to_version}: {len(new.functions)} functions, {len(new.classes)} classes")

    changes = diff_surfaces(old, new)
    print(f"\n{len(changes)} public API changes:")
    for c in changes:
        detail = f" (param: {c.param})" if c.param else ""
        print(f"  {c.kind}{detail}: {c.api}")

    usages = scan_directory(directory, package)
    print(f"\n{len(usages)} usages of {package} in {directory}")

    risks = match_precise(usages, changes, package)
    if not risks:
        print("No precise risks found — your usages look safe.")
        return True

    print(f"\n{len(risks)} FACT-BASED risks:")
    for r in risks:
        print(f"  [BREAKING] {r.usage.file}:{r.usage.line}")
        print(f"    {r.reason}")
    return False


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: deep_check.py <package> <from_version> <to_version> [directory]")
        sys.exit(1)
    package = sys.argv[1]
    from_v  = sys.argv[2]
    to_v    = sys.argv[3]
    directory = sys.argv[4] if len(sys.argv) > 4 else "."
    safe = deep_check(package, from_v, to_v, directory)
    sys.exit(0 if safe else 1)
