"""Tests for config loading."""

import os
import tempfile

import pytest

from depshift.config import Config, load_config


def write(path, content):
    with open(path, "w") as f:
        f.write(content)


def test_defaults_when_no_files():
    with tempfile.TemporaryDirectory() as d:
        cfg = load_config(d)
        assert cfg.fail_on == "breaking"
        assert cfg.min_severity == "warning"
        assert cfg.cache is True
        assert len(cfg.exclude_dirs) == 0
        assert len(cfg.ignore_packages) == 0


def test_pyproject_toml_section():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "pyproject.toml"), """
[tool.pyupcheck]
fail_on = "any"
min_severity = "deprecated"
cache = false
exclude = ["migrations", "legacy"]
ignore = ["internal-pkg"]
""")
        cfg = load_config(d)
        assert cfg.fail_on == "any"
        assert cfg.min_severity == "deprecated"
        assert cfg.cache is False
        assert "migrations" in cfg.exclude_dirs
        assert "legacy" in cfg.exclude_dirs
        assert "internal-pkg" in cfg.ignore_packages


def test_pyupcheckignore_directories():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, ".pyupcheckignore"), "migrations/\nlegacy/\n")
        cfg = load_config(d)
        assert "migrations" in cfg.exclude_dirs
        assert "legacy" in cfg.exclude_dirs


def test_pyupcheckignore_packages():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, ".pyupcheckignore"), "my-internal-pkg\n")
        cfg = load_config(d)
        assert "my-internal-pkg" in cfg.ignore_packages


def test_pyupcheckignore_comments_ignored():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, ".pyupcheckignore"), "# this is a comment\nmigrations/\n")
        cfg = load_config(d)
        assert "migrations" in cfg.exclude_dirs
        assert len([d for d in cfg.exclude_dirs if d.startswith("#")]) == 0


def test_pyupcheckignore_empty_lines_ignored():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, ".pyupcheckignore"), "\n\nmigrations/\n\n")
        cfg = load_config(d)
        assert "migrations" in cfg.exclude_dirs
        assert "" not in cfg.exclude_dirs


def test_both_files_merged():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "pyproject.toml"), """
[tool.pyupcheck]
exclude = ["migrations"]
""")
        write(os.path.join(d, ".pyupcheckignore"), "legacy/\n")
        cfg = load_config(d)
        assert "migrations" in cfg.exclude_dirs
        assert "legacy" in cfg.exclude_dirs


def test_ignore_packages_lowercased():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, ".pyupcheckignore"), "MyPkg\n")
        cfg = load_config(d)
        assert "mypkg" in cfg.ignore_packages


def test_nonexistent_dir_returns_defaults():
    cfg = load_config("/tmp/definitely_does_not_exist_xyz_abc")
    assert cfg.fail_on == "breaking"
    assert cfg.cache is True
