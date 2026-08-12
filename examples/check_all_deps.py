"""Example: check all dependencies in a project programmatically."""

import sys
from depshift.deps import discover_dependencies
from depshift.scanner import scan_directory
from depshift.changelog import get_changes_between, get_current_version
from depshift.analyzer import analyze


def check_all(directory: str = "."):
    deps = discover_dependencies(directory)
    if not deps:
        print("No dependency files found.")
        return True

    print(f"Found {len(deps)} dependencies\n")
    any_breaking = False

    for dep in deps:
        try:
            latest = get_current_version(dep.name)
        except Exception:
            continue

        from importlib.metadata import version as get_ver
        try:
            current = get_ver(dep.name)
        except Exception:
            current = "0.0.0"

        if current == latest:
            print(f"  {dep.name}: already on {latest}")
            continue

        usages = scan_directory(directory, dep.name)
        if not usages:
            print(f"  {dep.name}: no usages found, safe to upgrade to {latest}")
            continue

        changes = get_changes_between(dep.name, current, latest)
        risks, safe = analyze(usages, changes, dep.name)
        breaking = [r for r in risks if r.severity == "breaking"]

        if breaking:
            any_breaking = True
            print(f"  {dep.name}: {current} -> {latest}  [{len(breaking)} BREAKING]")
            for r in breaking:
                print(f"    x {r.usage.file}:{r.usage.line}  {r.change.description}")
        elif risks:
            print(f"  {dep.name}: {current} -> {latest}  [{len(risks)} warnings]")
        else:
            print(f"  {dep.name}: {current} -> {latest}  [safe]")

    return not any_breaking


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    safe = check_all(directory)
    sys.exit(0 if safe else 1)
