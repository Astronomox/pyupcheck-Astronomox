# Changelog

All notable changes to pyupcheck are documented here.

## [0.5.0] - 2026-09-25

### Added
- Works with any PyPI package whose import name differs from its install name (`pyyaml` -> `yaml`, `Pillow` -> `PIL`, `scikit-learn` -> `sklearn`, `protobuf` -> `google.protobuf`, ...). Import names come from installed metadata, a table of known mismatches, then a normalized-name fallback
- Namespace packages (`google.*`, `azure.*`) and packages with several top-level modules (`attrs` -> `attr` + `attrs`)
- Dependency discovery: every `requirements*.txt`/`requirements/*.txt`/`*.in`, `-r`/`-c` includes, line continuations, `--hash` options, environment markers, `#egg=` VCS requirements, `Pipfile`, PEP 735 `[dependency-groups]`, Poetry groups, PDM/uv dev dependencies, and conda-level packages in `environment.yml`
- `check-all --deep`
- When a package isn't installed, `check`/`check-all` use the version pinned in your dependency files instead of `0.0.0`

### Fixed
- `--deep` extraction now works for any wheel or sdist layout: import names that differ from the project name, `src/` layouts, single-module distributions, `.data/purelib`, `.pyi` stubs, and compiled extension modules
- `--deep` now reports methods removed from classes that still exist (e.g. Pillow 10's `ImageDraw.textsize`)
- `--deep` no longer matches a removed name against a same-named API in a different package, or a removed module by its last segment
- `import a.b.c` now tracks later `a.b.c.x` attribute use; relative imports are no longer mistaken for the package
- Package names are PEP 503 normalized, so `Flask_SQLAlchemy` and `flask-sqlalchemy` are one dependency and `ignore` matches either spelling
- `fix` rewrites every requirements file, matches any spelling of a name, and honours `ignore`
- First-run banner no longer prints in CI, pipes, `--quiet` or `--format` runs (it corrupted JSON output)
- Scanner crashed on Python 3.8 (`dict[str, str]` annotation)
- `packaging` is now a declared dependency; package and module versions agree
- Removed a PyPI token that was hardcoded in `upload.bat`

## [0.4.2] - 2026-08-13

### Fixed
- `diff` now validates both version arguments exist on PyPI before running (previously returned all changes for invalid versions)
- Removed dead deduplication code in deep mode that let a usage be flagged twice (once by changelog, once by API diff)
- Removed unused imports across apisurface, config, precise, scanner, welcome
- Removed dead `full` variable and unused loop index in banner typing
- Fixed empty f-strings (no placeholders) in cli and welcome

## [0.4.1] - 2026-08-11

### Added
- Live animated banner: fetches real version and release history from PyPI on every run
- `--deep` flag on `check`: downloads both package versions, extracts real API surfaces, diffs signatures
- Argument-level kwarg detection: flags removed parameters you actually pass in your code
- Fact-based findings marked `[FACT]` in output
- `apisurface.py`: public API surface extractor and differ
- `precise.py`: kwarg-aware risk matcher

### Changed
- Banner now types out character by character and shows live PyPI release history
- Private modules filtered from API surface diff (only public API reported)

## [0.4.0] - 2026-08-11

### Added
- Real API surface diffing engine
- Argument-level analysis in scanner (captures kwargs passed in calls)

## [0.3.4] - 2026-07-31

### Added
- `banner` command: shows live pyupcheck banner with version info
- Dynamic version number in banner (always reflects installed version)
- `upload.bat` release script for Windows

## [0.3.3] - 2026-07-31

### Fixed
- `install-completion` crash on PowerShell (removed invalid `err=True` kwarg)
- Progress bar in `check-all` no longer crashes when interleaving output
- `fix` command now compares against pinned version in requirements.txt (not installed)
- `--since` filter now correctly excludes changes with unknown dates
- `os.path.relpath` wrapped in try/except for Windows cross-drive paths

## [0.3.2] - 2026-07-31

### Fixed
- `re` import moved to top level in cli.py
- `suppress_errors` renamed from `quiet` in internal function to avoid confusion
- Progress bar stability improvements

## [0.3.1] - 2026-07-31

### Fixed
- `install-completion powershell` crash (console.print err kwarg)

## [0.3.0] - 2026-07-31

### Added
- `fix` command: rewrites pinned versions in requirements.txt to latest safe version (`--dry-run` supported)
- `ci-setup` command: generates GitHub Actions workflow and pre-commit config
- `install-completion` command: shell tab completion for bash, zsh, fish, PowerShell
- `--since YYYY-MM-DD` flag on `check` and `check-all`
- Progress bars on `check-all` and `outdated`
- `setup.cfg` dependency parsing
- `setup.py` AST-based dependency parsing
- `conda` environment.yml parsing
- Dynamic import detection (`importlib.import_module`, `__import__`)
- `.pyi` type stub scanning

## [0.2.1] - 2026-07-31

### Fixed
- Welcome banner version number now always reflects installed version

## [0.2.0] - 2026-07-31

### Added
- `check-all` command: batch check all project dependencies
- `outdated` command: list stale dependencies with major bump detection
- `diff` command: show changelog changes between two versions
- `cache-clear` command: wipe local response cache
- HTML, Markdown, and JSON report formats (`--format`, `--output`)
- 24-hour response cache
- Jupyter notebook (`.ipynb`) scanning
- `[tool.pyupcheck]` config in pyproject.toml
- `.pyupcheckignore` file support
- `--fail-on`, `--min-severity`, `--exclude`, `--quiet` flags
- First-run welcome banner

## [0.1.0] - 2026-07-31

### Added
- Initial release
- AST-based scanner for Python files
- Changelog fetching from GitHub releases and PyPI descriptions
- `check`, `scan`, `versions` commands
- JSON output for CI integration
