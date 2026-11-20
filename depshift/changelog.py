"""Fetch changelog and deprecation data for a package version."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import httpx

from depshift.http import http_get


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
    resp = http_get(url, timeout=15, follow_redirects=True)
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
    resp = http_get(url, timeout=15, follow_redirects=True)
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
        resp = http_get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code in (401, 403) and "Authorization" in headers:
            # an expired/invalid token must not block a public repo: retry anonymously
            headers.pop("Authorization")
            resp = http_get(url, headers=headers, timeout=15, follow_redirects=True)
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
    r"\b(breaking|backwards?[- ]incompatible|incompatible|removals?|removed|expired|"
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
    r"alias(?:es)?(?:\s+\([^)]*\))?\s+(?:of|for)|"
    r"(?:renam|mov)\w*\s+`{0,2}[\w.:~]+`{0,2}\s+(?:to|into))\s+"
    r"`{0,2}:?(?:\w+:)*`?~?\.?([a-zA-Z_][\w.]*)", re.IGNORECASE)

_RANK = {"low": 0, "medium": 1, "high": 2}
_PARAM_OF = re.compile(
    r"`{1,2}([a-zA-Z_]\w*)`{1,2}\s+(?:option|param|parameter|argument|arg|kwarg|keyword argument|keyword)s?"
    r"\s+(?:of|to|in|for|from)\s+(?:the\s+)?(?::\w+:)?`{1,2}~?\.?([a-zA-Z_][\w.]*(?:\(\))?)`{1,2}",
    re.IGNORECASE)
_KEYWORD_FROM = re.compile(
    r"\b(?:option|param|parameter|argument|arg|kwarg|keyword argument|keyword)s?\s+"
    r"`{1,2}([a-zA-Z_]\w*)`{1,2}\s+(?:from|of|in(?!\s+favou?r)|to|for)\b", re.IGNORECASE)
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


_API_CALL_KWARG = re.compile(r"`{1,2}([a-zA-Z_][\w.]*)\(([a-zA-Z_]\w*)=[^`]*\)`{1,2}")


_BARE_AFTER_VERB = re.compile(
    r"\b(?:remov|delet|drop|deprecat|renam)\w*\s+((?:[a-z][a-z0-9]*_[a-z0-9_]+(?:\(\))?"
    r"(?:\s*(?:,|&|and|or)\s*)?)+)", re.IGNORECASE)
_SNAKE = re.compile(r"[a-z][a-z0-9]*_[a-z0-9_]+")


def _extract_apis(line: str) -> List[str]:
    apis = _extract_marked_apis(line)
    if apis:
        return apis
    # "Remove to_numpy_matrix & from_numpy_matrix (#5746)": snake_case names
    # right after the verb, unmarked. Only directly after the verb, so prose
    # like "removed the read_only flag handling" doesn't count.
    m = _BARE_AFTER_VERB.search(line)
    return _SNAKE.findall(m.group(1)) if m else []


def _extract_marked_apis(line: str) -> List[str]:
    # ``Retry(method_whitelist=...)`` names the parameter Retry.method_whitelist
    line = _API_CALL_KWARG.sub(lambda m: f"``{m.group(1)}.{m.group(2)}``", line)
    apis = [a.rstrip("()").strip(".") for a in _API_BACKTICK.findall(line)]
    if not apis:
        apis = [a.rstrip("()").strip(".") for a in _API_DOTTED.findall(line)]
    # skip things that are clearly not APIs: versions, file names, urls
    return [a for a in apis if a and not re.match(r"^v?\d", a)
            and not re.search(r"\.(py|txt|md|rst|cfg|toml|json|yml|yaml|html)$", a)]


_BULLET = re.compile(r"^(\s*)(?:[-*\u2022+]|\d+[.)])\s+")


_TABLE_BORDER = re.compile(r"^\s*=+(?:\s+=+)+\s*$")


def _items(text: str) -> List[Tuple[Optional[str], str]]:
    """Split release notes into (section kind, item text) pairs.

    A bullet plus its wrapped continuation lines is one item; so is a plain
    paragraph, and so is each row of a reST simple table ("old name | use
    instead"). Headings set the section for the items below them; a
    sub-heading the classifier doesn't recognise ("Constants") inherits its
    parent's section ("Backwards incompatible changes").
    """
    lines = text.splitlines()
    out: List[Tuple[Optional[str], str]] = []
    stack: List[Tuple[int, Optional[str]]] = []   # (level, section kind)
    rst_levels: List[str] = []                    # underline chars in order seen
    cur: List[str] = []
    cur_section: Optional[str] = None
    cur_indent: Optional[int] = None  # indent of the bullet that started the item
    table_borders = 0

    def section() -> Optional[str]:
        for _, kind in reversed(stack):
            if kind is not None:
                return kind
        return None

    def flush():
        if cur:
            out.append((cur_section, " ".join(cur)))
            cur.clear()

    skip_next = False
    for i, raw in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        if _TABLE_BORDER.match(raw):
            flush()
            table_borders += 1
            continue
        heading = None if _in_table(table_borders) else _heading_text(lines, i)
        if heading is not None:
            flush()
            md = _MD_HEADING.match(raw)
            if md:
                level = len(raw.strip()) - len(raw.strip().lstrip("#"))
            elif i + 1 < len(lines) and _RST_UNDERLINE.match(lines[i + 1]):
                ch = lines[i + 1].strip()[0]
                if ch not in rst_levels:
                    rst_levels.append(ch)
                level = rst_levels.index(ch) + 1
                skip_next = True
            else:
                level = (stack[-1][0] + 1) if stack else 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            kind = _section_kind(heading)
            if kind == "benign" and section() in ("removed", "deprecated"):
                # "Miscellaneous" under "Features deprecated in 3.0" is still
                # about deprecations
                kind = None
            stack.append((level, kind))
            continue
        if _in_table(table_borders):
            # one row = one item: first column is the API, the rest says what
            # replaces it
            row = raw.strip()
            if row and table_borders % 3 != 1:   # skip the header row
                cols = re.split(r"\s{2,}", row, maxsplit=1)
                text_row = cols[0] + (f" replaced by {cols[1]}" if len(cols) > 1 else "")
                out.append((section(), text_row))
            continue
        if not raw.strip():
            nxt = next((ln for ln in lines[i + 1:] if ln.strip()), "")
            nb = _BULLET.match(nxt)
            if cur and cur_indent is not None and nb and len(nb.group(1)) > cur_indent:
                continue  # blank line before a nested list stays inside the item
            flush()
            continue
        if _RST_UNDERLINE.match(raw):
            flush()
            continue
        bullet = _BULLET.match(raw)
        if bullet and cur and cur_indent is not None and len(bullet.group(1)) > cur_indent:
            # nested bullet: part of its parent item ("Affected functions are:")
            cur.append(_BULLET.sub("", raw, count=1).strip())
        elif bullet:
            flush()
            cur_section = section()
            cur_indent = len(bullet.group(1))
            cur.append(_BULLET.sub("", raw, count=1).strip())
        else:
            if not cur:
                cur_section = section()
                cur_indent = None
            cur.append(raw.strip())
    flush()
    return out


def _in_table(borders: int) -> bool:
    """reST simple tables have three borders: top, under the header, bottom."""
    return borders % 3 in (1, 2)


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
    # everything named after "in favor of" / "use" / "replaced by" is a
    # replacement, not something that changed
    repl_at = _REPLACEMENT.search(line)
    replacements: List[str] = []
    if repl_at:
        replacements = _extract_apis(line[repl_at.start():])
        if repl_at.group(0).lower().startswith(("renam", "mov")):
            # "Renamed ``a`` to ``b``": the phrase itself contains the old name
            replacements = replacements[1:]
        apis = [a for a in apis if a not in replacements] or apis
    replacements = list(dict.fromkeys(replacements))
    # "a, b and c are deprecated in favour of x, y and z respectively": pair by
    # order, and only when the counts agree. One replacement for several old
    # names is ambiguous, so none of them gets one.
    paired: Dict[str, str] = {}
    if replacements:
        own = _PARAM_OWNER.search(line)
        # "``Retry`` options ``a``, ``b`` in favour of ``x``, ``y``": the owner
        # isn't one of the things being replaced
        olds = [a for a in apis if not (own and a == own.group(1).rstrip("()"))]
        if len(replacements) == len(olds):
            paired = dict(zip(olds, replacements))

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
    param_of = _PARAM_OF.search(line)
    kw_from = None if param_of else _KEYWORD_FROM.search(line)
    if param_of:
        # "The ``even`` argument of ``simpson`` has been removed": simpson.even
        targets = [f"{param_of.group(2).rstrip('()')}.{param_of.group(1)}"]
    elif kw_from:
        # "Remove keyword ``mangle_dupe_cols`` from ``read_csv`` and ``read_table``"
        owners = [o for o in _extract_apis(line[kw_from.end():]) if o != kw_from.group(1)]
        if owners:
            targets = [f"{o}.{kw_from.group(1)}" for o in owners]
    owner = None if (param_of or kw_from) else _PARAM_OWNER.search(line)
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
        c = conf
        if api != "unknown" and kind in ("removed", "changed", "deprecated", "renamed") \
                and not _is_subject(line, api.rsplit(".", 1)[-1]):
            # the item mentions the API but isn't about removing it
            # ("Passing dtype=... to ``DataFrame`` now raises"): a behaviour
            # change worth a warning, not proof the call breaks
            c = "low"
        entries.append(ChangeEntry(
            kind=kind, api=api, description=line[:200], version=version,
            confidence=c, replacement=_pick_replacement(api, paired),
        ))
    return entries


def _pick_replacement(api: str, paired: Dict[str, str]) -> Optional[str]:
    """The replacement paired with this API (or with its last segment, for
    ``Retry.method_whitelist`` built from ``method_whitelist``)."""
    repl = paired.get(api) or paired.get(api.rsplit(".", 1)[-1])
    return repl.rstrip(".") if repl and repl != api else None


_CHANGE_VERB = r"(?:remov|delet|drop|deprecat|renam|expir|replac|supersed)\w*"


def _is_subject(line: str, name: str) -> bool:
    """Is ``name`` what the item removes/deprecates/renames, rather than
    something it merely mentions?"""
    n = re.escape(name)
    before = re.compile(_CHANGE_VERB + r"\b(?:\s+\S+){0,3}?\s+\S*" + n + r"\b", re.IGNORECASE)
    after = re.compile(r"\b" + n + r"\S*(?:\s+\S+){0,6}?\s+(?:\S+\s+)?(?:" + _CHANGE_VERB +
                       r"|no\s+longer\s+(?:support|available|accept|exist|export|import|provid|work))\w*\b",
                       re.IGNORECASE)
    return bool(before.search(line) or after.search(line))


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
                    resp = http_get(url, headers=headers, timeout=10, follow_redirects=True)
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


# Projects that keep one release-notes file per version in their docs.
# {v} is the full version (2.0.0); sectioned files covering a whole series
# use {major}{minor}. Probed in order; the first that exists is remembered.
DOCS_NOTES_PATTERNS = [
    "doc/source/release/{v}-notes.rst",      # numpy, scipy
    "doc/release/{v}-notes.rst",
    "doc/source/whatsnew/v{v}.rst",          # pandas
    "docs/releasenotes/{v}.rst",             # Pillow
    "docs/release-notes/{v}.md",
    "docs/release-notes/{v}.rst",
    "docs/changelog/{v}.md",
    "docs/whatsnew/{v}.rst",
    "doc/release/release_{v}.rst",           # NetworkX
    "docs/releases/{v}.txt",                 # Django (two-part names for x.y.0)
    "docs/releases/{v}.rst",
]
DOCS_SERIES_PATTERNS = [
    "doc/build/changelog/changelog_{major}{minor}.rst",  # SQLAlchemy
]
DOCS_CHANGELOG_FILES = [
    "docs/changelog.md", "docs/changelog.rst", "docs/changes.rst", "docs/changes.md",
    "docs/history.rst", "docs/history.md", "docs/news.rst", "docs/release-notes.md",
    "docs/release_notes.md", "docs/source/changelog.rst", "doc/changelog.rst",
    "docs/CHANGELOG.md", "doc/source/changelog.rst",
]


def _raw(owner: str, repo: str, path: str, branch: str = "main") -> Optional[str]:
    """Fetch a public file from GitHub; None if it doesn't exist. Raises on
    network errors so callers can report them."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    resp = http_get(url, timeout=10, follow_redirects=True)
    if resp.status_code == 200 and len(resp.text) > 50:
        return resp.text
    if resp.status_code in (404, 400):
        return None
    resp.raise_for_status()
    return None


def _versions_between(pypi_info: dict, current: str, target: str) -> List[str]:
    """Final releases in (current, target], oldest first."""
    from packaging.version import Version, InvalidVersion
    try:
        lo, hi = Version(current), Version(target)
    except InvalidVersion:
        return []
    out = []
    for ver in (pypi_info.get("releases") or {}):
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        if not v.is_prerelease and not v.is_devrelease and lo < v <= hi:
            out.append(v)
    return [str(v) for v in sorted(out)]


def _docs_versions(pypi_info: dict, current: str, target: str, lookback: bool) -> List[str]:
    """Versions whose per-version docs notes to read: the upgrade range, plus
    (with lookback) the x.y.0 releases of the current major series, where
    deprecations and their replacements are usually announced."""
    versions = _versions_between(pypi_info, current, target)
    if not lookback:
        return versions
    from packaging.version import Version, InvalidVersion
    try:
        cur = Version(current)
    except InvalidVersion:
        return versions
    earlier = []
    for ver in (pypi_info.get("releases") or {}):
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        if v.major == cur.major and v.micro == 0 and not v.is_prerelease and v <= cur:
            earlier.append(v)
    return [str(v) for v in sorted(earlier)] + [v for v in versions if v not in {str(e) for e in earlier}]


def _three_part(v: str) -> str:
    parts = v.split(".")
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts[:3]) if all(p.isdigit() for p in parts[:3]) else v


def _short_version(v: str) -> str:
    """4.0.0 -> 4.0 (Django names x.y.0 notes 'x.y'); other versions unchanged."""
    parts = v.split(".")
    return ".".join(parts[:2]) if len(parts) >= 3 and parts[2] == "0" else v


def fetch_docs_release_notes(owner: str, repo: str, versions: List[str],
                             max_files: int = 40) -> Optional[List[ChangeEntry]]:
    """Release notes kept in the project's docs instead of a root CHANGES file.

    Returns None when the project has no recognised docs layout.
    """
    if not versions:
        return None
    key = f"docs-layout3:{owner}/{repo}"
    layout = cache_get(key)
    probe = _three_part(versions[-1])
    if layout is None:
        layout = ""
        from packaging.version import Version
        tv = Version(versions[-1])
        short = _short_version(versions[-1])
        candidates = [("version", p, p.format(v=form)) for p in DOCS_NOTES_PATTERNS
                      for form in dict.fromkeys([probe, short])]
        candidates += [("series", p, p.format(major=tv.major, minor=tv.minor)) for p in DOCS_SERIES_PATTERNS]
        candidates += [("file", p, p) for p in DOCS_CHANGELOG_FILES]
        for kind, pattern, path in candidates:
            if _raw(owner, repo, path) is not None:
                layout = f"{kind}|{pattern}"
                break
        cache_set(key, layout)
    if not layout:
        return None

    kind, pattern = layout.split("|", 1)
    entries: List[ChangeEntry] = []
    if kind == "version":
        for v in versions[-max_files:]:
            for form in dict.fromkeys([_three_part(v), _short_version(v)]):
                text = _cached_raw(owner, repo, pattern.format(v=form))
                if text:
                    entries.extend(parse_changelog_text(text, v))
                    break
    elif kind == "series":
        from packaging.version import Version
        series = sorted({(Version(v).major, Version(v).minor) for v in versions})
        for major, minor in series[-max_files:]:
            text = _cached_raw(owner, repo, pattern.format(major=major, minor=minor))
            if text:
                entries.extend(parse_sectioned_changelog(text, repo))
    else:
        text = _cached_raw(owner, repo, pattern)
        if text:
            entries.extend(parse_sectioned_changelog(text, repo))
    return entries


def _cached_raw(owner: str, repo: str, path: str) -> Optional[str]:
    key = f"gh-raw:{owner}/{repo}/{path}"
    cached = cache_get(key)
    if cached is not None:
        return cached or None
    text = _raw(owner, repo, path)
    cache_set(key, text or "")
    return text


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
    # every parsed entry up to the target, not just those in the upgrade
    # range: a deprecation announced before your version still names the
    # replacement for an API removed inside the range
    history: List[ChangeEntry] = field(default_factory=list)

    @property
    def checked(self) -> bool:
        """True if at least one source was actually read."""
        return bool(self.sources_ok)


def collect_changes(package: str, current_version: str, target_version: str,
                    github_token: Optional[str] = None, lookback: bool = False) -> ChangeReport:
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

        if True:
            # release notes in docs/ are often far more detailed than the root
            # CHANGES file (Pillow, NumPy, pandas), so read them too
            try:
                docs = fetch_docs_release_notes(
                    owner, repo, _docs_versions(pypi_info, current_version, target_version, lookback))
                if docs is not None:
                    ok.append("docs release notes")
                    all_entries.extend(docs)
            except Exception as e:
                failed.append(f"docs release notes ({_short_err(e)})")

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

    history = []
    for entry in all_entries:
        try:
            if Version(entry.version) <= v_target:
                history.append(entry)
        except InvalidVersion:
            continue
    return ChangeReport(filtered, ok, failed, skipped, history)


def _has_in_range(entries: List[ChangeEntry], current: str, target: str) -> bool:
    from packaging.version import Version, InvalidVersion
    try:
        lo, hi = Version(current), Version(target)
    except InvalidVersion:
        return bool(entries)
    for e in entries:
        try:
            if lo < Version(e.version) <= hi:
                return True
        except InvalidVersion:
            continue
    return False


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
                kind="removed", api=e.api, version=str(sweep_v),
                confidence="low" if e.confidence == "low" else "medium",
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
