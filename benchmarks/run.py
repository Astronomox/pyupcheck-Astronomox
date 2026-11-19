"""Accuracy benchmark for pyupcheck.

Each case in benchmarks/cases/<name>/ has:

  case.toml  package, from, to, source (where the ground truth comes from)
  app.py     code using the package; lines that really break on the upgrade
             end with ``# BREAK``. Every other line that uses the package
             must NOT be flagged.

Scoring is per line: a ``# BREAK`` line flagged as breaking is a true
positive, an unflagged one a miss, and a flagged line without the marker a
false positive. Recall = TP / (TP + FN), precision = TP / (TP + FP).

    python benchmarks/run.py                  # all cases, all modes
    python benchmarks/run.py flask_3 numpy_2  # some cases
    python benchmarks/run.py --mode deep      # changelog | deep | both
    python benchmarks/run.py --json out.json  # machine-readable results

Needs network access to PyPI (and GitHub for changelog mode). Results are
cached in ~/.cache/pyupcheck like normal runs.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Set

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from depshift._toml import tomllib  # noqa: E402
from depshift.analyzer import analyze  # noqa: E402
from depshift.apisurface import get_api_changes, get_return_types  # noqa: E402
from depshift.changelog import collect_changes  # noqa: E402
from depshift.names import import_names_for  # noqa: E402
from depshift.precise import match_precise  # noqa: E402
from depshift.scanner import scan_directory  # noqa: E402

CASES_DIR = os.path.join(HERE, "cases")
MODES = ("changelog", "deep", "both")


@dataclass
class Score:
    tp: Set[int] = field(default_factory=set)
    fp: Set[int] = field(default_factory=set)
    fn: Set[int] = field(default_factory=set)
    error: str = ""

    @property
    def precision(self) -> float:
        n = len(self.tp) + len(self.fp)
        return len(self.tp) / n if n else 1.0

    @property
    def recall(self) -> float:
        n = len(self.tp) + len(self.fn)
        return len(self.tp) / n if n else 1.0


def load_case(name: str) -> dict:
    d = os.path.join(CASES_DIR, name)
    with open(os.path.join(d, "case.toml"), "rb") as f:
        meta = tomllib.load(f)
    meta["name"] = name
    meta["dir"] = d
    with open(os.path.join(d, "app.py"), encoding="utf-8") as f:
        meta["expected"] = {i for i, line in enumerate(f.read().splitlines(), 1)
                            if line.rstrip().endswith("# BREAK")}
    return meta


def flagged_lines(case: dict, mode: str) -> Dict[str, Set[int]]:
    """Lines flagged as breaking, per detection source."""
    names = import_names_for(case["package"])
    usages = scan_directory(case["dir"], names)
    out: Dict[str, Set[int]] = {"changelog": set(), "deep": set()}

    if mode in ("changelog", "both"):
        report = collect_changes(case["package"], case["from"], case["to"],
                                 github_token=os.environ.get("GITHUB_TOKEN"))
        if not report.checked:
            raise RuntimeError("changelog unreadable: " + ", ".join(report.sources_failed))
        risks, _ = analyze(usages, report.entries, names)
        out["changelog"] = {r.usage.line for r in risks if r.severity == "breaking"}

    if mode in ("deep", "both"):
        changes = get_api_changes(case["package"], case["from"], case["to"])
        if changes is None:
            raise RuntimeError("deep: could not extract API surface")
        returns = get_return_types(case["package"], case["from"], case["to"])
        out["deep"] = {r.usage.line for r in match_precise(usages, changes, names, returns)}
    return out


def score(case: dict, flagged: Set[int]) -> Score:
    exp = case["expected"]
    return Score(tp=flagged & exp, fp=flagged - exp, fn=exp - flagged)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--mode", choices=MODES, default="both")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args(argv)

    names = args.cases or sorted(n for n in os.listdir(CASES_DIR)
                                 if os.path.isfile(os.path.join(CASES_DIR, n, "case.toml")))
    modes = ["changelog", "deep", "both"] if args.mode == "both" else [args.mode]
    totals = {m: Score() for m in modes}
    rows = []

    for name in names:
        case = load_case(name)
        try:
            flags = flagged_lines(case, args.mode)
            per_mode = {"changelog": flags["changelog"], "deep": flags["deep"],
                        "both": flags["changelog"] | flags["deep"]}
            err = ""
        except Exception as e:  # report and keep going: one bad case is data too
            per_mode = {m: set() for m in MODES}
            err = f"{type(e).__name__}: {e}"[:120]
        row = {"case": name, "package": case["package"], "from": case["from"],
               "to": case["to"], "expected": sorted(case["expected"]), "error": err}
        for m in modes:
            s = score(case, per_mode[m])
            row[m] = {"tp": sorted(s.tp), "fp": sorted(s.fp), "fn": sorted(s.fn)}
            t = totals[m]
            # offset line numbers by case so totals stay distinct
            off = len(rows) * 100000
            t.tp |= {off + x for x in s.tp}
            t.fp |= {off + x for x in s.fp}
            t.fn |= {off + x for x in s.fn}
        rows.append(row)

    width = max(len(n) for n in names) + 2
    head = f"{'case':<{width}}" + "".join(f"{m:>22}" for m in modes)
    print(head)
    print("-" * len(head))
    for row in rows:
        cells = []
        for m in modes:
            r = row[m]
            cells.append(f"{len(r['tp'])}/{len(row['expected'])} caught, {len(r['fp'])} FP".rjust(22))
        line = f"{row['case']:<{width}}" + "".join(cells)
        if row["error"]:
            line += f"   ! {row['error']}"
        print(line)
    print("-" * len(head))
    for m in modes:
        t = totals[m]
        print(f"{m:<10} recall {t.recall:6.1%}   precision {t.precision:6.1%}   "
              f"({len(t.tp)} caught, {len(t.fn)} missed, {len(t.fp)} false alarms)")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"cases": rows, "totals": {
                m: {"recall": totals[m].recall, "precision": totals[m].precision,
                    "tp": len(totals[m].tp), "fn": len(totals[m].fn), "fp": len(totals[m].fp)}
                for m in modes}}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
