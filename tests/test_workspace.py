"""uv workspaces: a root pyproject.toml that names member projects. Their
dependencies are the project's (with their constraints), the members
themselves aren't PyPI packages, and review and watch follow member files."""

import os
import subprocess

import pytest

from depshift import watch
from depshift.deps import discover_dependencies, is_workspace_member, workspace_members
from depshift.review import changed_dependencies

ROOT = '''[project]
name = "root"
dependencies = ["typer>=0.27"]

[tool.uv.workspace]
members = ["backend", "packages/*"]
exclude = ["packages/legacy"]
'''


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def workspace(tmp_path):
    write(tmp_path / "pyproject.toml", ROOT)
    write(tmp_path / "backend" / "pyproject.toml",
          '[project]\nname = "app"\ndependencies = ["sentry-sdk>=2.70,<3", "shared", "typer>=0.1"]\n')
    write(tmp_path / "packages" / "shared" / "pyproject.toml", '[project]\nname = "shared"\ndependencies = ["httpx==0.28.1"]\n')
    write(tmp_path / "packages" / "legacy" / "pyproject.toml", '[project]\nname = "legacy"\ndependencies = ["six"]\n')
    (tmp_path / "packages" / "notes").mkdir()  # matches the glob, but isn't a project
    return tmp_path


def test_members_and_their_dependencies(workspace):
    assert workspace_members(str(workspace)) == ["backend", "packages/shared"]
    deps = {d.name: d for d in discover_dependencies(str(workspace))}
    assert set(deps) == {"typer", "sentry-sdk", "httpx"}  # no members, no excluded member's deps
    assert deps["sentry-sdk"].source == "backend/pyproject.toml" and "<3" in deps["sentry-sdk"].raw
    assert deps["httpx"].pinned_version == "0.28.1"
    assert deps["typer"].source == "pyproject.toml"  # the root's declaration comes first


def test_members_never_leave_the_project(tmp_path):
    outside = tmp_path / "outside"
    write(outside / "pyproject.toml", '[project]\nname = "x"\ndependencies = ["evil"]\n')
    proj = tmp_path / "proj"
    write(proj / "pyproject.toml", '[tool.uv.workspace]\nmembers = ["../outside", "link", "/etc"]\n')
    os.symlink(outside, proj / "link")
    assert workspace_members(str(proj)) == []
    assert not is_workspace_member("../outside", ["*"], []) and not is_workspace_member("", ["*"], [])


def test_watch_sees_member_files(workspace):
    sig = watch.dep_file_signature(str(workspace))
    assert "backend/pyproject.toml" in sig and "packages/shared/pyproject.toml" in sig
    assert "packages/legacy/pyproject.toml" not in sig


def test_review_compares_member_files_too(workspace):
    def git(*args):
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    git("init", "-q")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base")
    write(workspace / "packages" / "shared" / "pyproject.toml",
          '[project]\nname = "shared"\ndependencies = ["httpx==0.29.0"]\n')
    changes = changed_dependencies(str(workspace), "HEAD")
    assert [(c.name, c.old, c.new) for c in changes] == [("httpx", "0.28.1", "0.29.0")]
