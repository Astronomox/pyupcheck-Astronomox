"""CLI entry point for pyupcheck."""

import json as _json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from depshift import __version__
from depshift.scanner import scan_directory
from depshift.changelog import (
    collect_changes,
    get_changes_between,
    get_current_version,
    get_available_versions,
    get_pypi_info,
)
from depshift.analyzer import analyze
from depshift.deps import discover_dependencies, normalize_name, requirement_files
from depshift.config import load_config
from depshift import cache as cache_mod
from depshift.report import render_markdown, render_html, render_sarif, render_github_annotations
from depshift.apisurface import get_api_changes, get_return_types
from depshift.precise import match_precise
from depshift.names import import_names_for
from depshift.lockfiles import locked_version, locked_versions
from depshift.scanner import imported_top_modules


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


def latest_allowed_version(package: str, spec: Optional[str] = None, fresh: bool = False) -> Optional[str]:
    """Newest non-yanked, non-prerelease version on PyPI that satisfies spec.

    With no spec this is the latest release. A constraint like ``>=2,<3`` means
    the project will never install 3.x, so checking against 3.x is noise.
    """
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
    from packaging.version import Version, InvalidVersion
    info = get_pypi_info(package, fresh=True) if fresh else get_pypi_info(package)
    try:
        spec_set = SpecifierSet(spec or "")
    except InvalidSpecifier:
        spec_set = SpecifierSet("")
    best = None
    for ver, files in (info.get("releases") or {}).items():
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        if v.is_prerelease or v.is_devrelease:
            continue
        if files and all(f.get("yanked") for f in files):
            continue
        if spec_set and v not in spec_set:
            continue
        if best is None or v > best:
            best = v
    return str(best) if best is not None else None


def specifier_of(raw: str) -> Optional[str]:
    """The version specifier of a requirement string, e.g. '>=2,<3'."""
    from packaging.requirements import Requirement, InvalidRequirement
    try:
        spec = str(Requirement(raw).specifier)
    except InvalidRequirement:
        return None
    return spec or None


def _satisfies(version: str, spec: str) -> bool:
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
    from packaging.version import Version, InvalidVersion
    try:
        return Version(version) in SpecifierSet(spec)
    except (InvalidSpecifier, InvalidVersion):
        return True


def _is_exact_pin(spec: Optional[str]) -> bool:
    if not spec:
        return False
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    return len(parts) == 1 and parts[0].startswith(("==", "===")) and "*" not in parts[0]


def _not_newer(target: str, current: str) -> bool:
    """True when target is the same as or older than current (nothing to upgrade)."""
    from packaging.version import Version, InvalidVersion
    try:
        return Version(target) <= Version(current)
    except InvalidVersion:
        return target == current


def _lower_bound(spec: Optional[str]) -> Optional[str]:
    """The minimum version a spec allows (>=, ~=, ==), used as 'current'
    when nothing is installed or pinned."""
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
    if not spec:
        return None
    try:
        specs = SpecifierSet(spec)
    except InvalidSpecifier:
        return None
    for sp in specs:
        if sp.operator in (">=", "~=", "==", "===") and "*" not in sp.version:
            return sp.version
    return None


UNDECLARED = "(imported, not declared)"


def _undeclared_imports(directory: str, deps, locks, ignored, exclude_dirs):
    """Locked packages the code imports directly without declaring them.

    They arrive transitively (``urllib3`` via ``requests``), so their version
    can change without you choosing to upgrade them, and they break the same.
    """
    from depshift.deps import Dependency
    if not locks:
        return []
    declared = {d.name for d in deps}
    imported = imported_top_modules(directory, exclude_dirs)
    out = []
    for name, lock in sorted(locks.items()):
        if name in declared or name in ignored:
            continue
        if any(n in imported for n in import_names_for(name)):
            out.append(Dependency(name=name, pinned_version=lock.version, raw=name,
                                  source=f"{lock.source} {UNDECLARED}"))
    return out


def _pinned_version(directory: str, package: str) -> Optional[str]:
    """The ==pinned version of package in the project's dependency files, if any."""
    target = normalize_name(package)
    for dep in discover_dependencies(directory):
        if dep.name == target and dep.pinned_version:
            return dep.pinned_version
    return None


def _baseline_version(package: str, directory: str, pinned: Optional[str] = None,
                      spec: Optional[str] = None, locked: Optional[str] = "auto"):
    """(version, source) the project is on now, most trustworthy first: the
    lockfile (what the project runs), an exact pin, what's installed here (if
    it fits the project's constraint), then the lowest version the constraint
    allows. (None, None) when there is no baseline."""
    if locked == "auto":
        locked = locked_version(directory, package)
    if locked:
        return locked, "lockfile"
    pin = pinned or _pinned_version(directory, package)
    if pin:
        return pin, "pinned"
    here = get_installed_version(package)
    if here and not (spec and not _satisfies(here, spec)):
        return here, "installed"
    if spec:
        # unpinned range: the oldest version it allows is the riskiest baseline
        low = _lower_bound(spec)
        if low:
            return low, "constraint lower bound"
    return None, None


def _run_single_check(package: str, target_version: Optional[str], directory: str,
                      github_token: Optional[str], cfg, suppress_errors: bool = False,
                      since: Optional[str] = None, deep: bool = False,
                      pinned: Optional[str] = None, spec: Optional[str] = None,
                      locked: Optional[str] = "auto") -> Optional[dict]:
    installed, current_source = _baseline_version(package, directory, pinned, spec, locked)

    if not target_version:
        try:
            # an exact pin says where you are, not where you may go
            range_spec = None if _is_exact_pin(spec) else spec
            target_version = latest_allowed_version(package, range_spec) or get_current_version(package)
        except Exception as e:
            if not suppress_errors:
                console.print(f"[red]{package}:[/] not found on PyPI ({e})")
            return None

    if installed and _not_newer(target_version, installed):
        return {
            "package": package, "current_version": installed,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "up_to_date": True,
        }

    if not installed:
        # Comparing against 0.0.0 would report every change in the package's
        # history. Be honest instead.
        return {
            "package": package, "current_version": None,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "unchecked": True,
            "warnings": ["current version unknown: install the package, pin it with ==, "
                         "or give a lower bound (>=) so there is a baseline to compare"],
        }

    current = installed
    import_names = import_names_for(package)
    usages = scan_directory(directory, import_names, exclude_dirs=cfg.exclude_dirs)
    if not usages:
        return {
            "package": package, "current_version": current,
            "target_version": target_version, "risks": [],
            "safe_count": 0, "breaking_count": 0, "deprecated_count": 0,
            "no_usages": True, "import_names": import_names,
        }

    warnings: List[str] = []
    try:
        report = collect_changes(package, current, target_version, github_token=github_token)
        changes = report.entries
        if report.sources_failed:
            warnings.append("could not read: " + ", ".join(report.sources_failed))
        changelog_checked = report.checked
    except Exception as e:
        changes = []
        changelog_checked = False
        warnings.append(f"changelog lookup failed ({type(e).__name__})")

    if since and changes:
        changes = _filter_by_since(changes, since, package)

    risks, safe = analyze(usages, changes, import_names)

    min_rank = SEVERITY_RANK.get(cfg.min_severity, 2)
    risks = [r for r in risks if SEVERITY_RANK.get(r.severity, 2) <= min_rank]

    # deep mode: real API surface diff, argument-level precision
    precise_findings = []
    if deep:
        try:
            api_changes = get_api_changes(package, current, target_version)
        except Exception as e:
            api_changes = None
            warnings.append(f"deep API diff failed ({type(e).__name__})")
        if api_changes is None and not any("deep" in w for w in warnings):
            warnings.append("deep API diff unavailable (no Python sources for one of the versions)")
        if api_changes:
            try:
                returns = get_return_types(package, current, target_version)
            except Exception:
                returns = {}
            precise = match_precise(usages, api_changes, import_names, returns)
            # dedupe against changelog risks by (file, line, api)
            existing_keys = {(r.usage.file, r.usage.line, r.usage.attr_chain) for r in risks}
            for pr in precise:
                key = (pr.usage.file, pr.usage.line, pr.usage.attr_chain)
                if key in existing_keys:
                    continue  # changelog already flagged this exact usage
                existing_keys.add(key)
                precise_findings.append({
                    "file": pr.usage.file, "line": pr.usage.line,
                    "code": pr.usage.code, "api": pr.usage.attr_chain,
                    "severity": "breaking", "change_kind": pr.change.kind,
                    "change_description": pr.reason,
                    "change_version": target_version,
                    "precise": True, "confidence": "high",
                    "replacement": getattr(pr.change, "replacement", None),
                })

    risk_dicts = [
        {
            "file": r.usage.file, "line": r.usage.line, "code": r.usage.code,
            "api": r.usage.attr_chain, "severity": r.severity,
            "change_kind": r.change.kind,
            "change_description": r.change.description,
            "change_version": r.change.version,
            "confidence": getattr(r.change, "confidence", "medium"),
            "replacement": getattr(r.change, "replacement", None),
        }
        for r in risks
    ]
    # prepend precise (fact-based) findings — they're more trustworthy
    all_risks = _dedupe_risks(precise_findings + risk_dicts)

    return {
        "package": package,
        "current_version": current,
        "target_version": target_version,
        "risks": all_risks,
        "safe_count": len(safe),
        "breaking_count": sum(1 for r in all_risks if r["severity"] == "breaking"),
        "deprecated_count": sum(1 for r in all_risks if r["severity"] == "deprecated"),
        "deep": deep,
        "current_source": current_source,
        "warnings": warnings,
        # nothing could be read at all: "OK" would be a lie
        "unchecked": not changelog_checked and not (deep and api_changes is not None),
    }


def _dedupe_risks(risks: List[dict]) -> List[dict]:
    """One finding per (file, line, API): the most severe, verified first.

    A removed API usually also has an earlier deprecation notice; once a
    line is known to break, repeating that it was deprecated is noise.
    """
    order = {"breaking": 0, "deprecated": 1, "warning": 2}
    best: Dict[tuple, dict] = {}
    for r in risks:
        key = (r["file"], r["line"], r["api"])
        cur = best.get(key)
        rank = (order.get(r["severity"], 3), 0 if r.get("precise") else 1)
        if cur is None or rank < (order.get(cur["severity"], 3), 0 if cur.get("precise") else 1):
            best[key] = r
    kept = {id(r) for r in best.values()}
    return [r for r in risks if id(r) in kept]


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
    elif fmt == "sarif":
        text = render_sarif(results)
    elif fmt == "github":
        text = render_github_annotations(results)
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


def _esc(text: str) -> str:
    """Text shown as is, never read as rich markup ("[cell 3] x = a[0]")."""
    from rich.markup import escape
    return escape(text)


def _clean_note(text: str) -> str:
    """Release-note text for the terminal: no :pr:/:issue: references or
    reST markup, and nothing rich would read as a style tag."""
    from rich.markup import escape
    from depshift.review import _clean
    return escape(_clean(text).replace("\\|", "|"))


def _print_terminal_result(r: dict, quiet: bool):
    if r.get("up_to_date"):
        if not quiet:
            console.print(f"[green]{r['package']}[/] already on {r['target_version']}")
        return
    if r.get("no_usages"):
        if not quiet:
            console.print(f"[dim]{r['package']}[/] no usages found, safe to upgrade to {r['target_version']}")
        return

    if r.get("unchecked") and not r["risks"]:
        cur = r.get("current_version") or "?"
        console.print(f"[magenta]{r['package']}[/] {cur} -> {r['target_version']}  "
                      f"[magenta]NOT CHECKED[/]")
        for w in r.get("warnings", []):
            console.print(f"    [dim]{w}[/]")
        return

    via = " [dim](locked)[/]" if r.get("current_source") == "lockfile" else ""
    undeclared = " [magenta](undeclared)[/]" if r.get("undeclared") else ""
    header = f"[bold]{r['package']}[/]{undeclared} {r['current_version']}{via} -> {r['target_version']}"
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
        if risk.get("precise"):
            tag = " [cyan][FACT][/]"
        elif risk.get("confidence") == "low":
            tag = " [dim](low confidence)[/]"
        else:
            tag = ""
        console.print(f"  [{color}]{mark}[/]{tag} [bold]{rel}:{risk['line']}[/]  {_esc(risk['code'])}")
        console.print(f"    [dim]{_clean_note(risk['change_description'])}[/]")
        if risk.get("replacement"):
            console.print(f"    [green]-> use {risk['replacement']}[/]")
    if not quiet:
        for w in r.get("warnings", []):
            console.print(f"    [magenta]note:[/] [dim]{w}[/]")


@click.group()
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


@main.command("banner")
def show_banner_cmd():
    """Show the pyupcheck banner with live version info."""
    from depshift.welcome import show_banner
    show_banner(console)


# ── check ─────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("package")
@click.argument("target_version", required=False)
@click.option("--dir", "-d", "directory", default=".", help="Directory to scan")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=click.Choice(["terminal", "json", "md", "html", "sarif", "github"]), default="terminal")
@click.option("--output", "-o", default=None, help="Write report to file")
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default=None)
@click.option("--min-severity", type=click.Choice(["breaking", "deprecated", "warning"]), default=None)
@click.option("--exclude", "-e", multiple=True)
@click.option("--no-cache", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
@click.option("--since", default=None, metavar="YYYY-MM-DD", help="Only show changes introduced since this date")
@click.option("--deep", is_flag=True, help="Deep mode: diff real API surface between versions for fact-based, argument-level detection")
def check(package, target_version, directory, github_token, fmt, output, fail_on,
          min_severity, exclude, no_cache, quiet, since, deep):
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

    status_msg = f"Deep-checking [bold]{package}[/]..." if deep else f"Checking [bold]{package}[/]..."
    with console.status(status_msg):
        result = _run_single_check(package, target_version, directory, github_token,
                                   cfg, suppress_errors=quiet, since=since, deep=deep)

    if result is None:
        sys.exit(2)

    _emit([result], fmt, output, quiet)
    sys.exit(1 if _should_fail([result], cfg.fail_on) else 0)


# ── check-all ─────────────────────────────────────────────────────────────────

@main.command("check-all")
@click.option("--dir", "-d", "directory", default=".", help="Project directory")
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--format", "-f", "fmt", type=click.Choice(["terminal", "json", "md", "html", "sarif", "github"]), default="terminal")
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
def check_all(directory, github_token, fmt, output, fail_on, min_severity, exclude, no_cache, quiet, since, deep,
              jobs):
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
    ignored = {normalize_name(p) for p in cfg.ignore_packages}
    deps = [d for d in deps if d.name not in ignored]
    locks = locked_versions(directory)
    deps += _undeclared_imports(directory, deps, locks, ignored, cfg.exclude_dirs)

    if not deps:
        console.print("[yellow]No dependency files found[/] (looked for requirements*.txt, "
                      "pyproject.toml, Pipfile, setup.cfg, setup.py, environment.yml)")
        sys.exit(2)

    # JSON/Markdown/HTML on stdout must be nothing but the report
    chatty = not quiet and (fmt == "terminal" or bool(output))
    if chatty:
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
        disable=not chatty,
    ) as progress:
        task = progress.add_task("Checking dependencies...", total=len(deps))

        def check_one(dep):
            lock = locks.get(dep.name)
            r = _run_single_check(dep.name, None, directory, github_token, cfg,
                                  suppress_errors=True, since=since, pinned=dep.pinned_version,
                                  deep=deep, spec=specifier_of(dep.raw),
                                  locked=lock.version if lock else None)
            if r and dep.source.endswith(UNDECLARED):
                r["undeclared"] = True
                r.setdefault("warnings", []).append(
                    f"imported by your code but not declared as a dependency "
                    f"(only present via {lock.source if lock else 'another package'})")
            return r

        # Checks are almost entirely network waits (PyPI, GitHub, downloads),
        # so threads overlap them well. Results keep the dependency order.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        slots: List[Optional[dict]] = [None] * len(deps)
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
        for r in results:
            _print_terminal_result(r, quiet)

        total_breaking = sum(r["breaking_count"] for r in results)
        total_deprecated = sum(r["deprecated_count"] for r in results)
        total_unchecked = sum(1 for r in results if r.get("unchecked"))
        if not quiet:
            console.print()
            if total_unchecked:
                console.print(f"[magenta]{total_unchecked} package(s) could not be checked[/] "
                              "(see notes above); they are not counted as safe")
            if total_breaking:
                console.print(Panel(
                    f"[red bold]{total_breaking} breaking[/] | [yellow]{total_deprecated} deprecated[/] across {len(results)} packages",
                    border_style="red"))
            elif total_deprecated:
                console.print(Panel(f"[yellow]{total_deprecated} deprecated[/] across {len(results)} packages",
                                    border_style="yellow"))
            else:
                checked = len(results) - total_unchecked
                console.print(Panel(f"[green]{checked} checked packages safe to upgrade[/]",
                                    border_style="green" if not total_unchecked else "magenta"))
        if output:
            _emit(results, "md", output, quiet)
    else:
        _emit(results, fmt, output, quiet)

    sys.exit(1 if _should_fail(results, cfg.fail_on) else 0)


# ── fix ───────────────────────────────────────────────────────────────────────

@main.command()
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
    cfg = load_config(directory)
    ignored = {normalize_name(p) for p in cfg.ignore_packages}
    deps = [d for d in discover_dependencies(directory) if d.name not in ignored and d.pinned_version]

    req_files = requirement_files(directory)  # .txt and pip-tools .in sources

    if not req_files:
        console.print("[yellow]No requirements.txt files found to fix.[/]")
        sys.exit(2)

    updates: List[dict] = []
    skipped_unchecked: List[str] = []
    held_back: List[dict] = []

    def one(dep):
        try:
            return dep, highest_safe_version(dep.name, dep.pinned_version, directory, cfg, github_token,
                                             specifier_of(dep.raw), deep=deep)
        except Exception:
            return dep, None

    with console.status(f"Finding the highest safe version of {len(deps)} pinned dependencies..."):
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            plans = list(pool.map(one, deps))

    for dep, plan in plans:
        if plan is None or (plan["unchecked"] and plan["safe"] == dep.pinned_version):
            if plan is None or plan["latest"] != dep.pinned_version:
                skipped_unchecked.append(dep.name)
            continue
        if plan["safe"] != dep.pinned_version:
            updates.append({"name": dep.name, "old": dep.pinned_version, "new": plan["safe"],
                            "source": dep.source})
        if plan["safe"] != plan["latest"]:
            held_back.append({"name": dep.name, "latest": plan["latest"],
                              "breaking": plan["breaking_at_latest"]})

    if skipped_unchecked:
        console.print(f"[magenta]Skipped (could not be checked):[/] {', '.join(skipped_unchecked)}")
    for h in held_back:
        console.print(f"[yellow]{h['name']}[/]: {h['latest']} breaks {h['breaking']} usage(s); "
                      f"run [bold]pyupcheck migrate {h['name']} {h['latest']}[/] to see what can be fixed")
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
                # match any spelling of the name: Foo_Bar, foo-bar, foo.bar
                name_pat = "[-_.]+".join(re.escape(p) for p in re.split(r"[-_.]+", u["name"]))
                content = re.sub(
                    rf"(?im)^(\s*{name_pat}\s*(?:\[[^\]]*\])?\s*==\s*){re.escape(u['old'])}(?![\w.])",
                    rf"\g<1>{u['new']}",
                    content,
                )
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            console.print(f"[red]Could not write {req_path}:[/] {e}")

    console.print("\n[green]Done.[/] Run [bold]pip install -r requirements.txt[/] to apply.")


# ── review ────────────────────────────────────────────────────────────────────

def _fixable_count(package: str, old: str, new: str, directory: str, cfg,
                   github_token: Optional[str]) -> Optional[int]:
    """How many breaking usages `migrate` would fix automatically."""
    from depshift.apisurface import extract_surface
    from depshift.rewrite import plan_migration
    names = import_names_for(package)
    usages = scan_directory(directory, names, exclude_dirs=cfg.exclude_dirs)
    changes = get_api_changes(package, old, new)
    surface = extract_surface(package, new)
    if changes is None or surface is None:
        return None
    try:
        history = collect_changes(package, old, new, github_token=github_token, lookback=True).history
    except Exception:
        history = []
    plan = plan_migration(usages, changes, surface, names, history,
                          get_return_types(package, old, new))
    return len(plan.fixes)


def _check_change(change, directory: str, cfg, github_token: Optional[str], deep: bool) -> Optional[dict]:
    """Check one version change (a review.VersionChange) from its old version
    to its new one, with what migrate could fix."""
    r = _run_single_check(change.name, change.new, directory, github_token, cfg,
                          suppress_errors=True, deep=deep, spec=specifier_of(change.raw),
                          locked=change.old)
    if r is None:
        return None
    r["current_source"] = "previous"  # the version before the change, not a lockfile's
    if r.get("up_to_date"):
        # a downgrade or no-op: nothing new to break
        r.setdefault("warnings", []).append(f"{change.old} -> {change.new} is not an upgrade; not checked")
        r["current_version"] = change.old
    elif deep and r["breaking_count"]:
        try:
            r["fixable"] = _fixable_count(change.name, change.old, change.new, directory, cfg,
                                          github_token)
        except Exception:
            r["fixable"] = None
    return r


@main.command()
@click.option("--base", required=True, help="Git ref to compare against (e.g. origin/main, the PR base SHA)")
@click.option("--dir", "-d", "directory", default=".", help="Repository root")
@click.option("--deep/--no-deep", default=True, show_default=True,
              help="Diff real API surfaces and count what migrate can fix")
@click.option("--output", "-o", default=None, help="Write the Markdown report to a file")
@click.option("--fail-on", type=click.Choice(["breaking", "deprecated", "any", "never"]), default="never",
              show_default=True)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
@click.option("--jobs", "-j", type=click.IntRange(1, 64), default=8, show_default=True)
@click.option("--annotations", is_flag=True,
              help="Also print GitHub Actions annotations (one per finding, shown on the PR diff)")
@click.option("--sarif", "sarif_out", default=None, help="Also write a SARIF file (GitHub code scanning)")
@click.option("--no-cache", is_flag=True)
def review(base, directory, deep, output, fail_on, github_token, jobs, annotations, sarif_out, no_cache):
    """Check the dependency versions changed since BASE (for pull requests).

    Compares dependency files and lockfiles at --base with the working tree,
    then checks each package that changed version, from its old version to
    its new one. Prints Markdown suitable for a PR comment.
    """
    import subprocess as _sp
    from concurrent.futures import ThreadPoolExecutor
    from depshift.review import changed_dependencies, render_review

    directory = os.path.abspath(directory)
    cfg = load_config(directory)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()
    try:
        changes = changed_dependencies(directory, base)
    except (_sp.CalledProcessError, FileNotFoundError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        console.print(f"[red]Could not read dependency files at {base}:[/] {detail.strip()}")
        sys.exit(2)
    ignored = {normalize_name(p) for p in cfg.ignore_packages}
    changes = [c for c in changes if c.name not in ignored]

    def one(change):
        return _check_change(change, directory, cfg, github_token, deep)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = [r for r in pool.map(one, changes) if r]

    text = render_review(results, repo_root=directory)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    if sarif_out:
        with open(sarif_out, "w", encoding="utf-8") as f:
            f.write(render_sarif(results, directory))
    if annotations:
        notes = render_github_annotations(results, directory)
        if notes:
            print(notes)
    sys.exit(1 if _should_fail(results, fail_on) else 0)


# ── watch ─────────────────────────────────────────────────────────────────────

def _print_watch_event(ev, directory: str, quiet: bool) -> bool:
    """Print one watcher event; True if it found something breaking."""
    import datetime as _dt
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    if ev.kind == "error":
        console.print(f"[dim]{stamp}[/] [yellow]{', '.join(ev.files) or 'dependency files'}:[/] {ev.message}"
                      " [dim](keeping the last good state)[/]")
        return False
    if ev.kind == "release":
        c = ev.changes[0]
        console.print(f"[dim]{stamp}[/] [bold]new release:[/] {c.name} {c.new} [dim](you have {c.old})[/]")
    else:
        moved = f"{len(ev.changes)} version change(s)" if ev.changes else "no version changes"
        console.print(f"[dim]{stamp}[/] [bold]{', '.join(ev.files)}[/] changed: {moved}")
        if ev.added:
            console.print(f"  [green]+[/] added: {', '.join(ev.added)} [dim](nothing to compare with)[/]")
        if ev.removed:
            console.print(f"  [red]-[/] removed: {', '.join(ev.removed)}")
    breaking = False
    for r in ev.results:
        _print_terminal_result(r, quiet)
        if r.get("breaking_count"):
            breaking = True
            if r.get("fixable"):
                console.print(f"    [green]pyupcheck migrate {r['package']} {r['target_version']} --write[/] "
                              f"fixes {r['fixable']} of these")
    if ev.kind == "release" and ev.results and not breaking and not ev.results[0].get("unchecked"):
        console.print("  [green]safe to upgrade:[/] nothing in your code breaks")
    return breaking


@main.command()
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
    cfg = load_config(directory)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()
    ignored = {normalize_name(p) for p in cfg.ignore_packages}

    def check(change):
        if change.name in ignored:
            return None
        return _check_change(change, directory, cfg, github_token, deep)

    def latest(name, raw):
        spec = specifier_of(raw)
        return latest_allowed_version(name, None if _is_exact_pin(spec) else spec, fresh=True)

    def direct():
        return [d.name for d in discover_dependencies(directory) if d.name not in ignored]

    watcher = Watcher(directory, check, latest=latest, direct=direct,
                      releases_every=releases_every * 60, jobs=jobs)
    with console.status("Reading dependencies" + (" and their latest releases" if releases_every else "") + "..."):
        events = watcher.start()
    for ev in events:
        _print_watch_event(ev, directory, quiet)
    files = sorted(dep_file_signature(directory))
    if not files:
        console.print(f"[yellow]No dependency files in {directory}[/] (requirements*.txt, pyproject.toml, "
                      "lockfiles...). Watching for one to appear.")
    else:
        rel = "" if not releases_every else \
            f", and PyPI every {releases_every:g} min for {len(watcher.known_latest)} direct dependencies"
        console.print(f"Watching [bold]{', '.join(files)}[/] ({len(watcher.snapshot)} packages){rel}. "
                      "[dim]Ctrl+C to stop.[/]")
    n = 0
    try:
        while cycles is None or n < cycles:
            n += 1
            _time.sleep(interval)
            for ev in watcher.poll():
                if _print_watch_event(ev, directory, quiet) and bell:
                    console.bell()
    except KeyboardInterrupt:
        console.print("[dim]Stopped.[/]")


# ── migrate ───────────────────────────────────────────────────────────────────

@main.command()
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
    import ast as _ast
    from depshift.apisurface import extract_surface
    from depshift import notebook
    from depshift.rewrite import apply_edits, plan_migration, unified_diff

    directory = os.path.abspath(directory)
    cfg = load_config(directory)
    cfg.exclude_dirs.update(exclude)
    if no_cache or not cfg.cache:
        cache_mod.disable_cache()

    dep = next((d for d in discover_dependencies(directory) if d.name == normalize_name(package)), None)
    spec = specifier_of(dep.raw) if dep else None
    current, source = _baseline_version(package, directory, dep.pinned_version if dep else None, spec)
    if not current:
        console.print(f"[red]{package}:[/] current version unknown. Install it, pin it with ==, "
                      "or lock it, so there is a version to migrate from.")
        sys.exit(2)
    if not target_version:
        try:
            target_version = latest_allowed_version(package, None if _is_exact_pin(spec) else spec)
        except Exception as e:
            console.print(f"[red]{package}:[/] not found on PyPI ({e})")
            sys.exit(2)
    if not target_version or _not_newer(target_version, current):
        console.print(f"[green]{package}[/] {current} is already the latest allowed version.")
        return

    names = import_names_for(package)
    with console.status(f"Comparing [bold]{package}[/] {current} -> {target_version}..."):
        usages = scan_directory(directory, names, exclude_dirs=cfg.exclude_dirs)
        changes = get_api_changes(package, current, target_version)
        new_surface = extract_surface(package, target_version)
        try:
            entries = collect_changes(package, current, target_version, github_token=github_token,
                                      lookback=True).history
        except Exception:
            entries = []
        try:
            returns = get_return_types(package, current, target_version)
        except Exception:
            returns = {}

    if changes is None or new_surface is None:
        console.print(f"[red]{package}:[/] could not read the API of {current} or {target_version} "
                      "(no Python sources published), so nothing can be rewritten safely.")
        sys.exit(2)

    plan = plan_migration(usages, changes, new_surface, names, entries, returns)
    console.print(f"[bold]{package}[/] {current} [dim]({source})[/] -> {target_version}\n")

    def where(item) -> str:
        rel = os.path.relpath(item.file, directory)
        return f"{rel} cell {item.cell}:{item.line}" if item.cell is not None else f"{rel}:{item.line}"

    changed_files = 0
    for path, fixes in sorted(plan.by_file().items()):
        nb = notebook.load(path) if path.endswith(".ipynb") else None
        if nb is not None:
            before = nb.source
        else:
            with open(path, encoding="utf-8") as f:
                before = f.read()
        after = apply_edits(before, [e for fx in fixes for e in fx.edits])
        if after == before:
            continue
        try:
            _ast.parse(after)
        except SyntaxError:
            console.print(f"[red]skipped {os.path.relpath(path)}:[/] the rewrite would not parse")
            continue
        rel = os.path.relpath(path, directory)
        for fx in sorted(fixes, key=lambda x: (x.cell or 0, x.line)):
            console.print(f"  [green]fix[/] {where(fx)}  {_esc(fx.description)}")
        if nb is not None:
            print(unified_diff(rel, notebook.cells_text(nb, before), notebook.cells_text(nb, after)))
        else:
            print(unified_diff(rel, before, after))
        changed_files += 1
        if write:
            with open(path, "w", encoding="utf-8") as f:
                f.write(notebook.dumps(notebook.restore(nb, after)) if nb is not None else after)

    if plan.manual:
        console.print(f"[yellow]{len(plan.manual)} change(s) need you:[/]")
        for m in sorted(plan.manual, key=lambda m: (m.file, m.cell or 0, m.line)):
            code = re.sub(r"^\[cell \d+\] ", "", m.code) if m.cell else m.code  # the cell is in where()
            console.print(f"  [yellow]![/] {where(m)}  {_esc(code)}")
            console.print(f"    [dim]{_esc(m.reason)}[/]")

    if not plan.fixes and not plan.manual:
        console.print("[green]Nothing to change:[/] no breaking API changes hit your code.")
    elif changed_files:
        verb = "Rewrote" if write else "Would rewrite"
        console.print(f"\n{verb} {changed_files} file(s)." + ("" if write else " Run again with --write to apply."))
    sys.exit(1 if plan.manual else 0)


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


# ── outdated / highest safe version ───────────────────────────────────────────

def _candidate_versions(package: str, current: str, spec: Optional[str]) -> List[str]:
    """The newest release of each minor series above ``current`` that the
    project's constraint allows (no pre-releases, no yanked), oldest first."""
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
    from packaging.version import Version, InvalidVersion
    info = get_pypi_info(package)
    try:
        cur = Version(current)
    except InvalidVersion:
        return []
    try:
        allowed = SpecifierSet("" if _is_exact_pin(spec) else (spec or ""))
    except InvalidSpecifier:
        allowed = SpecifierSet("")
    newest: dict = {}
    for ver, files in (info.get("releases") or {}).items():
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        if v <= cur or v.is_prerelease or v.is_devrelease:
            continue
        if files and all(f.get("yanked") for f in files):
            continue
        if allowed and v not in allowed:
            continue
        key = (v.major, v.minor)
        if key not in newest or v > newest[key]:
            newest[key] = v
    return [str(v) for v in sorted(newest.values())]


def highest_safe_version(package: str, current: str, directory: str, cfg,
                         github_token: Optional[str] = None, spec: Optional[str] = None,
                         deep: bool = True) -> dict:
    """The newest version that breaks none of your code.

    Breaking changes only pile up as versions go up, so the candidates (the
    newest release of each minor series) are binary-searched: the latest is
    checked first, then about log2(n) more. A version that couldn't be
    checked never counts as safe.
    """
    candidates = _candidate_versions(package, current, spec)
    out = {"package": package, "current": current, "latest": candidates[-1] if candidates else current,
           "safe": current, "breaking_at_latest": 0, "checked": [], "unchecked": False}
    if not candidates:
        return out

    cache: dict = {}

    def breaks(version: str) -> Optional[int]:
        """Breaking usages at ``version``; None if it couldn't be checked."""
        if version not in cache:
            r = _run_single_check(package, version, directory, github_token, cfg, suppress_errors=True,
                                  deep=deep, spec=spec, locked=current)
            cache[version] = None if (r is None or r.get("unchecked")) else r["breaking_count"]
            out["checked"].append((version, cache[version]))
        return cache[version]

    latest = breaks(candidates[-1])
    out["breaking_at_latest"] = latest if latest is not None else 0
    if latest == 0:
        out["safe"] = candidates[-1]
        return out
    if latest is None:
        out["unchecked"] = True
    lo, hi = 0, len(candidates) - 2  # the last one is known not to be safe
    while lo <= hi:
        mid = (lo + hi) // 2
        n = breaks(candidates[mid])
        if n == 0:
            out["safe"] = candidates[mid]
            lo = mid + 1
        else:
            if n is None:
                out["unchecked"] = True
            hi = mid - 1
    return out


@main.command()
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
    cfg = load_config(directory)
    deps = discover_dependencies(directory)
    if not deps:
        console.print("[yellow]No dependency files found[/]")
        sys.exit(2)

    from packaging.version import Version, InvalidVersion
    locks = locked_versions(directory)

    def one(dep):
        lock = locks.get(dep.name)
        spec = specifier_of(dep.raw)
        current, _ = _baseline_version(dep.name, directory, dep.pinned_version, spec,
                                       lock.version if lock else None)
        try:
            latest = latest_allowed_version(dep.name, None if _is_exact_pin(spec) else spec) \
                or get_current_version(dep.name)
        except Exception:
            return None
        row = {"name": dep.name, "current": current, "latest": latest}
        if not current:
            return row
        try:
            row["outdated"] = Version(current) < Version(latest)
            row["major"] = Version(current).major < Version(latest).major
        except InvalidVersion:
            row["outdated"] = current != latest
            row["major"] = False
        if safe and row["outdated"]:
            row["plan"] = highest_safe_version(dep.name, current, directory, cfg, github_token, spec)
        return row

    with console.status("Checking versions..." + (" (finding safe versions)" if safe else "")):
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            rows = [r for r in pool.map(one, deps) if r]

    table = Table(title="Outdated dependencies")
    table.add_column("Package", style="cyan")
    table.add_column("Locked" if locks else "Current", style="dim")
    table.add_column("Latest", style="green")
    if safe:
        table.add_column("Highest safe", style="bold")
    table.add_column("Status")
    outdated_count = 0
    for r in rows:
        if not r.get("current"):
            cells = [r["name"], "-", r["latest"]] + (["-"] if safe else []) + ["[dim]version unknown[/]"]
            table.add_row(*cells)
            continue
        if not r.get("outdated"):
            continue
        outdated_count += 1
        status = "[red]major bump[/]" if r["major"] else "[yellow]update available[/]"
        cells = [r["name"], r["current"], r["latest"]]
        if safe:
            plan = r.get("plan") or {}
            if plan.get("safe") == r["latest"]:
                cells.append(f"[green]{plan['safe']}[/]")
                status = "[green]safe to upgrade[/]"
            elif plan.get("safe") and plan["safe"] != r["current"]:
                cells.append(f"[yellow]{plan['safe']}[/]")
                status = f"[red]{r['latest']} breaks {plan['breaking_at_latest']} usage(s)[/]"
            else:
                cells.append("[red]none[/]" if not plan.get("unchecked") else "[magenta]not checked[/]")
                status = f"[red]{r['latest']} breaks {plan.get('breaking_at_latest', '?')} usage(s)[/]"
        cells.append(status)
        table.add_row(*cells)

    if outdated_count:
        console.print(table)
        hint = "" if safe else " Run with --safe to find the highest version that doesn't break your code."
        console.print(f"\n[bold]{outdated_count}[/] outdated.{hint}")
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

    # validate versions are real and parseable before doing any work
    from packaging.version import Version, InvalidVersion
    for label, v in (("from", from_version), ("to", to_version)):
        try:
            Version(v)
        except InvalidVersion:
            console.print(f"[red]Error:[/] '{v}' is not a valid version string ({label}_version).")
            sys.exit(2)

    try:
        available = set(get_available_versions(package))
    except Exception as e:
        console.print(f"[red]Error:[/] could not fetch {package} from PyPI: {e}")
        sys.exit(2)

    for label, v in (("from", from_version), ("to", to_version)):
        if v not in available:
            console.print(f"[red]Error:[/] {package} {v} not found on PyPI ({label}_version). "
                          f"Run [bold]pyupcheck versions {package}[/] to see available versions.")
            sys.exit(2)

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

    names = import_names_for(package)
    with console.status(f"Scanning for usages of [bold]{package}[/]..."):
        usages = scan_directory(directory, names, exclude_dirs=cfg.exclude_dirs)

    if not usages:
        console.print(f"[yellow]No usages of {package} found[/] (looked for imports of: {', '.join(names)})")
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
