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
