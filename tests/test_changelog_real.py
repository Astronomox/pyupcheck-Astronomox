"""Release-note lookup end to end on real release notes (tests/fixtures/
release_notes), served by a fake web: every source pyupcheck reads (GitHub
releases, their Atom feed, a changelog file, docs release notes, the
changelog linked from PyPI, the PyPI description) and how each failure is
reported. Nothing here touches the network."""

import json
import re
from pathlib import Path

import pytest

from depshift import changelog as cl

NOTES = Path(__file__).parent / "fixtures" / "release_notes"
FLASK = (NOTES / "flask_CHANGES.rst").read_text()
NUMPY = (NOTES / "numpy_2.0.0-notes.rst").read_text()
REQUESTS = (NOTES / "requests_HISTORY.md").read_text()


def section(text: str, version: str) -> str:
    """One version's part of a sectioned changelog, without its heading."""
    m = re.search(rf"^Version {re.escape(version)}\n-+\n(.*?)(?=^Version |\Z)", text, re.M | re.S)
    assert m, version
    return m.group(1)


class Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")


class Web:
    """URL -> response; anything else is a 404. Every request is recorded."""

    def __init__(self, monkeypatch):
        self.pages: dict = {}
        self.requests: list = []
        monkeypatch.setattr(cl, "http_get", self.get)
        monkeypatch.setattr(cl, "_github_token", None)

    def get(self, url, headers=None, **kw):
        self.requests.append((url, dict(headers or {})))
        page = self.pages.get(url)
        if callable(page):
            page = page(headers or {})
        if isinstance(page, Exception):
            raise page
        return page if isinstance(page, Resp) else Resp(404)

    def serve(self, url, body="", status=200, **kw):
        self.pages[url] = Resp(status, body if isinstance(body, str) else json.dumps(body), **kw)


def pypi(name, versions, urls=None, description=""):
    return {"info": {"name": name, "version": versions[-1], "project_urls": urls or {}, "home_page": None,
                     "description": description},
            "releases": {v: [{"upload_time": "2024-01-01T00:00:00", "yanked": False}] for v in versions}}


def serve_pypi(web, name, info):
    web.serve(cl.PYPI_API.format(package=name), info)
    for v in info["releases"]:
        web.serve(cl.PYPI_VERSION_API.format(package=name, version=v), {"info": info["info"]})


RAW = "https://raw.githubusercontent.com/{}/HEAD/{}"
RELEASES = "https://api.github.com/repos/{}/releases?per_page=50&page={}"
FLASK_VERSIONS = ["2.2.5", "2.3.0", "2.3.1", "2.3.2", "2.3.3", "3.0.0", "3.0.1", "3.0.2", "3.0.3"]


@pytest.fixture
def web(monkeypatch):
    return Web(monkeypatch)


# ── parsing real files ────────────────────────────────────────────────────────

def test_flask_changelog_names_what_moved_and_where():
    entries = cl.parse_sectioned_changelog(FLASK, "flask")
    by_api = {(e.version, e.api): e for e in entries}
    markup = by_api[("2.3.0", "Markup")]
    assert markup.kind == "deprecated" and markup.replacement == "markupsafe.Markup"
    assert by_api[("2.3.0", "escape")].replacement == "markupsafe.escape"
    assert by_api[("2.3.0", "json.JSONEncoder")].kind == "removed"
    assert {e.version for e in entries} <= set(FLASK_VERSIONS)  # every entry dated to its release


def test_numpy_release_notes_find_the_removed_aliases():
    entries = cl.parse_changelog_text(NUMPY, "2.0.0")
    removed = {e.api for e in entries if e.kind == "removed"}
    assert {"np.float_", "np.cast"} <= removed
    float_ = next(e for e in entries if e.api == "np.float_")
    assert float_.replacement == "np.float64"


def test_requests_history_is_read_by_version():
    entries = cl.parse_sectioned_changelog(REQUESTS, "requests")
    assert entries and {e.version for e in entries} <= {"2.30.0", "2.31.0", "2.32.0", "2.32.1", "2.32.2",
                                                       "2.32.3", "2.32.4", "2.32.5", "2.34.2"}


# ── where release notes are found ─────────────────────────────────────────────

def test_github_releases_then_the_changelog_file(web):
    serve_pypi(web, "flask", pypi("flask", FLASK_VERSIONS, {"Source": "https://github.com/pallets/flask/"}))
    web.serve(RELEASES.format("pallets/flask", 1), [
        {"tag_name": "3.0.0", "name": "3.0.0", "body": section(FLASK, "3.0.0")},
        {"tag_name": "nightly", "name": "nightly", "body": ""},  # no version: read as a sectioned body
    ])
    web.serve(RAW.format("pallets/flask", "CHANGES.rst"), FLASK)
    report = cl.collect_changes("flask", "2.2.5", "3.0.0", lookback=False)
    assert "GitHub releases" in report.sources_ok and "changelog file" in report.sources_ok
    assert report.sources_failed == [] and report.checked
    assert {"2.3.0", "3.0.0"} <= set(report.versions_read)
    apis = {(e.kind, e.api) for e in report.entries}
    assert ("deprecated", "Markup") in apis and ("removed", "json.JSONEncoder") in apis
    assert all(e.version in FLASK_VERSIONS[1:6] for e in report.entries)  # (2.2.5, 3.0.0] only


def test_rate_limited_api_falls_back_to_the_releases_feed(web):
    serve_pypi(web, "flask", pypi("flask", FLASK_VERSIONS, {"Source": "https://github.com/pallets/flask"}))
    web.serve(RELEASES.format("pallets/flask", 1), {"message": "API rate limit exceeded"}, status=403)
    body = section(FLASK, "2.3.0").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    web.serve(cl.GITHUB_RELEASES_FEED.format(owner="pallets", repo="flask"),
              '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>2.3.0</title>'
              '<link href="https://github.com/pallets/flask/releases/tag/2.3.0"/>'
              f'<content type="html">&lt;pre&gt;{body}&lt;/pre&gt;</content></entry></feed>')
    report = cl.collect_changes("flask", "2.2.5", "2.3.0", lookback=False)
    assert "GitHub releases" in report.sources_ok
    assert any(e.api == "Markup" for e in report.entries)


def test_rate_limited_without_a_feed_is_a_failure_not_silence(web):
    serve_pypi(web, "flask", pypi("flask", FLASK_VERSIONS, {"Source": "https://github.com/pallets/flask"}))
    web.serve(RELEASES.format("pallets/flask", 1), "{}", status=429)
    report = cl.collect_changes("flask", "2.2.5", "2.3.0", lookback=False)
    assert any(f.startswith("GitHub releases") for f in report.sources_failed)


def test_a_bad_token_retries_anonymously(web, monkeypatch):
    serve_pypi(web, "flask", pypi("flask", FLASK_VERSIONS, {"Source": "https://github.com/pallets/flask"}))
    good = Resp(200, json.dumps([{"tag_name": "3.0.0", "body": section(FLASK, "3.0.0")}]))
    web.pages[RELEASES.format("pallets/flask", 1)] = lambda h: Resp(401) if "Authorization" in h else good
    web.serve(RAW.format("pallets/flask", "CHANGES.rst"), FLASK)
    monkeypatch.setattr(cl, "repo_files", lambda o, r: None)
    report = cl.collect_changes("flask", "2.2.5", "3.0.0", github_token="expired", lookback=False)
    assert "GitHub releases" in report.sources_ok
    tries = [h for url, h in web.requests if url == RELEASES.format("pallets/flask", 1)]
    assert "Authorization" in tries[0] and "Authorization" not in tries[1]


def test_numpy_docs_release_notes(web):
    versions = ["1.26.4", "2.0.0"]
    serve_pypi(web, "numpy", pypi("numpy", versions, {"Source": "https://github.com/numpy/numpy"}))
    web.serve(RAW.format("numpy/numpy", "doc/source/release/2.0.0-notes.rst"), NUMPY)
    report = cl.collect_changes("numpy", "1.26.4", "2.0.0", lookback=False)
    assert "docs release notes" in report.sources_ok and "2.0.0" in report.versions_read
    assert {"np.float_", "np.cast"} <= {e.api for e in report.entries if e.kind == "removed"}
    # the layout is remembered: the next check fetches only the notes it needs
    web.requests.clear()
    cl.fetch_docs_release_notes("numpy", "numpy", versions)
    assert not any("whatsnew" in url or "releasenotes" in url for url, _ in web.requests)


def test_changelog_linked_from_pypi(web):
    info = pypi("requests", ["2.31.0", "2.32.0", "2.32.5"],
                {"Changelog": "https://github.com/psf/requests/blob/main/HISTORY.md"})
    serve_pypi(web, "requests", info)
    web.serve("https://raw.githubusercontent.com/psf/requests/main/HISTORY.md", REQUESTS,
              headers={"content-type": "text/plain"})
    report = cl.collect_changes("requests", "2.31.0", "2.32.5", lookback=False)
    assert "linked changelog" in report.sources_ok and "PyPI description" in report.sources_ok
    assert "2.32.0" in report.versions_read and report.entries


def test_changelog_in_the_pypi_description(web):
    desc = "Changelog\n=========\n\n" + FLASK  # some projects put their history in the long description
    serve_pypi(web, "flask", pypi("flask", FLASK_VERSIONS, description=desc))
    report = cl.collect_changes("flask", "2.2.5", "2.3.0", lookback=False)
    assert report.sources_ok == ["PyPI description"]
    assert any(e.api == "Markup" for e in report.entries)


def test_every_failure_is_named(web):
    serve_pypi(web, "flask", pypi("flask", FLASK_VERSIONS, {"Source": "https://github.com/pallets/flask"}))
    web.serve(RELEASES.format("pallets/flask", 1), "{}", status=503)
    for name in cl.CHANGELOG_FILENAMES:
        web.pages[RAW.format("pallets/flask", name)] = ConnectionError("reset")
    del web.pages[cl.PYPI_VERSION_API.format(package="flask", version="2.3.0")]
    report = cl.collect_changes("flask", "2.2.5", "2.3.0", lookback=False)
    joined = " | ".join(report.sources_failed)
    assert "changelog file" in joined and "PyPI description" in joined
    assert not report.entries


def test_pypi_down_says_so():
    report = cl.collect_changes("flask", "2.2.5", "3.0.0")  # the network is blocked in tests
    assert report.sources_failed[0].startswith("PyPI metadata") and not report.checked


def test_not_pep_440_versions_are_reported(web):
    serve_pypi(web, "oddpkg", pypi("oddpkg", ["1.0", "2.0"]))
    report = cl.collect_changes("oddpkg", "banana", "2.0", lookback=False)
    assert any("not PEP 440" in f for f in report.sources_failed)


# ── smaller pieces ────────────────────────────────────────────────────────────

def test_pypi_metadata_is_cached(web):
    serve_pypi(web, "flask", pypi("flask", ["1.0", "2.0"]))
    assert cl.get_current_version("flask") == "2.0" and cl.get_available_versions("flask") == ["1.0", "2.0"]
    assert cl.get_pypi_version_info("flask", "2.0")["info"]["name"] == "flask"
    count = len(web.requests)
    cl.get_pypi_info("flask")
    cl.get_pypi_version_info("flask", "2.0")
    assert len(web.requests) == count
    cl.get_pypi_info("flask", fresh=True)  # watch asks again for new releases
    assert len(web.requests) == count + 1


def test_repository_listing_saves_probes(web, monkeypatch):
    monkeypatch.setattr(cl, "_github_token", "t")
    web.serve("https://api.github.com/repos/o/r/git/trees/HEAD?recursive=1",
              {"tree": [{"path": "CHANGES.rst", "type": "blob"}, {"path": "src/x.py", "type": "blob"}]})
    assert cl.repo_files("o", "r") == {"CHANGES.rst"}
    assert cl._raw("o", "r", "HISTORY.md") is None  # listed as absent: no request made
    assert not any("HISTORY.md" in url for url, _ in web.requests)
    web.serve("https://api.github.com/repos/o/big/git/trees/HEAD?recursive=1", {"truncated": True, "tree": []})
    assert cl.repo_files("o", "big") is None


def test_pointer_changelog_is_followed(web):
    web.serve(RAW.format("o/r", "CHANGELOG.md"), "The changelog lives in docs/changes.rst.\n" + " " * 60)
    web.serve(RAW.format("o/r", "docs/changes.rst"), FLASK)
    assert cl.fetch_raw_changelog("o", "r") == FLASK


def test_unsafe_names_never_reach_a_url(web):
    assert cl.fetch_raw_changelog("bad owner", "r") is None
    assert cl.fetch_github_releases("o", "..") == []
    assert cl._raw("o", "r", "../../etc/passwd") is None
    assert web.requests == []


def test_linked_changelog_candidates():
    info = {"info": {"project_urls": {
        "Changelog": "https://flask.palletsprojects.com/en/stable/changes/",
        "Release notes": "https://example.org/notes.html",
        "Source": "https://github.com/googleapis/google-cloud-python/tree/main/packages/google-auth",
        "History": "http://insecure.example/HISTORY.md",
        "Releases": "https://github.com/o/r/releases",
    }}}
    urls = cl.changelog_link_candidates(info)
    assert "https://flask.palletsprojects.com/en/stable/_sources/changes.rst.txt" in urls
    assert "https://example.org/_sources/notes.rst.txt" in urls
    assert ("https://raw.githubusercontent.com/googleapis/google-cloud-python/main/packages/google-auth/"
            "CHANGELOG.md") in urls
    assert all(u.startswith("https://") for u in urls) and not any("/releases" in u for u in urls)


def test_migration_guide_for_a_major_jump(web):
    guide = "Migration guide\n===============\n\nRemoved\n-------\n\n- ``pkg.old_api`` was removed. Use ``pkg.new_api`` instead.\n"
    web.serve(RAW.format("o/r", "docs/migration.md"), guide)
    entries = cl.fetch_migration_guides("o", "r", "1.4.0", "2.1.0", ["2.0.0", "2.1.0"])
    assert any(e.api == "pkg.old_api" and e.version == "2.0.0" for e in entries)
    assert cl.fetch_migration_guides("o", "r", "x", "y", []) == []
