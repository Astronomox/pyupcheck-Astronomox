"""Example: CI gate script for upgrade safety.

Use this as a pre-merge check or scheduled CI job.
Exits with code 1 if any breaking changes are found.

Usage:
    python examples/ci_gate.py [directory] [--fail-on breaking|deprecated|any]
"""

import sys
from depshift.deps import discover_dependencies
from depshift.scanner import scan_directory
from depshift.changelog import get_changes_between, get_current_version
from depshift.analyzer import analyze
from depshift.config import load_config


SEVERITY_RANK = {"breaking": 0, "deprecated": 1, "any": 2}


def run_gate(directory: str, fail_on: str = "breaking") -> int:
    """Returns 0 (safe) or 1 (risks found at fail_on level)."""
    cfg = load_config(directory)
    deps = discover_dependencies(directory)

    if not deps:
        print("No dependency files found.")
        return 0

    total_breaking = 0
    total_deprecated = 0
    any_risk = False

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
            continue

        usages = scan_directory(directory, dep.name, exclude_dirs=cfg.exclude_dirs)
        if not usages:
            continue

        try:
            changes = get_changes_between(dep.name, current, latest)
        except Exception:
            continue

        risks, _ = analyze(usages, changes, dep.name)

        breaking = sum(1 for r in risks if r.severity == "breaking")
        deprecated = sum(1 for r in risks if r.severity == "deprecated")

        if risks:
            any_risk = True
            print(f"  {dep.name}: {current} -> {latest}  "
                  f"[{breaking} breaking, {deprecated} deprecated]")
            for r in risks:
                print(f"    {r.severity}: {r.usage.file}:{r.usage.line}")
                print(f"      {r.change.description}")

        total_breaking += breaking
        total_deprecated += deprecated

    print(f"\nTotal: {total_breaking} breaking, {total_deprecated} deprecated")

    if fail_on == "breaking" and total_breaking > 0:
        return 1
    if fail_on == "deprecated" and (total_breaking + total_deprecated) > 0:
        return 1
    if fail_on == "any" and any_risk:
        return 1
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    directory = "."
    fail_on = "breaking"

    for arg in args:
        if arg.startswith("--fail-on="):
            fail_on = arg.split("=", 1)[1]
        elif not arg.startswith("--"):
            directory = arg

    sys.exit(run_gate(directory, fail_on))
