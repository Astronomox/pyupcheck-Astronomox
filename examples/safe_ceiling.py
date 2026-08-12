"""Example: find the highest version you can safely upgrade to.

Instead of checking against the latest, this finds the highest version
that introduces no breaking changes for your code.

Usage:
    python examples/safe_ceiling.py <package> [directory]
"""

import sys
from packaging.version import Version, InvalidVersion

from depshift.changelog import get_available_versions, get_changes_between
from depshift.scanner import scan_directory
from depshift.analyzer import analyze
from depshift.config import load_config


def find_safe_ceiling(package: str, directory: str = "."):
    """Return the highest version safely upgradeable to, or None."""
    from importlib.metadata import version as get_ver
    try:
        current = get_ver(package)
    except Exception:
        print(f"{package} is not installed.")
        return None

    print(f"Current: {package} {current}")
    print("Scanning your code...")

    cfg = load_config(directory)
    usages = scan_directory(directory, package, exclude_dirs=cfg.exclude_dirs)

    if not usages:
        print("No usages found - any version is safe.")
        return None

    print(f"Found {len(usages)} usages. Fetching available versions...")

    all_versions = get_available_versions(package)
    parsed = []
    for v in all_versions:
        try:
            pv = Version(v)
            if pv > Version(current):
                parsed.append(pv)
        except InvalidVersion:
            continue

    parsed.sort()  # ascending — check lowest first
    if not parsed:
        print("Already on latest.")
        return current

    print(f"Checking {len(parsed)} versions above {current}...\n")

    safe_ceiling = None
    for v in parsed:
        vs = str(v)
        try:
            changes = get_changes_between(package, current, vs)
        except Exception:
            continue

        risks, _ = analyze(usages, changes, package)
        breaking = [r for r in risks if r.severity == "breaking"]

        if breaking:
            print(f"  {vs}: BREAKING ({len(breaking)} issues) — stop here")
            break
        else:
            safe_ceiling = vs
            print(f"  {vs}: safe")

    if safe_ceiling:
        print(f"\nSafe ceiling: {package}=={safe_ceiling}")
        print(f"Run: pip install {package}=={safe_ceiling}")
    else:
        print(f"\nNo safe upgrade found above {current}.")

    return safe_ceiling


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: safe_ceiling.py <package> [directory]")
        sys.exit(1)
    package = sys.argv[1]
    directory = sys.argv[2] if len(sys.argv) > 2 else "."
    find_safe_ceiling(package, directory)
