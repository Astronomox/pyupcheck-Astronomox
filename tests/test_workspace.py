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


# ── other monorepos: local path dependencies ──────────────────────────────────

def test_poetry_path_dependencies_are_members_not_pypi_packages(tmp_path):
    write(tmp_path / "pyproject.toml", '''[tool.poetry.dependencies]
python = "^3.10"
flask = "^3.0"
mylib = {path = "libs/mylib", develop = true}
vendored = {git = "https://github.com/example/vendored.git"}
''')
    write(tmp_path / "libs" / "mylib" / "pyproject.toml", '[project]\nname = "mylib"\ndependencies = ["numpy>=2"]\n')
    deps = {d.name: d for d in discover_dependencies(str(tmp_path))}
    assert set(deps) == {"flask", "numpy"}  # mylib and vendored aren't looked up on PyPI
    assert deps["numpy"].source == "libs/mylib/pyproject.toml"


def test_pep508_and_pdm_file_references(tmp_path):
    write(tmp_path / "pyproject.toml", '''[project]
name = "app"
dependencies = [
    "core @ file:///${PROJECT_ROOT}/packages/core",
    "tool @ git+https://github.com/example/tool.git@v1",
    "requests>=2",
]
''')
    write(tmp_path / "packages" / "core" / "pyproject.toml", '[project]\nname = "core"\ndependencies = ["attrs==23.1.0"]\n')
    assert {d.name for d in discover_dependencies(str(tmp_path))} == {"requests", "attrs"}


def test_uv_path_and_git_sources_are_not_pypi(tmp_path):
    write(tmp_path / "pyproject.toml", '''[project]
name = "app"
dependencies = ["helper", "fork", "httpx==0.28.1"]

[tool.uv.sources]
helper = { path = "helper" }
fork = { git = "https://github.com/example/fork" }
''')
    write(tmp_path / "helper" / "pyproject.toml", '[project]\nname = "helper"\ndependencies = ["rich"]\n')
    assert {d.name for d in discover_dependencies(str(tmp_path))} == {"httpx", "rich"}


def test_path_dependencies_outside_the_project_are_ignored(tmp_path):
    write(tmp_path / "elsewhere" / "pyproject.toml", '[project]\nname = "x"\ndependencies = ["evil"]\n')
    write(tmp_path / "proj" / "pyproject.toml", '[tool.poetry.dependencies]\nx = {path = "../elsewhere"}\n'
          'y = {path = "/etc"}\n')
    assert discover_dependencies(str(tmp_path / "proj")) == []
