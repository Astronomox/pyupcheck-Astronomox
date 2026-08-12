"""Example: compare upgrade risk across two projects side by side.

Useful when you have a monorepo or multiple services and want to see
which one has the most risky dependency state.

Usage:
    python examples/compare_projects.py <dir1> <dir2> [dir3...]
"""

import sys
import os
from depshift.deps import discover_dependencies
from depshift.scanner import scan_directory
from depshift.changelog import get_changes_between, get_current_version
from depshift.analyzer import analyze
from depshift.config import load_config


def score_project(directory: str) -> dict:
    cfg = load_config(directory)
    deps = discover_dependencies(directory)

    total_breaking = 0
    total_deprecated = 0
    checked = 0

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

        usages = scan_directory(directory, dep.name, exclude_dirs=cfg.exclude_dirs)
        if not usages:
            continue

        try:
            changes = get_changes_between(dep.name, current, latest)
        except Exception:
            continue

        risks, _ = analyze(usages, changes, dep.name)
        total_breaking += sum(1 for r in risks if r.severity == "breaking")
        total_deprecated += sum(1 for r in risks if r.severity == "deprecated")
        checked += 1

    return {
        "directory": directory,
        "deps_checked": checked,
        "breaking": total_breaking,
        "deprecated": total_deprecated,
        "score": total_breaking * 10 + total_deprecated,
    }


def compare(*directories):
    if len(directories) < 2:
        print("Provide at least 2 directories to compare.")
        return

    print(f"Comparing {len(directories)} projects...\n")
    results = []
    for d in directories:
        print(f"Scanning {d}...")
        results.append(score_project(d))

    results.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n{'Project':<40} {'Deps':<8} {'Breaking':<12} {'Deprecated':<12} {'Risk Score'}")
    print("-" * 85)
    for r in results:
        name = os.path.basename(r["directory"].rstrip("/\\")) or r["directory"]
        print(f"{name:<40} {r['deps_checked']:<8} {r['breaking']:<12} {r['deprecated']:<12} {r['score']}")

    safest = results[-1]
    riskiest = results[0]
    print(f"\nRiskiest:  {riskiest['directory']} (score {riskiest['score']})")
    print(f"Safest:    {safest['directory']} (score {safest['score']})")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: compare_projects.py <dir1> <dir2> [dir3...]")
        sys.exit(1)
    compare(*sys.argv[1:])
