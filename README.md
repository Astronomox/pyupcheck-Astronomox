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

Reads `requirements.txt` and `pyproject.toml` (PEP 621 and Poetry), checks every dependency against its latest version.

```bash
pyupcheck check-all
pyupcheck check-all --format html -o report.html
```

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

These findings are marked `[FACT]` in the output because they come from the real code, not changelog prose. Deep mode is slower (it downloads and parses packages) but far more accurate.

## Features

- AST-based scanning: imports, from-imports, aliases, attribute chains, calls
- Argument-level analysis: knows which keyword arguments you pass
- Real API surface diffing in `--deep` mode
- Jupyter notebook (`.ipynb`) and type stub (`.pyi`) scanning
- Dynamic import detection (`importlib.import_module`, `__import__`)
- Changelog sources: GitHub releases, raw changelog files, PyPI descriptions
- Parses requirements.txt, pyproject.toml, setup.cfg, setup.py, environment.yml
- 24h response cache (`--no-cache` to bypass, `cache-clear` to wipe)
- Severity filtering with `--min-severity`
- Quiet mode (`-q`) for hooks and scripts

## Limitations

- Changelog parsing relies on maintainers writing structured changelogs
- Dynamic attribute access (`getattr(pkg, name)`) is not detected
- GitHub API is rate limited to 60 req/hr unauthenticated; pass `--github-token` or set `GITHUB_TOKEN` for higher limits

## Contributing

Contributions are welcome. Here is how to get started:

```bash
git clone https://github.com/Astronomox/pyupcheck-Astronomox.git
cd pyupcheck
pip install -e "."
```

**Releasing (for maintainers and forks)**

An `upload.bat` script is included to build and publish in one step. Get a PyPI API token from https://pypi.org/manage/account/token/ and run:

```
upload.bat YOUR_PYPI_TOKEN
```

It cleans old builds, builds the package, uploads to PyPI, installs the new version locally, and shows the banner to confirm. Never commit your token.

**Things that would genuinely improve the tool:**

- Better changelog parsing for packages that use unconventional formats (Sphinx-based changelogs, HISTORY files)
- `pip-tools` and `conda` lockfile support
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
- pip-tools and conda-lock support
- VS Code extension: inline warnings on imports
- Jupyter notebook deep scan (currently scans cells for imports, not API calls inside them)
- `pyupcheck explain` with AI-assisted migration guidance
- Support for TypeScript / Node.js packages (npm ecosystem)
