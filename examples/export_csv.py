"""Example: export pyupcheck findings to CSV for spreadsheet review.

Usage:
    python examples/export_csv.py [directory] [output.csv]
"""

import csv
import sys
import os
from depshift.deps import discover_dependencies
from depshift.scanner import scan_directory
from depshift.changelog import get_changes_between, get_current_version
from depshift.analyzer import analyze
from depshift.config import load_config


def export_csv(directory: str, output: str = "pyupcheck.csv"):
    cfg = load_config(directory)
    deps = discover_dependencies(directory)

    if not deps:
        print("No dependency files found.")
        return

    rows = []

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
            rows.append({
                "package": dep.name,
                "current": current,
                "target": latest,
                "severity": "safe",
                "file": "",
                "line": "",
                "api": "",
                "description": "No usages found",
            })
            continue

        try:
            changes = get_changes_between(dep.name, current, latest)
        except Exception:
            changes = []

        risks, safe = analyze(usages, changes, dep.name)

        if not risks:
            rows.append({
                "package": dep.name,
                "current": current,
                "target": latest,
                "severity": "safe",
                "file": "",
                "line": "",
                "api": "",
                "description": f"{len(safe)} usages all safe",
            })
        else:
            for r in risks:
                rows.append({
                    "package": dep.name,
                    "current": current,
                    "target": latest,
                    "severity": r.severity,
                    "file": os.path.relpath(r.usage.file),
                    "line": r.usage.line,
                    "api": r.usage.attr_chain,
                    "description": r.change.description,
                })

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["package", "current", "target", "severity", "file", "line", "api", "description"]
        )
        writer.writeheader()
        writer.writerows(rows)

    breaking = sum(1 for r in rows if r["severity"] == "breaking")
    print(f"Wrote {len(rows)} rows to {output} ({breaking} breaking)")


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    output = sys.argv[2] if len(sys.argv) > 2 else "pyupcheck.csv"
    export_csv(directory, output)
