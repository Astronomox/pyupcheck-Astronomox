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
| changelog (default) | 70.0% | 100% |
| `--deep` | 100% | 100% |
| `migrate` (exact rewrite) | 8/8 cases | |

51 cases, 100 breaking lines, across click, Django, Flask, Jinja,
Matplotlib, NetworkX, NumPy, pandas, Pillow, Pydantic, PyYAML,
scikit-learn, SciPy, SQLAlchemy, urllib3 and Werkzeug, including inference
cases (objects reached through helpers, attributes and chains) and 8
negative controls (patch releases, APIs that keep working, a deprecation
that is not yet a removal). Changelog misses are mostly changes worded
loosely or not written down at all; deep mode catches them.
