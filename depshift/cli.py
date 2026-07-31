"""CLI entry point for pyupcheck."""

import json as _json
import os
import sys
from typing import List, Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from depshift import __version__
from depshift.scanner import scan_directory
from depshift.changelog import (
    get_changes_between,
    get_current_version,
    get_available_versions,
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


def _run_single_check(package: str, target_version: Optional[str], directory: str,
                      github_token: Optional[str], cfg, quiet: bool) -> Optional[dict]:
    """Run a check for one package. Returns result dict or None on error."""
    installed = get_installed_version(package)

    if not target_version:
        try:
            target_version = get_current_version(package)
        except Exception as e:
            if not quiet:
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

    # terminal format
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
        rel = os.path.relpath(risk["file"])
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


@main.command()
@click.argument("package")
@click.argument("target_version", required=False)
@click.option("--dir", "-d", "directory", default=".", help="Directory to scan")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=click.Choice(["terminal", "json", "md", "html"]), default="terminal")
@click.option("--output", "-o", default=None, help="Write report to file")
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default=None)
@click.option("--min-severity", type=click.Choice(["breaking", "deprecated", "warning"]), default=None)
@click.option("--exclude", "-e", multiple=True, help="Extra directories to exclude")
@click.option("--no-cache", is_flag=True, help="Bypass response cache")
@click.option("--quiet", "-q", is_flag=True, help="Only print problems")
def check(package, target_version, directory, github_token, fmt, output, fail_on,
          min_severity, exclude, no_cache, quiet):
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

    if not quiet and fmt == "terminal":
        with console.status(f"Checking [bold]{package}[/]..."):
            result = _run_single_check(package, target_version, directory, github_token, cfg, quiet)
    else:
        result = _run_single_check(package, target_version, directory, github_token, cfg, quiet)

    if result is None:
        sys.exit(2)

    _emit([result], fmt, output, quiet)
    sys.exit(1 if _should_fail([result], cfg.fail_on) else 0)


@main.command("check-all")
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=click.Choice(["terminal", "json", "md", "html"]), default="terminal")
@click.option("--output", "-o", default=None, help="Write report to file")
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default=None)
@click.option("--min-severity", type=click.Choice(["breaking", "deprecated", "warning"]), default=None)
@click.option("--exclude", "-e", multiple=True)
@click.option("--no-cache", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
def check_all(directory, github_token, fmt, output, fail_on, min_severity, exclude, no_cache, quiet):
    """Check ALL dependencies found in requirements.txt / pyproject.toml."""
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
        console.print("[yellow]No dependency files found[/] (looked for requirements.txt, pyproject.toml)")
        sys.exit(2)

    if not quiet:
        console.print(f"Found [bold]{len(deps)}[/] dependencies to check\n")

    results = []
    for dep in deps:
        if not quiet and fmt == "terminal":
            with console.status(f"Checking [bold]{dep.name}[/]..."):
                r = _run_single_check(dep.name, None, directory, github_token, cfg, quiet=True)
        else:
            r = _run_single_check(dep.name, None, directory, github_token, cfg, quiet=True)
        if r:
            results.append(r)
            if fmt == "terminal":
                _print_terminal_result(r, quiet)

    if fmt != "terminal":
        _emit(results, fmt, output, quiet)
    elif output:
        _emit(results, "md", output, quiet)

    total_breaking = sum(r["breaking_count"] for r in results)
    total_deprecated = sum(r["deprecated_count"] for r in results)
    if fmt == "terminal" and not quiet:
        console.print()
        if total_breaking:
            console.print(Panel(f"[red bold]{total_breaking} breaking[/] | "
                                f"[yellow]{total_deprecated} deprecated[/] across {len(results)} packages",
                                border_style="red"))
        elif total_deprecated:
            console.print(Panel(f"[yellow]{total_deprecated} deprecated[/] across {len(results)} packages",
                                border_style="yellow"))
        else:
            console.print(Panel(f"[green]All {len(results)} packages safe to upgrade[/]",
                                border_style="green"))

    sys.exit(1 if _should_fail(results, cfg.fail_on) else 0)


@main.command()
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
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
    with console.status("Checking versions..."):
        for dep in deps:
            installed = get_installed_version(dep.name)
            try:
                latest = get_current_version(dep.name)
            except Exception:
                continue
            if not installed:
                table.add_row(dep.name, "-", latest, "[dim]not installed[/]")
                continue
            try:
                if Version(installed) < Version(latest):
                    outdated_count += 1
                    major_bump = Version(installed).major < Version(latest).major
                    status = "[red]major bump[/]" if major_bump else "[yellow]update available[/]"
                    table.add_row(dep.name, installed, latest, status)
            except InvalidVersion:
                continue

    if outdated_count:
        console.print(table)
        console.print(f"\n[bold]{outdated_count}[/] outdated. Run [bold]pyupcheck check-all[/] to see if upgrades are safe.")
    else:
        console.print("[green]All dependencies up to date.[/]")


@main.command()
@click.argument("package")
@click.argument("from_version")
@click.argument("to_version")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--no-cache", is_flag=True)
def diff(package, from_version, to_version, github_token, no_cache):
    """Show breaking/deprecated changes in PACKAGE between two versions."""
    if no_cache:
        cache_mod.disable_cache()

    with console.status(f"Fetching changes for [bold]{package}[/] {from_version} -> {to_version}..."):
        try:
            changes = get_changes_between(package, from_version, to_version, github_token=github_token)
        except Exception as e:
            console.print(f"[red]Error:[/] {e}")
            sys.exit(2)

    if not changes:
        console.print(f"[green]No breaking/deprecated changes found[/] between {from_version} and {to_version}.")
        console.print("[dim]Note: relies on changelog quality. Check release notes for full detail.[/]")
        return

    console.print(f"\n[bold]{package}[/] {from_version} -> {to_version}: {len(changes)} changes\n")
    kind_color = {"removed": "red", "deprecated": "yellow", "renamed": "blue", "changed": "magenta"}
    for c in changes:
        color = kind_color.get(c.kind, "white")
        console.print(f"  [{color}]{c.kind.upper():<11}[/] [bold]{c.version:<10}[/] {c.description}")


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
    table.add_column("API", style="yellow")
    table.add_column("Code", style="dim")

    for u in usages:
        rel_path = os.path.relpath(u.file)
        table.add_row(rel_path, str(u.line), u.attr_chain, u.code[:80])

    console.print(table)
    console.print(f"\n[bold]{len(usages)}[/] usages across [bold]{len(set(u.file for u in usages))}[/] files.")


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


@main.command("cache-clear")
def cache_clear_cmd():
    """Clear the local response cache."""
    n = cache_mod.cache_clear()
    console.print(f"Removed [bold]{n}[/] cached entries.")


if __name__ == "__main__":
    main()
