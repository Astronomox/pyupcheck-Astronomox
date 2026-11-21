# Changelog

All notable changes to pyupcheck are documented here.

## [Unreleased]

## [0.8.1] - 2026-11-21

### Added
- `migrate` rewrites imports of names that moved to another package when the changelog says so ("Import them directly from ``markupsafe``": `from flask import Markup` -> `from markupsafe import Markup`). The name is verified first: third-party packages by reading their latest release's API, standard-library modules by parsing their source (never by importing what a changelog names)

### Added
- Benchmark: 26 cases / 57 breaking lines (NumPy 2 aliases, NetworkX 3, scikit-learn 1.2, Werkzeug 2.1, pandas 2 methods, Django 4.0 `urlquote`, NumPy and Django patch-release controls) and `migrated.py` expectations that check `migrate`'s exact output
- Changelog parsing picks up unmarked snake_case names right after a removal verb ("Remove to_numpy_matrix & from_numpy_matrix"); NetworkX `doc/release/release_X.rst` notes are read

### Fixed
- A module `__getattr__` branch that only raises (scikit-learn 1.2's `load_boston` explanation) no longer makes the removed name look available
- Test modules shipped inside a package (`sklearn/datasets/tests`) are no longer treated as API
- `--deep` no longer suggests a same-named function in another package as the new home (NumPy 2 `round_` is not `numpy.ma.core.round_`), and `migrate` prefers the replacement the changelog names

## [0.8.0] - 2026-11-20

### Added
- `pyupcheck review --base REF`: checks only the dependencies whose version changed since `REF` (dependency files and lockfiles, committed or not), from their old version to their new one, and prints a Markdown report with the breaking usages and how many `migrate` can fix
- GitHub Action (`uses: Astronomox/pyupcheck-Astronomox@vX.Y.Z`): runs `review` on pull requests, posts one comment and updates it on later pushes, writes the report to the job summary, and can fail the check (`fail-on`)

### Fixed
- A usage reported both as breaking (removed) and as deprecated (the earlier notice) is now reported once, as breaking; verified findings win over changelog ones

## [0.7.0] - 2026-11-20

### Added
- `pyupcheck migrate PACKAGE [VERSION]`: rewrites your code for an upgrade. Shows a diff by default, `--write` applies it. Only mechanical changes verified against the target version's API: arguments that became keyword-only, renamed keyword arguments, renamed or moved names (imports keep their local name via `as`). Everything else is listed as needing a manual change; exit code 1 while any remain
- Changelog replacements are paired one-to-one ("`a`, `b` in favour of `x`, `y` respectively"); one replacement named for several APIs is treated as ambiguous
- Changelog parsing understands "aliases of", ``Name(param=...)`` mentions, and generic sub-headings ("Miscellaneous") inside a deprecation or removal section

### Fixed
- "Deprecated ``Retry`` option ``x`` in favor of ``y``" was read as `y.x`
- In "the ``force_text()`` alias of ``force_str()`` is deprecated", the replacement `force_str` was reported as deprecated too
- Items under a "Miscellaneous" sub-heading of "Features deprecated in X" were ignored

### Changed
- Release and benchmark workflows use `actions/upload-artifact@v7` / `download-artifact@v7` (v4 and v5 run on the deprecated Node.js 20)

## [0.6.0] - 2026-11-20

First release published from GitHub Actions. 0.5.0 was never uploaded to PyPI, so coming from 0.4.3 also read the 0.5.0 section below.

### Added
- `check-all` checks dependencies in parallel (`--jobs/-j`, default 8) over one shared keep-alive HTTP connection pool: an 8-package project went from 95s to 5s cold (15s with `--deep`, under 4s warm). Output order is unchanged, and a crash in one package is reported for that package instead of stopping the run
- The scanner follows package objects through your own code: helper functions that return them (by return annotation or by what they `return`), attributes stored on `self` (`self.engine = create_engine(...)` then `self.engine.execute()`), and methods called on a call result (`get_engine().execute()`, `pd.DataFrame(...).append(...)`)
- Changelog parsing understands "Remove keyword ``x`` from ``f`` and ``g``" as parameters of `f` and `g`
- Benchmark cases for inference (SQLAlchemy and pandas objects reached through helpers, attributes and chains): 18 cases, 41 breaking lines
- Lockfiles: `uv.lock`, `poetry.lock`, `pdm.lock`, `pylock.toml` / `pylock.*.toml` (PEP 751) and `Pipfile.lock`. The locked version is the baseline for `check`, `check-all` and `outdated`
- `check-all` also checks packages your code imports that are only in the lockfile because another dependency pulls them in (e.g. `urllib3` via `requests`), marked "undeclared"
- Release notes kept in project docs are read: per-version files (NumPy/SciPy `doc/source/release/X-notes.rst`, pandas `whatsnew/vX.rst`, Pillow `docs/releasenotes/X.rst`, Django `docs/releases/X.txt`), series files (SQLAlchemy `changelog_XY.rst`) and `docs/changelog.*`
- Changelog parsing understands reST tables ("removed | use instead"), sub-headings inside a "Backwards incompatible" section, nested bullet lists, "``x`` argument of ``f``", several replacements after "in favor of", and conventional aliases (`np.NaN` means `numpy.NaN`)
- `--deep` follows `from .module import *` re-exports, and flags parameters that lost their default (PyYAML 6 `yaml.load(f)` needs `Loader`)
- The scanner records from-imported names used without a call: base classes, decorators, arguments (`class Settings(BaseSettings)`)
- Benchmark grown to 16 cases / 34 breaking lines (Django, SciPy, Matplotlib, PyYAML, Pydantic, Flask patch control)
- Accuracy benchmark (`benchmarks/`, `make bench`): real upgrades with known breaks, scored for recall and precision. Runs weekly in CI
- `--deep` sees names served by a module `__getattr__` (pandas `Int64Index`, Pillow `Image.ANTIALIAS`) and reports them when they disappear
- `--deep` follows annotated return types: `engine = create_engine(...); engine.execute()` matches changes to `Engine.execute`
- Changelog parsing treats a wrapped bullet as one item, reports every API an item names, and understands "``X`` options ``Y``" as parameters of `X`
- Changelog matching checks the keyword arguments you pass (`Retry(method_whitelist=...)`)
- "Remove previously deprecated code" entries promote every earlier deprecation to a removal
- `--deep` detects new required parameters, parameters that became keyword-only or positional-only, removed module/class constants, and re-exports that can no longer be imported (e.g. Flask 3's `from flask import Markup`)
- `--deep` suggests where a removed function or class now lives
- The scanner follows objects: `s = requests.Session(); s.get(verify=False)` resolves to `requests.Session.get`, including `with ... as s` and type-annotated parameters (`def f(s: Optional[requests.Session])`)
- Changelog parsing understands section headings (Markdown, reST, bold, `Heading:`): bullets under "Breaking changes"/"Removed" count without keywords, "Deprecations" sections are deprecations
- Findings carry a confidence level and the suggested replacement API when the changelog names one
- Release tags like `flask-3.0.1`, `release-2.0` and `Version 4.0` are mapped to versions
- Each result lists which sources could not be read; packages where nothing could be read are reported as NOT CHECKED (terminal, Markdown and HTML) instead of OK

### Changed
- Releases are published from GitHub Actions on a version tag using PyPI Trusted Publishing; `upload.bat` (which put the PyPI token on the command line) and `make publish` are removed. `bump.py` also dates the CHANGELOG and accepts `--yes`. See RELEASING.md
- Baseline order is now: lockfile, exact pin, installed version (if it satisfies the project's constraint), constraint lower bound. Previously the installed version came first
- Requires Python 3.10+ (3.8 and 3.9 are end-of-life and no longer available on CI runners); CI tests 3.10-3.14

### Fixed
- `check-all -f json` (and `md`/`html` to stdout) printed "Found N dependencies to check" before the report, so redirecting it to a file produced invalid JSON
- Cache files are written atomically, so parallel checks never read a half-written entry
- A changelog item that only mentions an API ("Passing dtype to ``DataFrame`` now raises") is reported as a warning, not as the API being removed
- API names from changelogs are compared case-sensitively (`numpy.NaN` removed is not `numpy.nan`)
- A bare `import numpy` no longer collects every change in the package as a warning
- An invalid or expired `GITHUB_TOKEN` no longer hides public changelogs (GitHub answers 404 for raw files sent with a bad token); requests fall back to anonymous
- Network failures are no longer cached for 24h as "no changelog" / "no downloadable files"
- `--deep` works for yanked releases when that exact version is requested
- `--deep` no longer flags a name that is still available at the path you use (`np.nan` after NumPy 2 moved `numpy.core`)
- A changelog entry naming the package itself ("import it from ``flask``") no longer flags every usage
- An exact pin (`==2.25.0`) no longer stops a dependency from being checked against newer versions
- The target version respects the project's constraint (`>=2,<3` is never checked against 3.x), skips pre-releases and yanked releases, and is never older than the current version
- An installed version that falls outside the project's constraint is no longer used as the baseline; an unpinned range uses its lower bound. With no baseline the package is NOT CHECKED rather than compared against `0.0.0`
- Keyword false positives: "Fixed crash when removing...", "dropdown", "outbreak", "breakpoint" and keyword hits under Bug fixes/Features headings are no longer reported as breaking
- Changelog entries whose version can't be determined are dropped instead of always being reported
- GitHub rate limits (403/429) are reported instead of silently yielding "no changes"
- `--deep` no longer reports a function as removed when it is still importable through a re-export, `__all__`, or a base class
- `--deep` no longer matches a removed method against a module-level function of the same name (`click.command` vs `Context.command`); constructor changes now match `pkg.Cls(...)` calls
- `fix` no longer rewrites a pin when no changelog could be read

## [0.5.0] - 2026-09-25 (not published to PyPI; included in 0.6.0)

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
