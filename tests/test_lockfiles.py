"""Lockfile parsing, baseline precedence, and undeclared-import discovery."""

import json
import textwrap

import pytest

from depshift.lockfiles import find_lockfiles, locked_version, locked_versions


def write(path, text):
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_poetry_lock(tmp_path):
    write(tmp_path / "poetry.lock", """\
        [[package]]
        name = "Flask"
        version = "2.2.5"

        [[package]]
        name = "werkzeug"
        version = "2.2.3"
        """)
    locks = locked_versions(str(tmp_path))
    assert locks["flask"].version == "2.2.5"
    assert locks["werkzeug"].source == "poetry.lock"


def test_uv_lock_skips_project_itself(tmp_path):
    write(tmp_path / "uv.lock", """\
        version = 1

        [[package]]
        name = "myapp"
        version = "0.1.0"
        source = { editable = "." }

        [[package]]
        name = "requests"
        version = "2.31.0"
        source = { registry = "https://pypi.org/simple" }
        """)
    locks = locked_versions(str(tmp_path))
    assert "myapp" not in locks
    assert locks["requests"].version == "2.31.0"


def test_pdm_lock_and_pylock(tmp_path):
    write(tmp_path / "pdm.lock", '[[package]]\nname = "click"\nversion = "8.1.7"\n')
    write(tmp_path / "pylock.toml", 'lock-version = "1.0"\n[[packages]]\nname = "rich"\nversion = "13.7.0"\n')
    locks = locked_versions(str(tmp_path))
    assert locks["click"].version == "8.1.7"
    assert locks["rich"].version == "13.7.0"


def test_named_pylock(tmp_path):
    write(tmp_path / "pylock.dev.toml", '[[packages]]\nname = "pytest"\nversion = "8.0.0"\n')
    assert [p.split("/")[-1] for p in find_lockfiles(str(tmp_path))] == ["pylock.dev.toml"]
    assert locked_version(str(tmp_path), "pytest") == "8.0.0"


def test_pipfile_lock(tmp_path):
    (tmp_path / "Pipfile.lock").write_text(json.dumps({
        "default": {"Django": {"version": "==4.2.1"}, "weird": {"git": "https://x"}},
        "develop": {"pytest": {"version": "==8.0.0"}},
    }))
    locks = locked_versions(str(tmp_path))
    assert locks["django"].version == "4.2.1"
    assert locks["pytest"].version == "8.0.0"
    assert "weird" not in locks


def test_first_lockfile_wins(tmp_path):
    write(tmp_path / "uv.lock", '[[package]]\nname = "a"\nversion = "2.0"\n')
    write(tmp_path / "poetry.lock", '[[package]]\nname = "a"\nversion = "1.0"\n')
    assert locked_version(str(tmp_path), "a") == "2.0"


def test_broken_lockfile_is_ignored(tmp_path):
    (tmp_path / "poetry.lock").write_text("not [valid toml")
    assert locked_versions(str(tmp_path)) == {}


# ── baseline precedence ───────────────────────────────────────────────────────

@pytest.fixture
def cli(monkeypatch):
    import depshift.cli as cli
    monkeypatch.setattr(cli, "get_installed_version", lambda p: "9.9.9")
    monkeypatch.setattr(cli, "scan_directory", lambda *a, **k: [])  # stop after baseline
    return cli


def run(cli, tmp_path, **kw):
    from depshift.config import load_config
    return cli._run_single_check("pkg", "10.0", str(tmp_path), None,
                                 load_config(str(tmp_path)), **kw)


def test_lockfile_beats_pin_and_installed(cli, tmp_path):
    write(tmp_path / "uv.lock", '[[package]]\nname = "pkg"\nversion = "1.5"\n')
    r = run(cli, tmp_path, pinned="1.0")
    assert r["current_version"] == "1.5"


def test_pin_beats_installed(cli, tmp_path):
    assert run(cli, tmp_path, pinned="1.0", locked=None)["current_version"] == "1.0"


def test_installed_used_when_it_fits_constraint(cli, tmp_path):
    assert run(cli, tmp_path, spec=">=9,<11", locked=None)["current_version"] == "9.9.9"
    assert run(cli, tmp_path, spec=">=1,<2", locked=None)["current_version"] == "1"


# ── undeclared imports ────────────────────────────────────────────────────────

def test_undeclared_import_found(tmp_path):
    import depshift.cli as cli
    from depshift.deps import discover_dependencies
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    write(tmp_path / "uv.lock", """\
        [[package]]
        name = "requests"
        version = "2.31.0"

        [[package]]
        name = "urllib3"
        version = "1.26.18"

        [[package]]
        name = "certifi"
        version = "2024.2.2"
        """)
    (tmp_path / "app.py").write_text("import requests\nfrom urllib3.util.retry import Retry\n")
    deps = discover_dependencies(str(tmp_path))
    extra = cli._undeclared_imports(str(tmp_path), deps, locked_versions(str(tmp_path)), set(), set())
    assert [(d.name, d.pinned_version) for d in extra] == [("urllib3", "1.26.18")]
    assert extra[0].source.endswith(cli.UNDECLARED)


def test_no_lockfile_no_undeclared(tmp_path):
    import depshift.cli as cli
    (tmp_path / "app.py").write_text("import urllib3\n")
    assert cli._undeclared_imports(str(tmp_path), [], {}, set(), set()) == []


def test_imported_top_modules(tmp_path):
    from depshift.scanner import imported_top_modules
    (tmp_path / "a.py").write_text("import os, google.protobuf.message\nfrom yaml import load\nfrom . import x\n")
    mods = imported_top_modules(str(tmp_path))
    assert {"os", "google", "google.protobuf", "yaml"} <= mods
    assert "x" not in mods
