"""CLI entry point for pyupcheck."""

import json as _json
import os
import re
import sys
from datetime import datetime
from typing import List, Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from depshift import __version__
from depshift.scanner import scan_directory
from depshift.changelog import (
    get_changes_between,
    get_current_version,
    get_available_versions,
    get_pypi_info,
)
from depshift.analyzer import analyze
from depshift.deps import discover_dependencies
from depshift.config import load_config
from depshift import cache as cache_mod
from depshift.report import render_markdown, render_html


console = Console()

SEVERITY_RANK = {"breaking": 0, "deprecated": 1, "warning": 2}


def get_installed_version(package: str) -> Optional[str]:
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return None


def _filter_by_since(changes, since_str: str, package: str):
    """Filter changelog entries to only those introduced after since_str (YYYY-MM-DD)."""
    try:
        since_dt = datetime.strptime(since_str, "%Y-%m-%d")
    except ValueError:
        return changes  # bad date format, return unfiltered

    try:
        info = get_pypi_info(package)
        releases = info.get("releases", {})
    except Exception:
        return changes  # can't fetch, return unfiltered

    def version_date(ver: str) -> Optional[datetime]:
        for f in releases.get(ver, []):
            upload = f.get("upload_time")
            if upload:
                try:
                    return datetime.strptime(upload[:10], "%Y-%m-%d")
                except Exception:
                    pass
        return None

    filtered = []
    for c in changes:
        vd = version_date(c.version)
        if vd is None:
            continue  # unknown date — exclude when filtering by date
        if vd >= since_dt:
            filtered.append(c)
    return filtered


def _run_single_check(package: str, target_version: Optional[str], directory: str,
                      github_token: Optional[str], cfg, suppress_errors: bool = False,
                      since: Optional[str] = None) -> Optional[dict]:
    installed = get_installed_version(package)

    if not target_version:
        try:
            target_version = get_current_version(package)
        except Exception as e:
            if not suppress_errors:
                console.print(f"[red]{package}:[/] not found on PyPI ({e})")
            return None

    if installed and installed == target_version:
        return {
            "package": package, "current_version": installed,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "up_to_date": True,
        }

    current = installed or "0.0.0"
    usages = scan_directory(directory, package, exclude_dirs=cfg.exclude_dirs)
    if not usages:
        return {
            "package": package, "current_version": current,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "no_usages": True,
        }

    try:
        changes = get_changes_between(package, current, target_version, github_token=github_token)
    except Exception:
        changes = []

    if since and changes:
        changes = _filter_by_since(changes, since, package)

    risks, safe = analyze(usages, changes, package)

    min_rank = SEVERITY_RANK.get(cfg.min_severity, 2)
    risks = [r for r in risks if SEVERITY_RANK.get(r.severity, 2) <= min_rank]

    return {
        "package": package,
        "current_version": current,
        "target_version": target_version,
        "risks": [
            {
                "file": r.usage.file, "line": r.usage.line, "code": r.usage.code,
                "api": r.usage.attr_chain, "severity": r.severity,
                "change_kind": r.change.kind,
                "change_description": r.change.description,
                "change_version": r.change.version,
            }
            for r in risks
        ],
        "safe_count": len(safe),
        "breaking_count": sum(1 for r in risks if r.severity == "breaking"),
        "deprecated_count": sum(1 for r in risks if r.severity == "deprecated"),
    }


def _should_fail(results: List[dict], fail_on: str) -> bool:
    breaking = sum(r["breaking_count"] for r in results)
    deprecated = sum(r["deprecated_count"] for r in results)
    any_risk = sum(len(r["risks"]) for r in results)
    if fail_on == "never":
        return False
    if fail_on == "breaking":
        return breaking > 0
    if fail_on == "deprecated":
        return breaking > 0 or deprecated > 0
    if fail_on == "any":
        return any_risk > 0
    return breaking > 0


def _emit(results: List[dict], fmt: str, output: Optional[str], quiet: bool):
    if fmt == "json":
        text = _json.dumps({"results": results}, indent=2)
    elif fmt == "md":
        text = render_markdown(results)
    elif fmt == "html":
        text = render_html(results)
    else:
        text = None

    if text is not None:
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(text)
            if not quiet:
                console.print(f"Report written to [bold]{output}[/]")
        else:
            print(text)
        return

    for r in results:
        _print_terminal_result(r, quiet)


def _print_terminal_result(r: dict, quiet: bool):
    if r.get("up_to_date"):
        if not quiet:
            console.print(f"[green]{r['package']}[/] already on {r['target_version']}")
        return
    if r.get("no_usages"):
        if not quiet:
            console.print(f"[dim]{r['package']}[/] no usages found, safe to upgrade to {r['target_version']}")
        return

    header = f"[bold]{r['package']}[/] {r['current_version']} -> {r['target_version']}"
    if r["breaking_count"]:
        console.print(f"{header}  [red bold]{r['breaking_count']} BREAKING[/]")
    elif r["deprecated_count"]:
        console.print(f"{header}  [yellow]{r['deprecated_count']} deprecated[/]")
    else:
        if quiet:
            return
        console.print(f"{header}  [green]OK[/] ({r['safe_count']} usages safe)")

    for risk in r["risks"]:
        color = {"breaking": "red", "deprecated": "yellow", "warning": "blue"}[risk["severity"]]
        mark = {"breaking": "x", "deprecated": "!", "warning": "?"}[risk["severity"]]
        try:
            rel = os.path.relpath(risk["file"])
        except ValueError:
            rel = risk["file"]
        console.print(f"  [{color}]{mark}[/] [bold]{rel}:{risk['line']}[/]  {risk['code']}")
        console.print(f"    [dim]{risk['change_description']}[/]")


@click.group()
@click.version_option(__version__, prog_name="pyupcheck")
@click.pass_context
def main(ctx):
    """pyupcheck - Check if upgrading a Python dependency will break your code."""
    if ctx.invoked_subcommand is not None:
        from depshift.welcome import show_if_first_run
        show_if_first_run(console)


@main.command("banner")
def show_banner():
    """Show the pyupcheck banner."""
    from depshift.welcome import BANNER
    console.print(BANNER)


# ── check ─────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("package")
@click.argument("target_version", required=False)
@click.option("--dir", "-d", "directory", default=".", help="Directory to scan")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=click.Choice(["terminal", "json", "md", "html"]), default="terminal")
@click.option("--output", "-o", default=None, help="Write report to file")
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default=None)
@click.option("--min-severity", type=click.Choice(["breaking", "deprecated", "warning"]), default=None)
@click.option("--exclude", "-e", multiple=True)
@click.option("--no-cache", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
@click.option("--since", default=None, metavar="YYYY-MM-DD", help="Only show changes introduced since this date")
def check(package, target_version, directory, github_token, fmt, output, fail_on,
          min_severity, exclude, no_cache, quiet, since):
    """Check if upgrading PACKAGE to TARGET_VERSION will break your code."""
    directory = os.path.abspath(directory)
    cfg = load_config(directory)
    if fail_on:
        cfg.fail_on = fail_on
    if min_severity:
        cfg.min_severity = min_severity
    cfg.exclude_dirs.update(exclude)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()

    with console.status(f"Checking [bold]{package}[/]..."):
        result = _run_single_check(package, target_version, directory, github_token,
                                   cfg, suppress_errors=quiet, since=since)

    if result is None:
        sys.exit(2)

    _emit([result], fmt, output, quiet)
    sys.exit(1 if _should_fail([result], cfg.fail_on) else 0)


# ── check-all ─────────────────────────────────────────────────────────────────

@main.command("check-all")
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=click.Choice(["terminal", "json", "md", "html"]), default="terminal")
@click.option("--output", "-o", default=None)
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default=None)
@click.option("--min-severity", type=click.Choice(["breaking", "deprecated", "warning"]), default=None)
@click.option("--exclude", "-e", multiple=True)
@click.option("--no-cache", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
@click.option("--since", default=None, metavar="YYYY-MM-DD")
def check_all(directory, github_token, fmt, output, fail_on, min_severity, exclude, no_cache, quiet, since):
    """Check ALL dependencies found in requirements.txt / pyproject.toml / setup.cfg / setup.py / environment.yml."""
    directory = os.path.abspath(directory)
    cfg = load_config(directory)
    if fail_on:
        cfg.fail_on = fail_on
    if min_severity:
        cfg.min_severity = min_severity
    cfg.exclude_dirs.update(exclude)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()

    deps = discover_dependencies(directory)
    deps = [d for d in deps if d.name not in cfg.ignore_packages]

    if not deps:
        console.print("[yellow]No dependency files found[/] (looked for requirements.txt, pyproject.toml, setup.cfg, setup.py, environment.yml)")
        sys.exit(2)

    if not quiet:
        console.print(f"Found [bold]{len(deps)}[/] dependencies to check\n")

    # collect all results with progress bar, then print
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
        disable=quiet,
    ) as progress:
        task = progress.add_task("Checking dependencies...", total=len(deps))
        for dep in deps:
            progress.update(task, description=f"Checking [bold]{dep.name}[/]...")
            r = _run_single_check(dep.name, None, directory, github_token, cfg,
                                  suppress_errors=True, since=since)
            if r:
                results.append(r)
            progress.advance(task)

    # print results after progress bar closes cleanly
    if fmt == "terminal":
        for r in results:
            _print_terminal_result(r, quiet)

        total_breaking = sum(r["breaking_count"] for r in results)
        total_deprecated = sum(r["deprecated_count"] for r in results)
        if not quiet:
            console.print()
            if total_breaking:
                console.print(Panel(
                    f"[red bold]{total_breaking} breaking[/] | [yellow]{total_deprecated} deprecated[/] across {len(results)} packages",
                    border_style="red"))
            elif total_deprecated:
                console.print(Panel(f"[yellow]{total_deprecated} deprecated[/] across {len(results)} packages",
                                    border_style="yellow"))
            else:
                console.print(Panel(f"[green]All {len(results)} packages safe to upgrade[/]",
                                    border_style="green"))
        if output:
            _emit(results, "md", output, quiet)
    else:
        _emit(results, fmt, output, quiet)

    sys.exit(1 if _should_fail(results, cfg.fail_on) else 0)


# ── fix ───────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--dir", "-d", "directory", default=".")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing")
@click.option("--no-cache", is_flag=True)
def fix(directory, dry_run, no_cache):
    """Update requirements to the latest safe version of each dependency.

    Rewrites pinned versions (==x.y.z) in requirements.txt to the latest
    version that has no breaking changes against your code.
    """
    if no_cache:
        cache_mod.disable_cache()

    directory = os.path.abspath(directory)
    cfg = load_config(directory)
    deps = discover_dependencies(directory)

    req_files = []
    for rel in ["requirements.txt", "requirements-dev.txt", "requirements/base.txt", "requirements/dev.txt"]:
        path = os.path.join(directory, rel)
        if os.path.isfile(path):
            req_files.append(path)

    if not req_files:
        console.print("[yellow]No requirements.txt files found to fix.[/]")
        sys.exit(2)

    from packaging.version import Version, InvalidVersion

    updates: List[dict] = []

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as progress:
        task = progress.add_task("Scanning...", total=len(deps))
        for dep in deps:
            progress.update(task, description=f"Checking [bold]{dep.name}[/]...")

            if not dep.pinned_version:
                progress.advance(task)
                continue

            pinned = dep.pinned_version
            try:
                latest = get_current_version(dep.name)
            except Exception:
                progress.advance(task)
                continue

            try:
                if Version(pinned) >= Version(latest):
                    progress.advance(task)
                    continue
            except InvalidVersion:
                progress.advance(task)
                continue

            try:
                changes = get_changes_between(dep.name, pinned, latest)
            except Exception:
                changes = []

            usages = scan_directory(directory, dep.name, exclude_dirs=cfg.exclude_dirs)
            risks, _ = analyze(usages, changes, dep.name)
            breaking = [r for r in risks if r.severity == "breaking"]

            if not breaking:
                updates.append({
                    "name": dep.name,
                    "old": pinned,
                    "new": latest,
                    "source": dep.source,
                })
            progress.advance(task)

    if not updates:
        console.print("[green]Nothing to update.[/] All pinned deps are at the latest safe version.")
        return

    if dry_run:
        console.print("[dim]DRY RUN - no files written[/]\n")
    console.print(f"[bold]{len(updates)}[/] safe update(s):\n")
    for u in updates:
        console.print(f"  [cyan]{u['name']}[/]  {u['old']} -> [green]{u['new']}[/]  [dim]({u['source']})[/]")

    if dry_run:
        return

    for req_path in req_files:
        try:
            with open(req_path, "r", encoding="utf-8") as f:
                content = f.read()
            for u in updates:
                content = re.sub(
                    rf"(?i)({re.escape(u['name'])}\s*==\s*){re.escape(u['old'])}",
                    rf"\g<1>{u['new']}",
                    content,
                )
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            console.print(f"[red]Could not write {req_path}:[/] {e}")

    console.print(f"\n[green]Done.[/] Run [bold]pip install -r requirements.txt[/] to apply.")


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

jobs:
  pyupcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

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


@main.command("ci-setup")
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
            console.print(f"[yellow]Already exists:[/] {os.path.relpath(path)}")
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_GH_ACTIONS)
            console.print(f"[green]Created:[/] {os.path.relpath(path)}")

    if ci_type in ("pre-commit", "all"):
        path = os.path.join(directory, ".pre-commit-config.yaml")
        if os.path.exists(path):
            console.print(f"[yellow]Already exists:[/] {os.path.relpath(path)} (not modified)")
            console.print("Add this to your existing config:")
            console.print(_PRE_COMMIT)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_PRE_COMMIT)
            console.print(f"[green]Created:[/] {os.path.relpath(path)}")
            console.print("\nInstall the hook with: [bold]pre-commit install[/]")

    console.print("\nDone. Commit the generated files and push.")


# ── outdated ──────────────────────────────────────────────────────────────────

@main.command()
@click.option("--dir", "-d", "directory", default=".")
@click.option("--no-cache", is_flag=True)
def outdated(directory, no_cache):
    """List all dependencies that have newer versions available."""
    if no_cache:
        cache_mod.disable_cache()
    directory = os.path.abspath(directory)
    deps = discover_dependencies(directory)
    if not deps:
        console.print("[yellow]No dependency files found[/]")
        sys.exit(2)

    from packaging.version import Version, InvalidVersion

    table = Table(title="Outdated dependencies")
    table.add_column("Package", style="cyan")
    table.add_column("Installed", style="dim")
    table.add_column("Latest", style="green")
    table.add_column("Status")

    outdated_count = 0
    rows = []
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as progress:
        task = progress.add_task("Checking versions...", total=len(deps))
        for dep in deps:
            progress.update(task, description=f"Checking [bold]{dep.name}[/]...")
            installed = get_installed_version(dep.name)
            try:
                latest = get_current_version(dep.name)
            except Exception:
                progress.advance(task)
                continue
            if not installed:
                rows.append((dep.name, "-", latest, "[dim]not installed[/]"))
                progress.advance(task)
                continue
            try:
                if Version(installed) < Version(latest):
                    outdated_count += 1
                    major_bump = Version(installed).major < Version(latest).major
                    status = "[red]major bump[/]" if major_bump else "[yellow]update available[/]"
                    rows.append((dep.name, installed, latest, status))
            except InvalidVersion:
                pass
            progress.advance(task)

    if outdated_count:
        for row in rows:
            table.add_row(*row)
        console.print(table)
        console.print(f"\n[bold]{outdated_count}[/] outdated. Run [bold]pyupcheck check-all[/] to see if upgrades are safe.")
    else:
        console.print("[green]All dependencies up to date.[/]")


# ── diff ──────────────────────────────────────────────────────────────────────

@main.command()
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

    with console.status(f"Fetching changes for [bold]{package}[/] {from_version} -> {to_version}..."):
        try:
            changes = get_changes_between(package, from_version, to_version, github_token=github_token)
        except Exception as e:
            console.print(f"[red]Error:[/] {e}")
            sys.exit(2)

    if since and changes:
        changes = _filter_by_since(changes, since, package)

    if not changes:
        console.print(f"[green]No breaking/deprecated changes found[/] between {from_version} and {to_version}.")
        return

    console.print(f"\n[bold]{package}[/] {from_version} -> {to_version}: {len(changes)} changes\n")
    kind_color = {"removed": "red", "deprecated": "yellow", "renamed": "blue", "changed": "magenta"}
    for c in changes:
        color = kind_color.get(c.kind, "white")
        console.print(f"  [{color}]{c.kind.upper():<11}[/] [bold]{c.version:<10}[/] {c.description}")


# ── scan ──────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("package")
@click.option("--dir", "-d", "directory", default=".")
@click.option("--exclude", "-e", multiple=True)
def scan(package, directory, exclude):
    """Scan your code for all usages of PACKAGE."""
    directory = os.path.abspath(directory)
    cfg = load_config(directory)
    cfg.exclude_dirs.update(exclude)

    with console.status(f"Scanning for usages of [bold]{package}[/]..."):
        usages = scan_directory(directory, package, exclude_dirs=cfg.exclude_dirs)

    if not usages:
        console.print(f"[yellow]No usages of {package} found.[/]")
        sys.exit(0)

    table = Table(title=f"Usages of {package}")
    table.add_column("File", style="cyan")
    table.add_column("Line", justify="right", style="green")
    table.add_column("Type", style="magenta")
    table.add_column("API", style="yellow")
    table.add_column("Code", style="dim")

    for u in usages:
        try:
            rel_path = os.path.relpath(u.file)
        except ValueError:
            rel_path = u.file
        table.add_row(rel_path, str(u.line), u.usage_type, u.attr_chain, u.code[:70])

    console.print(table)
    console.print(f"\n[bold]{len(usages)}[/] usages across [bold]{len(set(u.file for u in usages))}[/] files.")


# ── versions ──────────────────────────────────────────────────────────────────

@main.command()
@click.argument("package")
def versions(package):
    """List available versions for PACKAGE."""
    with console.status(f"Fetching versions for [bold]{package}[/]..."):
        try:
            vers = get_available_versions(package)
        except Exception as e:
            console.print(f"[red]Error:[/] {e}")
            sys.exit(1)

    from packaging.version import Version, InvalidVersion
    parsed = []
    for v in vers:
        try:
            parsed.append(Version(v))
        except InvalidVersion:
            pass

    parsed.sort(reverse=True)
    console.print(f"[bold]{package}[/] latest versions:\n")
    for v in parsed[:10]:
        console.print(f"  {v}")
    if len(parsed) > 10:
        console.print(f"\n  ...and {len(parsed) - 10} more")


# ── cache-clear ───────────────────────────────────────────────────────────────

@main.command("cache-clear")
def cache_clear_cmd():
    """Clear the local response cache."""
    n = cache_mod.cache_clear()
    console.print(f"Removed [bold]{n}[/] cached entries.")


# ── shell completion ──────────────────────────────────────────────────────────

@main.command("install-completion")
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
