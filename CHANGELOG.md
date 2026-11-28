# Changelog

All notable changes to pyupcheck are documented here.

## [Unreleased]

## [0.23.0] - 2026-11-28

### Fixed
Checking a real Flask 1.0 app ([flask-realworld-example-app](https://github.com/gothinkster/flask-realworld-example-app)) against today's releases gave 46 findings, and running the new versions showed 32 of them were false. All four causes are fixed. It now gives 15, each confirmed, including one that was missed before. Four benchmark cases keep each of them fixed.
- **Deep mode** reported 19 uses of `Blueprint.route()` as removed in Flask 3. The method moved to a base class imported under another name (`from .sansio.blueprints import Blueprint as SansioBlueprint`), and that name wasn't followed. Attributes that a base class now sets on instances (`self.root_path = ...`) also looked removed
- **Release notes** that change a member or argument were matched to every use of its owner. "Rename ``DateTime``'s ``dateformat`` Meta option" flagged every `fields.DateTime(...)`; "passing a string argument as ``only`` to ``fields.Nested``" and "passing the string `"self"` to `fields.Nested`" flagged every `Nested(...)`; notes about positional arguments flagged `@pytest.fixture(scope=...)`. Each now matches only the calls it describes
- Conditional notes ("If only a name is passed to `option()`, ...") are mentions, not changes
- `` ``@pytest.fixture`` `` in a note now names `pytest.fixture`; it was an unknown API, so pytest 4's removal of positional fixture arguments went unreported
- **Missed break:** objects created in one module and imported in others (`db = SQLAlchemy()` in `extensions.py`, `from .extensions import db` everywhere else) are now followed across the project. Flask-SQLAlchemy's `db` hands out SQLAlchemy's own names, so `db.Binary(...)` is checked as SQLAlchemy's `Binary`, removed in 1.4. Before, only the 4 lines in the module that created `db` were seen; now all 99 are
- Dependencies installed from a folder, a git repository or a URL (`name @ git+...`, Poetry `path =`/`git =`, `[tool.uv.sources]`) were looked up on PyPI, where a different package with the same name could be checked instead

### Added
- The GitHub CLI's token is used when you're signed in (`gh auth login`) and no token is set: 5,000 GitHub API requests an hour instead of 60. `GH_TOKEN` is read too. `gh` is never run from inside the project being checked, and `PYUPCHECK_NO_GH_AUTH=1` turns this off
- Once anonymous GitHub API requests are rate limited, the rest of the run reads the releases feed instead of spending requests on 403s
- Monorepos without a uv workspace: local path dependencies inside the project (Poetry `path =`, uv path sources, `name @ file:...` including PDM's `${PROJECT_ROOT}`) are read as members; `review` follows their files too
- `scripts/screenshot.py` regenerates the README's screenshot from a real run

### Internal
- If a rich release changes the internal step that trims trailing spaces, output is printed untrimmed instead of failing
- Measured: the 0.22.1 change to low-confidence notes demoted 9 findings on two real projects and none in the benchmark, all confirmed not to be breaks. Reading every release of an upgrade made no measurable difference to a cold `check-all` of 54 packages (about 35 s either way)
- Benchmark: 56 cases, 107 breaking lines. `--deep` catches 100%, the API diff alone 98.1%, release notes alone 83.2%, all with no false alarms

## [0.22.1] - 2026-11-28

### Fixed
- False "breaking" findings on real projects. A call that passes a parameter a release note names was counted as breaking even when pyupcheck itself rated the note low confidence; such notes are now listed as worth a look, as every other low-confidence match already was. Found by running `check-all` on [microblog](https://github.com/miguelgrinberg/microblog): SQLAlchemy 2.1 reported "2 breaking" (and exited 1) for notes about `relationship(primaryjoin=...)` and `mapped_column(default=...)` that don't break it; it now reports 0, which deep mode confirms. The benchmark is unchanged (release notes 83.5% recall at 100% precision)
- Sphinx links to other sections and pages (``:ref:`change_12168` ``, ``:doc:`...` ``) were read as API names
- When GitHub releases covered only some of the releases in an upgrade, the changelog file wasn't read, so changes in the other releases were missed (Flask 2.3's `Markup` deprecation, with 3.0.0 on GitHub). Every release in the upgrade must now have been read before other sources are skipped
- In a uv workspace, run from its root, the members' dependencies were missed: they showed as "imported but not declared", and their constraints were ignored (`sentry-sdk` was checked at 3.x despite `<3`). Members' `pyproject.toml` files are read (globs and `exclude` included, never outside the project), and `review` and `watch` follow them
- `pyupcheck ui` showed a 61-column table however narrow the window, so the status was off-screen below about 62 columns. The table now drops the uses, current and latest columns in turn so the status is always in view, and lays itself out again as the window is resized
- `migrate`'s diff on a narrow terminal broke long lines at spaces, so `-x =` and the rest of the line came apart. Long lines are now cut at the terminal's width and each piece keeps its `-` or `+`
- Lines no longer end in spaces (rich pads table cells and wrapped text); a status badge's coloured padding is kept

### Internal
- CI also runs the tests without the optional extras, as the release does and most people install: a UI test that needed Textual stopped the first 0.22.1 release run before anything was published
- Coverage counts branches now, at 90%; CI fails below 89%. Release-note lookups are tested end to end on real release notes (Flask, NumPy, requests) served by a fake web, covering every source and failure; `changelog.py` 80% to 90%, `tui.py` 82% to 96%

## [0.22.0] - 2026-11-28

### Added
- `review` shows its results in the terminal the way `check-all` does when you run it yourself, and writes Markdown when piped or with `-o`; `--format terminal|md` chooses
- `migrate` is laid out in two parts, what pyupcheck fixes (with a coloured diff) and what needs you, and exits 1 while manual changes remain or a file couldn't be written
- `explain --dry-run` shows the exact prompt in a panel and ends by confirming nothing was sent

### Changed
- Every command fits the terminal it runs in, from a 40-column split pane up. Tables measure their columns and pick the widest layout that fits: with "Your code", without it, with the two version columns merged, then just the package and its status. The status is never cut short and the package name gives way first. On narrow terminals `outdated`, `fix` and `scan` list each row with its details under it, and a finding's code moves to a line of its own instead of being cut off
- `review`'s git errors say what's wrong ("isn't a git repository", "git isn't installed") instead of showing git's output

### Fixed
- `watch` said "1 packages" and "1 direct dependencies"; `outdated --safe` said "usage(s)"
- `diff`, `ci-setup` and some progress lines printed package names, versions, paths and error text as rich markup, so a name like `[red]x` changed how the output looked; they're shown exactly as written now
- Release notes with code like ``np.cast[dtype](arg)`` lost the `(arg)` in pull-request comments: it was taken for a link. Code spans are now kept exactly, and only real link targets are removed

### Internal
- `cli.py` held every command and their logic, with module-level state for options. It is now three parts: `project.py` (what pyupcheck knows, never printing), `output.py` (presentation, with a frozen `View` instead of globals) and `cli.py` (the commands)
- Test coverage rose from 80% to 90% (`project.py` 54% to 98%, `cli.py` 64% to 93%, `explain.py` to 100%), and CI now fails below 88%. `tests/test_layout.py` renders every command at seven widths; `examples/` is linted too
- CONTRIBUTING.md describes the code as it is now

## [0.21.0] - 2026-11-27

### Added
- `pyupcheck check-all PACKAGE...` checks only the packages you name (`check-all flask numpy`); a name that isn't a dependency gets a "did you mean" suggestion
- `versions` shows each release's date and marks the latest, the one your project is on (lockfile, pin or installed), yanked releases and pre-releases; the newest 15 by default, `--all` for every one
- Configuration mistakes are reported (on stderr, so they never mix into `-f json`): unknown keys in `[tool.pyupcheck]` and values pyupcheck doesn't accept, with a suggestion when one is close. Before, they were silently ignored
- The JSON report carries `schema_version` (1) and `pyupcheck_version`, for tools that read it
- `check-all`'s summary says how long the check took

### Changed
- One table style across `check-all`, `outdated`, `fix` and `versions`; majors marked the same way everywhere
- `fix` prints one table: each pin, where it moves (or that it stays), the latest release, and why, with the `migrate` command for releases that break your code
- `diff` groups changes by release, newest first, shows each once as plain prose (no `:pr:` roles or doubled backticks) with wrapped lines indented, and starts with a count by kind
- `scan` groups lines by file and names every use on a line, with how it's used (call, attribute, import)
- Release-note items keep up to 300 characters, cut at a word: more of each note is read, and changelog-only recall rises from 80.6% to 83.5%, still with no false alarms

### Fixed
- Found by the stricter checks now run in CI (ruff and mypy, both clean):
  - `pyupcheck ui` defined a `visible()` method that replaced the attribute Textual uses to decide whether a widget is shown
  - the banner's info block reused the name of its `commands` flag for a list
  - a closure in changelog fetching captured a loop variable instead of binding it
- `fix` rewrote requirements files in place, not atomically, and changed their line endings on Windows; it now replaces them in one step, exactly as they were apart from the moved pins
- `zip()` calls that rely on equal lengths are now strict, so a mismatch fails loudly instead of silently dropping data

### Internal
- Type hints modernised to Python 3.10 syntax throughout; mypy (with `check_untyped_defs`) and ruff (pycodestyle, pyflakes, isort, bugbear, pyupgrade, simplify, ruff's own rules) are clean and run in CI (`make lint`)
- `explain` keeps the cause of each error (`raise ... from`); file replacement shared by `migrate` and `fix`

## [0.20.0] - 2026-11-27

### Security
A full security review of everything pyupcheck reads but doesn't control. Every fix has a test (`tests/test_security.py`); [SECURITY.md](SECURITY.md) has the threat model.
- **VS Code extension:** a repository's own `.vscode/settings.json` could set `pyupcheck.path`, and the extension activates on opening a Python project, so opening a malicious repository could run a program of its choosing. That setting is now ignored in workspaces you haven't trusted (and is machine-overridable). The migrate quick fix no longer types a shell command into a terminal (which mixed in names from the repository, and quoted for POSIX shells only): it runs pyupcheck directly with an argument list, only for valid package names and versions, shows the diff, and writes after you confirm
- **Requests to internal addresses (SSRF):** a package's PyPI "Changelog" link could make pyupcheck fetch any URL, including `http://`, `localhost`, private networks and the cloud metadata service (169.254.169.254) from a CI runner. Every request and redirect is now `https` on port 443 to a public host, at most 5 redirects
- **Memory exhaustion:** downloads, pages and archive members had no size limit when PyPI didn't state one. Responses are capped (32 MB; 300 MB for distributions, counted after decompression), and so are archive members (16 MB each, 768 MB in all, 200,000 files)
- **Terminal escape injection:** release notes, PyPI text and code lines from a cloned repository could carry control sequences that retitle the terminal, clear it or forge clickable links, and bidirectional overrides. Everything shown is cleaned, in the terminal, `ui`, reports and `explain`'s output; in GitHub Actions, text can't start a workflow command
- **Pull-request comments:** release notes could @-mention people, embed images, HTML or links, and a code line with a backtick broke out of its code span. Comments and Markdown reports now make all of it inert. Lockfile entries that don't look like real package names and versions are ignored
- **GitHub Action:** an input was pasted into a script (now passed through the environment); the action could update someone else's comment that started with its marker (now only its own); `base` must be a git revision, never an option (also checked by `review --base`)
- **Workflows:** every workflow runs with a read-only token unless a job asks for more, and third-party actions are pinned to commits. `ci-setup` writes a workflow that does the same
- **Hostile files:** a deeply nested notebook, Python file or package module crashed the whole check; it's now skipped. The releases feed is refused if it has a DTD (no entity-expansion attacks). Release-note tidying was quadratic on some input (a minute for 1 MB) and is now linear
- **migrate --write:** never writes through a symlink or outside the project, replaces each file atomically, and keeps its permissions and line endings (it used to change them on Windows)
- **Cache:** readable only by you (`0700`/`0600`); with a token it can hold a private repository's file listing
- **Dependencies:** `h11>=0.16.0` (versions before it have a known vulnerability, PYSEC-2026-348, and `httpx>=0.24` allowed them), so `httpx>=0.27.0`
- **Website:** a Content-Security-Policy that allows only its own script (by hash), its fonts and PyPI, plus `nosniff`, no framing and a referrer policy on Vercel

## [0.19.1] - 2026-11-27

### Added
- `pyupcheck ui` shows where your code uses each package: the details list the files with the most uses and their first lines (breaking lines marked), and `u` opens every line, file by file, with what breaks marked. Found when the package is selected, and found again after a re-check or after fixes are applied

### Fixed
- `pyupcheck ui` cut off "✓ up to date" and long versions ("0.165."): columns were sized from the "… checking" rows. They now have fixed widths, with the package column sized to the longest name

## [0.19.0] - 2026-11-27

### Added
- `pyupcheck ui`: a full-screen app (optional extra: `pip install "pyupcheck[ui]"`, built on Textual). Every dependency fills in its row as it is checked, with what needs attention first, and a running total underneath. The side panel shows the selected package's breaking lines with the release note and replacement, grouped notes worth a look, and source notes. `m` previews what `migrate` would rewrite (a coloured diff and the changes that need you); `w` applies it after a y/n confirmation, then re-checks the package. `f` filters (all, needs attention, used by your code), `d` toggles deep mode, `r` re-checks, `v` shows low-confidence mentions. Narrow windows stack the table over the details

### Changed
- The `migrate` planning is a function (`plan_project_migration`) shared by the command and the app; output unchanged

## [0.18.0] - 2026-11-26

### Changed
- `check-all` prints one table of every package (current, latest with major upgrades marked, status, uses in your code), with what needs attention first and up-to-date packages folded into one line. Details follow only for packages that break or deprecate something, plus notes worth a look. On narrow terminals the columns combine
- Mentions are grouped: one release note, with the lines it concerns, instead of the same note under every line, and a line is listed once however many ways it uses the API. Low-confidence mentions are counted and listed with `-v` (`check` and `check-all`)
- Release notes are shown as plain prose: no Markdown links, emoji, `**[sql] [usecase]**` tags or PR credits, and cut at a sentence instead of mid-word
- File paths are relative to the project checked, not to where pyupcheck runs

### Fixed
- False alarms found on a FastAPI + SQLAlchemy project (69 dependencies, about 90 spurious notes, now none):
  - a parameter change ("The `root_path` parameter in `FastAPI()` is deprecated") flagged every import, annotation and use of `FastAPI`; now only calls that pass `root_path=` are reported
  - a removed member of a class ("Remove `openapi_prefix` from `FastAPI`") flagged every `app.include_router(...)`; "Remove `x` from `Y`" now reads as `Y.x`, and using a class no longer counts as using each of its members
  - verbs inside code spans were read as the change ("column names such as ``add``, ``remove``, ``update``" removed `update`)
  - SQLAlchemy-style category tags (`:tags: usecase, sql` and `**[sql] [usecase]**`) are read: new features and use cases aren't reported as changes
  - pasted code examples and bare cross-references are no longer read as release-note items
  - catch-all arguments ("`**extra` removed from `Query`") no longer flag every `Query(...)` call
- Benchmark unchanged: deep 100% / 100%, changelog 80.6% recall with no false alarms, migrate 8/8

## [0.17.2] - 2026-11-26

### Changed
- `pyupcheck banner` runs full screen in an interactive terminal and lays itself out again whenever the window is resized, by width and by height, so it never breaks into fragments. Before, shrinking the window after the banner printed made the terminal re-wrap it. The logo's colours flow and a highlight sweeps across it, tips type out at the bottom, and any key exits. A copy sized to the final window is left in the scrollback. `--static` prints once as before, and pipes, CI, `PYUPCHECK_NO_ANIMATION=1` and the old Windows console host always print once
- Muted text in the banner is a solid grey instead of dimmed, so it stays readable over terminal background images

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
