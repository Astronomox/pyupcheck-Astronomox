# Contributing to pyupcheck

Thanks for your interest. This document covers how to get set up, what to work on, and how to submit changes.

## Setup

```bash
git clone https://github.com/Astronomox/pyupcheck-Astronomox.git
cd pyupcheck-Astronomox
pip install -e "."
pip install pytest
```

## Running tests

```bash
pytest tests/
```

## Project structure

```
depshift/           # Package source (named depshift internally)
  __init__.py       # Version string
  cli.py            # All CLI commands (click)
  scanner.py        # AST-based code scanner
  changelog.py      # Changelog fetching and parsing
  analyzer.py       # Cross-reference usages against changes
  apisurface.py     # Real API surface extraction and diffing
  precise.py        # Argument-level risk matching
  deps.py           # Dependency file parsing
  config.py         # Config loading (.pyupcheckignore, pyproject.toml)
  cache.py          # Local response cache
  report.py         # HTML and markdown report generation
  welcome.py        # Live banner

tests/              # Test suite
examples/           # Example scripts
```

## What to work on

The most impactful contributions are:

**Changelog parsing accuracy**
Many packages use non-standard changelog formats. If `pyupcheck diff` misses changes for a package you care about, open an issue with the package name and version range. The fix usually lives in `changelog.py`.

**API surface extraction**
`apisurface.py` currently handles pure-Python wheels and sdists. Packages that ship C extensions or use complex import machinery may not extract cleanly. Improving extraction coverage is high-value.

**Dependency file parsers**
`deps.py` handles requirements.txt, pyproject.toml, setup.cfg, setup.py, and environment.yml. `pip-tools` compiled requirements and `conda-lock` files are not yet supported.

**Test coverage**
Most of the codebase has no tests yet. Any test you add for the scanner, analyzer, or deps parser is a genuine improvement.

**`--watch` mode**
A background process that monitors your lockfile for changes and alerts when a new version would break your code. This is a meaningful feature that doesn't exist yet.

## Submitting changes

1. Open an issue describing what you want to change before writing code.
2. Fork the repo and create a branch.
3. Make your change. Keep commits focused — one logical change per commit.
4. Run `pytest tests/` and make sure nothing is broken.
5. Submit a pull request. Include a short description of what changed and why.

## Code style

No linter is enforced yet. Match the style of the file you're editing. Type hints on public functions.

## License

By contributing, you agree that your changes will be licensed under the MIT License.
