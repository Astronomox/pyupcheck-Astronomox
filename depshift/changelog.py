"""Fetch changelog and deprecation data for a package version."""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx


@dataclass
class ChangeEntry:
    """A single breaking or deprecated API change."""
    kind: str  # "removed", "deprecated", "changed", "renamed"
    api: str  # the affected API path, e.g. "requests.get.verify"
    description: str
    version: str  # version where this change happened


PYPI_API = "https://pypi.org/pypi/{package}/json"
PYPI_VERSION_API = "https://pypi.org/pypi/{package}/{version}/json"
GITHUB_RELEASES_API = "https://api.github.com/repos/{owner}/{repo}/releases"
GITHUB_TAGS_API = "https://api.github.com/repos/{owner}/{repo}/tags"


def get_pypi_info(package: str) -> dict:
    """Fetch full package info from PyPI."""
    url = PYPI_API.format(package=package)
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def get_pypi_version_info(package: str, version: str) -> dict:
    """Fetch info for a specific version."""
    url = PYPI_VERSION_API.format(package=package, version=version)
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def get_available_versions(package: str) -> List[str]:
    """Get all available versions for a package."""
    info = get_pypi_info(package)
    return list(info.get("releases", {}).keys())


def get_current_version(package: str) -> str:
    """Get the latest version of a package on PyPI."""
    info = get_pypi_info(package)
    return info["info"]["version"]


def extract_github_repo(pypi_info: dict) -> Optional[Tuple[str, str]]:
    """Try to find the GitHub owner/repo from PyPI metadata."""
    info = pypi_info.get("info", {})
    urls = []

    project_urls = info.get("project_urls") or {}
    for key, val in project_urls.items():
        if val:
            urls.append(val)

    home = info.get("home_page")
    if home:
        urls.append(home)

    pattern = re.compile(r"github\.com/([^/]+)/([^/\s#?]+)")
    for url in urls:
        m = pattern.search(url)
        if m:
            owner, repo = m.group(1), m.group(2)
            repo = repo.rstrip(".git")
            return owner, repo
    return None


def fetch_github_releases(owner: str, repo: str, token: Optional[str] = None) -> List[dict]:
    """Fetch releases from GitHub."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    releases = []
    page = 1
    while page <= 5:  # cap at 5 pages
        url = f"{GITHUB_RELEASES_API.format(owner=owner, repo=repo)}?per_page=50&page={page}"
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            break
        batch = resp.json()
        if not batch:
            break
        releases.extend(batch)
        page += 1
    return releases


def parse_changelog_text(text: str, version: str) -> List[ChangeEntry]:
    """Parse a changelog/release notes body for breaking/deprecated changes."""
    entries: List[ChangeEntry] = []
    if not text:
        return entries

    lines = text.splitlines()

    # patterns that signal breaking/deprecation info
    breaking_patterns = [
        re.compile(r"remov(ed?|ing|al)\b", re.IGNORECASE),
        re.compile(r"delet(ed?|ing)\b", re.IGNORECASE),
        re.compile(r"drop(ped|ping)?\b", re.IGNORECASE),
        re.compile(r"break(ing|s)?\b", re.IGNORECASE),
        re.compile(r"backwards?\s*incompatible", re.IGNORECASE),
        re.compile(r"no\s*longer\s*(support|available|accept)", re.IGNORECASE),
    ]
    deprecation_patterns = [
        re.compile(r"deprecat(ed?|ing|ion)\b", re.IGNORECASE),
        re.compile(r"will\s*be\s*removed", re.IGNORECASE),
        re.compile(r"use\s+\S+\s+instead", re.IGNORECASE),
    ]
    rename_patterns = [
        re.compile(r"renam(ed?|ing)\b", re.IGNORECASE),
        re.compile(r"moved?\s*(to|from)\b", re.IGNORECASE),
    ]

    # try to extract python identifiers from a line
    api_pattern = re.compile(r"`([a-zA-Z_][\w.]*(?:\(\))?)`")
    fallback_api_pattern = re.compile(r"(?:^|\s)([a-zA-Z_][\w]*\.[a-zA-Z_][\w.]*(?:\(\))?)")

    for line in lines:
        line_stripped = line.strip().lstrip("-*• ")
        if not line_stripped:
            continue

        kind = None
        for p in breaking_patterns:
            if p.search(line_stripped):
                kind = "removed"
                break
        if not kind:
            for p in deprecation_patterns:
                if p.search(line_stripped):
                    kind = "deprecated"
                    break
        if not kind:
            for p in rename_patterns:
                if p.search(line_stripped):
                    kind = "renamed"
                    break

        if not kind:
            continue

        # extract API references
        apis = api_pattern.findall(line_stripped)
        if not apis:
            apis = fallback_api_pattern.findall(line_stripped)

        api_str = apis[0] if apis else "unknown"
        api_str = api_str.rstrip("()")

        entries.append(ChangeEntry(
            kind=kind,
            api=api_str,
            description=line_stripped[:200],
            version=version,
        ))

    return entries


CHANGELOG_FILENAMES = [
    "CHANGES.rst", "CHANGELOG.rst", "CHANGELOG.md", "CHANGES.md",
    "CHANGES", "CHANGELOG", "HISTORY.rst", "HISTORY.md",
    "RELEASE_NOTES.md", "NEWS.rst", "NEWS.md",
]


def fetch_raw_changelog(owner: str, repo: str, token: Optional[str] = None) -> Optional[str]:
    """Try to fetch the raw changelog file from GitHub."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for branch in ["main", "master"]:
        for fname in CHANGELOG_FILENAMES:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{fname}"
            try:
                resp = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
                if resp.status_code == 200 and len(resp.text) > 50:
                    return resp.text
            except Exception:
                continue
    return None


def parse_sectioned_changelog(text: str, package: str) -> List[ChangeEntry]:
    """Parse a full changelog file that's divided by version headers."""
    entries: List[ChangeEntry] = []

    # match version headers like "Version 3.0.0", "## 3.0.0", "3.0.0 (2024-01-01)"
    version_header = re.compile(
        r"^(?:#{1,3}\s+)?(?:version\s+)?v?(\d+\.\d+(?:\.\d+)?(?:[a-z]\d*)?)\b",
        re.IGNORECASE | re.MULTILINE,
    )

    splits = list(version_header.finditer(text))
    if not splits:
        return entries

    for i, m in enumerate(splits):
        ver = m.group(1)
        start = m.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        section = text[start:end]
        section_entries = parse_changelog_text(section, ver)
        entries.extend(section_entries)

    return entries


def get_changes_between(package: str, current_version: str, target_version: str,
                        github_token: Optional[str] = None) -> List[ChangeEntry]:
    """Get all breaking/deprecated changes between two versions."""
    pypi_info = get_pypi_info(package)

    repo_info = extract_github_repo(pypi_info)
    all_entries: List[ChangeEntry] = []

    if repo_info:
        owner, repo = repo_info

        # try github releases API first
        try:
            releases = fetch_github_releases(owner, repo, token=github_token)
            for rel in releases:
                tag = rel.get("tag_name", "")
                body = rel.get("body", "") or ""
                ver = tag.lstrip("vV")
                entries = parse_changelog_text(body, ver)
                all_entries.extend(entries)
        except Exception:
            pass

        # if releases gave us nothing, try raw changelog file
        if not all_entries:
            try:
                raw = fetch_raw_changelog(owner, repo, token=github_token)
                if raw:
                    all_entries = parse_sectioned_changelog(raw, package)
            except Exception:
                pass

    # also check the PyPI description for the target version
    try:
        ver_info = get_pypi_version_info(package, target_version)
        desc = ver_info.get("info", {}).get("description", "")
        if desc:
            entries = parse_sectioned_changelog(desc, package)
            all_entries.extend(entries)
    except Exception:
        pass

    # filter to only changes relevant between current and target
    from packaging.version import Version, InvalidVersion

    try:
        v_current = Version(current_version)
        v_target = Version(target_version)
    except InvalidVersion:
        return all_entries

    filtered = []
    seen = set()
    for entry in all_entries:
        try:
            v_entry = Version(entry.version)
            if v_current < v_entry <= v_target:
                key = (entry.kind, entry.api, entry.version)
                if key not in seen:
                    seen.add(key)
                    filtered.append(entry)
        except InvalidVersion:
            key = (entry.kind, entry.api, entry.version)
            if key not in seen:
                seen.add(key)
                filtered.append(entry)

    return filtered
