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

## Current results

| Mode | Recall | Precision |
|---|---|---|
| changelog (default) | 65.9% | 100% |
| `--deep` | 100% | 100% |

18 cases, 41 breaking lines: click 8.1, Django 4.0, Flask 3, Jinja 3.1,
Matplotlib 3.9, NumPy 2, pandas 2, Pillow 10, Pydantic 2, PyYAML 6,
SciPy 1.14, SQLAlchemy 2, urllib3 2, Werkzeug 3, two inference cases
(objects reached through helper functions, `self` attributes and chained
calls), plus two negative controls. Changelog misses are mostly changes
worded loosely or described only in migration guides; deep mode catches them.
