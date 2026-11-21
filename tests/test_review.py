"""PR review: which dependency versions changed, and the comment it renders."""

import subprocess

import pytest

from depshift.review import COMMENT_MARKER, _clean, changed_dependencies, render_review


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    return tmp_path


def commit(repo, files, msg):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def test_changed_pins(repo):
    commit(repo, {"requirements.txt": "flask==2.2.5\nrequests==2.31.0\nold==1.0\n"}, "base")
    commit(repo, {"requirements.txt": "flask==3.0.0\nrequests==2.31.0\nnew==1.0\n"}, "bump")
    changes = changed_dependencies(str(repo), "HEAD~1")
    assert [(c.name, c.old, c.new) for c in changes] == [("flask", "2.2.5", "3.0.0")]


def test_lockfile_change_with_unchanged_range(repo):
    proj = '[project]\nname = "app"\nversion = "0"\ndependencies = ["urllib3>=1.26"]\n'
    lock = '[[package]]\nname = "urllib3"\nversion = "{}"\n'
    commit(repo, {"pyproject.toml": proj, "uv.lock": lock.format("1.26.18")}, "base")
    commit(repo, {"uv.lock": lock.format("2.0.0")}, "lock bump")
    changes = changed_dependencies(str(repo), "HEAD~1")
    assert [(c.name, c.old, c.new) for c in changes] == [("urllib3", "1.26.18", "2.0.0")]


def test_requirements_subdirectory_and_uncommitted_change(repo):
    commit(repo, {"requirements/base.txt": "click==7.1.2\n"}, "base")
    (repo / "requirements" / "base.txt").write_text("click==8.1.0\n")  # working tree, not committed
    changes = changed_dependencies(str(repo), "HEAD")
    assert [(c.name, c.old, c.new) for c in changes] == [("click", "7.1.2", "8.1.0")]


def test_bad_ref_raises(repo):
    commit(repo, {"requirements.txt": "a==1\n"}, "base")
    with pytest.raises(subprocess.CalledProcessError):
        changed_dependencies(str(repo), "no-such-ref")


# ── rendering ─────────────────────────────────────────────────────────────────

def result(pkg, breaking=0, risks=(), **kw):
    r = {"package": pkg, "current_version": "1.0", "target_version": "2.0", "risks": list(risks),
         "safe_count": 0, "breaking_count": breaking, "deprecated_count": 0}
    r.update(kw)
    return r


def test_render_has_marker_table_and_migrate_hint(tmp_path):
    risk = {"file": str(tmp_path / "app.py"), "line": 3, "code": "integrate.simps(y)", "api": "x",
            "severity": "breaking", "change_description": "Removed ``simps``. :pr:`123`",
            "replacement": "scipy.integrate.simpson", "precise": True}
    text = render_review([result("scipy", 1, [risk], fixable=1), result("requests")], repo_root=str(tmp_path))
    assert text.startswith(COMMENT_MARKER)
    assert "**1 breaking** usage(s) across 2 changed package(s)." in text
    assert "| `scipy` | 1.0 → 2.0 | 1 | 0 | 1 |" in text
    assert "`app.py:3`" in text and ":pr:" not in text
    assert "use `scipy.integrate.simpson` instead" in text
    assert "pyupcheck migrate scipy 2.0 --write` fixes 1" in text


def test_render_nothing_changed():
    assert "No dependency versions changed" in render_review([])


def test_render_unchecked():
    text = render_review([result("x", unchecked=True, warnings=["could not read: PyPI"])])
    assert "could not be checked" in text and "| not checked |" in text


def test_clean_release_note_markup():
    assert _clean("Use :func:`~markupsafe.escape` (``x | y``). :issue:`9`") == \
        "Use `markupsafe.escape` (`x \\| y`)."


# ── duplicate findings ────────────────────────────────────────────────────────

def test_dedupe_keeps_most_severe_verified_finding():
    from depshift.cli import _dedupe_risks
    base = {"file": "a.py", "line": 1, "api": "pkg.x"}
    risks = [dict(base, severity="deprecated"), dict(base, severity="breaking"),
             dict(base, severity="breaking", precise=True), dict(base, line=2, severity="deprecated")]
    kept = _dedupe_risks(risks)
    assert [(r["line"], r["severity"], r.get("precise", False)) for r in kept] == \
        [(1, "breaking", True), (2, "deprecated", False)]


def test_clean_truncated_reference():
    assert _clean("Import them from ``markupsafe`` instead. :pr:") == "Import them from `markupsafe` instead."
    assert _clean("Removed. :issue:`12") == "Removed."
