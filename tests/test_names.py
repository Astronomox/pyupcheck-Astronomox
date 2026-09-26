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


def test_dotted_project_names_use_normalized_keys():
    assert import_names_for("discord.py") == ["discord"]
    assert import_names_for("ruamel.yaml") == ["ruamel.yaml"]


def test_namespace_parent_dropped_when_child_known():
    from depshift.names import _drop_namespace_parents
    assert _drop_namespace_parents(["google", "google.protobuf"]) == ["google.protobuf"]


def test_namespace_expand_from_record():
    from depshift.names import _namespace_expand
    files = ["google/protobuf/__init__.py", "google/protobuf/message.py",
             "google/_upb/_message.abi3.so"]
    assert _namespace_expand("google", files)[0] == "google.protobuf"
    assert "google" not in _namespace_expand("google", files)
    assert _namespace_expand("yaml", ["yaml/__init__.py"]) == ["yaml"]


def test_import_names_from_archive_paths():
    from depshift.names import import_names_from_archive
    assert import_names_from_archive(["yaml/loader.py", "yaml.cyaml", "six.py"]) == ["yaml", "six"]
