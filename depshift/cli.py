"""CLI entry point for pyupcheck."""

import os
import re
import sys
import time
from contextlib import nullcontext

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

from depshift import __version__
from depshift import cache as cache_mod
from depshift.changelog import (
    get_available_versions,
    get_changes_between,
    get_pypi_info,
)
from depshift.config import load_config
from depshift.deps import discover_dependencies, normalize_name, requirement_files
from depshift.lockfiles import locked_versions
from depshift.names import import_names_for
from depshift.output import (
    View,
    console,
    diff_text,
    display_path,
    emit,
    err_console,
    esc,
    is_major,
    plural,
    print_changes,
    print_report,
    print_result,
    print_stacked,
    print_watch_event,
    styled_table,
    sym,
)
from depshift.project import (
    check_change,
    check_dependency,
    filter_by_since,
    highest_safe_version,
    is_exact_pin,
    latest_allowed_version,
    not_newer,
    outdated_status,
    plan_project_migration,
    project_dependencies,
    project_version,
    release_history,
    replace_file,
    run_check,
    should_fail,
    specifier_of,
)
from depshift.report import render_github_annotations, render_sarif
from depshift.scanner import scan_directory

FORMATS = click.Choice(["terminal", "json", "md", "html", "sarif", "github"])


_REPORTED_CONFIG: set[str] = set()


def _config(directory: str):
    """The project's configuration; problems with it are reported (once, on
    stderr), never silently ignored."""
    cfg = load_config(directory)
    if directory not in _REPORTED_CONFIG:
        _REPORTED_CONFIG.add(directory)
        for problem in cfg.problems:
            err_console.print(f"[yellow]config:[/] {esc(problem)}")
    return cfg


class _Ordered(click.Group):
    """Lists commands in the order they're defined: the everyday ones first."""

    def list_commands(self, ctx):
        return list(self.commands)


@click.group(cls=_Ordered, context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 100})
@click.version_option(__version__, prog_name="pyupcheck")
@click.pass_context
def main(ctx):
    """pyupcheck - Check if upgrading a Python dependency will break your code."""
    if ctx.invoked_subcommand is not None and ctx.invoked_subcommand != "banner":
        # Only greet humans: never in CI, pipes, or machine-readable output.
        argv = sys.argv[1:]
        machine = any(a in ("-q", "--quiet", "-f", "--format") or a.startswith("--format=")
                      for a in argv)
        if sys.stdout.isatty() and not os.environ.get("CI") and not machine:
            from depshift.welcome import show_if_first_run
            show_if_first_run(console)


@main.command("banner", short_help="Show the logo and version info")
@click.option("--static", is_flag=True, help="Print once instead of the full-screen animation.")
def show_banner_cmd(static):
    """Show the pyupcheck banner with live version info.

    In an interactive terminal it runs full screen: animated, and laid out
    again whenever the window is resized. Press any key to exit; a copy sized
    to the window is left behind. Elsewhere (pipes, CI) it prints once.
    """
    from depshift.welcome import show_banner
    show_banner(console, live=not static)


# ── check ─────────────────────────────────────────────────────────────────────

@main.command(short_help="Check one package upgrade")
@click.argument("package")
@click.argument("target_version", required=False)
@click.option("--dir", "-d", "directory", default=".", help="Directory to scan")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=FORMATS, default="terminal")
@click.option("--output", "-o", default=None, help="Write report to file")
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default=None)
@click.option("--min-severity", type=click.Choice(["breaking", "deprecated", "warning"]), default=None)
@click.option("--exclude", "-e", multiple=True)
@click.option("--no-cache", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
@click.option("--since", default=None, metavar="YYYY-MM-DD", help="Only show changes introduced since this date")
@click.option("--deep", is_flag=True,
              help="Deep mode: diff the real API of both versions (argument-level, verified findings)")
@click.option("--verbose", "-v", is_flag=True, help="Also list lines that release notes only mention")
def check(package, target_version, directory, github_token, fmt, output, fail_on,
          min_severity, exclude, no_cache, quiet, since, deep, verbose):
    """Check if upgrading PACKAGE to TARGET_VERSION will break your code."""
    directory = os.path.abspath(directory)
    view = View(verbose=verbose, root=directory)
    cfg = _config(directory)
    if fail_on:
        cfg.fail_on = fail_on
    if min_severity:
        cfg.min_severity = min_severity
    cfg.exclude_dirs.update(exclude)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()

    status_msg = f"Deep-checking [bold]{esc(package)}[/]..." if deep else f"Checking [bold]{esc(package)}[/]..."
    with console.status(status_msg):
        result = run_check(package, target_version, directory, github_token, cfg, since=since, deep=deep)

    if result is None:
        if not quiet:
            console.print(f"[red]{esc(package)}:[/] not found on PyPI. Check the name: "
                          f"[cyan]pyupcheck versions {esc(package)}[/]")
        sys.exit(2)

    emit([result], fmt, output, quiet, view)
    sys.exit(1 if should_fail([result], cfg.fail_on) else 0)


# ── check-all ─────────────────────────────────────────────────────────────────

@main.command("check-all", short_help="Check every dependency")
@click.argument("packages", nargs=-1)
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=FORMATS, default="terminal")
@click.option("--output", "-o", default=None)
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default=None)
@click.option("--min-severity", type=click.Choice(["breaking", "deprecated", "warning"]), default=None)
@click.option("--exclude", "-e", multiple=True)
@click.option("--no-cache", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
@click.option("--since", default=None, metavar="YYYY-MM-DD")
@click.option("--deep", is_flag=True, help="Deep mode: diff real API surfaces for every dependency (slower)")
@click.option("--jobs", "-j", type=click.IntRange(1, 64), default=8, show_default=True,
              help="Dependencies to check in parallel")
@click.option("--verbose", "-v", is_flag=True, help="Also list lines that release notes only mention")
def check_all(packages, directory, github_token, fmt, output, fail_on, min_severity, exclude, no_cache, quiet, since,
              deep, jobs, verbose):
    """Check ALL dependencies found in requirements.txt / pyproject.toml / setup.cfg / setup.py / environment.yml.

    Prints one table of every package (what needs attention first), then the
    lines that break or are deprecated, and a summary with the fixes to run.
    Name PACKAGES to check only those: pyupcheck check-all flask numpy
    """
    directory = os.path.abspath(directory)
    view = View(verbose=verbose, root=directory)
    cfg = _config(directory)
    if fail_on:
        cfg.fail_on = fail_on
    if min_severity:
        cfg.min_severity = min_severity
    cfg.exclude_dirs.update(exclude)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()

    deps, locks = project_dependencies(directory, cfg)

    if not deps:
        console.print("[yellow]No dependency files found[/] (looked for requirements*.txt, "
                      "pyproject.toml, Pipfile, setup.cfg, setup.py, environment.yml)")
        sys.exit(2)
    if packages:
        wanted = {normalize_name(p) for p in packages}
        unknown = sorted(wanted - {d.name for d in deps})
        if unknown:
            import difflib
            names = [d.name for d in deps]
            for name in unknown:
                close = difflib.get_close_matches(name, names, n=1)
                hint = f" (did you mean {close[0]}?)" if close else ""
                console.print(f"[red]{esc(name)}[/] is not a dependency of this project{esc(hint)}")
            sys.exit(2)
        deps = [d for d in deps if d.name in wanted]
    started = time.monotonic()

    # JSON/Markdown/HTML on stdout must be nothing but the report
    chatty = not quiet and (fmt == "terminal" or bool(output))
    if chatty:
        where = os.path.relpath(directory) if directory != os.getcwd() else "this project"
        console.print(f"[dim]Checking[/] [bold]{len(deps)}[/] [dim]dependencies in {esc(where)}[/]\n")

    # collect all results with progress bar, then print
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
        disable=not chatty,
    ) as progress:
        task = progress.add_task("Checking dependencies...", total=len(deps))

        def check_one(dep):
            return check_dependency(dep, locks, directory, cfg, github_token, deep, since)

        # Checks are almost entirely network waits (PyPI, GitHub, downloads),
        # so threads overlap them well. Results keep the dependency order.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        slots: list[dict | None] = [None] * len(deps)
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {pool.submit(check_one, dep): i for i, dep in enumerate(deps)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    slots[i] = fut.result()
                except Exception as e:  # one broken package must not sink the run
                    slots[i] = {
                        "package": deps[i].name, "current_version": None, "target_version": "?",
                        "risks": [], "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
                        "unchecked": True, "warnings": [f"check crashed ({type(e).__name__}: {e})"],
                    }
                progress.update(task, description=f"Checked [bold]{deps[i].name}[/]")
                progress.advance(task)
        results = [r for r in slots if r]

    # print results after progress bar closes cleanly
    if fmt == "terminal":
        print_report(results, quiet, view, elapsed=time.monotonic() - started)
        if output:
            emit(results, "md", output, quiet)
    else:
        emit(results, fmt, output, quiet)

    sys.exit(1 if should_fail(results, cfg.fail_on) else 0)


# ── fix ───────────────────────────────────────────────────────────────────────

@main.command(short_help="Move pins to the highest safe version")
@click.option("--dir", "-d", "directory", default=".")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing")
@click.option("--deep/--no-deep", default=True, show_default=True,
              help="Verify each candidate against the real API (slower, much more accurate)")
@click.option("--jobs", "-j", type=click.IntRange(1, 64), default=8, show_default=True)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
def fix(directory, dry_run, deep, jobs, github_token, no_cache):
    """Move each pinned dependency to the highest version that is safe for your code.

    Rewrites ==x.y.z pins in requirements files. If the latest release breaks
    your code, the pin moves to the newest earlier release that doesn't,
    instead of staying put. A version that couldn't be checked is never used.
    """
    from concurrent.futures import ThreadPoolExecutor
    if no_cache:
        cache_mod.disable_cache()

    directory = os.path.abspath(directory)
    cfg = _config(directory)
    ignored = {normalize_name(p) for p in cfg.ignore_packages}
    deps = [d for d in discover_dependencies(directory) if d.name not in ignored and d.pinned_version]

    req_files = requirement_files(directory)  # .txt and pip-tools .in sources

    if not req_files:
        console.print("[yellow]No requirements.txt files found to fix.[/]")
        sys.exit(2)

    def one(dep):
        try:
            return dep, highest_safe_version(dep.name, dep.pinned_version, directory, cfg, github_token,
                                             specifier_of(dep.raw), deep=deep)
        except Exception:
            return dep, None

    with console.status(f"Finding the highest safe version of {len(deps)} pinned dependencies..."), \
            ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        plans = list(pool.map(one, deps))

    updates: list[dict] = []
    table = styled_table("Package", "Pinned", "Moves to", "Latest", "Why")
    stacked: list[tuple[str, str]] = []  # the same rows for a narrow terminal
    arrow = sym("→", "->")
    for dep, plan in sorted(plans, key=lambda dp: dp[0].name):
        name, pinned = f"[bold]{esc(dep.name)}[/]", f"[dim]{esc(dep.pinned_version)}[/]"
        if plan is None or (plan["unchecked"] and plan["safe"] == dep.pinned_version):
            if plan is None or plan["latest"] != dep.pinned_version:
                table.add_row(name, pinned, "[dim]stays[/]", "", "[magenta]couldn't be checked: never moved[/]")
                stacked.append((f"{name} {pinned} [dim]stays[/]", "[magenta]couldn't be checked: never moved[/]"))
            continue
        latest = plan["latest"]
        latest_cell = (f"[yellow]{esc(latest)}[/] [dim]major[/]" if is_major(dep.pinned_version, latest)
                       else esc(latest))
        if plan["safe"] == latest:
            why = "[green]latest is safe[/]"
        else:
            n = plan["breaking_at_latest"]
            why = (f"[yellow]{esc(latest)} breaks {n} usage{'s' if n != 1 else ''}[/] [dim]·[/] "
                   f"[cyan]pyupcheck migrate {esc(dep.name)} {esc(latest)}[/]")
        if plan["safe"] != dep.pinned_version:
            updates.append({"name": dep.name, "old": dep.pinned_version, "new": plan["safe"], "source": dep.source})
            moves = f"[green]{arrow} {esc(plan['safe'])}[/]"
        else:
            moves = "[dim]stays[/]"
        if plan["safe"] != dep.pinned_version or plan["safe"] != latest:
            table.add_row(name, pinned, moves, latest_cell, why)
            stacked.append((f"{name} {pinned} {moves}", why))

    if table.row_count and console.width >= 80:
        console.print(table)
    elif stacked:
        print_stacked(stacked)
    if not updates:
        console.print(f"\n[green]{sym('✓', 'OK:')} Nothing to move:[/] every pin is at its highest safe version.")
        return
    files = sorted({display_path(p) for p in req_files})
    if dry_run:
        console.print(f"\n[bold]{len(updates)}[/] pin{'s' if len(updates) != 1 else ''} would move. "
                      f"[dim]Dry run: nothing written. Without --dry-run, {', '.join(files)} "
                      f"{'is' if len(files) == 1 else 'are'} updated.[/]")
        return

    for req_path in req_files:
        try:
            with open(req_path, encoding="utf-8", newline="") as f:  # newline="": line endings kept as they are
                content = f.read()
            updated = content
            for u in updates:
                # match any spelling of the name: Foo_Bar, foo-bar, foo.bar
                name_pat = "[-_.]+".join(re.escape(p) for p in re.split(r"[-_.]+", u["name"]))
                updated = re.sub(
                    rf"(?im)^(\s*{name_pat}\s*(?:\[[^\]]*\])?\s*==\s*){re.escape(u['old'])}(?![\w.])",
                    rf"\g<1>{u['new']}",
                    updated,
                )
            if updated != content:
                replace_file(req_path, updated)
        except OSError as e:
            console.print(f"[red]Could not write {esc(req_path)}:[/] {esc(str(e))}")

    console.print(f"\n[green]{sym('✓', 'OK:')} Moved {len(updates)} pin{'s' if len(updates) != 1 else ''}.[/] "
                  "Run [bold]pip install -r requirements.txt[/] to install them.")


# ── review ────────────────────────────────────────────────────────────────────


@main.command(short_help="Check dependency changes in a PR")
@click.option("--base", required=True, help="Git ref to compare against (e.g. origin/main, the PR base SHA)")
@click.option("--dir", "-d", "directory", default=".", help="Repository root")
@click.option("--deep/--no-deep", default=True, show_default=True,
              help="Diff real API surfaces and count what migrate can fix")
@click.option("--output", "-o", default=None, help="Write the Markdown report to a file")
@click.option("--format", "-f", "fmt", type=click.Choice(["auto", "terminal", "md"]), default="auto",
              show_default=True,
              help="auto: the terminal view when you run it, Markdown (for a PR comment) when piped or with -o")
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default="never",
              show_default=True)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--jobs", "-j", type=click.IntRange(1, 64), default=8, show_default=True)
@click.option("--annotations", is_flag=True,
              help="Also print GitHub Actions annotations (one per finding, shown on the PR diff)")
@click.option("--sarif", "sarif_out", default=None, help="Also write a SARIF file (GitHub code scanning)")
@click.option("--no-cache", is_flag=True)
def review(base, directory, deep, output, fmt, fail_on, github_token, jobs, annotations, sarif_out, no_cache):
    """Check the dependency versions changed since BASE (for pull requests).

    Compares dependency files and lockfiles at --base with the working tree,
    then checks each package that changed version, from its old version to
    its new one. In a terminal it shows the same table as check-all; piped,
    or with -o, it writes Markdown for a pull-request comment.
    """
    # only for its exception type: nothing is run here
    import subprocess as _sp  # nosec B404
    from concurrent.futures import ThreadPoolExecutor

    from depshift.review import changed_dependencies, render_review

    directory = os.path.abspath(directory)
    cfg = _config(directory)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()
    try:
        changes = changed_dependencies(directory, base)
    except FileNotFoundError:
        console.print("[red]git isn't installed[/] (or isn't on PATH): review compares two git revisions.")
        sys.exit(2)
    except (_sp.CalledProcessError, ValueError) as e:
        detail = (getattr(e, "stderr", "") or str(e)).strip()
        if "not a git repository" in detail:
            console.print(f"[red]{esc(directory)} isn't a git repository:[/] review compares the dependency "
                          f"files at a git revision with the working tree.")
        else:
            console.print(f"[red]Could not read the dependency files at {esc(base)}:[/] {esc(detail)}")
        sys.exit(2)
    ignored = {normalize_name(p) for p in cfg.ignore_packages}
    changes = [c for c in changes if c.name not in ignored]

    def one(change):
        return check_change(change, directory, cfg, github_token, deep)

    started = time.monotonic()
    terminal = fmt == "terminal" or (fmt == "auto" and not output and sys.stdout.isatty())
    with console.status(f"Checking {len(changes)} changed dependenc{'ies' if len(changes) != 1 else 'y'}...") \
            if terminal else nullcontext(), ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = [r for r in pool.map(one, changes) if r]

    if terminal:
        if not results:
            console.print(f"[green]{sym('✓', 'OK:')} No dependency versions changed since {esc(base)}.[/]")
        else:
            console.print(f"[dim]Dependency versions changed since[/] [bold]{esc(base)}[/]\n")
            print_report(results, view=View(root=directory), elapsed=time.monotonic() - started,
                         labels=("Before", "After"))
    text = render_review(results, repo_root=directory)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
    elif not terminal:
        print(text)
    if sarif_out:
        with open(sarif_out, "w", encoding="utf-8") as f:
            f.write(render_sarif(results, directory))
    if annotations:
        notes = render_github_annotations(results, directory)
        if notes:
            print(notes)
    sys.exit(1 if should_fail(results, fail_on) else 0)


# ── watch ─────────────────────────────────────────────────────────────────────


@main.command(short_help="Re-check as dependencies change")
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--interval", type=click.FloatRange(0.2, 3600), default=2.0, show_default=True,
              help="Seconds between looks at the dependency files")
@click.option("--releases", "releases_every", type=click.FloatRange(0, 7 * 24 * 60), default=60,
              show_default=True, metavar="MINUTES",
              help="Look for new PyPI releases of your direct dependencies this often (0: off)")
@click.option("--deep/--no-deep", default=True, show_default=True,
              help="Diff real API surfaces and count what migrate can fix")
@click.option("--bell", is_flag=True, help="Ring the terminal bell when something breaks")
@click.option("--quiet", "-q", is_flag=True, help="Only print what breaks")
@click.option("--jobs", "-j", type=click.IntRange(1, 64), default=8, show_default=True)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
@click.option("--cycles", type=int, default=None, hidden=True)  # for tests: stop after N polls
def watch(directory, interval, releases_every, deep, bell, quiet, jobs, github_token, no_cache, cycles):
    """Re-check your code whenever a dependency version changes.

    Watches requirements files, pyproject.toml and lockfiles. When a version
    in them changes (pip-compile, uv lock, poetry update, Dependabot, an
    edit), each package that moved is checked from its old version to its
    new one. Every --releases minutes it also looks for new PyPI releases
    your constraints allow, and tells you whether they break your code
    before you upgrade. Stop with Ctrl+C.
    """
    import time as _time

    from depshift.watch import Watcher, dep_file_signature

    directory = os.path.abspath(directory)
    cfg = _config(directory)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()
    ignored = {normalize_name(p) for p in cfg.ignore_packages}

    def check(change):
        if change.name in ignored:
            return None
        return check_change(change, directory, cfg, github_token, deep)

    def latest(name, raw):
        spec = specifier_of(raw)
        return latest_allowed_version(name, None if is_exact_pin(spec) else spec, fresh=True)

    def direct():
        return [d.name for d in discover_dependencies(directory) if d.name not in ignored]

    watcher = Watcher(directory, check, latest=latest, direct=direct,
                      releases_every=releases_every * 60, jobs=jobs)
    with console.status("Reading dependencies" + (" and their latest releases" if releases_every else "") + "..."):
        events = watcher.start()
    for ev in events:
        print_watch_event(ev, directory, quiet)
    files = sorted(dep_file_signature(directory))
    if not files:
        console.print(f"[yellow]No dependency files in {esc(directory)}[/] (requirements*.txt, pyproject.toml, "
                      "lockfiles...). Watching for one to appear.")
    else:
        rel = "" if not releases_every else \
            f", and PyPI every {releases_every:g} min for " \
            f"{plural(len(watcher.known_latest), 'direct dependency', 'direct dependencies')}"
        console.print(f"Watching [bold]{esc(', '.join(files))}[/] ({plural(len(watcher.snapshot), 'package')}){rel}. "
                      "[dim]Ctrl+C to stop.[/]")
    n = 0
    try:
        while cycles is None or n < cycles:
            n += 1
            _time.sleep(interval)
            for ev in watcher.poll():
                if print_watch_event(ev, directory, quiet) and bell:
                    console.bell()
    except KeyboardInterrupt:
        console.print("[dim]Stopped.[/]")


# ── explain ───────────────────────────────────────────────────────────────────

@main.command(short_help="Ask Claude how to fix breaks")
@click.argument("package")
@click.argument("target_version", required=False)
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--deep/--no-deep", default=True, show_default=True,
              help="Find breaking usages from the real API diff too (more accurate)")
@click.option("--max", "max_findings", type=click.IntRange(1, 100), default=20, show_default=True,
              help="Explain at most this many findings")
@click.option("--effort", type=click.Choice(["low", "medium", "high", "xhigh", "max"]), default="high",
              show_default=True, help="How hard Claude thinks (higher: slower, more thorough)")
@click.option("--model", default=None, help="Claude model (default: claude-opus-5-5)")
@click.option("--no-fallbacks", is_flag=True,
              help="Don't retry on another model when a request is declined")
@click.option("--dry-run", is_flag=True, help="Print what would be sent, and send nothing")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
def explain(package, target_version, directory, deep, max_findings, effort, model, no_fallbacks,
            dry_run, github_token, no_cache):
    """Ask Claude how to fix what upgrading PACKAGE breaks.

    For the changes `pyupcheck migrate` can't make by itself. Sends each
    breaking usage (a few lines of your code around it), the release note
    that explains it and the verified replacement to the Claude API, and
    streams back a before/after for each. Needs `pip install "pyupcheck[ai]"`
    and ANTHROPIC_API_KEY (or `ant auth login`). Use --dry-run to see exactly
    what would be sent.
    """
    from depshift import explain as ex

    directory = os.path.abspath(directory)
    cfg = _config(directory)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()
    dep = next((d for d in discover_dependencies(directory) if d.name == normalize_name(package)), None)
    spec = specifier_of(dep.raw) if dep else None
    with console.status(f"Checking what upgrading [bold]{esc(package)}[/] breaks..."):
        r = run_check(package, target_version, directory, github_token, cfg, deep=deep, spec=spec)
    if r is None:
        console.print(f"[red]{esc(package)}:[/] not found on PyPI.")
        sys.exit(2)
    if r.get("up_to_date") or r.get("no_usages") or not r.get("breaking_count"):
        print_result(r, quiet=False, view=View(root=directory))
        console.print(f"[green]{sym('✓', 'OK:')} Nothing breaks, so there's nothing to explain.[/]")
        return
    findings = ex.findings_from(r, directory, limit=max_findings)
    prompt = ex.build_prompt(package, r.get("current_version") or "?", r["target_version"], findings)
    lines = sum(f["code"].count("\n") + 1 for f in findings if f["code"])
    more = r["breaking_count"] - len(findings)
    what = (f"{plural(len(findings), 'finding')} and {plural(lines, 'line')} of your code"
            + (f" ({more} more not included: --max)" if more > 0 else ""))
    if dry_run:
        from rich.panel import Panel
        console.print(f"[bold]Dry run:[/] this is what would be sent to {esc(model or ex.MODEL)}, after its "
                      f"system prompt: {what}.\n")
        console.print(Panel(esc(prompt.rstrip()), border_style="dim", padding=(0, 1), expand=False))
        console.print(f"\n[green]{sym('✓', 'OK:')} Nothing was sent.[/] [dim]Run without --dry-run to ask Claude.[/]")
        return

    try:
        client = ex.make_client()
    except ex.ExplainError as e:
        console.print(f"[red]{esc(str(e))}[/]")
        sys.exit(2)
    console.print(f"[dim]Sending {what} to the Claude API ({esc(model or ex.MODEL)}).[/]\n")
    try:
        from depshift.safe import plain
        # the model's answer is shown as text: never as terminal control sequences
        def write(text: str) -> None:
            sys.stdout.write(plain(text))
            sys.stdout.flush()

        info = ex.stream_advice(client, prompt, write,
                                model=model or ex.MODEL, effort=effort, fallbacks=not no_fallbacks)
    except ex.ExplainError as e:
        console.print(f"\n[red]{esc(str(e))}[/]")
        sys.exit(2)
    print()
    if info["stop_reason"] == "refusal":
        console.print("[yellow]Claude declined this request.[/] The findings are still listed by "
                      f"[cyan]pyupcheck check {esc(package)} {esc(r['target_version'])}[/].")
    elif info["stop_reason"] == "max_tokens":
        console.print("[yellow]The answer was cut off;[/] use --max to explain fewer findings at once.")
    console.print(f"[dim]{esc(info['model'])}: {info['input_tokens']} tokens in, {info['output_tokens']} out. "
                  "Check the advice before applying it; `pyupcheck check` verifies the result.[/]")


# ── migrate ───────────────────────────────────────────────────────────────────

@main.command(short_help="Rewrite code for an upgrade")
@click.argument("package")
@click.argument("target_version", required=False)
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--write", is_flag=True, help="Apply the changes (default: show a diff only)")
@click.option("--exclude", "-e", multiple=True)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
def migrate(package, target_version, directory, write, exclude, github_token, no_cache):
    """Rewrite your code for upgrading PACKAGE to TARGET_VERSION.

    Applies only mechanical, verified changes: arguments that became
    keyword-only, renamed keyword arguments, and names that moved or were
    renamed (each replacement is checked against the target version's real
    API). Everything else that breaks is listed for you to change by hand.

    Shows a diff by default; pass --write to change files. Exits 1 if
    anything still needs a manual change.
    """
    from rich.padding import Padding

    from depshift import notebook
    from depshift.rewrite import unified_diff

    directory = os.path.abspath(directory)
    cfg = _config(directory)
    cfg.exclude_dirs.update(exclude)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()

    with console.status(f"Comparing [bold]{esc(package)}[/]..."):
        mp = plan_project_migration(package, target_version, directory, cfg, github_token)
    if mp.error:
        console.print(f"[red]{esc(package)}:[/] {esc(mp.error)}")
        sys.exit(2)
    if mp.up_to_date:
        console.print(f"[green]{sym('✓', 'OK:')} {esc(package)} {esc(mp.current or '')}[/] is already the latest "
                      "version your constraints allow.")
        return

    arrow = sym("→", "->")
    console.print(f"[bold]{esc(package)}[/] {esc(mp.current or '')} {arrow} [bold]{esc(mp.target or '')}[/]  "
                  f"[dim]({esc(mp.source or '')})[/]")

    fixable = [fc for fc in mp.files if not fc.error]
    if fixable:
        n_fixes = sum(len(fc.fixes) for fc in fixable)
        console.print(f"\n[bold]What pyupcheck fixes[/]  [dim]{plural(n_fixes, 'change')} in "
                      f"{plural(len(fixable), 'file')}[/]")
    written, failed = 0, 0
    for fc in mp.files:
        if fc.error:
            console.print(f"  [red]{sym('✗', 'x')} {esc(fc.rel)} skipped:[/] {esc(fc.error)}")
            continue
        for fx in fc.fixes:
            if console.width >= 80:
                console.print(f"  [green]{sym('✓', '+')}[/] [bold]{esc(mp.where(fx))}[/]  {esc(fx.description)}")
            else:
                console.print(f"  [green]{sym('✓', '+')}[/] [bold]{esc(mp.where(fx))}[/]", overflow="fold")
                console.print(Padding(esc(fx.description), (0, 0, 0, 4)))
        if fc.notebook is not None:
            diff = unified_diff(fc.rel, notebook.cells_text(fc.notebook, fc.before),
                                notebook.cells_text(fc.notebook, fc.after))
        else:
            diff = unified_diff(fc.rel, fc.before, fc.after)
        console.print(Padding(diff_text(diff, width=console.width - 4), (0, 0, 0, 4), expand=False),
                      no_wrap=True, overflow="fold")
        if write:
            try:
                fc.write(directory)
                written += 1
            except OSError as e:
                failed += 1
                console.print(f"  [red]not written:[/] {esc(str(e))}")

    if mp.manual:
        console.print(f"\n[bold]What needs you[/]  [dim]{plural(len(mp.manual), 'change')}[/]")
        for m in mp.manual:
            code = re.sub(r"^\[cell \d+\] ", "", m.code) if m.cell else m.code  # the cell is in where()
            if console.width >= 80:
                console.print(f"  [yellow]![/] [bold]{esc(mp.where(m))}[/]  {esc(code.strip())}",
                              overflow="ellipsis", no_wrap=True)
            else:
                console.print(f"  [yellow]![/] [bold]{esc(mp.where(m))}[/]", overflow="fold")
                console.print(Padding(esc(code.strip()), (0, 0, 0, 4)), overflow="ellipsis", no_wrap=True)
            console.print(Padding(f"[dim]{esc(m.reason)}[/]", (0, 0, 0, 4)))

    console.print()
    if not mp.has_fixes and not mp.manual:
        console.print(f"[green]{sym('✓', 'OK:')} Nothing to change:[/] no breaking API change hits your code.")
    elif write and written:
        console.print(f"[green]{sym('✓', 'OK:')} Rewrote {plural(written, 'file')}.[/]"
                      + (f" [red]{plural(failed, 'file')} not written.[/]" if failed else "")
                      + (" Check the changes that need you, above." if mp.manual else ""))
    elif fixable:
        console.print(f"Would rewrite {plural(len(fixable), 'file')}. [dim]Run again with --write to apply.[/]")
    elif mp.manual:
        console.print("[dim]Nothing can be rewritten automatically: the changes above need you.[/]")
    sys.exit(1 if mp.manual or failed else 0)


# ── ui ────────────────────────────────────────────────────────────────────────

@main.command(short_help="Browse upgrades and fix them, full screen")
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--deep", is_flag=True, help="Start in deep mode (compare the real API of both versions)")
@click.option("--jobs", "-j", type=click.IntRange(1, 64), default=8, show_default=True)
@click.option("--exclude", "-e", multiple=True)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
def ui(directory, deep, jobs, exclude, github_token, no_cache):
    """Browse every dependency upgrade and fix what breaks, in a full-screen app.

    Packages fill in as they are checked, what needs attention first. Select
    one to see the lines that break, with the release note and replacement.
    Keys: m preview fixes (w in the preview applies them), f filter,
    d deep mode, r re-check, v low-confidence mentions, q quit.
    Needs the ui extra: pip install "pyupcheck[ui]".
    """
    try:
        from depshift import tui
    except ImportError:
        console.print('pyupcheck ui needs Textual: [bold]pip install "pyupcheck\\[ui]"[/]')
        sys.exit(2)
    if no_cache:
        cache_mod.disable_cache()
    tui.run(directory, github_token=github_token, deep=deep, jobs=jobs, exclude=exclude)


# ── ci-setup ──────────────────────────────────────────────────────────────────

_GH_ACTIONS = """\
name: Dependency upgrade check

on:
  push:
    paths:
      - 'requirements*.txt'
      - 'pyproject.toml'
      - 'setup.cfg'
      - 'setup.py'
      - 'environment.yml'
  schedule:
    - cron: '0 9 * * 1'  # every Monday 9am UTC

# the check only reads the repository
permissions:
  contents: read

jobs:
  pyupcheck:
    runs-on: ubuntu-latest
    steps:
      # actions pinned to a commit (the tag in the comment): a moved tag can't
      # change what runs here. Dependabot keeps them current.
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7

      - name: Set up Python
        uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0  # v7
        with:
          python-version: '3.13'

      - name: Install pyupcheck
        run: pip install pyupcheck

      - name: Check dependency upgrades
        run: pyupcheck check-all --fail-on breaking --quiet
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""

_PRE_COMMIT = """\
repos:
  - repo: local
    hooks:
      - id: pyupcheck
        name: pyupcheck
        entry: pyupcheck check-all --quiet --fail-on breaking
        language: system
        pass_filenames: false
        stages: [pre-push]
"""


@main.command("ci-setup", short_help="Write CI config for pyupcheck")
@click.option("--type", "ci_type", type=click.Choice(["github", "pre-commit", "all"]), default="all",
              help="Which CI config to generate")
@click.option("--dir", "-d", "directory", default=".")
def ci_setup(ci_type, directory):
    """Generate CI config files for running pyupcheck automatically."""
    directory = os.path.abspath(directory)

    if ci_type in ("github", "all"):
        gha_dir = os.path.join(directory, ".github", "workflows")
        os.makedirs(gha_dir, exist_ok=True)
        path = os.path.join(gha_dir, "pyupcheck.yml")
        if os.path.exists(path):
            console.print(f"[yellow]Already exists:[/] {esc(os.path.relpath(path))}")
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_GH_ACTIONS)
            console.print(f"[green]Created:[/] {esc(os.path.relpath(path))}")

    if ci_type in ("pre-commit", "all"):
        path = os.path.join(directory, ".pre-commit-config.yaml")
        if os.path.exists(path):
            console.print(f"[yellow]Already exists:[/] {esc(os.path.relpath(path))} (not modified)")
            console.print("Add this to your existing config:")
            console.print(esc(_PRE_COMMIT))
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_PRE_COMMIT)
            console.print(f"[green]Created:[/] {esc(os.path.relpath(path))}")
            console.print("\nInstall the hook with: [bold]pre-commit install[/]")

    console.print("\nDone. Commit the generated files and push.")


# ── outdated / highest safe version ───────────────────────────────────────────


@main.command(short_help="List dependencies with newer versions")
@click.option("--dir", "-d", "directory", default=".")
@click.option("--safe", is_flag=True,
              help="Also find the highest version that breaks none of your code (runs deep checks)")
@click.option("--jobs", "-j", type=click.IntRange(1, 64), default=8, show_default=True)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
def outdated(directory, safe, jobs, github_token, no_cache):
    """List dependencies that have newer versions available.

    With --safe, also show the highest version you can move to without
    breaking your code, so a breaking latest release doesn't leave you
    stuck on an old one.
    """
    from concurrent.futures import ThreadPoolExecutor
    if no_cache:
        cache_mod.disable_cache()
    directory = os.path.abspath(directory)
    cfg = _config(directory)
    deps = discover_dependencies(directory)
    if not deps:
        console.print("[yellow]No dependency files found[/]")
        sys.exit(2)

    locks = locked_versions(directory)

    def one(dep):
        return outdated_status(dep, locks, directory, cfg, github_token, safe)

    with console.status("Checking versions..." + (" (finding safe versions)" if safe else "")), \
            ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        rows = [r for r in pool.map(one, deps) if r]

    table = styled_table("Package", "Locked" if locks else "Current", "Latest",
                         *(["Highest safe"] if safe else []), "Status")
    stacked: list[tuple[str, str]] = []  # the same rows for a narrow terminal
    outdated_count = 0
    # majors first: they carry the most risk
    for r in sorted(rows, key=lambda r: (not r.get("major"), r["name"])):
        name = f"[bold]{esc(r['name'])}[/]"
        if not r.get("current"):
            cells = [name, "[dim]?[/]", esc(r["latest"])] + (["[dim]-[/]"] if safe else [])
            table.add_row(*cells, "[dim]current version unknown[/]")
            stacked.append((f"{name} [dim]?[/] {sym('→', '->')} {esc(r['latest'])}", "[dim]current version unknown[/]"))
            continue
        if not r.get("outdated"):
            continue
        outdated_count += 1
        latest = f"[yellow]{esc(r['latest'])}[/] [dim]major[/]" if r["major"] else esc(r["latest"])
        status = "[yellow]major upgrade[/]" if r["major"] else "[dim]update available[/]"
        cells = [name, f"[dim]{esc(r['current'])}[/]", latest]
        if safe:
            plan = r.get("plan") or {}
            if plan.get("safe") == r["latest"]:
                cells.append(f"[green]{esc(plan['safe'])}[/]")
                status = "[green]safe to upgrade[/]"
            elif plan.get("safe") and plan["safe"] != r["current"]:
                cells.append(f"[yellow]{esc(plan['safe'])}[/]")
                status = f"[red]{esc(r['latest'])} breaks {plural(plan['breaking_at_latest'], 'usage')}[/]"
            else:
                cells.append("[red]none[/]" if not plan.get("unchecked") else "[magenta]not checked[/]")
                n = plan.get("breaking_at_latest")
                status = (f"[red]{esc(r['latest'])} breaks {plural(n, 'usage')}[/]" if isinstance(n, int)
                          else "[magenta]couldn't be checked[/]")
        cells.append(status)
        table.add_row(*cells)
        detail = status + (f" [dim]· highest safe:[/] {cells[3]}" if safe else "")
        stacked.append((f"{name} [dim]{esc(r['current'])}[/] {sym('→', '->')} {esc(r['latest'])}", detail))

    if outdated_count:
        if console.width >= 64 + (16 if safe else 0):
            console.print(table)
        else:
            print_stacked(stacked)
        hint = "" if safe else " Run with --safe to find the highest version that doesn't break your code."
        console.print(f"\n[bold]{outdated_count}[/] outdated.[dim]{hint}[/]")
    else:
        console.print(f"[green]{sym('✓', 'OK:')} All dependencies are up to date.[/]")


# ── diff ──────────────────────────────────────────────────────────────────────

@main.command(short_help="Show changes between two versions")
@click.argument("package")
@click.argument("from_version")
@click.argument("to_version")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
@click.option("--since", default=None, metavar="YYYY-MM-DD")
def diff(package, from_version, to_version, github_token, no_cache, since):
    """Show breaking/deprecated changes in PACKAGE between two versions."""
    if no_cache:
        cache_mod.disable_cache()

    # validate versions are real and parseable before doing any work
    from packaging.version import InvalidVersion, Version
    for label, v in (("from", from_version), ("to", to_version)):
        try:
            Version(v)
        except InvalidVersion:
            console.print(f"[red]Error:[/] '{esc(v)}' is not a valid version string ({label}_version).")
            sys.exit(2)

    try:
        available = set(get_available_versions(package))
    except Exception as e:
        console.print(f"[red]Error:[/] could not fetch {esc(package)} from PyPI: {esc(str(e))}")
        sys.exit(2)

    for label, v in (("from", from_version), ("to", to_version)):
        if v not in available:
            console.print(f"[red]Error:[/] {esc(package)} {esc(v)} not found on PyPI ({label}_version). "
                          f"Run [bold]pyupcheck versions {esc(package)}[/] to see available versions.")
            sys.exit(2)

    with console.status(f"Fetching changes for [bold]{esc(package)}[/] {esc(from_version)} -> {esc(to_version)}..."):
        try:
            changes = get_changes_between(package, from_version, to_version, github_token=github_token)
        except Exception as e:
            console.print(f"[red]Error:[/] {esc(str(e))}")
            sys.exit(2)

    if since and changes:
        changes = filter_by_since(changes, since, package)

    if not changes:
        console.print(f"[green]No breaking/deprecated changes found[/] between {esc(from_version)} and "
                      f"{esc(to_version)}.")
        return

    print_changes(package, from_version, to_version, changes)


# ── scan ──────────────────────────────────────────────────────────────────────

@main.command(short_help="Find where your code uses a package")
@click.argument("package")
@click.option("--dir", "-d", "directory", default=".")
@click.option("--exclude", "-e", multiple=True)
def scan(package, directory, exclude):
    """Scan your code for all usages of PACKAGE."""
    directory = os.path.abspath(directory)
    cfg = _config(directory)
    cfg.exclude_dirs.update(exclude)

    names = import_names_for(package)
    with console.status(f"Scanning for usages of [bold]{esc(package)}[/]..."):
        usages = scan_directory(directory, names, exclude_dirs=cfg.exclude_dirs)

    if not usages:
        console.print(f"[yellow]No usages of {esc(package)} found[/] (looked for imports of: {esc(', '.join(names))})")
        sys.exit(0)

    from rich.padding import Padding
    kinds = {"import": "import", "function_call": "call", "attribute_access": "attribute",
             "dynamic_attribute": "getattr", "dynamic_import": "dynamic import"}
    by_file: dict[str, dict[int, list]] = {}
    for u in usages:
        by_file.setdefault(u.file, {}).setdefault(u.line, []).append(u)
    lines = sum(len(v) for v in by_file.values())
    for path in sorted(by_file, key=lambda p: display_path(p, directory)):
        found = by_file[path]
        count = f"{len(found)} line{'s' if len(found) != 1 else ''}"
        console.print(f"\n[bold]{esc(display_path(path, directory))}[/]  [dim]{count}[/]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="right", style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)  # what the line uses: never shortened
        grid.add_column(overflow="ellipsis", no_wrap=True)
        rows = []
        for line in sorted(found):
            same_line = found[line]
            uses: dict[str, str] = {}  # each name used on the line, with how
            for u in same_line:
                uses.setdefault(u.attr_chain, kinds.get(u.usage_type, u.usage_type.replace("_", " ")))
            names_used = ", ".join(f"[cyan]{esc(api)}[/] [dim]{kind}[/]" for api, kind in uses.items())
            code = f"[dim]{esc(same_line[0].code.strip())}[/]"
            rows.append((str(line), names_used, code))
        # the code column gets what's left; with less than 30 columns for it,
        # each line's code goes on a line of its own, under what it uses
        num_w = max(len(n) for n, _, _ in rows)
        uses_w = max(Text.from_markup(u).cell_len for _, u, _ in rows)
        if console.width - 2 - num_w - uses_w - 4 >= 30:
            grid.columns[1].min_width = uses_w
            for row in rows:
                grid.add_row(*row)
            console.print(Padding(grid, (0, 0, 0, 2)))
        else:
            for n, uses_text, code in rows:
                console.print(f"  [dim]{n}[/]  {uses_text}", overflow="fold")
                console.print(Padding(code, (0, 0, 0, 4)), overflow="ellipsis", no_wrap=True)
    files = len(by_file)
    console.print(f"\n[bold]{lines}[/] line{'s' if lines != 1 else ''} in [bold]{files}[/] "
                  f"file{'s' if files != 1 else ''} use {esc(package)}"
                  + (f" [dim](imported as {esc(', '.join(names))})[/]" if names != [package] else "") + ".")


# ── versions ──────────────────────────────────────────────────────────────────

@main.command(short_help="List a package's versions")
@click.argument("package")
@click.option("--dir", "-d", "directory", default=".", help="Project, to mark the version you're on")
@click.option("--all", "show_all", is_flag=True, help="Every release, not only the newest 15")
def versions(package, directory, show_all):
    """List PACKAGE's releases, newest first, with their dates.

    Marks the latest release, the one your project is on (from its lockfile,
    pin or installed version), yanked releases and pre-releases.
    """
    with console.status(f"Fetching versions for [bold]{esc(package)}[/]..."):
        try:
            info = get_pypi_info(package)
        except Exception as e:
            console.print(f"[red]{esc(package)}:[/] not found on PyPI ({esc(str(e))})")
            sys.exit(1)

    latest = (info.get("info") or {}).get("version")
    releases = release_history(info)
    if not releases:
        console.print(f"[yellow]{esc(package)} has no releases on PyPI.[/]")
        return
    yours, source = project_version(package, os.path.abspath(directory))

    shown = releases if show_all else releases[:15]
    if yours and all(r.version != yours for r in shown):
        shown = shown + [r for r in releases if r.version == yours]  # always show where you are
    table = styled_table("Version", "Released", "")
    for r in shown:
        tags = []
        if r.version == latest:
            tags.append("[green]latest[/]")
        if r.version == yours:
            tags.append(f"[cyan]you're on this[/] [dim]({esc(source or '')})[/]")
        if r.yanked:
            tags.append("[red]yanked[/]")
        if r.prerelease:
            tags.append("[dim]pre-release[/]")
        name = f"[bold]{esc(r.version)}[/]" if r.version in (latest, yours) else esc(r.version)
        table.add_row(name, f"[dim]{r.date}[/]", " · ".join(tags))
    console.print(table)
    hidden = len(releases) - len(shown)
    more = f" [dim]({hidden} older: --all lists them)[/]" if hidden > 0 else ""
    console.print(f"\n[bold]{len(releases)}[/] releases{more}")
    if yours and latest and not not_newer(latest, yours):
        console.print(f"[dim]See what upgrading breaks:[/] [cyan]pyupcheck check {esc(package)}[/]")


# ── cache-clear ───────────────────────────────────────────────────────────────

@main.command("cache-clear", short_help="Clear the local cache")
def cache_clear_cmd():
    """Clear the local response cache."""
    n = cache_mod.cache_clear()
    console.print(f"Removed [bold]{n}[/] cached entries.")


# ── shell completion ──────────────────────────────────────────────────────────

@main.command("install-completion", short_help="Print shell completion script")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell"]))
def install_completion(shell):
    """Print shell completion script. Pipe it to your shell config.

    \b
    bash:        pyupcheck install-completion bash >> ~/.bashrc
    zsh:         pyupcheck install-completion zsh >> ~/.zshrc
    fish:        pyupcheck install-completion fish > ~/.config/fish/completions/pyupcheck.fish
    powershell:  pyupcheck install-completion powershell >> $PROFILE
    """
    scripts = {
        "bash": """\
# pyupcheck bash completion
_pyupcheck_completion() {
    local IFS=$'\\n'
    COMPREPLY=($(env COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD _PYUPCHECK_COMPLETE=bash_complete pyupcheck))
}
complete -F _pyupcheck_completion pyupcheck
""",
        "zsh": """\
# pyupcheck zsh completion
autoload -Uz compinit && compinit
eval "$(_PYUPCHECK_COMPLETE=zsh_source pyupcheck)"
""",
        "fish": """\
# pyupcheck fish completion
eval (env _PYUPCHECK_COMPLETE=fish_source pyupcheck)
""",
        "powershell": """\
# pyupcheck PowerShell completion
$env:_PYUPCHECK_COMPLETE = "powershell_complete"
Invoke-Expression (pyupcheck | Out-String)
Remove-Item Env:_PYUPCHECK_COMPLETE
""",
    }
    print(scripts[shell])
    console.print("\n[dim]Pipe this to your shell config and restart your terminal.[/]")


if __name__ == "__main__":
    main()
