# Changelog

All notable changes to pyupcheck are documented here.

## [Unreleased]

## [0.17.1] - 2026-11-25

### Changed
- `pyupcheck banner` fits any terminal. The block logo is drawn in one row when there's room, stacked as PYUP over CHECK on medium widths, and as a one-line wordmark on phones and split panes. Below the logo, the version box, links, releases and commands all reflow to the width, and links are clickable in terminals that support them. The logo has a colour gradient, and terminals that can't print block characters get plain text. PyPI is asked in the background while the logo is drawn, so the banner shows up faster. `PYUPCHECK_NO_ANIMATION=1` turns the animation off
- The first logo row no longer starts one column to the right of the others
- `pyupcheck --help` lists commands in a sensible order with descriptions short enough to read on narrow terminals, and `-h` works as well as `--help`
- The banner suggests `python -m pip install -U pyupcheck`, which also works on Windows when `pip.exe` can't update itself

## [0.17.0] - 2026-11-25

### Changed
- Terminal output redesigned. Each package shows a status badge (`✗ 2 BREAKING`, `! 1 DEPRECATED`, `✓ OK`, `? NOT CHECKED`), and notes wrap indented under their finding instead of at column 0. `check-all` ends with one summary line, a single note for a source that couldn't be read (rather than the same note under every package), and the `pyupcheck migrate` commands for the packages that break. Rich's automatic highlighting is off, so versions are no longer split into coloured fragments. Consoles that can't print Unicode get ASCII symbols
- Website redesigned (`index.html`). It's a normal scrolling page instead of sliding panels, current for every feature, with the measured accuracy, a terminal replaying real output, `migrate` and pull-request examples, dark mode, and phone layouts. It respects reduced motion
- README opens with a real screenshot of `check-all` (`docs/check-all.svg`) and the measured numbers, instead of an invented example

## [0.16.1] - 2026-11-25

### Fixed
- Release-note lookups sent bursts of requests that GitHub's raw file host answered with 429 Too Many Requests (about 450 lookups in the top-1,000 run, and a large project's `check-all` could hit it too). Requests to one host are now capped at 8 at a time, and a 429 is retried once after the wait the server asks for (up to 20 s)
- With a GitHub token, a repository's release-note files are listed with one API call, so lookups skip paths that don't exist instead of probing each one
- Files are read from `HEAD`, which GitHub resolves to the default branch: no more branch detection requests

### Changed
- Top 1,000 PyPI packages (0.16.0, with a token): deep mode 94.5% -> 96.7%; without a token, release notes for the top 100 are now found for 79%

## [0.16.0] - 2026-11-23

### Added
- Deep mode for type-stub packages: `requests-stubs/api.pyi` is `requests.api` (PEP 561), and `types-requests`, `pandas-stubs` and the like are checked against the modules they describe (`import requests`, `import pandas`)
- Deep mode for meta-packages that ship no code and pin the package that does (`fastmcp` -> `fastmcp-slim==4.2.5`, `logfire` -> `logfire-sdk==6.0.0`): their API is that package's
- Release notes in more places: the repository's default branch is detected (API, else `main`/`master`/`trunk`/`develop`) instead of assuming `main`; a changelog that only points to another file is followed; per-version docs at `docs/releases/v{v}.rst` (tornado), `docs/source/release-notes/{v}.rst` (flake8) and `docs/source/whatsnew/version{major}.rst` (IPython); versions written as list items (`- 0.10.0: ...`, tabulate)

### Fixed
- When a project had GitHub releases that didn't cover the upgrade (tags without notes), its changelog file was never read (boto3, decorator, distlib in the top 1,000)

## [0.15.0] - 2026-11-23

### Added
- Release notes in more places: Keep a Changelog headings (`## [15.0.0] - 2026-04-12`), linked versions (`## [26.1.0](url)`), `# Release 4.16.0 (date)`, `Revision 0.6.5, released ...`, reST link titles (`` `v2.15.1 <url>`__ ``), towncrier's centred titles, `pytest 8.3.0 (date)` and `# Apache Arrow 26.0.1 (date)`; `CHANGES.txt`, `NEWS`, `HISTORY` and more file names; pytest's `doc/en/changelog.rst` and other docs locations
- The changelog a project links from PyPI: a GitHub file read raw, a Sphinx/Read the Docs page read from the source Sphinx publishes (`_sources/<page>.rst.txt`), and a package's own folder in a monorepo (`google-cloud-python/packages/google-auth`)
- When GitHub's API rate-limits release lookups, the releases Atom feed is read instead (the latest releases)
- `ChangeReport.versions_read`: the versions in the upgrade whose notes were read, with or without breaking changes
- `benchmarks/coverage.py` and a monthly *PyPI coverage* workflow: how many of the most-downloaded packages pyupcheck can check, deep and by release notes

### Changed
- Top 100 PyPI packages: release notes found for the upgrade 53% -> 73% here (GitHub-releases-only projects need a token); deep mode compares all 97 comparable packages
- Faster cold runs: release-note lookups probe candidate files concurrently and fetch their sources together (5x faster: 4 packages 20 s -> 4 s); surface extraction skips bundled test suites and writes its cache with the C JSON encoder (pandas 9.4 s -> 4.5 s); the API diff indexes names once instead of per lookup (SQLAlchemy 1.8 s -> 0.03 s). Results are identical

## [0.14.0] - 2026-11-23

### Added
- `pyupcheck explain PACKAGE [VERSION]`: sends each breaking usage (a few lines of code around it), the release note behind it and the verified replacement to Claude (`claude-opus-5-5`) and streams back a before/after for each. Optional: `pip install "pyupcheck[ai]"` and `ANTHROPIC_API_KEY` (or `ant auth login`); says how much code it sends before sending, `--dry-run` sends nothing, `--max`, `--effort`. Requests a safety classifier declines are retried on another model (`--no-fallbacks` to turn off)
- VS Code extension (`editors/vscode`): diagnostics on breaking and deprecated lines (on the right cell for notebooks), a quick fix that runs `pyupcheck migrate`, a status bar count, re-checks when dependency files are saved. Built as a `.vsix` in CI
- `getattr(np, "float_")` with a literal name is a usage of `np.float_`: checked like any other, and `migrate` rewrites the string for a rename in the same place. `getattr` with a default (a compatibility shim) and computed names are left alone
- Scheduled removals: "deprecated in 1.0 and will be removed in 1.2" counts as a removal in 1.2 even when 1.2's notes don't repeat it (scikit-learn's `load_boston`, `plot_confusion_matrix`). `check` reads the notes of the last three feature releases before yours for these
- scikit-learn and astropy release notes in their docs (`doc/whats_new/v1.2.rst`, `docs/whatsnew/5.0.rst`)
- Findings in JSON output carry `cell` and `cell_line` for notebooks

### Changed
- Changelog-only recall on the benchmark: 70.9% -> 80.6%, still with no false alarms (`--deep` stays at 100%)
- A nested list under a statement ("Remove previously deprecated code.") is one item per change, so each is classified on its own and the parent is a bare sweep; a child that doesn't say what happened takes its parent's kind ("The old names are deprecated.")
- Release notes that name an API bare (`BaseResponse`) match it wherever your code reaches it, when the name is distinctive (not `get`, `load`, `Request`...)
- A "removed previously deprecated code" sweep removes only what was deprecated in the same major series (or the previous one, for x.0), and only APIs the deprecating sentence names

### Fixed
- "Deprecates X in 1.0 and it will be removed in 1.2" was read as a removal in 1.0
- A list's destination ("merged into the ``Request`` and ``Response`` classes") is not something that was removed

## [0.13.0] - 2026-11-23

### Added
- conda and pixi support: `conda-lock.yml` and `pixi.lock` (format versions 4 to 7) give locked versions, and `pixi.toml` and `[tool.pixi]` in `pyproject.toml` give declared dependencies (conda and PyPI, every feature, environment and target). `review` and `watch` follow changes to all four files
- Only Python packages are taken from a conda environment: conda packages that don't depend on `python`, or that pixi's lock records as not on PyPI (`purls: []`), are skipped, so `openssl`, `libblas` or `rust` are never looked up on PyPI
- Conda names become PyPI names: from the lock's package URLs where pixi records them (`python-build` is `build`), and a built-in list otherwise (`pytorch` is `torch`, `matplotlib-base` is `matplotlib`). Also applied to `environment.yml`
- Conda version specs become PEP 440 ones (`2.1.*`, `=1.11` is `1.11.*`, a bare version is exact)
- A lock with several versions of a package for this platform (pixi locks several environments) gives the highest
- A `pixi.toml` that Python's TOML parser rejects (pixi accepts TOML 1.1, such as an inline table over several lines) is read line by line for its dependency tables

## [0.12.0] - 2026-11-23

### Added
- `pyupcheck watch`: watches requirements files, `pyproject.toml` and lockfiles, and when a version changes checks each package that moved, from its old version to its new one, with what `migrate` can fix. Packages added or removed are listed. A file is read once it stops changing, and one that can't be read never replaces the last good state
- `watch --releases MINUTES` (default 60, `0` off): looks for new PyPI releases your constraints allow for each direct dependency, and checks each new one against your code before you upgrade. Moving to a release yourself silences it. `--bell` rings on breakage, `-q` prints only what breaks

### Fixed
- Terminal output dropped text in square brackets (rich read `[cell 3]` in notebook code as a style tag) and showed release-note markup (`:pr:` references, ``` ``double backticks`` ```)

## [0.11.0] - 2026-11-21

### Added
- `pyupcheck migrate` rewrites Jupyter notebooks: fixes land in the right cell, new imports go in the cell with the other imports, and magics, shell lines, outputs and metadata are left exactly as they were
- Benchmark notebook case (NumPy 2): 52 cases / 103 breaking lines, `--deep` still 100% with no false alarms

### Fixed
- Notebook cells were scanned one by one, so a name imported in one cell (`import numpy as np`) was not followed into the others and every later `np.…` call was missed. Cells are now scanned as one module
- Notebook findings pointed at a line within the cell, which GitHub annotations and SARIF put on the wrong line of the `.ipynb` file and which could merge two findings from different cells; they now point at the notebook file's own line and say which cell
- Advice like "use ``np.asarray`` with a proper dtype instead" was taken as a drop-in replacement, so `migrate` rewrote `np.asfarray(x)` to `np.asarray(x)`, which returns integers for integer input. Replacements qualified by "with …", "and pass …", "passing …" and similar are now manual
- Annotation and SARIF messages repeated the replacement when the release note already named it

## [0.10.0] - 2026-11-21

### Added
- `pyupcheck outdated --safe`: a "Highest safe" column with the newest version that breaks none of your code, so a breaking latest release doesn't leave you stuck on an old one. Candidates are the newest release of each minor series your constraint allows; the latest is checked first, then a binary search (about log2(n) deep checks). A version that couldn't be checked never counts as safe

### Changed
- `pyupcheck fix` moves each pin to the highest *safe* version, verified with deep checks (`--no-deep` for changelog only), instead of the latest version or nothing; it names the packages held back and how many usages their latest release breaks. Runs dependencies in parallel (`--jobs`)

## [0.9.0] - 2026-11-21

### Added
- `-f sarif` (SARIF 2.1.0, validated against the official schema) for GitHub code scanning and other SARIF viewers, with stable per-issue fingerprints that survive code moving between lines
- `-f github`: GitHub Actions annotations, one per finding, shown on the line in the PR diff
- `review --annotations` and `review --sarif FILE`; the GitHub Action annotates findings by default (`annotations: "false"` to turn off) and can write a SARIF file (`sarif: path`) for `github/codeql-action/upload-sarif`
- Annotations and SARIF carry breaking and deprecated findings only, once per line and message, with release-note markup cleaned

### Fixed
- A `:pr:`/`:issue:` reference cut off by truncation was left in PR comments

## [0.8.3] - 2026-11-21

### Added
- Benchmark grown to 51 cases / 100 breaking lines (NumPy 2 types and constants, Django 4.0 and 5.0, Werkzeug 2.1 and 3, Flask 2.3, Jinja 3.1 filters, urllib3 2 contrib, Pillow 10, scikit-learn 1.2, Matplotlib 3.9, PyYAML 6 `load_all`, NetworkX 3 I/O, pandas 2, Pydantic 2, SQLAlchemy 2), including 8 negative controls (patch releases, and Jinja 3.0 where `Markup` is only deprecated). `--deep` catches 100/100 with no false alarms
- Changelog lists that name one API in full and its siblings by short name ("``django.utils.translation.ugettext()``, ``ugettext_lazy()``, ... are removed") qualify the short names

### Fixed
- reST-escaped names in migration-guide tables (`unicode\_`) were not read, and the replacement column (`np.str_`) was reported as removed instead
- "Deprecate the ``werkzeug.urls`` module, except for ``uri_to_iri`` and ``iri_to_uri``" flagged the kept functions and the module itself; names after "except (for)" are now excluded
- "Positional use of ..." was read as a "use X instead" replacement phrase

## [0.8.2] - 2026-11-21

### Added
- `migrate` rewrites attribute uses of names moved to another package and adds the import: `flask.Markup(...)` -> `markupsafe.Markup(...)` plus `import markupsafe` (once per file, after the existing imports, skipped if already imported). Only plain dotted names are rewritten; methods called on the moved name (`flask.Markup(...).format(...)`) are handled
- Benchmark case `flask_3_attr`: 27 cases / 59 breaking lines; `migrate` exact on 8/8

## [0.8.1] - 2026-11-21

Changelog-only accuracy on the benchmark: 61.4% -> 73.7% of breaking lines caught, still with no false alarms.

### Added
- Migration guides are read alongside release notes: per-series guides (NumPy `numpy_2_0_migration_guide.rst`, SQLAlchemy `migration_20.rst`), per-version `removals.rst` / `deprecations.rst` (Matplotlib), and `docs/migration.md`-style guides for major-version jumps (Pydantic V1 -> V2)
- Markdown tables are parsed ("| Pydantic V1 | Pydantic V2 |"), a table's header ("removed member") marks its rows, and bare names in a table's first column count as APIs. Only plain "use ``X`` instead" cells give a replacement; advice with conditions ("use ``np.asarray`` with a float dtype") marks a removal without one, so `migrate` never rewrites it
- "Moved" headings mark breaking changes; a list entry that opens with the API under "Removals" ("``get_cmap``; use ...") is a removal
- `migrate` rewrites imports of names that moved to another package when the changelog says so ("Import them directly from ``markupsafe``": `from flask import Markup` -> `from markupsafe import Markup`). The name is verified first: third-party packages by reading their latest release's API, standard-library modules by parsing their source (never by importing what a changelog names)

### Added
- Benchmark: 26 cases / 57 breaking lines (NumPy 2 aliases, NetworkX 3, scikit-learn 1.2, Werkzeug 2.1, pandas 2 methods, Django 4.0 `urlquote`, NumPy and Django patch-release controls) and `migrated.py` expectations that check `migrate`'s exact output
- Changelog parsing picks up unmarked snake_case names right after a removal verb ("Remove to_numpy_matrix & from_numpy_matrix"); NetworkX `doc/release/release_X.rst` notes are read

### Fixed
- A module `__getattr__` branch that only raises (scikit-learn 1.2's `load_boston` explanation) no longer makes the removed name look available
- Test modules shipped inside a package (`sklearn/datasets/tests`) are no longer treated as API
- `--deep` no longer suggests a same-named function in another package as the new home (NumPy 2 `round_` is not `numpy.ma.core.round_`), and `migrate` prefers the replacement the changelog names

### Fixed
- The GitHub repository was taken from the first GitHub link in a package's metadata, which for Pydantic (and others) is a `github.com/sponsors/...` funding link; the source link is now preferred and sponsor links are skipped
- "use ``X`` instead" under a Removals heading no longer turns the removal into a deprecation
- `SyntaxWarning`s from parsing old third-party sources are no longer printed
- Tests can no longer reach the network or the real `~/.cache/pyupcheck`

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
