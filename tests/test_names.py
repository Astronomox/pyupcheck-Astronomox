"""Tests for distribution-name -> import-name resolution."""

from depshift.names import import_names_for, normalize_dist_name


def test_normalize_dist_name():
    assert normalize_dist_name("Flask_SQLAlchemy") == "flask-sqlalchemy"
    assert normalize_dist_name("ruamel.yaml") == "ruamel-yaml"


def test_known_mismatches():
    assert "yaml" in import_names_for("PyYAML")
    assert "bs4" in import_names_for("beautifulsoup4")
    assert "PIL" in import_names_for("Pillow")
    assert "sklearn" in import_names_for("scikit-learn")
    assert "dateutil" in import_names_for("python-dateutil")
    assert "cv2" in import_names_for("opencv-python-headless")


def test_fallback_replaces_dashes():
    assert import_names_for("some-unknown-dist-xyz") == ["some_unknown_dist_xyz"]


def test_installed_metadata_is_used():
    # click is a runtime dependency, so it is always installed
    assert "click" in import_names_for("click")


def test_always_returns_a_name():
    assert import_names_for("x")
