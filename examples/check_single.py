"""Example: check a single package upgrade programmatically."""

from depshift.scanner import scan_directory
from depshift.changelog import get_changes_between, get_current_version
from depshift.analyzer import analyze


def check_package(package: str, directory: str = ".", target_version: str = None):
    """Check if upgrading package will break code in directory."""
    if target_version is None:
        target_version = get_current_version(package)

    from importlib.metadata import version as get_ver
    try:
        current = get_ver(package)
    except Exception:
        current = "0.0.0"

    print(f"Checking {package} {current} -> {target_version}")

    usages = scan_directory(directory, package)
    print(f"Found {len(usages)} usages across {len(set(u.file for u in usages))} files")

    changes = get_changes_between(package, current, target_version)
    print(f"Found {len(changes)} changelog changes")

    risks, safe = analyze(usages, changes, package)

    if not risks:
        print(f"All {len(safe)} usages are safe.")
        return True

    breaking = [r for r in risks if r.severity == "breaking"]
    print(f"\n{len(breaking)} BREAKING, {len(risks) - len(breaking)} other risks:\n")
    for r in risks:
        print(f"  [{r.severity}] {r.usage.file}:{r.usage.line}")
        print(f"    {r.change.description}")
    return len(breaking) == 0


if __name__ == "__main__":
    import sys
    pkg = sys.argv[1] if len(sys.argv) > 1 else "flask"
    safe = check_package(pkg, ".")
    sys.exit(0 if safe else 1)
