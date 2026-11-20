# Accuracy benchmark

Real upgrades with known breaking changes, used to measure how many breaks
pyupcheck catches (recall) and how many of its warnings are real (precision).

```bash
python benchmarks/run.py                  # all cases, changelog + deep + both
python benchmarks/run.py flask_3 numpy_2  # selected cases
python benchmarks/run.py --mode deep      # one mode
python benchmarks/run.py --json out.json  # machine-readable
```

Needs network access to PyPI and GitHub.

## Adding a case

Create `cases/<name>/` with:

- `case.toml`: `package`, `from`, `to`, and `source`, which says where the
  ground truth comes from (changelog, migration guide, release notes).
- `app.py`: realistic code using the package. End every line that really
  breaks on the upgrade with `# BREAK`. Also include lines that use the
  package and keep working, since flagging those counts as a false alarm.

Only mark a line `# BREAK` when an upstream source says so.

Optionally add `migrated.py`: the exact file `pyupcheck migrate --write`
should produce. Only expect rewrites whose replacement the package's own
notes name; if they don't (NetworkX 3's `to_numpy_matrix`), `migrated.py`
is identical to `app.py`, which checks that migrate doesn't guess.

## Current results

| Mode | Recall | Precision |
|---|---|---|
| changelog (default) | 61.4% | 100% |
| `--deep` | 100% | 100% |
| `migrate` (exact rewrite) | 6/6 cases | |

26 cases, 57 breaking lines: click 8.1, Django 4.0 (two cases), Flask 3,
Jinja 3.1, Matplotlib 3.9, NetworkX 3, NumPy 2 (two cases), pandas 2 (two
cases), Pillow 10, Pydantic 2, PyYAML 6, scikit-learn 1.2, SciPy 1.14,
SQLAlchemy 2, urllib3 2, Werkzeug 2.1 and 3, two inference cases, and four
negative controls (requests, Flask, NumPy and Django patch releases).
Changelog misses are mostly changes worded loosely or described only in
migration guides; deep mode catches them.
