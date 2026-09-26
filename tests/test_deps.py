"""Tests for dependency file parsers."""

import os
import tempfile

import pytest

from depshift.deps import (
    parse_requirements_txt,
    parse_setup_cfg,
    parse_setup_py,
    parse_conda_env,
    discover_dependencies,
)


def write(path: str, content: str):
    with open(path, "w") as f:
        f.write(content)


# ── requirements.txt ─────────────────────────────────────────────────────────

def test_requirements_basic():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("flask==2.3.3\nrequests>=2.28\nnumpy\n")
        path = f.name
    try:
        deps = parse_requirements_txt(path)
        names = [d.name for d in deps]
        assert "flask" in names
        assert "requests" in names
        assert "numpy" in names
    finally:
        os.unlink(path)


def test_requirements_pinned_version():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("flask==2.3.3\n")
        path = f.name
    try:
        deps = parse_requirements_txt(path)
        flask = next(d for d in deps if d.name == "flask")
        assert flask.pinned_version == "2.3.3"
    finally:
        os.unlink(path)


def test_requirements_skips_comments():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("# this is a comment\nflask==2.3.3\n")
        path = f.name
    try:
        deps = parse_requirements_txt(path)
        assert len(deps) == 1
    finally:
        os.unlink(path)


def test_requirements_skips_git_deps():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("git+https://github.com/org/repo.git\nflask==2.3.3\n")
        path = f.name
    try:
        deps = parse_requirements_txt(path)
        assert len(deps) == 1
        assert deps[0].name == "flask"
    finally:
        os.unlink(path)


# ── setup.cfg ────────────────────────────────────────────────────────────────

def test_setup_cfg_basic():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
        f.write("[metadata]\nname = myapp\n\n[options]\ninstall_requires =\n    flask>=2.0\n    requests==2.28.0\n")
        path = f.name
    try:
        deps = parse_setup_cfg(path)
        names = [d.name for d in deps]
        assert "flask" in names
        assert "requests" in names
    finally:
        os.unlink(path)


def test_setup_cfg_pinned():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
        f.write("[options]\ninstall_requires =\n    requests==2.28.0\n")
        path = f.name
    try:
        deps = parse_setup_cfg(path)
        req = next(d for d in deps if d.name == "requests")
        assert req.pinned_version == "2.28.0"
    finally:
        os.unlink(path)


# ── setup.py ─────────────────────────────────────────────────────────────────

def test_setup_py_basic():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write('from setuptools import setup\nsetup(name="myapp", install_requires=["flask>=2.0", "requests"])\n')
        path = f.name
    try:
        deps = parse_setup_py(path)
        names = [d.name for d in deps]
        assert "flask" in names
        assert "requests" in names
    finally:
        os.unlink(path)


# ── conda environment.yml ─────────────────────────────────────────────────────

def test_conda_env_basic():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("name: myenv\ndependencies:\n  - python=3.11\n  - pip:\n    - flask==2.3.3\n    - requests>=2.28\n")
        path = f.name
    try:
        deps = parse_conda_env(path)
        names = [d.name for d in deps]
        assert "flask" in names
        assert "requests" in names
    finally:
        os.unlink(path)


def test_conda_env_pinned():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("name: myenv\ndependencies:\n  - pip:\n    - flask==2.3.3\n")
        path = f.name
    try:
        deps = parse_conda_env(path)
        flask = next(d for d in deps if d.name == "flask")
        assert flask.pinned_version == "2.3.3"
    finally:
        os.unlink(path)


# ── discover_dependencies ─────────────────────────────────────────────────────

def test_discover_finds_requirements_txt():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "requirements.txt"), "flask==2.3.3\nrequests>=2.28\n")
        deps = discover_dependencies(d)
        names = [dep.name for dep in deps]
        assert "flask" in names
        assert "requests" in names


def test_discover_deduplicates():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "requirements.txt"), "flask==2.3.3\n")
        write(os.path.join(d, "requirements-dev.txt"), "flask==2.3.3\npytest\n")
        deps = discover_dependencies(d)
        flask_count = sum(1 for d in deps if d.name == "flask")
        assert flask_count == 1


# ── wider format coverage ────────────────────────────────────────────────────

def test_names_are_pep503_normalized():
    from depshift.deps import parse_requirement_line
    assert parse_requirement_line("Flask_SQLAlchemy==3.0", "r").name == "flask-sqlalchemy"


def test_markers_hashes_and_extras():
    from depshift.deps import parse_requirement_line
    d = parse_requirement_line('requests[socks]==2.31.0 ; python_version >= "3.8" --hash=sha256:ab', "r")
    assert d.name == "requests" and d.pinned_version == "2.31.0"


def test_vcs_egg_fragment():
    from depshift.deps import parse_requirement_line
    d = parse_requirement_line("git+https://github.com/org/repo.git#egg=MyPkg", "r")
    assert d.name == "mypkg"


def test_requirements_include_and_glob():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "requirements.txt"), "-r requirements-base.txt\nflask==2.3.3\n")
        write(os.path.join(d, "requirements-base.txt"), "pyyaml==6.0\n")
        write(os.path.join(d, "requirements-test.txt"), "pytest\n")
        names = [dep.name for dep in discover_dependencies(d)]
        assert {"flask", "pyyaml", "pytest"} <= set(names)


def test_backslash_continuation():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "requirements.txt"), "flask==2.3.3 \\\n    --hash=sha256:abc\n")
        deps = discover_dependencies(d)
        assert deps[0].name == "flask" and deps[0].pinned_version == "2.3.3"


def test_pyproject_groups_and_poetry():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "pyproject.toml"), """
[project]
dependencies = ["httpx>=0.24"]

[dependency-groups]
test = ["pytest>=8", {include-group = "lint"}]
lint = ["ruff"]

[tool.poetry.dependencies]
python = "^3.10"
django = "^4.2"
numpy = {version = "1.26.0", optional = true}

[tool.poetry.group.dev.dependencies]
black = "*"
""")
        deps = {dep.name: dep for dep in discover_dependencies(d)}
        assert {"httpx", "pytest", "ruff", "django", "numpy", "black"} <= set(deps)
        assert deps["numpy"].pinned_version == "1.26.0"


def test_pipfile():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "Pipfile"), '[packages]\nrequests = "==2.31.0"\nflask = "*"\n'
                                          '[dev-packages]\npytest = {version = ">=8"}\n')
        deps = {dep.name: dep for dep in discover_dependencies(d)}
        assert deps["requests"].pinned_version == "2.31.0"
        assert {"flask", "pytest"} <= set(deps)


def test_conda_level_packages():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "environment.yml"),
              "dependencies:\n  - python=3.11\n  - conda-forge::numpy=1.26.0\n  - pip\n  - pip:\n    - flask\n")
        deps = {dep.name: dep for dep in discover_dependencies(d)}
        assert deps["numpy"].pinned_version == "1.26.0"
        assert "flask" in deps and "python" not in deps and "pip" not in deps


def test_pep735_include_group_resolved_with_cycle_guard():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "pyproject.toml"), """
[dependency-groups]
all = [{include-group = "test"}]
test = ["pytest", {include-group = "all"}]
""")
        names = [dep.name for dep in discover_dependencies(d)]
        assert names == ["pytest"]
