"""Example: generate a full HTML upgrade safety report for a project.

Usage:
    python examples/generate_report.py [directory] [output.html]
"""

import sys
from depshift.deps import discover_dependencies
from depshift.scanner import scan_directory
from depshift.changelog import get_changes_between, get_current_version
from depshift.analyzer import analyze
from depshift.report import render_html
from depshift.config import load_config


def build_report(directory: str, output: str = "pyupcheck-report.html"):
    cfg = load_config(directory)
    deps = discover_dependencies(directory)

    if not deps:
        print("No dependency files found.")
        return

    print(f"Building upgrade safety report for {len(deps)} dependencies...")
    results = []

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
            results.append({
                "package": dep.name,
                "current_version": current,
                "target_version": latest,
                "risks": [],
                "safe_count": 0,
                "breaking_count": 0,
                "deprecated_count": 0,
                "no_usages": True,
            })
            continue

        try:
            changes = get_changes_between(dep.name, current, latest)
        except Exception:
            changes = []

        risks, safe = analyze(usages, changes, dep.name)

        results.append({
            "package": dep.name,
            "current_version": current,
            "target_version": latest,
            "risks": [
                {
                    "file": r.usage.file,
                    "line": r.usage.line,
                    "code": r.usage.code,
                    "api": r.usage.attr_chain,
                    "severity": r.severity,
                    "change_kind": r.change.kind,
                    "change_description": r.change.description,
                    "change_version": r.change.version,
                }
                for r in risks
            ],
            "safe_count": len(safe),
            "breaking_count": sum(1 for r in risks if r.severity == "breaking"),
            "deprecated_count": sum(1 for r in risks if r.severity == "deprecated"),
        })
        print(f"  {dep.name}: done")

    html = render_html(results)
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    total_breaking = sum(r["breaking_count"] for r in results)
    print(f"\nReport written to {output}")
    print(f"{total_breaking} breaking issue(s) found across {len(results)} packages.")


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    output = sys.argv[2] if len(sys.argv) > 2 else "pyupcheck-report.html"
    build_report(directory, output)
