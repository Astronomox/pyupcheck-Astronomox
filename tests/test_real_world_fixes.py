"""False alarms and a miss found by checking a real Flask 1.0 app
(gothinkster/flask-realworld-example-app) against today's releases, each
confirmed against the installed packages. Before: 46 findings, 32 false.
After: 15, all real, including a break that was missed (db.Binary)."""

from depshift.analyzer import match_usage_to_change
from depshift.apisurface import build_surface, diff_surfaces
from depshift.changelog import ChangeEntry, parse_changelog_text
from depshift.scanner import Usage, project_objects, scan_directory


def call(chain, code, kwargs=(), positional=0):
    return Usage("app.py", 1, code, chain, "function_call", call_args=[f"pos:{positional}"], kwargs=list(kwargs))


# ── deep mode ─────────────────────────────────────────────────────────────────

def test_a_base_imported_under_an_alias_keeps_its_methods():
    """Flask 3: `from .sansio.blueprints import Blueprint as SansioBlueprint`;
    Blueprint.route lives on the base. It was reported removed (19 times)."""
    old = build_surface("1.0", {"flask.blueprints": ["class Blueprint:\n    def route(self, rule): pass\n"]})
    new = build_surface("3.0", {
        "flask.sansio.scaffold": ["class Scaffold:\n    def route(self, rule): pass\n"],
        "flask.sansio.blueprints": ["from .scaffold import Scaffold\nclass Blueprint(Scaffold): pass\n"],
        "flask.blueprints": ["from .sansio.blueprints import Blueprint as SansioBlueprint\n"
                             "class Blueprint(SansioBlueprint): pass\n"],
    })
    assert not [c for c in diff_surfaces(old, new) if c.api.endswith(".route")]


def test_instance_attributes_set_by_a_base_still_exist():
    """Blueprint.template_folder and root_path moved from class level to
    `self.x = ...` in a base's __init__: still there, not removed."""
    old = build_surface("1.0", {"pkg": ["class Bp:\n    root_path = None\n    gone = 1\n"]})
    new = build_surface("2.0", {"pkg": ["class Base:\n    def __init__(self):\n        self.root_path = '/'\n"
                                        "class Bp(Base): pass\n"]})
    assert [c.api for c in diff_surfaces(old, new) if c.kind == "removed_attribute"] == ["pkg.Bp.gone"]


# ── release notes ─────────────────────────────────────────────────────────────

def test_a_possessive_names_the_member_not_the_owner():
    note = "- *Backwards-incompatible*: Rename ``DateTime``'s ``dateformat`` Meta option to ``datetimeformat``."
    entries = parse_changelog_text(note, "3.0.0")
    assert "DateTime" not in {e.api for e in entries} and "DateTime.dateformat" in {e.api for e in entries}
    plain = call("marshmallow.fields.DateTime", "fields.DateTime(attribute='c')", ["attribute"])
    assert all(match_usage_to_change(plain, e, "marshmallow") is None for e in entries)


def test_argument_as_x_to_f_is_about_calls_passing_x():
    note = "- *Backwards-incompatible*: Passing a string argument as ``only`` to ``fields.Nested`` is no longer supported."
    (entry,) = [e for e in parse_changelog_text(note, "3.0.0") if e.api.endswith("only")]
    assert entry.api == "fields.Nested.only"
    assert match_usage_to_change(call("marshmallow.fields.Nested", "fields.Nested(P)"), entry, "marshmallow") is None
    passing = call("marshmallow.fields.Nested", "fields.Nested(P, only='a')", ["only"])
    assert match_usage_to_change(passing, entry, "marshmallow").severity == "breaking"


def test_a_note_about_passing_one_value_needs_that_value():
    entry = ChangeEntry("removed", "fields.Nested", 'Passing the string `"self"` to `fields.Nested` is deprecated.',
                        "4.0.0", confidence="high")
    assert match_usage_to_change(call("marshmallow.fields.Nested", "fields.Nested(ProfileSchema)"),
                                 entry, "marshmallow") is None
    hit = match_usage_to_change(call("marshmallow.fields.Nested", "fields.Nested('self', load_only=True)"),
                                entry, "marshmallow")
    assert hit.severity == "breaking"


def test_positional_argument_notes_need_a_positional_argument():
    entry = ChangeEntry("removed", "pytest.fixture", "``@pytest.fixture`` no longer supports positional arguments.",
                        "4.0.0", confidence="high")
    assert match_usage_to_change(call("pytest.fixture", '@pytest.fixture(scope="function")', ["scope"]),
                                 entry, "pytest") is None
    bare = Usage("t.py", 1, "@pytest.fixture", "pytest.fixture", "attribute_access")
    assert match_usage_to_change(bare, entry, "pytest") is None
    assert match_usage_to_change(call("pytest.fixture", '@pytest.fixture("module")', positional=1),
                                 entry, "pytest").severity == "breaking"


def test_a_decorator_in_backticks_names_the_api():
    (entry,) = parse_changelog_text("- ``@pytest.fixture`` no longer supports positional arguments.", "4.0.0")
    assert entry.api == "pytest.fixture"


def test_conditional_notes_are_mentions():
    note = "- If only a name is passed to `option()`, Click suggests renaming it to `--name`."
    assert all(e.confidence == "low" for e in parse_changelog_text(note, "8.2.0"))


# ── objects shared across the project ─────────────────────────────────────────

def _flask_app(tmp_path):
    (tmp_path / "conduit").mkdir()
    (tmp_path / "conduit" / "__init__.py").write_text("")
    (tmp_path / "conduit" / "extensions.py").write_text("from flask_sqlalchemy import SQLAlchemy\ndb = SQLAlchemy()\n")
    (tmp_path / "conduit" / "database.py").write_text("from .extensions import db\nColumn = db.Column\n")
    (tmp_path / "conduit" / "models.py").write_text(
        "from conduit.database import db\n\nclass User(db.Model):\n    password = db.Column(db.Binary(128))\n")
    return tmp_path


def test_objects_are_followed_through_project_imports(tmp_path):
    objects = project_objects(str(_flask_app(tmp_path)))
    assert objects["conduit.models"]["db"] == "flask_sqlalchemy.SQLAlchemy"  # via database.py
    used = {(u.file.rsplit("/", 1)[-1], u.attr_chain) for u in scan_directory(str(tmp_path), "flask_sqlalchemy")}
    assert ("models.py", "flask_sqlalchemy.SQLAlchemy.Model") in used


def test_flask_sqlalchemy_db_hands_out_sqlalchemy_names(tmp_path):
    chains = {u.attr_chain for u in scan_directory(str(_flask_app(tmp_path)), "sqlalchemy")}
    assert {"sqlalchemy.Binary", "sqlalchemy.Column"} <= chains


def test_a_rebound_name_is_not_the_object(tmp_path):
    (tmp_path / "a.py").write_text("from flask_sqlalchemy import SQLAlchemy\ndb = SQLAlchemy()\n")
    (tmp_path / "b.py").write_text("from a import db\ndb = None\nx = 1\n")
    assert not [u for u in scan_directory(str(tmp_path), "sqlalchemy") if u.file.endswith("b.py")]
