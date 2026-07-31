"""CLI entry point for depshift."""

import os
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from depshift import __version__
from depshift.scanner import scan_directory
from depshift.changelog import (
    get_changes_between,
    get_current_version,
    get_available_versions,
    get_pypi_info,
)
from depshift.analyzer import analyze


console = Console()


def get_installed_version(package: str) -> Optional[str]:
    """Try to get the locally installed version of a package."""
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return None


@click.group()
@click.version_option(__version__, prog_name="pyupcheck")
def main():
    """pyupcheck - Check if upgrading a Python dependency will break your code."""
    pass


@main.command()
@click.argument("package")
@click.argument("target_version", required=False)
@click.option("--dir", "-d", "directory", default=".", help="Directory to scan (default: current dir)")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None, help="GitHub token for higher rate limits")
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON")
def check(package: str, target_version: Optional[str], directory: str, github_token: Optional[str], json_output: bool):
    """Check if upgrading PACKAGE to TARGET_VERSION will break your code.

    If TARGET_VERSION is omitted, checks against the latest version on PyPI.
    """
    directory = os.path.abspath(directory)

    # resolve current version
    installed = get_installed_version(package)
    if not installed:
        console.print(f"[yellow]Warning:[/] {package} is not installed locally. "
                       "Cannot determine current version, will show all known changes.")

    # resolve target version
    if not target_version:
        with console.status(f"Fetching latest version of [bold]{package}[/]..."):
            try:
                target_version = get_current_version(package)
            except Exception as e:
                console.print(f"[red]Error:[/] Could not find {package} on PyPI: {e}")
                sys.exit(1)
        console.print(f"Latest version: [bold]{target_version}[/]")

    if installed and installed == target_version:
        console.print(f"[green]You're already on {target_version}. Nothing to check.[/]")
        sys.exit(0)

    current = installed or "0.0.0"
    console.print(f"\n[bold]depshift[/] {current} -> {target_version}\n")

    # step 1: scan codebase
    with console.status(f"Scanning [bold]{directory}[/] for usages of [bold]{package}[/]..."):
        usages = scan_directory(directory, package)

    if not usages:
        console.print(f"[yellow]No usages of {package} found in {directory}[/]")
        console.print("Nothing to check. You're safe to upgrade.")
        sys.exit(0)

    file_count = len(set(u.file for u in usages))
    console.print(f"Found [bold]{len(usages)}[/] usages across [bold]{file_count}[/] files.\n")

    # step 2: fetch changes
    with console.status(f"Fetching changelog for [bold]{package}[/] {current} -> {target_version}..."):
        try:
            changes = get_changes_between(package, current, target_version, github_token=github_token)
        except Exception as e:
            console.print(f"[red]Error fetching changelog:[/] {e}")
            changes = []

    if not changes:
        console.print("[yellow]No changelog data found.[/] Could not find breaking changes info.")
        console.print("This could mean the upgrade is safe, or that the changelog isn't machine-readable.")
        console.print(f"\nTip: check the changelog manually at the project's GitHub/docs.")
        _print_usages_summary(usages, package)
        sys.exit(0)

    console.print(f"Found [bold]{len(changes)}[/] potentially breaking/deprecated changes.\n")

    # step 3: cross-reference
    risks, safe = analyze(usages, changes, package)

    if json_output:
        _print_json(risks, safe, package, current, target_version)
    else:
        _print_report(risks, safe, changes, package, current, target_version)

    # exit code
    breaking_count = sum(1 for r in risks if r.severity == "breaking")
    sys.exit(1 if breaking_count > 0 else 0)


def _print_report(risks, safe, changes, package, current, target_version):
    """Print a human-readable report."""
    if not risks:
        console.print(Panel(
            f"[green bold]All clear![/]\n\n"
            f"None of your {len(safe)} usages of {package} are affected by "
            f"known changes in {target_version}.",
            title="depshift report",
            border_style="green",
        ))
        return

    # breaking
    breaking = [r for r in risks if r.severity == "breaking"]
    deprecated = [r for r in risks if r.severity == "deprecated"]
    warnings = [r for r in risks if r.severity == "warning"]

    if breaking:
        console.print(f"[red bold]BREAKING ({len(breaking)}):[/]\n")
        for r in breaking:
            rel_path = os.path.relpath(r.usage.file)
            console.print(f"  [red]x[/] [bold]{rel_path}:{r.usage.line}[/]  {r.usage.code}")
            console.print(f"    [dim]{r.change.description}[/]\n")

    if deprecated:
        console.print(f"[yellow bold]DEPRECATED ({len(deprecated)}):[/]\n")
        for r in deprecated:
            rel_path = os.path.relpath(r.usage.file)
            console.print(f"  [yellow]![/] [bold]{rel_path}:{r.usage.line}[/]  {r.usage.code}")
            console.print(f"    [dim]{r.change.description}[/]\n")

    if warnings:
        console.print(f"[blue bold]WARNINGS ({len(warnings)}):[/]\n")
        for r in warnings:
            rel_path = os.path.relpath(r.usage.file)
            console.print(f"  [blue]?[/] [bold]{rel_path}:{r.usage.line}[/]  {r.usage.code}")
            console.print(f"    [dim]{r.change.description}[/]\n")

    console.print(f"[green]{len(safe)}[/] other usages are safe.\n")

    # summary
    if breaking:
        console.print(Panel(
            f"[red bold]Upgrade risky.[/] {len(breaking)} breaking change(s) affect your code.",
            border_style="red",
        ))
    elif deprecated:
        console.print(Panel(
            f"[yellow]Upgrade possible[/] but {len(deprecated)} deprecated API(s) should be updated.",
            border_style="yellow",
        ))


def _print_usages_summary(usages, package):
    """Print a summary of found usages."""
    console.print(f"\n[bold]Your usages of {package}:[/]")
    apis = set()
    for u in usages:
        if u.usage_type != "import":
            apis.add(u.attr_chain)
    for api in sorted(apis):
        console.print(f"  {api}")


def _print_json(risks, safe, package, current, target_version):
    """Print JSON output."""
    import json
    output = {
        "package": package,
        "current_version": current,
        "target_version": target_version,
        "risks": [
            {
                "file": r.usage.file,
                "line": r.usage.line,
                "code": r.usage.code,
                "api": r.usage.attr_chain,
                "severity": r.severity,
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
    console.print_json(json.dumps(output))


@main.command()
@click.argument("package")
def scan(package: str):
    """Just scan your code for usages of PACKAGE (no upgrade check)."""
    directory = os.path.abspath(".")

    with console.status(f"Scanning for usages of [bold]{package}[/]..."):
        usages = scan_directory(directory, package)

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
def versions(package: str):
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
    latest_10 = parsed[:10]

    console.print(f"[bold]{package}[/] latest versions:\n")
    for v in latest_10:
        console.print(f"  {v}")
    if len(parsed) > 10:
        console.print(f"\n  ...and {len(parsed) - 10} more")


if __name__ == "__main__":
    main()
