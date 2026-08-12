"""Example: poll requirements.txt for changes and alert on risky upgrades.

This is a simple simulation of --watch mode. It checks requirements.txt
every 60 seconds and prints a warning if any dependency has a new version
that would break your code.

Run it in the background while developing:
    python examples/watch_deps.py &
"""

import sys
import time
from depshift.deps import discover_dependencies
from depshift.scanner import scan_directory
from depshift.changelog import get_changes_between, get_current_version
from depshift.analyzer import analyze
from depshift.config import load_config


def check_once(directory: str) -> list:
    """Run one check cycle. Returns list of (package, version, risk_count) tuples."""
    cfg = load_config(directory)
    deps = discover_dependencies(directory)
    alerts = []

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
        breaking = [r for r in risks if r.severity == "breaking"]
        if breaking:
            alerts.append((dep.name, latest, len(breaking)))

    return alerts


def watch(directory: str, interval: int = 60):
    print(f"Watching {directory} for risky upgrades (checking every {interval}s)...")
    print("Press Ctrl+C to stop.\n")

    last_alerts = set()

    while True:
        try:
            alerts = check_once(directory)
            current_alerts = {(pkg, ver) for pkg, ver, _ in alerts}

            new_alerts = current_alerts - last_alerts
            if new_alerts:
                for pkg, ver, count in alerts:
                    if (pkg, ver) in new_alerts:
                        print(f"[ALERT] {pkg} {ver} is available but has {count} breaking change(s) affecting your code.")
                        print(f"        Run: pyupcheck check {pkg} {ver} for details.")
            elif not alerts:
                print(f"[OK] All deps safe at {time.strftime('%H:%M:%S')}")

            last_alerts = current_alerts
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(interval)


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    watch(directory, interval)
