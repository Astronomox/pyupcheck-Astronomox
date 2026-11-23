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

If a lockfile is present (`uv.lock`, `poetry.lock`, `pdm.lock`, `pylock.toml`, `Pipfile.lock`, `conda-lock.yml`, `pixi.lock`), the locked versions are used as the starting point. Packages your code imports that are only in the lockfile because another dependency pulls them in (`urllib3` via `requests`) are checked too and marked **undeclared**: their version can change without you upgrading anything.

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
- names moved to another package: `from flask import Markup` -> `from markupsafe import Markup`, and `flask.Markup(...)` -> `markupsafe.Markup(...)` with `import markupsafe` added (checked against that package's real API)

Replacements come from the package's own deprecation notes (including ones published before your current version), are paired one-to-one ("`a`, `b` in favour of `x`, `y` respectively"), and are only applied if the replacement exists in the target version. Anything else that breaks (a new required argument, an API moved to another package) is listed for you to change by hand, and the command exits 1 while such items remain.

### `outdated` - list stale dependencies

```bash
pyupcheck outdated
```

Flags major version bumps separately since they carry the most risk.

```bash
pyupcheck outdated --safe
```

Adds a **Highest safe** column: the newest version that breaks none of your code. If the latest release breaks you (`scipy 1.18` removes `simps`), you still learn you can move to `1.13.1` today. `pyupcheck fix` moves each `==` pin to exactly that version.

### `watch` - re-check as your dependencies change

```bash
pyupcheck watch
```

Leave it running in a terminal while you work. When a version in your requirements files, `pyproject.toml` or lockfile changes (`pip-compile`, `uv lock`, `poetry update`, a merged Dependabot PR, an edit), it checks each package that moved, from its old version to its new one, and says what breaks and what `migrate` can fix:

```
10:24:10 requirements.txt changed: 1 version change(s)
flask 2.2.5 -> 3.0.0  1 BREAKING
  x app.py:3  x = flask.Markup("<b>")
    -> use markupsafe.Markup
    pyupcheck migrate flask 3.0.0 --write fixes 1 of these
```

Every hour (`--releases MINUTES`, `0` to turn off) it also asks PyPI for new releases your constraints allow, and tells you whether each one breaks your code before you upgrade. `--bell` rings the terminal bell when something breaks; `-q` prints only what breaks. A lockfile written in several steps is read once it stops changing, and a half-written one never replaces the last good state.

### `diff` - changelog diff between versions

See breaking/deprecated changes between any two versions without scanning code:

```bash
pyupcheck diff django 4.2.0 5.0.0
```

### `explain` - ask Claude how to fix what breaks

For what `migrate` can't change by itself (no drop-in replacement, an argument whose meaning changed), `explain` sends each breaking usage to Claude with the release note behind it and the replacement pyupcheck verified, and streams back a before/after for each:

```bash
pip install "pyupcheck[ai]"
export ANTHROPIC_API_KEY=...      # or: ant auth login
pyupcheck explain numpy 2.0.0
pyupcheck explain numpy 2.0.0 --dry-run   # shows exactly what would be sent, sends nothing
```

Only the few lines around each breaking usage are sent (`--max` caps how many usages), and the command says how much before it sends anything. `--effort` trades speed for thoroughness. Check the advice before applying it: `pyupcheck check` verifies the result.

### `scan` - list your usages of a package

```bash
pyupcheck scan requests
```

### `versions` and `cache-clear`

```bash
pyupcheck versions flask
pyupcheck cache-clear
```

## Editor: VS Code

`editors/vscode` is a VS Code extension: breaking usages show as errors on their lines (deprecations as warnings, notebook findings on their cell), a quick fix runs `pyupcheck migrate`, and it re-checks when a requirements file, `pyproject.toml` or lockfile is saved. CI builds it as a `.vsix` (the `pyupcheck-vscode` artifact): install it with `code --install-extension pyupcheck-vscode.vsix`. See [editors/vscode/README.md](editors/vscode/README.md).

## Output formats

```bash
pyupcheck check flask -f json          # machine-readable
pyupcheck check flask -f md -o r.md   # markdown report
pyupcheck check flask -f html -o r.html # styled HTML report
pyupcheck check-all -f sarif -o r.sarif # SARIF, for GitHub code scanning
pyupcheck check-all -f github           # GitHub Actions annotations
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
        #   sarif: pyupcheck.sarif  # then upload it for the Security tab:
      # - uses: github/codeql-action/upload-sarif@v3
      #   with:
      #     sarif_file: pyupcheck.sarif
```

Each finding is also annotated on its line in the PR diff (`annotations: "false"` to turn that off).

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
- Jupyter notebooks (`.ipynb`): code cells are scanned as one module, the way they run, so `import numpy as np` in the first cell is followed into every later one. `%magics`, `!shell` lines and `%%bash`-style cells are skipped. Findings point at the notebook file's line and name the cell, and `migrate --write` rewrites cells in place, leaving outputs and metadata untouched
- Type stub (`.pyi`) scanning
- conda and pixi projects: `environment.yml`, `conda-lock.yml`, `pixi.toml` (and `[tool.pixi]` in `pyproject.toml`) and `pixi.lock`. Only conda packages that are Python packages are checked (not `openssl` or `rust`), under their PyPI names (`pytorch` is `torch`, and pixi's lock says exactly which name each package has on PyPI). Manifests using TOML 1.1 syntax that Python's `tomllib` can't read are still read
- Dynamic import detection (`importlib.import_module`, `__import__`)
- Changelog sources: GitHub releases, raw changelog files, PyPI descriptions
- Works for any PyPI package: resolves import names (`pyyaml` -> `yaml`), namespace packages, C extensions, `src/` layouts
- Parses requirements*.txt, pyproject.toml, Pipfile, setup.cfg, setup.py, environment.yml
- 24h response cache (`--no-cache` to bypass, `cache-clear` to wipe)
- Severity filtering with `--min-severity`
- Quiet mode (`-q`) for hooks and scripts

## Accuracy

`benchmarks/` holds 52 real upgrades (103 breaking lines, plus 8 negative controls, and one Jupyter notebook) with known breaking changes (Django 4, Flask 3, NumPy 2, pandas 2, Pydantic 2, SQLAlchemy 2, SciPy 1.14, Pillow 10 and more). On the current set, `--deep` catches 100% of breaking lines with no false alarms; changelog-only mode catches 80.6%, also with no false alarms; `migrate` produces the exact expected rewrite in all 8 rewrite cases. Run it with `make bench`.

**Reach.** `benchmarks/coverage.py` runs pyupcheck against the most-downloaded PyPI packages (latest release against the newest one of the previous minor series). On the top 100, deep mode compared the real API of all 97 packages that have two series to compare, and release notes for the upgrade were found for 73% with this session's limited GitHub access. On the top 1,000 with a GitHub token (the monthly *PyPI coverage* workflow), 0.15.0 compared 94.5% in deep mode and found notes for 82.1%; type-stub and meta-package support and the release-note fixes since target most of the rest. Compiled-only packages (CUDA libraries) have no Python API to compare.

Changelogs are read from GitHub releases, root CHANGES files, per-version release notes in project docs (NumPy, SciPy, pandas, Pillow, Django, SQLAlchemy, NetworkX layouts), and migration guides (NumPy 2, SQLAlchemy 2, Matplotlib removals, `docs/migration.md`).

## Limitations

- Changelog parsing relies on maintainers writing structured changelogs
- Dynamic attribute access is detected when the name is written out (`getattr(np, "float_")`), not when it's computed at run time (`getattr(np, name)`); `getattr` with a default is a compatibility shim and isn't reported
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

- Support for TypeScript / Node.js packages (npm ecosystem)
