# pyupcheck

Check if upgrading a Python dependency will break your code.

```
$ pyupcheck check-all

Found 12 dependencies to check

flask 2.3.3 -> 3.1.3  OK (15 usages safe)
requests 2.28.0 -> 2.34.2  2 BREAKING
  x src/api.py:23  resp = requests.get(url, verify=False)
    Removed: `verify` parameter no longer accepted
django 4.2.0 -> 5.0.6  1 deprecated
  ! core/models.py:8  from django.utils import timezone
    Deprecated: use datetime.timezone instead

2 breaking | 1 deprecated across 12 packages
```

## Install

```bash
pip install pyupcheck
```

## Commands

### `check` - check one package

```bash
pyupcheck check flask 3.0.0        # against specific version
pyupcheck check flask              # against latest
```

### `check-all` - check every dependency

Reads every `requirements*.txt` (following `-r` includes), `pyproject.toml` (PEP 621, PEP 735 dependency groups, Poetry, PDM, uv), `Pipfile`, `setup.cfg`, `setup.py` and `environment.yml`, and checks every dependency against its latest version.

```bash
pyupcheck check-all
pyupcheck check-all --deep          # fact-based API diff for every dependency
pyupcheck check-all -j 16           # check 16 dependencies in parallel (default 8)
pyupcheck check-all --format html -o report.html
```

If a lockfile is present (`uv.lock`, `poetry.lock`, `pdm.lock`, `pylock.toml`, `Pipfile.lock`), the locked versions are used as the starting point. Packages your code imports that are only in the lockfile because another dependency pulls them in (`urllib3` via `requests`) are checked too and marked **undeclared**: their version can change without you upgrading anything.

Install names and import names don't have to match: `pyyaml` is scanned as `yaml`, `Pillow` as `PIL`, `scikit-learn` as `sklearn`, `protobuf` as `google.protobuf`. Names come from the installed package's metadata when available, then a built-in table of known mismatches.

### `migrate` - rewrite your code for an upgrade

```bash
pyupcheck migrate scipy 1.14.0          # show a diff of the fixes
pyupcheck migrate scipy 1.14.0 --write  # apply them
```

Applies only mechanical changes it can verify against the new version's real API:

- arguments that became keyword-only: `f(a, 1)` -> `f(a, b=1)`
- renamed keyword arguments: `Retry(method_whitelist=...)` -> `Retry(allowed_methods=...)`
- renamed or moved names: `integrate.simps` -> `integrate.simpson`, `np.float_` -> `np.float64`, `from django.utils.encoding import force_text` -> `... import force_str as force_text`
- imports of names moved to another package: `from flask import Markup` -> `from markupsafe import Markup` (checked against that package's real API)

Replacements come from the package's own deprecation notes (including ones published before your current version), are paired one-to-one ("`a`, `b` in favour of `x`, `y` respectively"), and are only applied if the replacement exists in the target version. Anything else that breaks (a new required argument, an API moved to another package) is listed for you to change by hand, and the command exits 1 while such items remain.

### `outdated` - list stale dependencies

```bash
pyupcheck outdated
```

Flags major version bumps separately since they carry the most risk.

### `diff` - changelog diff between versions

See breaking/deprecated changes between any two versions without scanning code:

```bash
pyupcheck diff django 4.2.0 5.0.0
```

### `scan` - list your usages of a package

```bash
pyupcheck scan requests
```

### `versions` and `cache-clear`

```bash
pyupcheck versions flask
pyupcheck cache-clear
```

## Output formats

```bash
pyupcheck check flask -f json          # machine-readable
pyupcheck check flask -f md -o r.md   # markdown report
pyupcheck check flask -f html -o r.html # styled HTML report
```

## Review dependency pull requests

Comment on every pull request that changes a dependency version (Dependabot, Renovate, or by hand) with the call sites each upgrade breaks and how many `pyupcheck migrate` can fix:

```yaml
# .github/workflows/dependency-review.yml
name: Dependency review
on:
  pull_request:
    paths: ["requirements*.txt", "pyproject.toml", "*.lock", "pylock*.toml", "Pipfile*"]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: Astronomox/pyupcheck-Astronomox@v0.8.0
        # with:
        #   fail-on: breaking   # make the check fail on breaking usages
        #   deep: "false"       # changelog only (faster)
```

The comment is updated in place on later pushes, and the same report is written to the job summary, so it is still visible when the token can't comment (for example on pull requests from forks). Locally:

```bash
pyupcheck review --base origin/main
```

## CI integration

Exit code is 1 when the `--fail-on` condition is met:

```bash
pyupcheck check-all --fail-on breaking     # default
pyupcheck check-all --fail-on deprecated   # stricter
pyupcheck check-all --fail-on any          # strictest
pyupcheck check-all --fail-on never        # report only
```

GitHub Actions example:

```yaml
- name: Check dependency upgrades
  run: |
    pip install pyupcheck
    pyupcheck check-all --fail-on breaking --quiet
```

Pre-commit hook (`.pre-commit-config.yaml`):

```yaml
- repo: local
  hooks:
    - id: pyupcheck
      name: pyupcheck
      entry: pyupcheck check-all --quiet
      language: system
      pass_filenames: false
```

## Configuration

`pyproject.toml`:

```toml
[tool.pyupcheck]
exclude = ["migrations", "legacy"]
ignore = ["internal-package"]
fail_on = "breaking"
min_severity = "deprecated"
cache = true
```

Or `.pyupcheckignore`:

```
migrations/          # trailing slash = directory
legacy/
internal-package     # no slash = package to skip
```

## Deep mode

By default, pyupcheck detects breaking changes by parsing changelogs. That's fast but depends on maintainers writing good changelogs. Deep mode goes further:

```bash
pyupcheck check requests 3.0.0 --deep
```

In deep mode, pyupcheck downloads both versions of the package, extracts their actual public API surface (every module, class, function, and signature), and computes a precise diff. It then matches that against your code at the argument level, so it can tell you:

- A function you call was removed
- A class you import no longer exists
- A keyword argument you pass (like `verify=False`) was removed from a function signature
- A function now requires an argument you don't pass, or a parameter you pass positionally became keyword-only
- A name you import from the package is no longer exported there (`from flask import Markup` in Flask 3)
- A constant you use was removed

Functions that moved but are still importable from the same place (re-exports, `__all__`, base classes) are not reported.

These findings are marked `[FACT]` in the output because they come from the real code, not changelog prose. Deep mode is slower (it downloads and parses packages) but far more accurate.

## Features

- AST-based scanning: imports, from-imports, aliases, attribute chains, calls
- Argument-level analysis: knows which keyword arguments you pass
- Follows objects: `s = requests.Session(); s.get(...)`, `with ... as s`, type-annotated parameters, helper functions that return package objects, attributes on `self`, and chained calls
- Respects your version constraints and never reports an unchecked package as safe
- Real API surface diffing in `--deep` mode
- Jupyter notebook (`.ipynb`) and type stub (`.pyi`) scanning
- Dynamic import detection (`importlib.import_module`, `__import__`)
- Changelog sources: GitHub releases, raw changelog files, PyPI descriptions
- Works for any PyPI package: resolves import names (`pyyaml` -> `yaml`), namespace packages, C extensions, `src/` layouts
- Parses requirements*.txt, pyproject.toml, Pipfile, setup.cfg, setup.py, environment.yml
- 24h response cache (`--no-cache` to bypass, `cache-clear` to wipe)
- Severity filtering with `--min-severity`
- Quiet mode (`-q`) for hooks and scripts

## Accuracy

`benchmarks/` holds 26 real upgrades with known breaking changes (Django 4, Flask 3, NumPy 2, pandas 2, Pydantic 2, SQLAlchemy 2, SciPy 1.14, Pillow 10 and more). On the current set, `--deep` catches 100% of breaking lines with no false alarms; changelog-only mode catches 73.7%, also with no false alarms; `migrate` produces the exact expected rewrite in all 7 rewrite cases. Run it with `make bench`.

Changelogs are read from GitHub releases, root CHANGES files, per-version release notes in project docs (NumPy, SciPy, pandas, Pillow, Django, SQLAlchemy, NetworkX layouts), and migration guides (NumPy 2, SQLAlchemy 2, Matplotlib removals, `docs/migration.md`).

## Limitations

- Changelog parsing relies on maintainers writing structured changelogs
- Dynamic attribute access (`getattr(pkg, name)`) is not detected
- GitHub API is rate limited to 60 req/hr unauthenticated; pass `--github-token` or set `GITHUB_TOKEN` for higher limits

## Contributing

Contributions are welcome. Here is how to get started:

```bash
git clone https://github.com/Astronomox/pyupcheck-Astronomox.git
cd pyupcheck-Astronomox
pip install -e "."
```

**Releasing (for maintainers)**

Releases are published by GitHub Actions when a version tag is pushed, using PyPI Trusted Publishing (no tokens). See [RELEASING.md](RELEASING.md).

**Things that would genuinely improve the tool:**

- Better changelog parsing for packages that use unconventional formats (Sphinx-based changelogs, HISTORY files)
- `conda` lockfile support
- A `--watch` mode that monitors your lockfile for changes and alerts on risky upgrades
- Test coverage

To contribute, open an issue describing what you want to work on, then submit a pull request. Include a short test or example showing the bug or feature.

If you find a package whose changelog pyupcheck fails to parse correctly, open an issue with the package name and version range. That is the most common and most impactful thing to fix.

## License

MIT

## FAQ

**Q: How accurate is pyupcheck?**

Without `--deep`, accuracy depends on how well maintainers document breaking changes in their changelogs. Major well-maintained packages (Flask, Django, requests, click) have good changelogs and high detection rates. Smaller packages with poor changelogs may have misses.

With `--deep`, accuracy is much higher because it diffs the actual source code. It will catch any removed public function or deleted parameter regardless of whether the changelog mentioned it.

**Q: Why do I see "[FACT]" next to some findings?**

`[FACT]` means the finding came from a real API surface diff (via `--deep`), not changelog parsing. These are confirmed removals, not guesses.

**Q: How does pyupcheck handle packages that ship C extensions?**

The `--deep` mode downloads and parses Python source files. If a package ships only compiled C extensions with no Python stubs, extraction will fail gracefully and fall back to changelog-based detection.

**Q: Will pyupcheck send my code anywhere?**

No. Your code is never sent anywhere. pyupcheck only reads local files and makes outbound requests to PyPI and GitHub to fetch package metadata and changelogs.

**Q: How do I use it in CI without hitting GitHub rate limits?**

Set the `GITHUB_TOKEN` environment variable. In GitHub Actions this is available as `${{ secrets.GITHUB_TOKEN }}` automatically.

**Q: Can I ignore certain packages?**

Yes. Add them to `.pyupcheckignore` or `[tool.pyupcheck]` in your `pyproject.toml`:

```toml
[tool.pyupcheck]
ignore = ["my-internal-package", "legacy-lib"]
```

**Q: The version I want to check is not on PyPI yet. Can I still use pyupcheck?**

No. pyupcheck needs to download the package from PyPI. Pre-release or private packages are not supported.

## Comparison

| Tool | What it does | pyupcheck advantage |
|------|-------------|---------------------|
| `pip install --upgrade` | Upgrades blindly | pyupcheck checks first |
| `pip-audit` | Finds security vulnerabilities | pyupcheck finds API breaks |
| `safety` | Checks known CVEs | pyupcheck checks your specific usage |
| `pipdeptree` | Shows dependency tree | pyupcheck shows upgrade risk |
| Reading changelogs manually | Slow, easy to miss things | pyupcheck automates this |

## Roadmap

Items being actively considered for future versions:

- `--watch` mode: monitor your lockfile and alert when a new version would break your code
- conda-lock support
- VS Code extension: inline warnings on imports
- Jupyter notebook deep scan (currently scans cells for imports, not API calls inside them)
- `pyupcheck explain` with AI-assisted migration guidance
- Support for TypeScript / Node.js packages (npm ecosystem)
