"""Release-note formats and sources found on the most-used PyPI packages."""

import pytest

from depshift import changelog as cl
from depshift.changelog import (changelog_link_candidates, html_to_text, parse_releases_feed,
                                parse_sectioned_changelog)


def versions(text, package="pkg"):
    seen = set()
    parse_sectioned_changelog(text, package, seen)
    return seen


@pytest.mark.parametrize("text,expected", [
    # Keep a Changelog (rich, tomlkit)
    ("# Changelog\n\n## [15.0.0] - 2026-04-12\n\n- Removed `Foo`\n\n## [14.3.4] - 2026-04-11\n", {"15.0.0", "14.3.4"}),
    # linked version (attrs)
    ("## [26.1.0](https://github.com/python-attrs/attrs/tree/26.1.0) - 2026-03-19\n\n- x\n", {"26.1.0"}),
    # "Release" prefix and pre-releases (typing-extensions)
    ("# Release 4.16.0 (July 2, 2026)\n\n- a\n\n# Release 4.16.0rc2 (June 25, 2026)\n", {"4.16.0", "4.16.0rc2"}),
    # reST link title (pyjwt)
    ("`v2.15.1 <https://github.com/jpadilla/pyjwt/compare/2.15.0...2.15.1>`__\n"
     "-----------------------------------------------------------------------\n\n- a\n", {"2.15.1"}),
    # "Revision" (pyasn1)
    ("Revision 0.6.5, released 26-10-2026\n---------------------------------\n\n- a\n", {"0.6.5"}),
    # towncrier's centred reST title (platformdirs, filelock)
    ("*********************\n 4.14.8 (2026-11-20)\n*********************\n\n- a\n", {"4.14.8"}),
    # package-name prefix (pytest)
    ("pytest 8.3.0 (2024-07-20)\n=========================\n\n- a\n", {"8.3.0"}),
    # a display name in a Markdown heading (pyarrow)
    ("# Apache Arrow 26.0.1 (2026-11-03 00:00:00+00:00)\n## Bug Fixes\n- a\n", {"26.0.1"}),
])
def test_version_headings(text, expected):
    package = "pytest" if text.startswith("pytest") else "pkg"
    assert versions(text, package) == expected


def test_python_versions_are_not_release_headings():
    text = "## [2.0.0] - 2026-01-01\n\n## Python 3.8 support dropped\n\n- Removed ``old``\n"
    assert versions(text) == {"2.0.0"}


def test_named_heading_keeps_its_items():
    text = "# Apache Arrow 26.0.1 (2026-11-03)\n\n- ``pyarrow.foo`` has been removed.\n"
    (e,) = parse_sectioned_changelog(text, "pyarrow")
    assert e.version == "26.0.1" and e.kind == "removed"


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Repository/1/v2.0.0</id>
    <link rel="alternate" type="text/html" href="https://github.com/o/r/releases/tag/v2.0.0"/>
    <title>v2.0.0</title>
    <content type="html">&lt;h2&gt;Breaking changes&lt;/h2&gt;
&lt;ul&gt;
&lt;li&gt;Removed &lt;code&gt;old_func&lt;/code&gt;; use &lt;code&gt;new_func&lt;/code&gt; instead.&lt;/li&gt;
&lt;/ul&gt;</content>
  </entry>
</feed>"""


def test_releases_feed():
    (rel,) = parse_releases_feed(FEED)
    assert rel["tag_name"] == "v2.0.0"
    assert "## Breaking changes" in rel["body"]
    assert "- Removed `old_func`; use `new_func` instead." in rel["body"]
    (e,) = [e for e in cl.parse_changelog_text(rel["body"], "2.0.0") if e.api == "old_func"]
    assert e.kind == "removed" and e.replacement == "new_func"


def test_releases_feed_used_when_api_is_rate_limited(monkeypatch):
    class Resp:
        def __init__(self, status, text=""):
            self.status_code, self.text = status, text

        def raise_for_status(self):
            raise AssertionError("must not raise when the feed works")

        def json(self):
            return []

    def fake_get(url, **kw):
        return Resp(200, FEED) if url.endswith("releases.atom") else Resp(403)

    monkeypatch.setattr(cl, "http_get", fake_get)
    rels = cl.fetch_github_releases("o", "r")
    assert [r["tag_name"] for r in rels] == ["v2.0.0"]


def test_html_to_text():
    assert html_to_text("<p>a &amp; b</p><ul><li>x <code>y</code></li></ul>") == "a & b\n\n- x `y`"


def test_changelog_links():
    info = {"info": {"project_urls": {
        "Changelog": "https://github.com/python/typing_extensions/blob/main/CHANGELOG.md",
        "Docs changes": "https://anyio.readthedocs.io/en/stable/versionhistory.html",
        "Releases": "https://github.com/o/r/releases",
        "Homepage": "https://github.com/googleapis/google-cloud-python/tree/main/packages/google-auth",
        "Source": "https://github.com/o/r",
    }}}
    urls = changelog_link_candidates(info)
    assert "https://raw.githubusercontent.com/python/typing_extensions/main/CHANGELOG.md" in urls
    assert "https://anyio.readthedocs.io/en/stable/_sources/versionhistory.rst.txt" in urls
    assert ("https://raw.githubusercontent.com/googleapis/google-cloud-python/main/packages/google-auth/"
            "CHANGELOG.md") in urls
    assert not any(u.endswith("/releases") or u == "https://github.com/o/r" for u in urls)


def test_versions_read_reported(monkeypatch):
    text = "## [2.0.0] - 2026-01-01\n\n- Added things.\n\n## [1.0.0] - 2025-01-01\n\n- First.\n"
    monkeypatch.setattr(cl, "get_pypi_info", lambda p: {"info": {"project_urls": {"Source": "https://github.com/o/r"}},
                                                         "releases": {"1.0.0": [{}], "2.0.0": [{}]}})
    monkeypatch.setattr(cl, "fetch_github_releases", lambda *a, **k: [])
    monkeypatch.setattr(cl, "fetch_raw_changelog", lambda *a, **k: text)
    monkeypatch.setattr(cl, "fetch_docs_release_notes", lambda *a, **k: None)
    monkeypatch.setattr(cl, "fetch_migration_guides", lambda *a, **k: [])
    monkeypatch.setattr(cl, "get_pypi_version_info", lambda *a: {"info": {"description": ""}})
    r = cl.collect_changes("pkg", "1.0.0", "2.0.0")
    assert r.versions_read == ["2.0.0"] and r.entries == []  # read, and nothing breaks


# ── concurrent probing ────────────────────────────────────────────────────────

def test_parallel_keeps_order_and_returns_errors():
    def f(x):
        if x == 2:
            raise ValueError("boom")
        return x * 10
    out = cl._parallel(f, [1, 2, 3])
    assert out[0] == 10 and isinstance(out[1], ValueError) and out[2] == 30


def test_prefetch_raises_only_for_the_file_asked_for(monkeypatch):
    def fake(owner, repo, path):
        if path == "bad":
            raise RuntimeError("network")
        return None if path == "missing" else f"text of {path}"
    monkeypatch.setattr(cl, "_cached_raw", fake)
    get = cl._prefetch("o", "r", ["good", "missing", "bad"])
    assert get("good") == "text of good" and get("missing") is None
    with pytest.raises(RuntimeError):
        get("bad")


def test_first_existing_changelog_wins_in_preference_order(monkeypatch):
    class Resp:
        def __init__(self, status, text=""):
            self.status_code, self.text = status, text
    hits = {"master/CHANGES.rst": "x" * 60, "main/NEWS": "y" * 60}

    def fake_get(url, **kw):
        tail = url.split("/o/r/", 1)[1]
        return Resp(200, hits[tail]) if tail in hits else Resp(404)

    monkeypatch.setattr(cl, "http_get", fake_get)
    # main/ is preferred over master/, whatever finishes first
    assert cl.fetch_raw_changelog("o", "r") == "y" * 60


def test_failed_docs_probe_is_not_cached_as_no_layout(monkeypatch):
    calls = []

    def flaky(owner, repo, path, branch="main"):
        calls.append(path)
        raise RuntimeError("HTTP 500")
    monkeypatch.setattr(cl, "_raw", flaky)
    with pytest.raises(RuntimeError):
        cl.fetch_docs_release_notes("o", "r", ["2.0.0"])
    assert cl.cache_get("docs-layout5:o/r") is None
