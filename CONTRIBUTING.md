# Contributing to pyupcheck

Thanks for your interest. This document covers how to get set up, what to work on, and how to submit changes.

## Setup

```bash
git clone https://github.com/Astronomox/pyupcheck-Astronomox.git
cd pyupcheck-Astronomox
pip install -e ".[ai,ui]"
pip install pytest pytest-cov ruff mypy
```

## Checks

CI runs all of these on every pull request; run them before you push:

```bash
make test    # pytest, failing below 89% branch coverage
make lint    # ruff (depshift, tests, examples) and mypy
make bench   # the accuracy benchmark (needs the network)
```

Tests never touch the network or your real cache: `tests/conftest.py` blocks
every HTTP request and points the cache at a temporary directory. Fake PyPI,
release notes and API diffs with `monkeypatch` instead (see
`tests/test_project.py` and `tests/test_apisurface_download.py`).

## How the code is laid out

```
depshift/           # the package (named depshift internally)
  cli.py            # the commands: parse options, call project.py, print with output.py
  project.py        # what a command needs to know, with no printing: checks,
                    #   the highest safe version, migration plans, outdated rows
  output.py         # everything shown in the terminal: tables that fit any
                    #   width, findings, summaries; a frozen View, never globals
  safe.py           # making untrusted text safe for terminals and Markdown
  http.py           # the one way out to the network (https, public hosts, size caps)
  scanner.py        # where code uses a package (AST)
  changelog.py      # finding and parsing release notes
  analyzer.py       # release-note changes against usages
  apisurface.py     # downloading both versions and diffing their real API
  precise.py        # API changes against usages, down to arguments
  rewrite.py        # the edits migrate makes
  deps.py, conda.py, lockfiles.py, notebook.py, names.py   # reading projects
  review.py, watch.py, report.py, explain.py, tui.py, welcome.py
```

The rule that keeps it tidy: `project.py` and the analysis modules never
print, and `cli.py` never decides anything a test would want to check.
Anything printed from text pyupcheck doesn't control (release notes, PyPI
metadata, your code) goes through `output.esc` or `safe.plain`.

Output must fit the terminal: `tests/test_layout.py` renders every command at
40 to 160 columns (and `pyupcheck ui` at six window sizes) and fails on any
line that is too wide, ends in spaces, or cuts a status short. Add your
command there if it prints a table.

Release-note parsing is tested on real release notes in
`tests/fixtures/release_notes/` (sources and licenses in its README). When a
package's notes are misread, add a trimmed excerpt there with a test.

## What to work on

**Release-note parsing.** If `pyupcheck diff` misses changes for a package you
care about, open an issue with the package and the versions. The fix usually
lives in `changelog.py`, and a benchmark case in `benchmarks/cases/` keeps it fixed.

**API extraction.** Packages that are mostly compiled code, or that build
their API at import time, extract less cleanly. Improvements belong in `apisurface.py`.

**Benchmark cases.** A small project that uses an API a real upgrade broke,
with the breaking lines marked `# BREAK`, is the most useful contribution of all.

## Submitting changes

1. For anything larger than a fix, open an issue first.
2. Fork the repo and create a branch; keep commits focused.
3. Add a test that fails without your change.
4. Run `make test` and `make lint`.
5. Open a pull request saying what changed and why.

## Code style

ruff and mypy settle it (configured in `pyproject.toml`): Python 3.10 syntax,
lines up to 120 characters, type hints on public functions. Comments say why,
not what.

## License

By contributing, you agree that your changes will be licensed under the MIT License.
