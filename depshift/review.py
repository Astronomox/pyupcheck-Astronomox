"""Review the dependency changes in a pull request.

``pyupcheck review --base origin/main`` compares the dependency files (and
lockfiles) at the base commit with the working tree, and checks every
package whose version changed from its old version to its new one. The
report is Markdown meant for a pull-request comment.
"""

import os
import re

# git only: run without a shell, with validated arguments
import subprocess  # nosec B404
import tempfile
from dataclasses import dataclass

from depshift.deps import discover_dependencies
from depshift.lockfiles import locked_versions

# a hidden marker so the GitHub Action can find and update its own comment
COMMENT_MARKER = "<!-- pyupcheck-review -->"

# dependency files pyupcheck reads, relative to the project root
_ROOT_FILES = {
    "pyproject.toml", "Pipfile", "Pipfile.lock", "setup.cfg", "setup.py",
    "environment.yml", "environment.yaml", "uv.lock", "poetry.lock", "pdm.lock",
    "conda-lock.yml", "conda-lock.yaml", "pixi.lock", "pixi.toml",
}
_SUBDIRS = {"requirements", "reqs"}


@dataclass
class VersionChange:
    name: str
    old: str
    new: str
    raw: str  # requirement text at head, for its specifier


def is_dep_file(path: str) -> bool:
    """Is this path (relative to the project root, '/'-separated) a file
    pyupcheck reads dependencies from?"""
    parts = path.split("/")
    name = parts[-1]
    if len(parts) == 1:
        return (name in _ROOT_FILES
                or (name.startswith("requirements") and name.endswith((".txt", ".in")))
                or (name.startswith("pylock") and name.endswith(".toml")))
    return len(parts) == 2 and parts[0] in _SUBDIRS and name.endswith((".txt", ".in"))


# a commit, branch, tag or expression like HEAD~1 / origin/main: never
# something git would read as an option ("--output=...")
_REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./@{}^~:+-]{0,250}$")


def valid_ref(ref: str) -> bool:
    return bool(_REF.match(ref or "")) and ".." not in ref and not ref.endswith((".", "/", ".lock"))


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,  # nosec B603 B607
                          text=True).stdout


def export_dependency_files(repo: str, ref: str, dest: str) -> list[str]:
    """Write the dependency files as they are at ``ref`` into ``dest``."""
    if not valid_ref(ref):
        raise ValueError(f"not a git revision pyupcheck accepts: {ref!r}")
    listed = _git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()
    written = []
    for path in listed:
        if not is_dep_file(path):
            continue
        content = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"], check=True,  # nosec B603 B607
                                 capture_output=True).stdout
        target = os.path.join(dest, path)
        if not os.path.realpath(target).startswith(os.path.realpath(dest) + os.sep):
            continue  # is_dep_file only allows plain names, but never write outside dest
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
        written.append(path)
    return written


def _lower_bound(raw: str) -> str | None:
    from packaging.requirements import InvalidRequirement, Requirement
    try:
        spec = Requirement(raw).specifier
    except InvalidRequirement:
        return None
    for sp in spec:
        if sp.operator in (">=", "~=", "==", "===") and "*" not in sp.version:
            return sp.version
    return None


def resolved_versions(directory: str) -> dict[str, tuple]:
    """{name: (version, requirement text)}: lockfile version, else exact pin,
    else the lowest version the constraint allows."""
    locks = locked_versions(directory)
    out: dict[str, tuple] = {}
    for dep in discover_dependencies(directory):
        lock = locks.get(dep.name)
        version = lock.version if lock else (dep.pinned_version or _lower_bound(dep.raw))
        if version:
            out[dep.name] = (version, dep.raw)
    for name, lock in locks.items():  # locked-only (transitive) packages
        out.setdefault(name, (lock.version, name))
    return out


def changed_dependencies(repo: str, base_ref: str) -> list[VersionChange]:
    """Packages whose resolved version differs between ``base_ref`` and the
    working tree, upgrades and downgrades alike. Added and removed packages
    have nothing to compare and are skipped."""
    with tempfile.TemporaryDirectory() as base_dir:
        export_dependency_files(repo, base_ref, base_dir)
        before = resolved_versions(base_dir)
    return version_changes(before, resolved_versions(repo))


def version_changes(before: dict[str, tuple], after: dict[str, tuple]) -> list[VersionChange]:
    """Packages in both :func:`resolved_versions` results whose version differs."""
    changes = []
    for name in sorted(set(before) & set(after)):
        old, _ = before[name]
        new, raw = after[name]
        if old != new:
            changes.append(VersionChange(name, old, new, raw))
    return changes


# ── report ────────────────────────────────────────────────────────────────────

# also a reference cut off by truncation (":pr:" with no "`1234`" after it)
_ROLE_REF = re.compile(r"\s*:(?:pr|issue|ghissue|bpo|user|cve):(?:`[^`]*`?)?")
_ROLE = re.compile(r":[\w-]+:`~?\.?([^`]+)`")


def _clean(text: str) -> str:
    """Release-note text for a comment: drop :pr:/:issue: references, turn
    reST roles and ``literals`` into Markdown code."""
    text = _ROLE_REF.sub("", text)
    text = _ROLE.sub(lambda m: f"`{m.group(1)}`", text)
    text = text.replace("``", "`").replace("|", "\\|").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()

def _note(text: str, limit: int = 160) -> str:
    """A release note for the comment: cleaned, cut at a word, then made inert
    (it comes from the internet: no mentions, HTML, images or links)."""
    from depshift.safe import md_text, plain
    s = _clean(plain(text, keep_newlines=False)[:2000]).replace("\\|", "|")
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return md_text(s)


def render_review(results: list[dict], max_findings: int = 10, repo_root: str = ".") -> str:
    """Markdown for a pull-request comment. Each result is a check result
    dict with an optional ``fixable`` count from ``migrate``."""
    import html as _html

    from depshift.safe import md_code, md_text
    lines = [COMMENT_MARKER, "## pyupcheck: dependency changes in this PR", ""]
    if not results:
        lines += ["No dependency versions changed. Nothing to check.", ""]
        return "\n".join(lines)

    breaking = sum(r["breaking_count"] for r in results)
    unchecked = [r for r in results if r.get("unchecked")]
    if breaking:
        lines.append(f"**{breaking} breaking** usage(s) across {len(results)} changed package(s).")
    elif unchecked:
        lines.append(f"No breaking usages found, but {len(unchecked)} package(s) could not be checked.")
    else:
        lines.append(f"No breaking usages found in {len(results)} changed package(s).")
    lines.append("")
    lines.append("| Package | Change | Breaking | Deprecated | Auto-fixable |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        status = "not checked" if r.get("unchecked") and not r["risks"] else str(r["breaking_count"])
        fixable = r.get("fixable")
        lines.append(f"| {md_code(r['package'], in_table=True)} | {md_text(r.get('current_version') or '?')} → "
                     f"{md_text(r['target_version'])} | {status} | {int(r['deprecated_count'])} "
                     f"| {'' if fixable is None else int(fixable)} |")
    lines.append("")

    for r in results:
        risks = [x for x in r["risks"] if x["severity"] in ("breaking", "deprecated")]
        notes = r.get("warnings", [])
        if not risks and not notes:
            continue
        lines.append(f"<details><summary><b>{_html.escape(r['package'])}</b> "
                     f"{_html.escape(r.get('current_version') or '?')} → "
                     f"{_html.escape(r['target_version'])}</summary>")
        lines.append("")
        for x in risks[:max_findings]:
            try:
                rel = os.path.relpath(x["file"], repo_root)
            except ValueError:
                rel = x["file"]
            tag = " (verified)" if x.get("precise") else ""
            sev = x["severity"] if x["severity"] in ("breaking", "deprecated") else "warning"
            where = md_code(f"{rel}:{int(x['line'])}")
            code = md_code(x["code"][:80])
            lines.append(f"- **{sev}**{tag} {where} {code}: {_note(x['change_description'])}")
            if x.get("replacement"):
                lines.append(f"  use {md_code(x['replacement'][:120])} instead")
        if len(risks) > max_findings:
            lines.append(f"- ...and {len(risks) - max_findings} more")
        for n in notes:
            lines.append(f"- _note: {_note(n, 300)}_")
        if r.get("fixable"):
            lines.append("")
            command = md_code(f"pyupcheck migrate {r['package']} {r['target_version']} --write")
            lines.append(f"{command} fixes {int(r['fixable'])} of these automatically.")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    return "\n".join(lines)
