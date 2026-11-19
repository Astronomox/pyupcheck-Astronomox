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
    confidence: str = "medium"  # "high" (explicit section / strong phrase), "medium", "low"
    replacement: Optional[str] = None  # suggested replacement API, when the text names one


PYPI_API = "https://pypi.org/pypi/{package}/json"
PYPI_VERSION_API = "https://pypi.org/pypi/{package}/{version}/json"
GITHUB_RELEASES_API = "https://api.github.com/repos/{owner}/{repo}/releases"
GITHUB_TAGS_API = "https://api.github.com/repos/{owner}/{repo}/tags"


from depshift.cache import cache_get, cache_set


def get_pypi_info(package: str) -> dict:
    """Fetch full package info from PyPI (cached)."""
    key = f"pypi:{package}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    url = PYPI_API.format(package=package)
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    cache_set(key, data)
    return data


def get_pypi_version_info(package: str, version: str) -> dict:
    """Fetch info for a specific version (cached)."""
    key = f"pypi:{package}:{version}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    url = PYPI_VERSION_API.format(package=package, version=version)
    resp = httpx.get(url, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    cache_set(key, data)
    return data


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
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            return owner, repo
    return None


def fetch_github_releases(owner: str, repo: str, token: Optional[str] = None) -> List[dict]:
    """Fetch releases from GitHub (cached)."""
    key = f"gh-releases:{owner}/{repo}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    releases = []
    page = 1
    while page <= 5:  # cap at 5 pages
        url = f"{GITHUB_RELEASES_API.format(owner=owner, repo=repo)}?per_page=50&page={page}"
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code in (401, 403) and "Authorization" in headers:
            # an expired/invalid token must not block a public repo: retry anonymously
            headers.pop("Authorization")
            resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            if page == 1 and resp.status_code in (401, 403, 429):
                # rate limited or bad token: say so rather than "no breaking changes"
                resp.raise_for_status()
            break
        batch = resp.json()
        if not batch:
            break
        releases.extend(batch)
        page += 1
    if releases:
        cache_set(key, releases)
    return releases


# headings that classify the bullet points beneath them
_BREAKING_HEADING = re.compile(
    r"\b(breaking|backwards?[- ]incompatible|incompatible|removals?|removed|"
    r"api changes|migration|upgrade notes?)\b", re.IGNORECASE)
_DEPRECATION_HEADING = re.compile(r"\bdeprecat", re.IGNORECASE)
_BENIGN_HEADING = re.compile(
    r"\b(bug ?fix(es)?|fixe[sd]|features?|added|new|enhancements?|improvements?|"
    r"documentation|docs|internal|misc(ellaneous)?|performance|testing|tests|"
    r"dependencies|build|ci|chores?|refactor(ing)?)\b", re.IGNORECASE)

_MD_HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")
_BOLD_HEADING = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__):?\s*$")
_RST_UNDERLINE = re.compile(r"^\s*([=\-~^\"'`#*+])\1{2,}\s*$")
_COLON_HEADING = re.compile(r"^\s*([A-Z][\w /&-]{2,40}):\s*$")

_BREAKING_PATTERNS = [
    (re.compile(r"\bremov(e|ed|es|ing|al)\b", re.IGNORECASE), "medium"),
    (re.compile(r"\bdelet(e|ed|es|ing)\b", re.IGNORECASE), "low"),
    (re.compile(r"\bdrop(ped|s|ping)?\s+(support|the|deprecated|python|compat)", re.IGNORECASE), "medium"),
    (re.compile(r"\bbreaking\s+change", re.IGNORECASE), "high"),
    (re.compile(r"\bbackwards?[- ]?incompatible", re.IGNORECASE), "high"),
    (re.compile(r"\bno\s+longer\s+(support|available|accept|exist|provide|export)", re.IGNORECASE), "high"),
    (re.compile(r"\b(now|is)\s+(required|mandatory|keyword[- ]only|positional[- ]only)\b", re.IGNORECASE), "medium"),
    (re.compile(r"\bbreaks?\b", re.IGNORECASE), "low"),
]
_DEPRECATION_PATTERNS = [
    (re.compile(r"\bdeprecat(e|ed|es|ing|ion)\b", re.IGNORECASE), "medium"),
    (re.compile(r"\bwill\s+be\s+removed\b", re.IGNORECASE), "high"),
    (re.compile(r"\buse\s+\S+\s+instead\b", re.IGNORECASE), "low"),
]
_RENAME_PATTERNS = [
    (re.compile(r"\brenam(e|ed|es|ing)\b", re.IGNORECASE), "medium"),
    (re.compile(r"\bmoved?\s+(to|from|into)\b", re.IGNORECASE), "low"),
]
# lines that describe a fix, not an API change ("Fixed crash when removing items")
_FIX_LINE = re.compile(r"^(fix(e[sd])?|fixing|correct(ed|s)?|resolve[sd]?|prevent(ed|s)?)\b", re.IGNORECASE)

_API_BACKTICK = re.compile(r"`{1,2}:?(?:\w+:)*`?~?\.?([a-zA-Z_][\w.]*(?:\(\))?)`{1,2}")
_API_DOTTED = re.compile(r"(?:^|[\s(])([a-zA-Z_]\w*\.[a-zA-Z_][\w.]*(?:\(\))?)")
_REPLACEMENT = re.compile(
    r"(?:use|in favou?r of|replaced (?:by|with)|superseded by|renamed to|moved to|instead,? use|"
    r"(?:renam|mov)\w*\s+`{0,2}[\w.:~]+`{0,2}\s+(?:to|into))\s+"
    r"`{0,2}:?(?:\w+:)*`?~?\.?([a-zA-Z_][\w.]*)", re.IGNORECASE)

_RANK = {"low": 0, "medium": 1, "high": 2}
_PARAM_OWNER = re.compile(
    r"`{1,2}:?(?:\w+:)*`?~?\.?([a-zA-Z_][\w.]*(?:\(\))?)`{1,2}(?:'s)?\s+"
    r"(?:option|param|parameter|argument|arg|kwarg|keyword argument|keyword)s?\b", re.IGNORECASE)


def _heading_text(lines: List[str], i: int) -> Optional[str]:
    """If lines[i] is a heading, return its text."""
    line = lines[i]
    m = _MD_HEADING.match(line) or _BOLD_HEADING.match(line) or _COLON_HEADING.match(line)
    if m:
        return m.group(1)
    if i + 1 < len(lines) and line.strip() and _RST_UNDERLINE.match(lines[i + 1]) \
            and not line.lstrip().startswith(("-", "*", "+")):
        return line.strip()
    return None


def _section_kind(heading: str) -> Optional[str]:
    if _BREAKING_HEADING.search(heading):
        return "removed"
    if _DEPRECATION_HEADING.search(heading):
        return "deprecated"
    if _BENIGN_HEADING.search(heading):
        return "benign"
    return None


def _extract_apis(line: str) -> List[str]:
    apis = [a.rstrip("()").strip(".") for a in _API_BACKTICK.findall(line)]
    if not apis:
        apis = [a.rstrip("()").strip(".") for a in _API_DOTTED.findall(line)]
    # skip things that are clearly not APIs: versions, file names, urls
    return [a for a in apis if a and not re.match(r"^v?\d", a)
            and not re.search(r"\.(py|txt|md|rst|cfg|toml|json|yml|yaml|html)$", a)]


_BULLET = re.compile(r"^(\s*)(?:[-*\u2022+]|\d+[.)])\s+")


def _items(text: str) -> List[Tuple[Optional[str], str]]:
    """Split release notes into (section kind, item text) pairs.

    A bullet plus its wrapped continuation lines is one item; so is a plain
    paragraph. Headings set the section for the items below them.
    """
    lines = text.splitlines()
    out: List[Tuple[Optional[str], str]] = []
    section: Optional[str] = None
    cur: List[str] = []
    cur_section: Optional[str] = None

    def flush():
        if cur:
            out.append((cur_section, " ".join(cur)))
            cur.clear()

    skip_next = False
    for i, raw in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        heading = _heading_text(lines, i)
        if heading is not None:
            flush()
            section = _section_kind(heading)
            if i + 1 < len(lines) and _RST_UNDERLINE.match(lines[i + 1]):
                skip_next = True
            continue
        if _RST_UNDERLINE.match(raw) or not raw.strip():
            flush()
            continue
        if _BULLET.match(raw):
            flush()
            cur_section = section
            cur.append(_BULLET.sub("", raw, count=1).strip())
        else:
            if not cur:
                cur_section = section
            cur.append(raw.strip())
    flush()
    return out


def _classify_item(line: str, section: Optional[str], version: str) -> List[ChangeEntry]:
    """Turn one release-note item into change entries (one per API it names)."""
    kind, conf = None, "low"
    for patterns, k in ((_BREAKING_PATTERNS, "removed"),
                        (_DEPRECATION_PATTERNS, "deprecated"),
                        (_RENAME_PATTERNS, "renamed")):
        for p, c in patterns:
            if p.search(line):
                kind, conf = k, c
                break
        if kind:
            break

    apis = _extract_apis(line)

    if section == "removed":
        # everything under a "Breaking changes" heading breaks, except explicit
        # deprecations; a rename there has no compatibility alias
        if kind == "deprecated" and not re.search(r"\bremov", line, re.IGNORECASE):
            pass
        elif kind == "renamed":
            kind = "changed"
        else:
            kind = "removed"
        conf = "high"
    elif section == "deprecated":
        kind = "removed" if kind == "removed" and re.search(r"\bremoved\b", line, re.IGNORECASE) \
            and not re.search(r"will be removed", line, re.IGNORECASE) else "deprecated"
        conf = "high"
    elif kind is None:
        return []
    else:
        if section == "benign" or _FIX_LINE.match(line):
            # keyword hits under "Bug fixes"/"Features" are mostly noise
            # ("Fixed crash when removing an item"); keep only strong wording
            if _RANK[conf] < 2:
                return []
            conf = "medium"
        if not apis:
            conf = "low"

    repl = _REPLACEMENT.search(line)
    replacement = repl.group(1).rstrip(".") if repl else None
    targets = [a for a in apis if a != replacement] or (apis[:1] if apis else ["unknown"])
    owner = _PARAM_OWNER.search(line)
    if owner:
        # "Removed ``Retry`` options ``method_whitelist``": the parameters are
        # what changed, not ``Retry`` itself
        o = owner.group(1).rstrip("()")
        params = [a for a in targets if a != o and "." not in a]
        if params:
            targets = [f"{o}.{a}" for a in params]
    dotted = [a for a in targets if "." in a]
    if dotted:
        # "`pkg.connect` takes a `Config` object": the dotted names are the subject
        targets = dotted
    seen = set()
    entries = []
    for api in targets[:8]:
        if api in seen:
            continue
        seen.add(api)
        entries.append(ChangeEntry(
            kind=kind, api=api, description=line[:200], version=version,
            confidence=conf, replacement=replacement if replacement != api else None,
        ))
    return entries


def parse_changelog_text(text: str, version: str) -> List[ChangeEntry]:
    """Parse a changelog/release notes body for breaking/deprecated changes.

    Understands section headings (Markdown, reST, bold, ``Heading:``): bullets
    under "Breaking changes" / "Removed" are breaking even without keywords,
    bullets under "Deprecations" are deprecations, and keyword matches under
    benign sections (bug fixes, features, docs) are discarded unless the
    wording is unambiguous.
    """
    entries: List[ChangeEntry] = []
    if not text:
        return entries
    for section, item in _items(text):
        entries.extend(_classify_item(item, section, version))
    return entries


CHANGELOG_FILENAMES = [
    "CHANGES.rst", "CHANGELOG.rst", "CHANGELOG.md", "CHANGES.md",
    "CHANGES", "CHANGELOG", "HISTORY.rst", "HISTORY.md",
    "RELEASE_NOTES.md", "NEWS.rst", "NEWS.md",
]


def fetch_raw_changelog(owner: str, repo: str, token: Optional[str] = None) -> Optional[str]:
    """Try to fetch the raw changelog file from GitHub (cached)."""
    key = f"gh-changelog:{owner}/{repo}"
    cached = cache_get(key)
    if cached is not None:
        return cached or None

    # Public files need no token, and a bad token turns them into 404s, so go
    # anonymous first and only use the token as a fallback (private repos).
    attempts = [{}]
    if token:
        attempts.append({"Authorization": f"Bearer {token}"})

    errors = 0
    for headers in attempts:
        for branch in ["main", "master"]:
            for fname in CHANGELOG_FILENAMES:
                url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{fname}"
                try:
                    resp = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
                    if resp.status_code == 200 and len(resp.text) > 50:
                        cache_set(key, resp.text)
                        return resp.text
                    if resp.status_code not in (404, 400):
                        errors += 1
                except Exception:
                    errors += 1
    if errors:
        # don't cache: a transient failure must not hide the changelog for 24h
        raise RuntimeError(f"{errors} changelog fetches failed")
    cache_set(key, "")
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


_TAG_VERSION = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+)+(?:[._-]?(?:a|b|rc|alpha|beta|post|dev)\.?\d*)?)(?![\w])",
                          re.IGNORECASE)


def version_from_tag(tag: str, package: str = "") -> Optional[str]:
    """Pull a PEP 440 version out of a release tag.

    Handles ``v2.0.1``, ``2.0``, ``flask-3.0.1``, ``release-2.0``,
    ``pkg==1.2`` and ``Version 4.0``. Returns None for tags with no version.
    """
    from packaging.version import Version, InvalidVersion
    tag = (tag or "").strip()
    if not tag:
        return None
    for m in _TAG_VERSION.finditer(tag):
        try:
            return str(Version(m.group(1)))
        except InvalidVersion:
            continue
    return None


@dataclass
class ChangeReport:
    """Changelog changes plus an honest account of where they came from."""
    entries: List[ChangeEntry]
    sources_ok: List[str]
    sources_failed: List[str]
    skipped_unversioned: int = 0

    @property
    def checked(self) -> bool:
        """True if at least one source was actually read."""
        return bool(self.sources_ok)


def collect_changes(package: str, current_version: str, target_version: str,
                    github_token: Optional[str] = None) -> ChangeReport:
    """Get breaking/deprecated changes between two versions, reporting which
    sources worked and which failed instead of silently returning nothing."""
    ok: List[str] = []
    failed: List[str] = []
    all_entries: List[ChangeEntry] = []

    try:
        pypi_info = get_pypi_info(package)
    except Exception as e:
        return ChangeReport([], [], [f"PyPI metadata ({_short_err(e)})"])

    repo_info = extract_github_repo(pypi_info)
    if repo_info:
        owner, repo = repo_info
        try:
            releases = fetch_github_releases(owner, repo, token=github_token)
            if releases:
                ok.append("GitHub releases")
            for rel in releases:
                tag = rel.get("tag_name", "") or rel.get("name", "")
                ver = version_from_tag(tag, package) or version_from_tag(rel.get("name", ""), package)
                body = rel.get("body", "") or ""
                if ver is None:
                    # sectioned bodies still carry their own version headers
                    all_entries.extend(parse_sectioned_changelog(body, package))
                    continue
                all_entries.extend(parse_changelog_text(body, ver))
        except Exception as e:
            failed.append(f"GitHub releases ({_short_err(e)})")

        if not all_entries:
            try:
                raw = fetch_raw_changelog(owner, repo, token=github_token)
                if raw:
                    ok.append("changelog file")
                    all_entries = parse_sectioned_changelog(raw, package)
            except Exception as e:
                failed.append(f"changelog file ({_short_err(e)})")

    try:
        ver_info = get_pypi_version_info(package, target_version)
        desc = ver_info.get("info", {}).get("description", "")
        ok.append("PyPI description")
        if desc:
            all_entries.extend(parse_sectioned_changelog(desc, package))
    except Exception as e:
        failed.append(f"PyPI description ({_short_err(e)})")

    from packaging.version import Version, InvalidVersion

    try:
        v_current = Version(current_version)
        v_target = Version(target_version)
    except InvalidVersion:
        failed.append(f"version range ({current_version} -> {target_version} not PEP 440)")
        return ChangeReport([], ok, failed)

    all_entries = _promote_swept_deprecations(all_entries, v_current, v_target)

    filtered: List[ChangeEntry] = []
    seen = set()
    skipped = 0
    for entry in all_entries:
        try:
            v_entry = Version(entry.version)
        except InvalidVersion:
            # unknown version: we can't tell if it's in range, so it's noise
            skipped += 1
            continue
        if v_current < v_entry <= v_target:
            key = (entry.kind, entry.api, entry.description)
            if key not in seen:
                seen.add(key)
                filtered.append(entry)

    return ChangeReport(filtered, ok, failed, skipped)


_SWEEP = re.compile(r"\bremov\w*\b.*\b(previously|all|the)\s+deprecated\b|"
                    r"\bdeprecated\b.*\b(?:have been|are|were)\s+removed\b", re.IGNORECASE)


def _promote_swept_deprecations(entries: List[ChangeEntry], v_current, v_target) -> List[ChangeEntry]:
    """Many projects remove deprecated APIs with a single line ("Remove
    previously deprecated code.") instead of listing them. When such a sweep
    falls inside the upgrade range, every API deprecated before it is gone."""
    from packaging.version import Version, InvalidVersion

    sweeps = []
    for e in entries:
        if e.kind == "removed" and e.api == "unknown" and _SWEEP.search(e.description):
            try:
                v = Version(e.version)
            except InvalidVersion:
                continue
            if v_current < v <= v_target:
                sweeps.append(v)
    if not sweeps:
        return entries

    out = list(entries)
    for e in entries:
        if e.kind != "deprecated" or e.api == "unknown":
            continue
        try:
            v_dep = Version(e.version)
        except InvalidVersion:
            continue
        later = [v for v in sweeps if v > v_dep]
        if later:
            sweep_v = min(later)
            out.append(ChangeEntry(
                kind="removed", api=e.api, version=str(sweep_v), confidence="medium",
                description=f"Removed in {sweep_v} (deprecated in {e.version}: {e.description[:120]})",
                replacement=e.replacement,
            ))
    return out


def _short_err(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, httpx.HTTPError):
        return "network error"
    return type(e).__name__


def get_changes_between(package: str, current_version: str, target_version: str,
                        github_token: Optional[str] = None) -> List[ChangeEntry]:
    """Get all breaking/deprecated changes between two versions."""
    return collect_changes(package, current_version, target_version, github_token).entries
