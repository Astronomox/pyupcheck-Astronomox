"""Review the dependency changes in a pull request.

``pyupcheck review --base origin/main`` compares the dependency files (and
lockfiles) at the base commit with the working tree, and checks every
package whose version changed from its old version to its new one. The
report is Markdown meant for a pull-request comment.
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional

from depshift.deps import discover_dependencies
from depshift.lockfiles import locked_versions

# a hidden marker so the GitHub Action can find and update its own comment
COMMENT_MARKER = "<!-- pyupcheck-review -->"

# dependency files pyupcheck reads, relative to the project root
_ROOT_FILES = {
    "pyproject.toml", "Pipfile", "Pipfile.lock", "setup.cfg", "setup.py",
    "environment.yml", "environment.yaml", "uv.lock", "poetry.lock", "pdm.lock",
}
_SUBDIRS = {"requirements", "reqs"}


@dataclass
class VersionChange:
    name: str
    old: str
    new: str
    raw: str  # requirement text at head, for its specifier


def _is_dep_file(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if len(parts) == 1:
        return (name in _ROOT_FILES
                or (name.startswith("requirements") and name.endswith((".txt", ".in")))
                or (name.startswith("pylock") and name.endswith(".toml")))
    return len(parts) == 2 and parts[0] in _SUBDIRS and name.endswith((".txt", ".in"))


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                          text=True).stdout


def export_dependency_files(repo: str, ref: str, dest: str) -> List[str]:
    """Write the dependency files as they are at ``ref`` into ``dest``."""
    listed = _git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()
    written = []
    for path in listed:
        if not _is_dep_file(path):
            continue
        content = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"], check=True,
                                 capture_output=True).stdout
        target = os.path.join(dest, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
        written.append(path)
    return written


def _lower_bound(raw: str) -> Optional[str]:
    from packaging.requirements import Requirement, InvalidRequirement
    try:
        spec = Requirement(raw).specifier
    except InvalidRequirement:
        return None
    for sp in spec:
        if sp.operator in (">=", "~=", "==", "===") and "*" not in sp.version:
            return sp.version
    return None


def resolved_versions(directory: str) -> Dict[str, tuple]:
    """{name: (version, requirement text)}: lockfile version, else exact pin,
    else the lowest version the constraint allows."""
    locks = locked_versions(directory)
    out: Dict[str, tuple] = {}
    for dep in discover_dependencies(directory):
        lock = locks.get(dep.name)
        version = lock.version if lock else (dep.pinned_version or _lower_bound(dep.raw))
        if version:
            out[dep.name] = (version, dep.raw)
    for name, lock in locks.items():  # locked-only (transitive) packages
        out.setdefault(name, (lock.version, name))
    return out


def changed_dependencies(repo: str, base_ref: str) -> List[VersionChange]:
    """Packages whose resolved version differs between ``base_ref`` and the
    working tree, upgrades and downgrades alike. Added and removed packages
    have nothing to compare and are skipped."""
    with tempfile.TemporaryDirectory() as base_dir:
        export_dependency_files(repo, base_ref, base_dir)
        before = resolved_versions(base_dir)
    after = resolved_versions(repo)
    changes = []
    for name in sorted(set(before) & set(after)):
        old, _ = before[name]
        new, raw = after[name]
        if old != new:
            changes.append(VersionChange(name, old, new, raw))
    return changes


# ── report ────────────────────────────────────────────────────────────────────

_ROLE_REF = re.compile(r"\s*:(?:pr|issue|ghissue|bpo|user|cve):`[^`]*`")
_ROLE = re.compile(r":[\w-]+:`~?\.?([^`]+)`")


def _clean(text: str) -> str:
    """Release-note text for a comment: drop :pr:/:issue: references, turn
    reST roles and ``literals`` into Markdown code."""
    text = _ROLE_REF.sub("", text)
    text = _ROLE.sub(lambda m: f"`{m.group(1)}`", text)
    text = text.replace("``", "`").replace("|", "\\|").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()

def render_review(results: List[dict], max_findings: int = 10, repo_root: str = ".") -> str:
    """Markdown for a pull-request comment. Each result is a check result
    dict with an optional ``fixable`` count from ``migrate``."""
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
        if r.get("unchecked") and not r["risks"]:
            status = "not checked"
        else:
            status = str(r["breaking_count"])
        fixable = r.get("fixable")
        lines.append(f"| `{r['package']}` | {r.get('current_version') or '?'} → {r['target_version']} "
                     f"| {status} | {r['deprecated_count']} | {'' if fixable is None else fixable} |")
    lines.append("")

    for r in results:
        risks = [x for x in r["risks"] if x["severity"] in ("breaking", "deprecated")]
        notes = r.get("warnings", [])
        if not risks and not notes:
            continue
        lines.append(f"<details><summary><b>{r['package']}</b> {r.get('current_version') or '?'} → "
                     f"{r['target_version']}</summary>")
        lines.append("")
        for x in risks[:max_findings]:
            try:
                rel = os.path.relpath(x["file"], repo_root)
            except ValueError:
                rel = x["file"]
            tag = " (verified)" if x.get("precise") else ""
            desc = _clean(x["change_description"])[:160]
            lines.append(f"- **{x['severity']}**{tag} `{rel}:{x['line']}` `{x['code'][:80]}`: {desc}")
            if x.get("replacement"):
                lines.append(f"  use `{x['replacement']}` instead")
        if len(risks) > max_findings:
            lines.append(f"- ...and {len(risks) - max_findings} more")
        for n in notes:
            lines.append(f"- _note: {n}_")
        if r.get("fixable"):
            lines.append("")
            lines.append(f"`pyupcheck migrate {r['package']} {r['target_version']} --write` "
                         f"fixes {r['fixable']} of these automatically.")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    return "\n".join(lines)
