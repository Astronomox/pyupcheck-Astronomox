"""Each attack the security review found, and proof it no longer works.

pyupcheck reads things it doesn't control: package archives and metadata from
PyPI, release notes from GitHub and docs sites, and the project being checked
(which may be a repository you just cloned, or a pull request's branch).
"""

import io
import os
import re
import tarfile
import time
import zipfile

import httpx
import pytest

from depshift import apisurface, changelog, http
from depshift.safe import md_code, md_text, plain

# ── URLs pyupcheck will fetch ─────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://pypi.org/x",                          # not https
    "https://pypi.org:8443/x",                    # other port
    "https://localhost/x",
    "https://127.0.0.1/x",
    "https://169.254.169.254/latest/meta-data/",  # cloud metadata service
    "https://10.0.0.5/x",
    "https://192.168.1.1/x",
    "https://[::1]/x",
    "https://[::ffff:127.0.0.1]/x",               # IPv4-mapped loopback
    "https://metadata.google.internal/x",
    "https://printer.local/x",
    "https://intranet/x",                         # single-label name
    "https://user:pass@pypi.org/x",               # credentials in the URL
    "file:///etc/passwd",
])
def test_blocked_urls(url):
    with pytest.raises(http.BlockedURL):
        http.check_url(url)


def test_public_urls_allowed():
    for url in ("https://pypi.org/pypi/flask/json", "https://raw.githubusercontent.com/o/r/HEAD/CHANGES.md",
                "https://flask.palletsprojects.com/en/stable/changes/"):
        http.check_url(url)


def test_name_resolving_to_an_internal_address_is_blocked(monkeypatch):
    monkeypatch.setattr(http, "_resolves_internal", lambda host: host == "evil.example.com")
    with pytest.raises(http.BlockedURL):
        http.check_url("https://evil.example.com/changelog.md")


def _mock_client(monkeypatch, handler):
    c = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                     event_hooks={"request": [http._on_request]})
    monkeypatch.setattr(http, "_client", c)


def test_redirect_to_an_internal_address_is_blocked(monkeypatch):
    def handler(request):
        if request.url.host == "docs.example.com":
            return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/meta-data/"})
        raise AssertionError("followed the redirect")
    _mock_client(monkeypatch, handler)
    with pytest.raises(http.BlockedURL):
        http._http_get("https://docs.example.com/changelog.md")


def test_response_size_is_capped(monkeypatch):
    _mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 5000))
    assert http._http_get("https://docs.example.com/a", max_bytes=5000).text == "x" * 5000
    with pytest.raises(http.TooLarge):
        http._http_get("https://docs.example.com/a", max_bytes=4999)


def test_compressed_responses_and_gzip_bombs(monkeypatch):
    import gzip
    payload = b'{"info": {"name": "flask"}}'
    _mock_client(monkeypatch, lambda request: httpx.Response(
        200, content=gzip.compress(payload), headers={"content-encoding": "gzip"}))
    assert http._http_get("https://pypi.org/pypi/flask/json").json() == {"info": {"name": "flask"}}
    # small on the wire, huge once decompressed: the limit counts what it becomes
    _mock_client(monkeypatch, lambda request: httpx.Response(
        200, content=gzip.compress(b"0" * 2_000_000), headers={"content-encoding": "gzip"}))
    with pytest.raises(http.TooLarge):
        http._http_get("https://docs.example.com/a", max_bytes=100_000)


def test_pypi_changelog_links_only_https_and_real_repos():
    info = {"info": {"project_urls": {
        "Changelog": "http://169.254.169.254/latest/changes.md",
        "Release notes": "https://github.com/o%2F..%2Fx/r/blob/main/CHANGES.md",
        "History": "https://github.com/ok-owner/repo/blob/main/../../etc/CHANGES.md",
        "Changes": "https://github.com/ok-owner/repo/blob/main/docs/CHANGES.md",
    }}}
    assert changelog.changelog_link_candidates(info) == [
        "https://raw.githubusercontent.com/ok-owner/repo/main/docs/CHANGES.md"]


def test_github_repo_names_are_validated():
    bad = {"info": {"project_urls": {"Source": "https://github.com/a..b/r%2e%2e"}}}
    assert changelog.extract_github_repo(bad) is None
    good = {"info": {"project_urls": {"Source": "https://github.com/pallets/flask.git"}}}
    assert changelog.extract_github_repo(good) == ("pallets", "flask")
    assert changelog._raw("pallets", "flask", "../../x") is None  # no request made


# ── archives from PyPI ────────────────────────────────────────────────────────

def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_bomb_member_is_skipped(monkeypatch):
    monkeypatch.setattr(apisurface, "MAX_MEMBER_BYTES", 1000)
    data = _zip({"pkg/__init__.py": "def ok(): pass\n", "pkg/bomb.py": "#" * 50_000})
    names = [rel for rel, _ in apisurface._read_wheel(data)]
    assert names == ["pkg/__init__.py"]


def test_total_budget_and_member_count(monkeypatch):
    monkeypatch.setattr(apisurface, "MAX_TOTAL_BYTES", 2500)
    data = _zip({f"pkg/m{i}.py": "x" * 1000 for i in range(5)})
    assert len(apisurface._read_wheel(data)) == 2


def test_tar_paths_never_touch_the_disk(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("pkg-1.0/pkg/__init__.py", b"x = 1\n"), ("../../escape.py", b"y = 2\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("pkg-1.0/pkg/link.py")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(link)
    monkeypatch.chdir(tmp_path)
    members = apisurface._read_sdist(buf.getvalue())
    assert ("pkg/__init__.py", b"x = 1\n") in members
    assert not any("link" in rel for rel, _ in members)  # symlinks aren't read
    assert os.listdir(tmp_path) == []                     # nothing was extracted


# ── text shown in your terminal and in pull requests ─────────────────────────

EVIL = "Removed x.\x1b]0;PWNED\x07\x1b[2J\x1b]8;;https://evil.example\x1b\\click\x1b]8;;\x1b\\ ‮evil\r"


def test_plain_removes_control_sequences():
    out = plain(EVIL)
    assert "\x1b" not in out and "\x07" not in out and "‮" not in out and "\r" not in out
    assert "PWNED" not in out and "evil.example" not in out and out.startswith("Removed x.")


def test_workflow_commands_neutralised_in_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert "::" not in plain("note\n::add-mask::secret")
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert "::" in plain("Example::")


def test_terminal_output_has_no_injected_sequences(monkeypatch):
    from rich.console import Console

    from depshift import cli
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, force_terminal=True, width=120))
    risk = {"file": "/p/app.py", "line": 3, "code": "x = 1 # \x1b]0;code\x07", "severity": "breaking",
            "change_description": EVIL, "replacement": "y\x1b[2J", "precise": True}
    cli._print_terminal_result({"package": "pkg", "current_version": "1", "target_version": "2", "risks": [risk],
                                "breaking_count": 1, "deprecated_count": 0, "safe_count": 0,
                                "warnings": ["could not read \x1b]0;w\x07"]}, False)
    out = buf.getvalue()
    assert "\x1b]" not in out and "\x1b[2J" not in out and "\x07" not in out


def test_markdown_is_inert():
    text = md_text("ping @octocat, see #12, <img src=x onerror=alert(1)> ![t](https://t.example/p.png) "
                   "[click](https://evil.example) a|b")
    assert "@octocat" not in text and "<img" not in text and "](" not in text and "#12" not in text
    assert "a\\|b" in text and "click" in text
    code = md_code("x = `a` + ``b``")
    assert code.startswith("```") and code.endswith("```")


def test_review_comment_from_hostile_notes_and_code():
    from depshift.review import render_review
    risk = {"file": "/repo/app.py", "line": 2, "code": "x = 1  # ` @everyone <script>", "severity": "breaking",
            "change_description": "Removed. Thanks @octocat! <img src=x> [win](https://evil.example)",
            "replacement": "y`z", "precise": False}
    r = {"package": "pkg", "current_version": "1.0", "target_version": "2.0", "risks": [risk],
         "breaking_count": 1, "deprecated_count": 0, "safe_count": 0, "warnings": ["@everyone note"],
         "fixable": 1}
    md = render_review([r], repo_root="/repo")
    # the code line stays one code span: its backtick can't end it early, and
    # inside a code span GitHub shows mentions and tags as plain text
    assert "``x = 1  # ` @everyone <script>``" in md
    outside = re.sub(r"(`+)(?:(?!\1).)+\1", "", md)  # everything not in a code span
    assert "@octocat" not in outside and "@everyone" not in outside
    assert "<img" not in outside and "<script>" not in outside and "evil.example" not in outside


def test_tidy_note_is_linear():
    from depshift.cli import tidy_note
    for ch in ("[", " ", "[a](", "PR 1 ", "("):
        start = time.perf_counter()
        tidy_note(ch * 500_000 + "x")
        assert time.perf_counter() - start < 1.0, ch


# ── the project being checked ─────────────────────────────────────────────────

def test_lockfile_entries_must_look_real(tmp_path):
    from depshift.lockfiles import locked_versions
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "ok-pkg"\nversion = "1.2.3"\n\n'
        '[[package]]\nname = "evil`@everyone"\nversion = "1"\n\n'
        '[[package]]\nname = "pkg2"\nversion = "1 <img src=x>"\n')
    assert set(locked_versions(str(tmp_path))) == {"ok-pkg"}


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-c", "a..b", "main;id", "$(id)", "ref with space", ""])
def test_review_refuses_option_like_refs(ref, tmp_path):
    from depshift.review import export_dependency_files, valid_ref
    assert not valid_ref(ref)
    with pytest.raises(ValueError):
        export_dependency_files(str(tmp_path), ref, str(tmp_path))


def test_review_accepts_real_refs():
    from depshift.review import valid_ref
    for ref in ("origin/main", "HEAD~1", "v1.2.3", "3f1c2a9", "refs/heads/feat-x", "main@{1}"):
        assert valid_ref(ref), ref


def _change(path, before, after):
    from depshift.cli import FileChange
    return FileChange(str(path), path.name, before, after, [])


def test_rewrite_refuses_symlinks_and_paths_outside_the_project(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("import numpy\n")
    project = tmp_path / "project"
    project.mkdir()
    link = project / "evil.py"
    link.symlink_to(outside)
    with pytest.raises(PermissionError):
        _change(link, "import numpy\n", "pwned\n").write(str(project))
    with pytest.raises(PermissionError):
        _change(outside, "import numpy\n", "pwned\n").write(str(project))
    assert outside.read_text() == "import numpy\n"


def test_rewrite_is_atomic_and_keeps_line_endings_and_mode(tmp_path):
    f = tmp_path / "app.py"
    f.write_bytes(b"import numpy as np\r\nx = np.float_\r\n")
    os.chmod(f, 0o640)
    _change(f, "import numpy as np\nx = np.float_\n", "import numpy as np\nx = np.float64\n").write(str(tmp_path))
    assert f.read_bytes() == b"import numpy as np\r\nx = np.float64\r\n"
    assert oct(os.stat(f).st_mode & 0o777) == oct(0o640)
    assert [p.name for p in tmp_path.iterdir()] == ["app.py"]  # no temp file left behind


def test_feed_with_a_dtd_is_refused():
    from depshift.changelog import parse_releases_feed
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&lol2;</title></entry></feed>')
    assert parse_releases_feed(bomb) == []


def test_source_has_no_invisible_characters():
    import pathlib
    for f in pathlib.Path(__file__).resolve().parent.parent.joinpath("depshift").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not any(0x200b <= ord(c) <= 0x200f or 0x202a <= ord(c) <= 0x202e or 0x2066 <= ord(c) <= 0x2069
                       or ord(c) in (0x61c, 0xfeff, 0x2060) for c in text), f.name


def test_pathological_files_are_skipped_not_fatal(tmp_path):
    from depshift.scanner import scan_directory
    (tmp_path / "ok.py").write_text("import numpy as np\nx = np.float_\n")
    (tmp_path / "deep.py").write_text("import numpy as np\nx = " + "-" * 50_000 + "np.float_\n")
    (tmp_path / "nested.ipynb").write_text('{"cells": [' * 5000)
    (tmp_path / "binary.py").write_bytes(b"import numpy\n\x00\xff\xfe")
    files = {os.path.basename(u.file) for u in scan_directory(str(tmp_path), "numpy")}
    assert "ok.py" in files and "deep.py" not in files and "nested.ipynb" not in files


def test_pathological_package_module_is_skipped():
    from depshift.apisurface import build_surface
    surface = build_surface("1.0", {"pkg.deep": ["x = " + "-" * 50_000 + "1\n"],
                                    "pkg": ["def ok(a): pass\n"]})
    assert "pkg.ok" in surface.functions


def test_cache_is_private(tmp_path, monkeypatch):
    from depshift import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "c")
    cache.cache_set("k", {"a": 1})
    assert cache.cache_get("k") == {"a": 1}
    if os.name == "posix":
        assert (os.stat(tmp_path / "c").st_mode & 0o777) == 0o700
        (entry,) = (tmp_path / "c").iterdir()
        assert (os.stat(entry).st_mode & 0o777) == 0o600


def test_website_csp_matches_its_script():
    """The site allows one inline script by hash: an edit to it must update
    the hash (index.html and vercel.json), or the page stops working."""
    import base64
    import hashlib
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    page = (root / "index.html").read_text(encoding="utf-8")
    (script,) = re.findall(r"<script>(.*?)</script>", page, re.S)
    digest = "sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
    assert f"script-src '{digest}'" in page
    headers = json.loads((root / "vercel.json").read_text())["headers"][0]["headers"]
    csp = next(h["value"] for h in headers if h["key"] == "Content-Security-Policy")
    assert f"script-src '{digest}'" in csp and "frame-ancestors 'none'" in csp
    assert not re.search(r"\son[a-z]+=\"", page)  # inline handlers would be blocked
