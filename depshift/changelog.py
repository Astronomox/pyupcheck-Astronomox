"""Fetch changelog and deprecation data for a package version."""

import os
import re
from dataclasses import dataclass, field

import httpx

from depshift.cache import cache_get, cache_set
from depshift.http import http_get


@dataclass
class ChangeEntry:
    """A single breaking or deprecated API change."""
    kind: str  # "removed", "deprecated", "changed", "renamed"
    api: str  # the affected API path, e.g. "requests.get.verify"
    description: str
    version: str  # version where this change happened
    confidence: str = "medium"  # "high" (explicit section / strong phrase), "medium", "low"
    # "Deprecate the ``urls`` module, except for ``iri_to_uri``": members that stay
    exceptions: list[str] = field(default_factory=list)
    replacement: str | None = None  # suggested replacement API, when the text names one
    # a deprecation's announced removal ("will be removed in 1.2")
    removed_in: str | None = None
    # the parent item a nested one was listed under, when that is what says
    # what happened ("The old names are deprecated.")
    context: str | None = None


def clip(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters, at a word boundary, with
    an ellipsis when anything was cut."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


PYPI_API = "https://pypi.org/pypi/{package}/json"
PYPI_VERSION_API = "https://pypi.org/pypi/{package}/{version}/json"
GITHUB_RELEASES_API = "https://api.github.com/repos/{owner}/{repo}/releases"
GITHUB_TAGS_API = "https://api.github.com/repos/{owner}/{repo}/tags"


def get_pypi_info(package: str, fresh: bool = False) -> dict:
    """Fetch full package info from PyPI (cached; ``fresh`` asks PyPI again
    and refreshes the cache, for spotting new releases)."""
    key = f"pypi:{package}"
    cached = None if fresh else cache_get(key)
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


def get_available_versions(package: str) -> list[str]:
    """Get all available versions for a package."""
    info = get_pypi_info(package)
    return list(info.get("releases", {}).keys())


def get_current_version(package: str) -> str:
    """Get the latest version of a package on PyPI."""
    info = get_pypi_info(package)
    return info["info"]["version"]


# what GitHub allows: owners are 1-39 letters, digits and single hyphens;
# repository names letters, digits, '.', '_' and '-' (but not "." or "..")
_GH_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_GH_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_GH_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/ -]{1,300}$")


def valid_repo(owner: str, repo: str) -> bool:
    """A real GitHub owner/repo, safe to put in a URL path."""
    return bool(_GH_OWNER.match(owner or "")) and bool(_GH_REPO.match(repo or "")) and repo not in (".", "..")


def _safe_path(path: str) -> bool:
    return bool(_SAFE_PATH.match(path)) and ".." not in path.split("/") and not path.startswith("/")


def extract_github_repo(pypi_info: dict) -> tuple[str, str] | None:
    """Try to find the GitHub owner/repo from PyPI metadata."""
    info = pypi_info.get("info", {})
    urls = []

    project_urls = info.get("project_urls") or {}
    # the source link first: "Funding" often points at github.com/sponsors/...
    preferred = ("source", "repository", "code", "github", "source code", "homepage", "home")
    ranked = sorted(project_urls.items(),
                    key=lambda kv: (0 if kv[0].strip().lower() in preferred else 1))
    for _label, val in ranked:
        if val:
            urls.append(val)

    home = info.get("home_page")
    if home:
        urls.append(home)

    pattern = re.compile(r"github\.com/([^/]+)/([^/\s#?]+)")
    for url in urls:
        m = pattern.search(url)
        if m and m.group(1).lower() not in ("sponsors", "orgs", "apps", "marketplace"):
            owner, repo = m.group(1), m.group(2)
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            if valid_repo(owner, repo):
                return owner, repo
    return None


_TOKEN = re.compile(r"^[A-Za-z0-9_]{20,255}$")
_found_token: list = []  # [token or None] once looked up: gh is asked once per run


def gh_cli_token(project_dir: str | None = None) -> str | None:
    """The token of the GitHub CLI (``gh auth token``), if it is installed
    and signed in: GitHub allows 5,000 API requests an hour with it, 60
    without. Asked once per run; never a ``gh`` inside the current or
    project directory (Windows looks there first, and the project may be
    someone else's); off with PYUPCHECK_NO_GH_AUTH=1."""
    if _found_token:
        return _found_token[0]
    token = None
    import shutil

    # gh, found on PATH and run without a shell, with a fixed argument list
    import subprocess  # nosec B404
    exe = None if os.environ.get("PYUPCHECK_NO_GH_AUTH") else shutil.which("gh")
    if exe:
        real = os.path.realpath(exe)
        here = [os.path.realpath(d) for d in (os.getcwd(), project_dir) if d]
        if not any(real == d or real.startswith(d + os.sep) for d in here):
            try:
                out = subprocess.run([real, "auth", "token", "--hostname", "github.com"],  # nosec B603
                                     capture_output=True, text=True, timeout=5, check=False)
                candidate = out.stdout.strip()
                if out.returncode == 0 and _TOKEN.match(candidate):
                    token = candidate
            except (OSError, subprocess.SubprocessError, ValueError):
                token = None
    _found_token.append(token)
    return token


def github_token_for(explicit: str | None) -> str | None:
    """The token to use: --github-token / GITHUB_TOKEN, else GH_TOKEN, else
    the GitHub CLI's. It is only ever sent to GitHub's own hosts."""
    return explicit or os.environ.get("GH_TOKEN") or gh_cli_token()


# set once anonymous API requests are rate limited: for the rest of the run
# the releases feed is read instead of spending requests on 403s
_api_exhausted: list = []


def fetch_github_releases(owner: str, repo: str, token: str | None = None) -> list[dict]:
    """Fetch releases from GitHub (cached)."""
    if not valid_repo(owner, repo):
        return []
    key = f"gh-releases:{owner}/{repo}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif _api_exhausted:
        feed = fetch_releases_feed(owner, repo)
        if feed:
            cache_set(key, feed)
            return feed
        raise RuntimeError("HTTP 403: GitHub API rate limit reached")

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
            if resp.status_code in (403, 429) and "Authorization" not in headers:
                _api_exhausted.append(True)
            if page == 1 and resp.status_code in (401, 403, 429):
                # rate limited: the releases feed isn't, though it only has
                # the latest releases. If that fails too, say so rather than
                # "no breaking changes"
                feed = fetch_releases_feed(owner, repo)
                if feed:
                    cache_set(key, feed)
                    return feed
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


GITHUB_RELEASES_FEED = "https://github.com/{owner}/{repo}/releases.atom"
_ATOM = "{http://www.w3.org/2005/Atom}"


def html_to_text(html_text: str) -> str:
    """Release-note HTML as the Markdown-ish text the parser reads: headings
    stay headings, list items become "- " bullets, code keeps backticks."""
    import html as _html
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html_text)
    t = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", t)
    t = re.sub(r"(?i)</h[1-6]>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "\n- ", t)
    t = re.sub(r"(?i)</?(?:code|tt)[^>]*>", "`", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</ul>|</ol>|</div>|</pre>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_releases_feed(xml_text: str) -> list[dict]:
    """GitHub's releases Atom feed as release dicts (``tag_name``, ``name``,
    ``body``), like the API returns."""
    # a document with a DTD is refused below, before parsing: no entity expansion
    import xml.etree.ElementTree as ET  # nosec B405
    from urllib.parse import unquote
    # an Atom feed never needs a DTD: refusing one rules out entity-expansion
    # ("billion laughs") and external-entity tricks before the parser runs
    if not xml_text or re.search(r"<!(?:DOCTYPE|ENTITY)", xml_text, re.IGNORECASE):
        return []
    try:
        root = ET.fromstring(xml_text)  # nosec B314
    except ET.ParseError:
        return []
    out = []
    for entry in root.findall(f"{_ATOM}entry"):
        link = entry.find(f"{_ATOM}link")
        href = link.get("href", "") if link is not None else ""
        tag = unquote(href.rsplit("/tag/", 1)[-1]) if "/tag/" in href else ""
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        content = entry.findtext(f"{_ATOM}content") or ""
        out.append({"tag_name": tag or title, "name": title, "body": html_to_text(content)})
    return out


def fetch_releases_feed(owner: str, repo: str) -> list[dict]:
    """The latest releases from the Atom feed (no API rate limit)."""
    try:
        resp = http_get(GITHUB_RELEASES_FEED.format(owner=owner, repo=repo), timeout=15, follow_redirects=True)
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    return parse_releases_feed(resp.text)


# headings that classify the bullet points beneath them
_BREAKING_HEADING = re.compile(
    r"\b(breaking|backwards?[- ]incompatible|incompatible|removals?|removed|expired|moved|"
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

# ``@pytest.fixture`` (a decorator) names pytest.fixture
_API_BACKTICK = re.compile(r"`{1,2}:?(?:\w+:)*`?[~@]?\.?([a-zA-Z_][\w.]*(?:\(\))?)`{1,2}")
_API_DOTTED = re.compile(r"(?:^|[\s(])([a-zA-Z_]\w*\.[a-zA-Z_][\w.]*(?:\(\))?)")
_REPLACEMENT = re.compile(
    r"(?:use|in favou?r of|replaced (?:by|with)|superseded by|renamed to|moved to|instead,? use|"
    r"merged\s+into(?:\s+the)?|"
    r"alias(?:es)?(?:\s+\([^)]*\))?\s+(?:of|for)|"
    r"(?:renam|mov)\w*\s+`{0,2}[\w.:~]+`{0,2}\s+(?:to|into))\s+"
    r"`{0,2}:?(?:\w+:)*`?~?\.?([a-zA-Z_][\w.]*)", re.IGNORECASE)

# words right after a replacement that make it more than a rename:
# "use ``np.asarray`` with a float dtype", "use ``X`` and pass ``y=``"
_QUALIFIED = re.compile(
    r"^`{0,2}(?:\(\))?`{0,2}\s+(?:with(?!\s+the\s+same\b)|passing|plus|and\s+(?:pass|set|specify|add|call|wrap)\w*|"
    r"together\s+with|combined\s+with|followed\s+by|on\s+the\s+result|wrapped\s+in)\b", re.IGNORECASE)


def _qualified(line: str, repl: "re.Match") -> bool:
    return bool(_QUALIFIED.match(line[repl.end(1):]))


# "will be removed in 1.2", "scheduled for removal in version 2.0", "to be
# dropped in v3", "removal in 1.4"; never "in a future version"
_REMOVED_IN = re.compile(
    r"\b(?:(?:will|shall|would)\s+be|to\s+be|is\s+(?:scheduled|slated|planned)\s+to\s+be|scheduled\s+for|"
    r"slated\s+for|planned\s+for|marked\s+for|for)?\s*(?:removed|removal|dropped|deleted)\s+"
    r"(?:in|from|with|by|as\s+of|starting\s+(?:with|in|from))\s+(?:(?!python\b)[A-Za-z][\w-]*\s+)?"
    r"(?:version\s+|release\s+|v)?"
    r"(\d+(?:\.\d+){0,2})(?![\d.]*\d)(?!\s+of\s+python)", re.IGNORECASE)


_FUTURE_REMOVAL = re.compile(
    r"\b(?:will|shall)\s+be\s+(?:removed|dropped|deleted)|\bto\s+be\s+(?:removed|dropped)|"
    r"\b(?:scheduled|slated|planned|marked)\s+(?:for|to\s+be)\s+(?:removal|removed)", re.IGNORECASE)


def removed_in_version(line: str) -> str | None:
    """The version a deprecation says its API will be removed in, if any."""
    m = _REMOVED_IN.search(line)
    return m.group(1).rstrip(".") if m else None


_RANK = {"low": 0, "medium": 1, "high": 2}
_PARAM_OF = re.compile(
    r"`{1,2}([a-zA-Z_]\w*)`{1,2}\s+(?:option|param|parameter|argument|arg|kwarg|keyword argument|keyword)s?"
    r"\s+(?:of|to|in|for|from)\s+(?:the\s+)?(?::\w+:)?`{1,2}~?\.?([a-zA-Z_][\w.]*(?:\(\))?)`{1,2}",
    re.IGNORECASE)
# "Passing a string argument as ``only`` to ``fields.Nested``": Nested.only
_ARG_AS = re.compile(
    r"\b(?:option|param|parameter|argument|arg|kwarg|keyword argument|keyword)s?\s+(?:as|named|called)\s+"
    r"`{1,2}([a-zA-Z_]\w*)`{1,2}\s+(?:to|of|in|for|on)\s+(?:the\s+)?(?::\w+:)?`{1,2}~?\.?"
    r"([a-zA-Z_][\w.]*(?:\(\))?)`{1,2}", re.IGNORECASE)
# "Rename ``DateTime``'s ``dateformat`` Meta option": DateTime.dateformat
_POSSESSIVE = re.compile(r"(?::\w+:)?`{1,2}~?\.?([a-zA-Z_][\w.]*?)(?:\(\))?`{1,2}['\u2019]s\s+"
                         r"`{1,2}([a-zA-Z_]\w*)`{1,2}")


_CONDITIONAL = re.compile(r"^\W*(?:if|when|whenever|unless)\b", re.IGNORECASE)


class _ParamOf:
    """A (parameter, owner) pair found in a note, shaped like _PARAM_OF's match."""

    def __init__(self, param: str, owner: str):
        self._groups = (param, owner)

    def group(self, i: int) -> str:
        return self._groups[i - 1]


def _param_of(line: str):
    """The parameter (or option, or member) of one API that a note is about,
    as group(1) and the API as group(2); None when the note names none."""
    m = _PARAM_OF.search(line)
    if m:
        return m
    m = _ARG_AS.search(line)
    if m:
        return _ParamOf(m.group(1), m.group(2))
    m = _POSSESSIVE.search(line)
    if m:
        return _ParamOf(m.group(2), m.group(1))
    return None


_KEYWORD_FROM = re.compile(
    r"\b(?:option|param|parameter|argument|arg|kwarg|keyword argument|keyword)s?\s+"
    r"`{1,2}([a-zA-Z_]\w*)`{1,2}\s+(?:from|of|in(?!\s+favou?r)|to|for)\b", re.IGNORECASE)
_PARAM_OWNER = re.compile(
    r"`{1,2}:?(?:\w+:)*`?~?\.?([a-zA-Z_][\w.]*(?:\(\))?)`{1,2}(?:'s)?\s+"
    r"(?:option|param|parameter|argument|arg|kwarg|keyword argument|keyword)s?\b", re.IGNORECASE)


def _heading_text(lines: list[str], i: int) -> str | None:
    """If lines[i] is a heading, return its text."""
    line = lines[i]
    m = _MD_HEADING.match(line) or _BOLD_HEADING.match(line) or _COLON_HEADING.match(line)
    if m:
        return m.group(1)
    if i + 1 < len(lines) and line.strip() and _RST_UNDERLINE.match(lines[i + 1]) \
            and not line.lstrip().startswith(("-", "*", "+")):
        return line.strip()
    return None


def _section_kind(heading: str) -> str | None:
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


def _extract_apis(line: str) -> list[str]:
    apis = _extract_marked_apis(line)
    if apis:
        return apis
    # "Remove to_numpy_matrix & from_numpy_matrix (#5746)": snake_case names
    # right after the verb, unmarked. Only directly after the verb, so prose
    # like "removed the read_only flag handling" doesn't count.
    m = _BARE_AFTER_VERB.search(line)
    return _SNAKE.findall(m.group(1)) if m else []


# Sphinx cross-references to pages, sections and terms, not APIs:
# :ref:`change_12168`, :doc:`migration`, :term:`2.0 style`
_NON_API_ROLE = re.compile(r":(?:ref|doc|term|download|numref|envvar|token|keyword|option|pep|rfc):`[^`]*`")


def _extract_marked_apis(line: str) -> list[str]:
    line = _NON_API_ROLE.sub("", line)
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


def _items(text: str, default_section: str | None = None) -> list[tuple[str | None, str]]:
    """Split release notes into (section kind, item text) pairs.

    A bullet plus its wrapped continuation lines is one item; so is a plain
    paragraph, and so is each row of a reST simple table ("old name | use
    instead"). Headings set the section for the items below them; a
    sub-heading the classifier doesn't recognise ("Constants") inherits its
    parent's section ("Backwards incompatible changes").
    """
    lines = text.splitlines()
    out: list[tuple[str | None, str]] = []
    stack: list[tuple[int, str | None]] = []   # (level, section kind)
    rst_levels: list[str] = []                    # underline chars in order seen
    cur: list[str] = []
    cur_section: str | None = None
    cur_indent: int | None = None  # indent of the bullet that started the item
    table_borders = 0
    table_kind: str | None = None      # what a reST table's header row says
    md_table_rows = 0                     # 0: none, -1: header seen, >=1: body
    md_header_kind: str | None = None

    # SQLAlchemy/Alembic/Mako ".. change::" blocks say what each entry is in a
    # ":tags: usecase, sql" line
    change_kind: list[str | None] = [None]

    def section() -> str | None:
        for _, kind in reversed(stack):
            if kind is not None:
                return kind
        return change_kind[0] or default_section

    # statement-parents of the current nested item: (indent, text)
    parents: list[tuple[int, str]] = []

    def flush():
        if cur:
            text = " ".join(cur)
            if parents:
                # the parent says what kind of change this is ("The old names
                # are deprecated."); the child names the APIs
                text += _CTX + parents[-1][1]
            out.append((cur_section, text))
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
            parents.clear()
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
            # replaces it. The header row ("removed member | guideline") can
            # say what the whole table is.
            row = raw.strip()
            if row and table_borders % 3 == 1:
                table_kind = _section_kind(row)
            elif row:
                cols = re.split(r"\s{2,}", row, maxsplit=1)
                out.append((table_kind if table_kind in ("removed", "deprecated") else section(),
                            _table_row_item(cols[0], cols[1] if len(cols) > 1 else "")))
            continue
        md_row = _MD_TABLE_ROW.match(raw)
        if md_row:
            flush()
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
                md_table_rows = 1  # separator: the row before it was the header
                continue
            if md_table_rows == 0:
                md_header_kind = _section_kind(" ".join(cells))
                md_table_rows = -1  # header seen, waiting for the separator
                continue
            if md_table_rows >= 1 and cells:
                kind = md_header_kind if md_header_kind in ("removed", "deprecated") else section()
                out.append((kind, _table_row_item(cells[0], cells[1] if len(cells) > 1 else "")))
            continue
        md_table_rows = 0
        if _CHANGE_DIRECTIVE.match(raw):
            flush()
            change_kind[0] = None
            continue
        tag_line = _CHANGE_TAGS.match(raw)
        if tag_line:
            flush()
            change_kind[0] = _tags_kind({t.strip().lower() for t in tag_line.group(1).split(",")})
            continue
        if _CHANGE_FIELD.match(raw):
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
        if bullet and cur and cur_indent is not None and len(bullet.group(1)) > cur_indent \
                and _introduces_list(cur):
            # nested bullet under "Affected functions are:": part of its parent
            cur.append(_BULLET.sub("", raw, count=1).strip())
        elif bullet:
            # a top-level bullet, or a nested one under a parent that is a
            # statement of its own ("Remove previously deprecated code."):
            # each nested change is an item, and the parent stays a bare
            # sweep, which removes everything deprecated before it
            ind = len(bullet.group(1))
            nested = bool(cur) and cur_indent is not None and ind > cur_indent
            parent_text = _CTX_SPLIT(" ".join(cur)) if nested else None
            flush()
            while parents and parents[-1][0] >= ind:
                parents.pop()
            if nested and cur_indent is not None and parent_text is not None:
                parents.append((cur_indent, parent_text))
            cur_section = section()
            cur_indent = len(bullet.group(1))
            cur.append(_BULLET.sub("", raw, count=1).strip())
        else:
            if not cur:
                cur_section = section()
                cur_indent = None
                if not raw[:1].isspace():
                    parents.clear()  # a paragraph at the margin: out of any list
            cur.append(raw.strip())
    flush()
    return out


_CHANGE_DIRECTIVE = re.compile(r"^\s*\.\.\s+change::\s*$")
_CHANGE_TAGS = re.compile(r"^\s*:tags:\s*(.+?)\s*$")
_CHANGE_FIELD = re.compile(r"^\s*:(?:tickets|versions|title|changeset|pullreq|references):")


def _tags_kind(names: set) -> str | None:
    if names & {"removed", "remove"}:
        return "removed"
    if any(n.startswith("deprecat") for n in names):
        return "deprecated"
    if names & _BENIGN_TAGS:
        return "benign"
    return None


# separates an item from its parent's text (context for its kind only)
_CTX = "\x1f"


def _CTX_SPLIT(text: str) -> str:
    return text.split(_CTX, 1)[0]


_TRAILING_REFS = re.compile(r"(?:\s*:(?:pr|issue|ghissue|bpo|user|cve):`[^`]*`)+\s*$")


def _introduces_list(parts: list[str]) -> bool:
    """Does the item so far end by introducing a list ("...are:")?"""
    text = _TRAILING_REFS.sub("", " ".join(parts)).rstrip()
    return text.endswith(":")


def _in_table(borders: int) -> bool:
    """reST simple tables have three borders: top, under the header, bottom."""
    return borders % 3 in (1, 2)


def _kind_of(text: str):
    for patterns, k in ((_BREAKING_PATTERNS, "removed"),
                        (_DEPRECATION_PATTERNS, "deprecated"),
                        (_RENAME_PATTERNS, "renamed")):
        for p, c in patterns:
            if p.search(text):
                return k, c
    return None, "low"


# words in code spans are names, not verbs: "column names such as ``add``,
# ``remove``" removes nothing
_CODE_SPAN = re.compile(r"(?::\w+:)?`{1,2}[^`]+`{1,2}")
# SQLAlchemy/Mako-style category tags: "**[sql] [usecase]** ..."
_TAGS = re.compile(r"^\W*?(?:\*\*)?((?:\[[\w ]+\]\s*)+)(?:\*\*)?\s*")
_BENIGN_TAGS = {"feature", "usecase", "bug", "performance", "typing", "tests", "misc",
                "documentation", "doc", "docs", "installation", "platform"}
# "Remove deprecated ``a`` and ``b`` from ``FastAPI``": a and b belong to FastAPI
_FROM_OWNERS = re.compile(
    r"\bfrom\s+(?:the\s+)?((?:(?::\w+:)?`{1,2}~?\.?[A-Za-z_][\w.]*(?:\(\))?`{1,2}"
    r"(?:\s*,\s*|\s*,?\s*(?:and|or)\s+)?)+)", re.IGNORECASE)


def _prose(text: str) -> str:
    return _CODE_SPAN.sub("NAME", text)


def _looks_like_code(text: str) -> bool:
    """A pasted example ("select( func.sum(table.c.value).over(...") or a bare
    cross-reference, not a sentence about a change."""
    if any(not span.startswith((":ref:", ":doc:")) for span in _CODE_SPAN.findall(text)):
        return False  # "- ``pkg.a``" under a Removed heading names an API
    words = re.findall(r"(?<![\w.(=])[A-Za-z]{2,}(?![\w(=.])", _CODE_SPAN.sub(" ", text))
    return not words


def _classify_item(line: str, section: str | None, version: str) -> list[ChangeEntry]:
    """Turn one release-note item into change entries (one per API it names)."""
    line, _, parent = line.partition(_CTX)
    tags = _TAGS.match(line)
    if tags:
        names = {t.strip().lower() for t in re.findall(r"\[([\w ]+)\]", tags.group(1))}
        line = line[tags.end():]
        if section is None:
            section = _tags_kind(names)
    if _looks_like_code(line):
        return []
    kind, conf = _kind_of(_prose(line))
    context = None
    parent_words = len(_TRAILING_REFS.sub("", parent).split())
    if parent and (parent_words <= 20 or _SWEEP.search(parent)):
        # only a short statement ("Remove previously deprecated code.", "The
        # old names are deprecated.") says what its list is; a paragraph that
        # mentions "removal of a deprecated keyword" in passing doesn't
        p_kind, p_conf = _kind_of(_prose(parent))
        # "``pass_context`` replaces ``contextfunction``" under "The old
        # names are deprecated."; "``contextfunction`` is replaced by
        # ``pass_context``" under "Remove previously deprecated code.":
        # the parent says what happened to the names the child lists
        parent_decides = kind is None or (p_kind == "removed" and kind in ("renamed", "deprecated")
                                          and not _FUTURE_REMOVAL.search(line))
        if p_kind and parent_decides:
            kind, conf, context = p_kind, p_conf, parent

    apis = _extract_apis(line)
    # everything named after "in favor of" / "use" / "replaced by" is a
    # replacement, not something that changed
    repl_at = None if NO_DROP_IN in line else _REPLACEMENT.search(line)
    if repl_at and repl_at.group(0).lower().startswith("use") \
            and repl_at.group(1).lower() in ("of", "in", "for", "with", "case", "cases", "by", "to"):
        # "Positional use of some arguments...": "use" as a noun, not "use X"
        repl_at = None
    replacements: list[str] = []
    if repl_at:
        replacements = _extract_apis(line[repl_at.start():])
        if repl_at.group(0).lower().startswith(("renam", "mov")):
            # "Renamed ``a`` to ``b``": the phrase itself contains the old name
            replacements = replacements[1:]
        # only replacements named: nothing identifies what changed
        apis = [a for a in apis if a not in replacements]
    replacements = list(dict.fromkeys(replacements))
    # "a, b and c are deprecated in favour of x, y and z respectively": pair by
    # order, and only when the counts agree. One replacement for several old
    # names is ambiguous, so none of them gets one.
    paired: dict[str, str] = {}
    if replacements:
        own = _PARAM_OWNER.search(line)
        # "``Retry`` options ``a``, ``b`` in favour of ``x``, ``y``": the owner
        # isn't one of the things being replaced
        olds = [a for a in apis if not (own and a == own.group(1).rstrip("()"))]
        if repl_at and len(replacements) == len(olds) and not _qualified(line, repl_at):
            paired = dict(zip(olds, replacements, strict=True))

    if section == "removed":
        # everything under a "Breaking changes" heading breaks, except explicit
        # deprecations; a rename there has no compatibility alias
        if kind == "deprecated" and re.search(r"\bdeprecat", line, re.IGNORECASE) \
                and not re.search(r"\bremov", line, re.IGNORECASE):
            pass  # an explicit deprecation listed under "Breaking changes"
        elif kind == "deprecated":
            kind = "removed"  # "use X instead" under "Removals" is advice, not a deprecation
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

    if _CONDITIONAL.match(_prose(line)):
        # "If only a name is passed to ``option()``, Click suggests...": what
        # happens under a condition, not a change to every use of the API
        conf = "low"

    repl = _REPLACEMENT.search(line)
    replacement = repl.group(1).rstrip(".") if repl else None
    targets = [a for a in apis if a != replacement] or (apis[:1] if apis else ["unknown"])
    if repl and _qualified(line, repl):
        replacement = None  # advice ("use X with a float dtype"), not a drop-in
    param_of = _param_of(line)
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
    from_owners = None if (param_of or kw_from or owner) else _FROM_OWNERS.search(line)
    if from_owners and kind in ("removed", "deprecated") and not _IMPORT_FROM.search(line) \
            and not _IMPORTING_FROM.search(line):
        # "Remove deprecated ``openapi_prefix`` from ``FastAPI``": FastAPI.openapi_prefix,
        # not FastAPI itself. "``**extra`` removed from ``Path``, ``Query``": only
        # catch-all arguments named, which release notes can't pin to a call
        owners_ = _extract_apis(from_owners.group(1))
        members = [a for a in _extract_apis(line[:from_owners.start()]) if a not in owners_]
        if members:
            targets = [m if "." in m else f"{o}.{m}" for o in owners_ for m in members]
        elif re.search(r"`\*{1,2}\w+`", line[:from_owners.start()]):
            return []
    dotted = [a for a in targets if "." in a]
    if dotted:
        if _LISTED_AFTER_FIRST.search(line) and "." in targets[0]:
            # "``django.utils.translation.ugettext()``, ``ugettext_lazy()``, and
            # ``ungettext()`` are removed": the short names are siblings of the first
            parent = targets[0].rsplit(".", 1)[0]
            targets = [a if "." in a else f"{parent}.{a}" for a in targets]
        else:
            # "`pkg.connect` takes a `Config` object": the dotted names are the subject
            targets = dotted
    # "Import them directly from ``markupsafe`` instead": same names, new package
    new_home = _IMPORT_FROM.search(line)
    home = new_home.group(1).strip(".") if new_home else None
    if home:
        old_home = _IMPORTING_FROM.search(line)
        modules = {home, old_home.group(1).strip(".") if old_home else None}
        targets = [a for a in targets if a not in modules] or targets
    exc = _EXCEPT.search(line)
    excepted = [a.rsplit(".", 1)[-1] for a in _extract_apis(line[exc.end():])] if exc else []
    if excepted:
        targets = [a for a in targets if a.rsplit(".", 1)[-1] not in excepted] or targets
    if kind == "removed" and _FUTURE_REMOVAL.search(line) and re.search(r"\bdeprecat", line, re.IGNORECASE):
        # "Deprecates X in 1.0 and it will be removed in 1.2": a notice of a
        # removal to come, not a removal in this release
        kind = "deprecated"
    scheduled = removed_in_version(line) if kind == "deprecated" else None
    # the removal date belongs to what its own sentence names: in
    # ":class:`A` gains a method. `plot_a` is deprecated ... removed in 1.2",
    # only plot_a goes away
    scheduled_apis = set()
    removal = _REMOVED_IN.search(line) if scheduled else None
    if removal:
        m = removal
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z:`*(])", line)
        pos = 0
        for sent in sentences:
            start = line.find(sent, pos)
            pos = start + len(sent)
            if start <= m.start() < pos:
                scheduled_apis = {a.rsplit(".", 1)[-1] for a in _extract_apis(sent)}
                break
    seen = set()
    entries = []
    for api in targets[:8]:
        if api in seen:
            continue
        seen.add(api)
        c = conf
        if api != "unknown" and kind in ("removed", "changed", "deprecated", "renamed") \
                and context is None \
                and not _is_subject(line, api.rsplit(".", 1)[-1]) \
                and not (section in ("removed", "deprecated") and _leads_with(line, api)):
            # the item mentions the API but isn't about removing it
            # ("Passing dtype=... to ``DataFrame`` now raises"): a behaviour
            # change worth a warning, not proof the call breaks
            c = "low"
        entries.append(ChangeEntry(
            kind=kind, api=api, description=clip(line, 300), version=version,
            confidence=c, exceptions=excepted, replacement=_pick_replacement(api, paired)
            or (f"{home}.{api.rsplit('.', 1)[-1]}" if home and api != "unknown" else None),
            removed_in=scheduled if c != "low" and api.rsplit(".", 1)[-1] in scheduled_apis else None,
            context=context,
        ))
    return entries


# the item opens with a list of marked names: ``a.b.c()``, ``d()``, and ``e()``
_LISTED_AFTER_FIRST = re.compile(
    r"^\W*(?::\w+:)?`{1,2}[^`]+`{1,2}(?:\s*,\s*(?:and\s+|or\s+)?(?::\w+:)?`{1,2}[^`]+`{1,2})+"
    r"\s*,?\s*(?:and|or)?\s*(?::\w+:)?`{0,2}")
_EXCEPT = re.compile(r"\b(?:except(?:\s+for)?|other\s+than|apart\s+from)\b", re.IGNORECASE)
_IMPORT_FROM = re.compile(
    r"\bimport\s+(?:them|it|this|these|those|the\s+\w+)?\s*(?:directly\s+)?from\s+"
    r"(?::\w+:)?`{0,2}~?([a-zA-Z_][\w.]*)`{0,2}", re.IGNORECASE)
_IMPORTING_FROM = re.compile(
    r"\bimporting\b.*?\bfrom\s+(?::\w+:)?`{0,2}~?([a-zA-Z_][\w.]*)`{0,2}", re.IGNORECASE)


def _pick_replacement(api: str, paired: dict[str, str]) -> str | None:
    """The replacement paired with this API (or with its last segment, for
    ``Retry.method_whitelist`` built from ``method_whitelist``)."""
    repl = paired.get(api) or paired.get(api.rsplit(".", 1)[-1])
    return repl.rstrip(".") if repl and repl != api else None


_CHANGE_VERB = r"(?:remov|delet|drop|deprecat|renam|expir|replac|supersed)\w*"


def _leads_with(line: str, api: str) -> bool:
    """Does the item open with this API? In a list under "Removals"
    ("``matplotlib.cm.get_cmap``; use ...") the leading name is the subject."""
    # a listing ("``get_cmap``; use ...", "``get_cmap``, ``register_cmap``"),
    # not a sentence about the API ("``connect`` takes a Config object")
    # ':' is out ("``read_csv``: ... now raises" describes a behaviour change),
    # and so is a bare name with nothing after it (it says nothing)
    m = re.match(r"\s*(?::\w+:)?`{0,2}~?\.?([a-zA-Z_][\w.]*)(?:\(\))?`{0,2}\s*[;,\u2014-]\s*\S", line)
    return m is not None and m.group(1) in (api, api.rsplit(".", 1)[-1])


def _is_subject(line: str, name: str) -> bool:
    """Is ``name`` what the item removes/deprecates/renames, rather than
    something it merely mentions?"""
    n = re.escape(name)
    before = re.compile(_CHANGE_VERB + r"\b(?:\s+\S+){0,3}?\s+\S*" + n + r"\b", re.IGNORECASE)
    after = re.compile(r"\b" + n + r"\S*(?:\s+\S+){0,6}?\s+(?:\S+\s+)?(?:" + _CHANGE_VERB +
                       r"|no\s+longer\s+(?:support|available|accept|exist|export|import|provid|work))\w*\b",
                       re.IGNORECASE)
    return bool(before.search(line) or after.search(line))


# marks advice that isn't a drop-in replacement ("use X with a float dtype")
NO_DROP_IN = " [no drop-in replacement]"
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_STRICT_REPLACEMENT = re.compile(
    r"^(?:use\s+|it'?s\s+still\s+available\s+(?:as|under)\s+|now\s+(?:it'?s\s+)?available\s+(?:as|under)\s+)?"
    r"\[?(?::\w+:)?`{1,2}~?([a-zA-Z_][\w.]*)(?:\(\))?`{1,2}\]?(?:\([^)]*\)|\[[^\]]*\])?"
    r"(?:\s+instead)?\.?$", re.IGNORECASE)


def _table_row_item(old: str, new: str) -> str:
    """One table row ("old | what to do") as release-note text.

    Only a plain "use ``X`` instead" / "still available as ``X``" / bare
    ``X`` cell names a drop-in replacement. A cell with conditions ("Use
    ``np.asarray`` with a float dtype") still marks a removal but gives no
    replacement, so migrate never rewrites one behaviour into another.
    """
    old = old.strip().replace("\\_", "_").replace("\\*", "*")  # reST escapes: unicode\_
    new = new.replace("\\_", "_")
    if re.fullmatch(r"[a-zA-Z_][\w.]*(?:\(\))?", old):
        old = f"``{old}``"  # bare name in the first column
    new = new.strip()
    m = _STRICT_REPLACEMENT.match(new)
    if m:
        return f"{old} replaced by ``{m.group(1)}``"
    plain = re.sub(r"`+", "", new)  # keep the advice, drop the API markers
    return f"{old} removed ({plain}){NO_DROP_IN}" if plain else f"{old} removed"


def parse_changelog_text(text: str, version: str,
                         default_section: str | None = None) -> list[ChangeEntry]:
    """Parse a changelog/release notes body for breaking/deprecated changes.

    Understands section headings (Markdown, reST, bold, ``Heading:``): bullets
    under "Breaking changes" / "Removed" are breaking even without keywords,
    bullets under "Deprecations" are deprecations, and keyword matches under
    benign sections (bug fixes, features, docs) are discarded unless the
    wording is unambiguous.
    """
    entries: list[ChangeEntry] = []
    if not text:
        return entries
    for section, item in _items(text, default_section):
        entries.extend(_classify_item(item, section, version))
    return entries


CHANGELOG_FILENAMES = [
    "CHANGES.rst", "CHANGELOG.rst", "CHANGELOG.md", "CHANGES.md",
    "CHANGES", "CHANGELOG", "HISTORY.rst", "HISTORY.md",
    "RELEASE_NOTES.md", "NEWS.rst", "NEWS.md",
    "CHANGES.txt", "CHANGELOG.txt", "HISTORY.txt", "NEWS", "NEWS.txt", "HISTORY",
]


PROBE_JOBS = 6  # concurrent probes per lookup (check-all runs packages in parallel too)


def _parallel(fn, items: list) -> list:
    """fn over items concurrently, results in the items' order; an exception
    is returned in place of its result. Lookups probe many candidate paths
    that mostly don't exist, and waiting for each 404 in turn was most of a
    cold check's time."""
    if len(items) <= 1:
        out = []
        for it in items:
            try:
                out.append(fn(it))
            except Exception as e:
                out.append(e)
        return out
    from concurrent.futures import ThreadPoolExecutor

    def safe(it):
        try:
            return fn(it)
        except Exception as e:
            return e

    with ThreadPoolExecutor(max_workers=min(PROBE_JOBS, len(items))) as pool:
        return list(pool.map(safe, items))


def fetch_raw_changelog(owner: str, repo: str, token: str | None = None) -> str | None:
    """Try to fetch the raw changelog file from GitHub (cached)."""
    if not valid_repo(owner, repo):
        return None
    key = f"gh-changelog4:{owner}/{repo}"
    cached = cache_get(key)
    if cached is not None:
        return cached or None

    # Public files need no token, and a bad token turns them into 404s, so go
    # anonymous first and only use the token as a fallback (private repos).
    attempts: list[dict[str, str]] = [{}]
    if token:
        attempts.append({"Authorization": f"Bearer {token}"})

    errors = 0
    names = list(CHANGELOG_FILENAMES)
    listed = repo_files(owner, repo)
    if listed is not None:
        names = [n for n in names if n in listed or not _LISTED.search(n)]
    urls = [f"https://raw.githubusercontent.com/{owner}/{repo}/{DEFAULT_BRANCH}/{fname}" for fname in names]
    for headers in attempts:
        # all at once; the first in preference order that exists wins
        results = _parallel(lambda u, h=headers: http_get(u, headers=h, timeout=10, follow_redirects=True), urls)
        for resp in results:
            if isinstance(resp, Exception):
                errors += 1
            elif resp.status_code == 200 and len(resp.text) > 50:
                text = _follow_pointer(owner, repo, resp.text) or resp.text
                cache_set(key, text)
                return text
            elif resp.status_code not in (404, 400):
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
    "docs/releases/v{v}.rst",                # tornado
    "docs/source/release-notes/{v}.rst",     # flake8
    "docs/release-notes/v{v}.rst",
]
DOCS_SERIES_PATTERNS = [
    "doc/build/changelog/changelog_{major}{minor}.rst",  # SQLAlchemy
    "doc/whats_new/v{major}.{minor}.rst",                # scikit-learn
    "docs/whatsnew/{major}.{minor}.rst",                 # astropy
    "doc/whatsnew/{major}.{minor}.rst",
    "docs/source/whatsnew/version{major}.rst",           # IPython
]
DOCS_CHANGELOG_FILES = [
    "docs/changelog.md", "docs/changelog.rst", "docs/changes.rst", "docs/changes.md",
    "docs/history.rst", "docs/history.md", "docs/news.rst", "docs/release-notes.md",
    "docs/release_notes.md", "docs/source/changelog.rst", "doc/changelog.rst",
    "docs/CHANGELOG.md", "doc/source/changelog.rst",
    "doc/en/changelog.rst", "docs/source/history.rst", "docs/versionhistory.rst", "docs/whatsnew.rst",
    "docs/source/whatsnew.rst", "doc/changes.rst", "docs/project/changelog.rst",
]


_CHANGELOG_LINK = re.compile(r"change|release|history|news|what'?s.?new", re.IGNORECASE)
_GITHUB_TREE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+?)/?(?:[?#].*)?$")
_GITHUB_BLOB = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+?)(?:[?#].*)?$")
# a Read the Docs page: /<language>/<version>/<page>.html
_RTD_PAGE = re.compile(r"^(https?://[^/]+/[a-z]{2}(?:[-_][a-zA-Z]+)?/[^/]+/)(.+?)(?:\.html)?/?$")


def changelog_link_candidates(pypi_info: dict) -> list[str]:
    """Raw-text URLs to try for the changelog a project links from PyPI:
    a GitHub file as its raw content; a Sphinx docs page as the source
    Sphinx publishes next to it (``_sources/<page>.rst.txt``)."""
    urls = (pypi_info.get("info") or {}).get("project_urls") or {}
    out: list[str] = []
    for label, url in urls.items():
        if not isinstance(url, str):
            continue
        url = url.strip()
        m = _GITHUB_TREE.match(url)
        if m:
            # a package's folder in a monorepo (google-cloud-python/packages/google-auth):
            # its changelog is there, not at the repository root
            o, r, branch, path = m.groups()
            if not (valid_repo(o, r) and _GH_REF.match(branch) and _safe_path(path)):
                continue
            out += [f"https://raw.githubusercontent.com/{o}/{r}/{branch}/{path.rstrip('/')}/{f}"
                    for f in ("CHANGELOG.md", "CHANGES.md", "CHANGELOG.rst", "CHANGES.rst")]
            continue
        if not _CHANGELOG_LINK.search(label):
            continue
        m = _GITHUB_BLOB.match(url)
        if m:
            o, r, branch, path = m.groups()
            if not (valid_repo(o, r) and _GH_REF.match(branch) and _safe_path(path)):
                continue
            out.append(f"https://raw.githubusercontent.com/{o}/{r}/{branch}/{path}")
            continue
        if "github.com/" in url and url.rstrip("/").endswith("/releases"):
            continue  # GitHub releases: read through the API or the feed
        m = _RTD_PAGE.match(url)
        if m:
            root, page = m.groups()
            page = page.rstrip("/") or "index"
            out += [f"{root}_sources/{page}.rst.txt", f"{root}_sources/{page}.md.txt", f"{root}_sources/{page}.txt"]
        elif url.endswith(".html"):
            base, _, page = url.rpartition("/")
            stem = page[:-5]
            out += [f"{base}/_sources/{stem}.rst.txt", f"{base}/_sources/{stem}.md.txt"]
        elif re.search(r"\.(md|rst|txt)$", url):
            out.append(url)
    # links anyone can publish: only https to a public host (http.check_url
    # enforces the same on every request and redirect)
    return [u for u in dict.fromkeys(out) if u.startswith("https://")]


def fetch_linked_changelog(package: str, pypi_info: dict) -> str | None:
    """The changelog the project links from PyPI, as text (cached)."""
    key = f"linked-changelog2:{package}"
    cached = cache_get(key)
    if cached is not None:
        return cached or None
    errors = 0
    for url in changelog_link_candidates(pypi_info):
        try:
            resp = http_get(url, timeout=10, follow_redirects=True)
        except Exception:
            errors += 1
            continue
        ctype = resp.headers.get("content-type", "")
        if resp.status_code == 200 and len(resp.text) > 50 and "html" not in ctype:
            cache_set(key, resp.text)
            return resp.text
        if resp.status_code not in (404, 400, 403):
            errors += 1
    if not errors:
        cache_set(key, "")
    return None


# GitHub's raw host resolves HEAD to the repository's default branch, whatever
# it is called (main, master, trunk...)
DEFAULT_BRANCH = "HEAD"

# set from collect_changes: lets a lookup list a repository's files once
# instead of probing for each candidate path
_github_token: str | None = None
_LISTED = re.compile(r"(?i)(change|histor|news|release|what'?s.?new|migrat|upgrad|api_changes)")


_POINTER_PATH = re.compile(r"(?:github\.com/[^/\s]+/[^/\s]+/blob/[^/\s]+/|(?<![\w/:.]))"
                           r"((?:[\w.-]+/)*[\w.-]+\.(?:rst|md|txt))\b")


def _follow_pointer(owner: str, repo: str, text: str) -> str | None:
    """A short changelog that only says where the real one is ("The
    changelog is in doc/en/changelog.rst"): that file, if it's in the repo."""
    if len(text) > 1000:
        return None
    for path in _POINTER_PATH.findall(text):
        if path.lower().startswith(("readme", "license")):
            continue
        try:
            target = _raw(owner, repo, path)
        except Exception:
            continue
        if target and len(target) > len(text):
            return target
    return None


def repo_files(owner: str, repo: str) -> set | None:
    """The repository's changelog-like files (paths that look like release
    notes), from one GitHub API call, when a token allows it (cached). None
    when unknown: callers then probe for each candidate."""
    if not _github_token or not valid_repo(owner, repo):
        return None
    key = f"gh-files:{owner}/{repo}"
    cached = cache_get(key)
    if cached is not None:
        return set(cached) if isinstance(cached, list) else None
    try:
        resp = http_get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
                        headers={"Accept": "application/vnd.github+json",
                                 "Authorization": f"Bearer {_github_token}"}, timeout=20)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get("truncated"):
        cache_set(key, "truncated")  # too big to list: probe instead
        return None
    files = sorted(t["path"] for t in data.get("tree", [])
                   if t.get("type") == "blob" and _LISTED.search(t.get("path", "")))
    cache_set(key, files)
    return set(files)


def _raw(owner: str, repo: str, path: str, branch: str = DEFAULT_BRANCH) -> str | None:
    """Fetch a public file from GitHub; None if it doesn't exist. Raises on
    network errors so callers can report them."""
    if not (valid_repo(owner, repo) and _GH_REF.match(branch) and _safe_path(path)):
        return None  # names from metadata or release notes: never odd URL paths
    listed = repo_files(owner, repo)
    if listed is not None and _LISTED.search(path) and path not in listed:
        return None  # the listing says it isn't there: no request
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    resp = http_get(url, timeout=10, follow_redirects=True)
    if resp.status_code == 200 and len(resp.text) > 50:
        return resp.text
    if resp.status_code in (404, 400):
        return None
    resp.raise_for_status()
    return None


def _versions_between(pypi_info: dict, current: str, target: str) -> list[str]:
    """Final releases in (current, target], oldest first."""
    from packaging.version import InvalidVersion, Version
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


def _docs_versions(pypi_info: dict, current: str, target: str, lookback) -> list[str]:
    """Versions whose per-version docs notes to read: the upgrade range, plus
    (with lookback) the x.y.0 releases of the current major series, where
    deprecations, their replacements and their removal dates are usually
    announced. ``lookback`` True reads them all, a number the last that many."""
    versions = _versions_between(pypi_info, current, target)
    if not lookback:
        return versions
    from packaging.version import InvalidVersion, Version
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
    earlier = sorted(earlier)
    if lookback is not True and isinstance(lookback, int):
        earlier = earlier[-lookback:]
    return [str(v) for v in earlier] + [v for v in versions if v not in {str(e) for e in earlier}]


def _three_part(v: str) -> str:
    parts = v.split(".")
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts[:3]) if all(p.isdigit() for p in parts[:3]) else v


def _short_version(v: str) -> str:
    """4.0.0 -> 4.0 (Django names x.y.0 notes 'x.y'); other versions unchanged."""
    parts = v.split(".")
    return ".".join(parts[:2]) if len(parts) >= 3 and parts[2] == "0" else v


def fetch_docs_release_notes(owner: str, repo: str, versions: list[str],
                             max_files: int = 40, seen: set | None = None) -> list[ChangeEntry] | None:
    """Release notes kept in the project's docs instead of a root CHANGES file.

    Returns None when the project has no recognised docs layout.
    """
    if not versions:
        return None
    key = f"docs-layout7:{owner}/{repo}"
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
        found = _parallel(lambda c: _raw(owner, repo, c[2]), candidates)
        errors = [r for r in found if isinstance(r, Exception)]
        for (kind, pattern, _path), text in zip(candidates, found, strict=True):
            if text is not None and not isinstance(text, Exception):
                layout = f"{kind}|{pattern}"
                break
        if not layout and errors:
            raise errors[0]  # a failed probe isn't proof there's no layout: don't cache it
        cache_set(key, layout)
    if not layout:
        return None

    kind, pattern = layout.split("|", 1)
    entries: list[ChangeEntry] = []
    if kind == "version":
        get = _prefetch(owner, repo, [pattern.format(v=form) for v in versions[-max_files:]
                                      for form in dict.fromkeys([_three_part(v), _short_version(v)])])
        for v in versions[-max_files:]:
            for form in dict.fromkeys([_three_part(v), _short_version(v)]):
                text = get(pattern.format(v=form))
                if text:
                    entries.extend(parse_changelog_text(text, v))
                    if seen is not None:
                        seen.add(v)
                    break
    elif kind == "series":
        from packaging.version import Version
        series = sorted({(Version(v).major, Version(v).minor) for v in versions})
        for major, minor in series[-max_files:]:
            text = _cached_raw(owner, repo, pattern.format(major=major, minor=minor))
            if text:
                entries.extend(parse_sectioned_changelog(text, repo, seen))
    else:
        text = _cached_raw(owner, repo, pattern)
        if text:
            entries.extend(parse_sectioned_changelog(text, repo, seen))
    return entries


# Migration guides: per series ({major}{minor} / {major}_{minor}), per
# version with a fixed section ({v}, e.g. Matplotlib's removals.rst), or one
# guide for a major-version jump.
MIGRATION_SERIES = [
    "doc/source/numpy_{major}_{minor}_migration_guide.rst",   # NumPy 2.0
    "doc/build/changelog/migration_{major}{minor}.rst",        # SQLAlchemy 2.0
]
MIGRATION_PER_VERSION = [
    ("doc/api/prev_api_changes/api_changes_{v}/removals.rst", "removed"),        # Matplotlib
    ("doc/api/prev_api_changes/api_changes_{v}/deprecations.rst", "deprecated"),
]
MIGRATION_MAJOR = ["docs/migration.md", "docs/migration.rst", "docs/migration_guide.md",
                   "MIGRATION.md", "docs/upgrading.md"]


def fetch_migration_guides(owner: str, repo: str, current: str, target: str,
                           versions: list[str]) -> list[ChangeEntry]:
    """Changes described in migration guides rather than release notes.
    Each guide's entries are dated to the release it is about."""
    from packaging.version import InvalidVersion, Version
    try:
        cur, tgt = Version(current), Version(target)
    except InvalidVersion:
        return []
    entries: list[ChangeEntry] = []
    series = sorted({(Version(v).major, Version(v).minor) for v in versions})
    wanted = [p.format(major=ma, minor=mi) for ma, mi in series for p in MIGRATION_SERIES]
    wanted += [p.format(v=_three_part(v)) for v in versions[-20:] for p, _k in MIGRATION_PER_VERSION]
    if tgt.major > cur.major:
        wanted += MIGRATION_MAJOR
    get = _prefetch(owner, repo, wanted)
    for major, minor in series:
        for pattern in MIGRATION_SERIES:
            text = get(pattern.format(major=major, minor=minor))
            if text:
                entries.extend(parse_changelog_text(text, f"{major}.{minor}.0"))
    for v in versions[-20:]:
        for pattern, kind in MIGRATION_PER_VERSION:
            text = get(pattern.format(v=_three_part(v)))
            if text:
                entries.extend(parse_changelog_text(text, v, default_section=kind))
    if tgt.major > cur.major:
        # an unversioned guide describes the major jump ("Pydantic V1 -> V2")
        first_of_major = next((v for v in versions if Version(v).major == tgt.major), f"{tgt.major}.0.0")
        for path in MIGRATION_MAJOR:
            text = get(path)
            if text:
                entries.extend(parse_changelog_text(text, first_of_major))
                break
    return entries


def _prefetch(owner: str, repo: str, paths: list[str]):
    """Fetch these repository files concurrently; returns a lookup that
    gives each one's text (None if missing) and raises a fetch error only
    when that file is asked for, as a sequential fetch would have."""
    paths = list(dict.fromkeys(paths))
    got = dict(zip(paths, _parallel(lambda path: _cached_raw(owner, repo, path), paths), strict=True))

    def get(path: str) -> str | None:
        if path not in got:
            return _cached_raw(owner, repo, path)
        value = got[path]
        if isinstance(value, Exception):
            raise value
        return value
    return get


def _cached_raw(owner: str, repo: str, path: str) -> str | None:
    key = f"gh-raw:{owner}/{repo}/{path}"
    cached = cache_get(key)
    if cached is not None:
        return cached or None
    text = _raw(owner, repo, path)
    cache_set(key, text or "")
    return text


class _Shift:
    """A named-header match whose version is its second group, presented
    like a plain one (version in group 1)."""

    def __init__(self, m):
        self._m = m

    def group(self, i):
        return self._m.group(i + 1) if i else self._m.group(0)

    def start(self):
        return self._m.start()

    def end(self):
        return self._m.end()


def parse_sectioned_changelog(text: str, package: str, seen: set | None = None) -> list[ChangeEntry]:
    """Parse a full changelog file that's divided by version headers.
    ``seen`` collects the version of every section found, changes or not."""
    entries: list[ChangeEntry] = []

    # version headers: "Version 3.0.0", "## 3.0.0", "3.0.0 (2024-01-01)", and
    # Keep a Changelog "## [3.0.0] - 2024-01-01", linked "## [3.0.0](url)",
    # "# Release 3.0.0 (date)", "pytest 8.3.0 (2024-07-20)", "**3.0.0**"
    name = re.escape(package).replace(r"\-", "[-_]")
    ver = r"(\d+\.\d+(?:\.\d+)*(?:(?:a|b|rc|alpha|beta|\.?post|\.?dev)\.?\d*)?)"
    version_header = re.compile(
        # " 4.3.0 (date)": towncrier centres reST titles with one space
        r"^ ?(?:#{1,4}\s+)?(?:\*\*|`)?\[?(?:(?:version|release|revision)\s+|" + name + r"\s+)?v?"
        + ver + r"\b(?!\.\d)",
        re.IGNORECASE | re.MULTILINE,
    )
    # "- 0.10.0: Add support for ..." (tabulate): a version as a list item
    item_header = re.compile(r"^[-*]\s+v?" + ver + r":", re.MULTILINE)
    # "# Apache Arrow 26.0.1 (2026-11-03)": a display name before the version
    # in a Markdown heading, the version then dated or alone on the line
    named_header = re.compile(
        r"^#{1,4}\s+((?:[A-Za-z][\w.+-]*\s+){1,3})v?" + ver + r"\b(?!\.\d)\s*(?:\(|-|\u2013|\u2014|$)",
        re.MULTILINE,
    )
    found: dict[int, re.Match[str] | _Shift] = {m.start(): m for m in version_header.finditer(text)}
    for m in item_header.finditer(text):
        found.setdefault(m.start(), m)
    for m in named_header.finditer(text):
        if m.start() not in found and not re.search(r"\bpython\b", m.group(1), re.IGNORECASE):
            found[m.start()] = _Shift(m)

    splits = [found[k] for k in sorted(found)]
    if not splits:
        return entries

    for i, heading in enumerate(splits):
        ver = heading.group(1)
        if seen is not None:
            seen.add(ver)
        start = heading.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        section = text[start:end]
        section_entries = parse_changelog_text(section, ver)
        entries.extend(section_entries)

    return entries


_TAG_VERSION = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+)+(?:[._-]?(?:a|b|rc|alpha|beta|post|dev)\.?\d*)?)(?![\w])",
                          re.IGNORECASE)


def version_from_tag(tag: str, package: str = "") -> str | None:
    """Pull a PEP 440 version out of a release tag.

    Handles ``v2.0.1``, ``2.0``, ``flask-3.0.1``, ``release-2.0``,
    ``pkg==1.2`` and ``Version 4.0``. Returns None for tags with no version.
    """
    from packaging.version import InvalidVersion, Version
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
    entries: list[ChangeEntry]
    sources_ok: list[str]
    sources_failed: list[str]
    skipped_unversioned: int = 0
    # every parsed entry up to the target, not just those in the upgrade
    # range: a deprecation announced before your version still names the
    # replacement for an API removed inside the range
    history: list[ChangeEntry] = field(default_factory=list)
    # versions in the upgrade range whose release notes were read (with or
    # without breaking changes): none means the range's notes weren't found
    versions_read: list[str] = field(default_factory=list)

    @property
    def checked(self) -> bool:
        """True if at least one source was actually read."""
        return bool(self.sources_ok)


def collect_changes(package: str, current_version: str, target_version: str,
                    github_token: str | None = None, lookback=3) -> ChangeReport:
    """Get breaking/deprecated changes between two versions, reporting which
    sources worked and which failed instead of silently returning nothing.

    ``lookback`` also reads the notes of feature releases before
    ``current_version`` (True: all of the current major series; a number:
    the last that many), for deprecations whose announced removal falls in
    the upgrade and for the replacements they name."""
    global _github_token
    github_token = github_token_for(github_token)
    if github_token:
        _github_token = github_token
    ok: list[str] = []
    failed: list[str] = []
    all_entries: list[ChangeEntry] = []
    versions_seen: set = set()  # versions whose notes were read, changes or not

    try:
        pypi_info = get_pypi_info(package)
    except Exception as e:
        return ChangeReport([], [], [f"PyPI metadata ({_short_err(e)})"])

    repo_info = extract_github_repo(pypi_info)
    in_range = _versions_between(pypi_info, current_version, target_version)
    # the sources don't depend on each other: fetch them together, then
    # combine them in a fixed order
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        if repo_info:
            owner, repo = repo_info
            f_releases = pool.submit(fetch_github_releases, owner, repo, token=github_token)
            f_docs = pool.submit(fetch_docs_release_notes, owner, repo,
                                 _docs_versions(pypi_info, current_version, target_version, lookback),
                                 seen=versions_seen)
            f_guides = pool.submit(fetch_migration_guides, owner, repo, current_version, target_version,
                                   in_range)
        f_desc = pool.submit(get_pypi_version_info, package, target_version)

        if repo_info:
            try:
                releases = f_releases.result()
                if releases:
                    ok.append("GitHub releases")
                for rel in releases:
                    tag = rel.get("tag_name", "") or rel.get("name", "")
                    ver = version_from_tag(tag, package) or version_from_tag(rel.get("name", ""), package)
                    body = rel.get("body", "") or ""
                    if ver is None:
                        # sectioned bodies still carry their own version headers
                        all_entries.extend(parse_sectioned_changelog(body, package, versions_seen))
                        continue
                    versions_seen.add(ver)
                    all_entries.extend(parse_changelog_text(body, ver))
            except Exception as e:
                failed.append(f"GitHub releases ({_short_err(e)})")

            if not all_entries or not _covers(versions_seen, in_range, current_version, target_version):
                # no releases on GitHub, or not for every release in this
                # upgrade (many projects tag without writing release notes,
                # or publish only some releases there)
                try:
                    raw = fetch_raw_changelog(owner, repo, token=github_token)
                    if raw:
                        ok.append("changelog file")
                        all_entries.extend(parse_sectioned_changelog(raw, package, versions_seen))
                except Exception as e:
                    failed.append(f"changelog file ({_short_err(e)})")

            # release notes in docs/ are often far more detailed than the root
            # CHANGES file (Pillow, NumPy, pandas), so read them too
            try:
                docs = f_docs.result()
                if docs is not None:
                    ok.append("docs release notes")
                    all_entries.extend(docs)
            except Exception as e:
                failed.append(f"docs release notes ({_short_err(e)})")
            try:
                guides = f_guides.result()
                if guides:
                    ok.append("migration guide")
                    all_entries.extend(guides)
            except Exception as e:
                failed.append(f"migration guide ({_short_err(e)})")

        if not all_entries or not _covers(versions_seen, in_range, current_version, target_version):
            # GitHub didn't cover this upgrade: the changelog the project links from PyPI
            try:
                linked = fetch_linked_changelog(package, pypi_info)
                if linked:
                    entries = parse_sectioned_changelog(linked, package, versions_seen)
                    if entries:
                        ok.append("linked changelog")
                        all_entries.extend(entries)
            except Exception as e:
                failed.append(f"linked changelog ({_short_err(e)})")

        try:
            ver_info = f_desc.result()
            desc = ver_info.get("info", {}).get("description", "")
            ok.append("PyPI description")
            if desc:
                all_entries.extend(parse_sectioned_changelog(desc, package, versions_seen))
        except Exception as e:
            failed.append(f"PyPI description ({_short_err(e)})")

    from packaging.version import InvalidVersion, Version

    try:
        v_current = Version(current_version)
        v_target = Version(target_version)
    except InvalidVersion:
        failed.append(f"version range ({current_version} -> {target_version} not PEP 440)")
        return ChangeReport([], ok, failed)

    all_entries = _promote_swept_deprecations(all_entries, v_current, v_target)
    all_entries = _promote_scheduled_removals(all_entries, v_current, v_target)

    filtered: list[ChangeEntry] = []
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
    return ChangeReport(filtered, ok, failed, skipped, history,
                        sorted(v for v in versions_seen if _in_range(v, v_current, v_target)))


def _in_range(v: str, lo, hi) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        return lo < Version(v) <= hi
    except InvalidVersion:
        return False


def _seen_in_range(seen: set, current: str, target: str) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        lo, hi = Version(current), Version(target)
    except InvalidVersion:
        return bool(seen)
    return any(_in_range(v, lo, hi) for v in seen)


def _covers(seen: set, wanted: list[str], current: str, target: str) -> bool:
    """Were the notes of every release in the upgrade read? Without a list
    of releases, any release in the range counts."""
    if not wanted:
        return _seen_in_range(seen, current, target)
    from packaging.version import InvalidVersion, Version

    def norm(v: str):
        try:
            return Version(v)
        except InvalidVersion:
            return v
    have = {norm(v) for v in seen}
    return all(norm(v) in have for v in wanted)


def _has_in_range(entries: list[ChangeEntry], current: str, target: str) -> bool:
    from packaging.version import InvalidVersion, Version
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


_DEPRECATES = re.compile(r"\bdeprecat(?!ion\s+warnings?\b)\w*", re.IGNORECASE)


def _deprecates(text: str, api: str) -> bool:
    """Is ``api`` deprecated by the sentence that names it? Not when the
    deprecation is in another sentence, or is only "a deprecation warning"
    about some behaviour ("``iri_to_uri`` shows a deprecation warning when
    passing bytes")."""
    name = api.rsplit(".", 1)[-1]
    for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z:`*(])", text):
        if re.search(rf"\b{re.escape(name)}\b", sent) and _DEPRECATES.search(sent):
            return True
    return False


def _promote_swept_deprecations(entries: list[ChangeEntry], v_current, v_target) -> list[ChangeEntry]:
    """Many projects remove deprecated APIs with a single line ("Remove
    previously deprecated code.") instead of listing them. When such a sweep
    falls inside the upgrade range, every API deprecated before it is gone."""
    from packaging.version import InvalidVersion, Version

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
        if e.kind != "deprecated" or e.api == "unknown" or e.confidence == "low":
            continue  # low: the item only mentions it, it isn't what was deprecated
        if not _deprecates(e.description, e.api) and not (e.context and _DEPRECATES.search(e.context)):
            continue  # "``f`` supports scrypt. Plain hash methods are deprecated"
        try:
            v_dep = Version(e.version)
        except InvalidVersion:
            continue
        # a sweep removes what its own deprecation window covers: the same
        # major series, or the one before for an x.0 release. Projects don't
        # keep deprecated code for decades ("Everything in ``wrappers`` is
        # deprecated" in Werkzeug 0.15 was gone long before 2.1's sweep)
        later = [v for v in sweeps if v > v_dep
                 and v_dep.major >= v.major - (1 if v.minor == 0 else 0)]
        if later:
            sweep_v = min(later)
            out.append(ChangeEntry(
                kind="removed", api=e.api, version=str(sweep_v),
                confidence="low" if e.confidence == "low" else "medium",
                description=f"Removed in {sweep_v} (deprecated in {e.version}: {clip(e.description, 200)})",
                replacement=e.replacement, exceptions=list(e.exceptions),
            ))
    return out


def _promote_scheduled_removals(entries: list[ChangeEntry], v_current, v_target) -> list[ChangeEntry]:
    """A deprecation that names its removal ("deprecated in 1.0 and will be
    removed in 1.2") is a removal in that version, whether or not that
    release's notes mention it again; many projects don't."""
    from packaging.version import InvalidVersion, Version

    out = list(entries)
    for e in entries:
        if e.kind != "deprecated" or not e.removed_in or e.api == "unknown":
            continue
        try:
            v_rm, v_dep = Version(e.removed_in), Version(e.version)
        except InvalidVersion:
            continue
        if v_dep < v_rm and v_current < v_rm <= v_target:
            out.append(ChangeEntry(
                kind="removed", api=e.api, version=_three_part(e.removed_in),
                confidence=e.confidence,
                description=f"Removed in {e.removed_in} (deprecated in {e.version}: {clip(e.description, 200)})",
                replacement=e.replacement, exceptions=list(e.exceptions),
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
                        github_token: str | None = None) -> list[ChangeEntry]:
    """Get all breaking/deprecated changes between two versions."""
    return collect_changes(package, current_version, target_version, github_token).entries
