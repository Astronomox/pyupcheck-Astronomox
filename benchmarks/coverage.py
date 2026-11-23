"""How much of PyPI pyupcheck can check: run against the most-downloaded packages.

For each package it takes the latest release and the newest release of the
previous minor series (the upgrade most people make) and records:

* deep:  both versions' public API could be read and compared (the check
         that catches every breaking line in the accuracy benchmark)
* notes: release notes were found for that upgrade (read, whether or not
         they mention a breaking change)

This measures reach, not accuracy: benchmarks/run.py measures accuracy.

    python benchmarks/coverage.py --top 1000 --json coverage.json --markdown coverage.md

Set GITHUB_TOKEN: without it GitHub's API allows 60 requests an hour, and
release notes published only as GitHub releases are missed.
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from depshift.apisurface import get_api_changes  # noqa: E402
from depshift.changelog import collect_changes, get_pypi_info  # noqa: E402
from depshift.http import http_get  # noqa: E402

TOP_PACKAGES = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json"


def upgrade_pair(info: dict) -> Tuple[Optional[str], Optional[str]]:
    """(newest release of the previous minor series, latest release)."""
    from packaging.version import InvalidVersion, Version
    vs = []
    for v, files in (info.get("releases") or {}).items():
        try:
            pv = Version(v)
        except InvalidVersion:
            continue
        if pv.is_prerelease or pv.is_devrelease or not files or all(f.get("yanked") for f in files):
            continue
        vs.append(pv)
    if not vs:
        return None, None
    vs.sort()
    latest = vs[-1]
    prev = [v for v in vs if (v.major, v.minor) < (latest.major, latest.minor)]
    return (str(prev[-1]) if prev else None), str(latest)


def measure(package: str, token: Optional[str]) -> dict:
    out = {"package": package}
    start = time.time()
    try:
        old, new = upgrade_pair(get_pypi_info(package))
        out.update(old=old, new=new)
        if not old:
            out["skipped"] = "one release series: nothing to compare"
            return out
        try:
            changes = get_api_changes(package, old, new)
            out["deep"] = changes is not None
        except Exception as e:  # noqa: BLE001 - recorded, not fatal
            out["deep"], out["deep_error"] = False, f"{type(e).__name__}: {e}"[:200]
        try:
            report = collect_changes(package, old, new, github_token=token)
            out["notes"] = bool(report.versions_read)
            out["breaking_notes"] = bool(report.entries)
            out["sources"], out["failed"] = report.sources_ok, report.sources_failed
        except Exception as e:  # noqa: BLE001
            out["notes"], out["notes_error"] = False, f"{type(e).__name__}: {e}"[:200]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        out["seconds"] = round(time.time() - start, 1)
    return out


def summarize(rows: List[dict]) -> dict:
    compared = [r for r in rows if r.get("old") and not r.get("error")]
    n = len(compared) or 1
    pct = lambda k: round(100 * sum(1 for r in compared if r.get(k)) / n, 1)  # noqa: E731
    return {
        "packages": len(rows),
        "compared": len(compared),
        "skipped": sum(1 for r in rows if r.get("skipped")),
        "errors": sum(1 for r in rows if r.get("error")),
        "deep_pct": pct("deep"),
        "notes_pct": pct("notes"),
        "either_pct": round(100 * sum(1 for r in compared if r.get("deep") or r.get("notes")) / n, 1),
    }


def markdown(rows: List[dict], s: dict) -> str:
    lines = [
        f"## pyupcheck coverage: top {s['packages']} PyPI packages", "",
        f"Compared {s['compared']} (skipped {s['skipped']} with one release series, {s['errors']} errors).", "",
        "| | Packages |", "|---|---|",
        f"| Deep mode (API compared) | {s['deep_pct']}% |",
        f"| Release notes found for the upgrade | {s['notes_pct']}% |",
        f"| Either | {s['either_pct']}% |", "",
    ]
    misses = [r for r in rows if r.get("old") and not r.get("deep")]
    if misses:
        lines += ["<details><summary>Deep mode failed</summary>", ""]
        lines += [f"- `{r['package']}` {r.get('deep_error', '')}" for r in misses]
        lines += ["", "</details>", ""]
    no_notes = [r for r in rows if r.get("old") and not r.get("notes")]
    if no_notes:
        lines += ["<details><summary>No release notes found</summary>", ""]
        lines += [f"- `{r['package']}` {r['old']} -> {r['new']}" for r in no_notes]
        lines += ["", "</details>", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--markdown", dest="md_out")
    args = ap.parse_args(argv)

    resp = http_get(TOP_PACKAGES, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    names = [r["project"] for r in resp.json()["rows"][:args.top]]
    token = os.environ.get("GITHUB_TOKEN")

    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for i, row in enumerate(pool.map(lambda n: measure(n, token), names), 1):
            rows.append(row)
            if i % 50 == 0:
                print(f"{i}/{len(names)}", file=sys.stderr, flush=True)
    s = summarize(rows)
    text = markdown(rows, s)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"summary": s, "packages": rows}, f, indent=1)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
