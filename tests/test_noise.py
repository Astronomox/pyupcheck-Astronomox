"""Release-note text that names an API without changing it is not a finding
(found on a real FastAPI + SQLAlchemy project)."""

from depshift.analyzer import analyze
from depshift.changelog import ChangeEntry, parse_changelog_text, parse_sectioned_changelog
from depshift.scanner import scan_directory


def apis(text):
    return [(e.api, e.kind) for e in parse_changelog_text("## 2.0.0\n\n" + text + "\n", "2.0.0")]


def test_verbs_in_code_spans_are_names():
    text = ("- The :class:`.ColumnCollection` class hierarchy has been refactored to allow column "
            "names such as ``add``, ``remove``, ``update`` to be used without conflicts.")
    assert apis(text) == []


def test_category_tags():
    assert apis("- **[schema] [usecase]** :class:`.ForeignKeyConstraint` now accepts a constraint "
                "that names a column twice.") == []
    assert apis("- **[orm] [removed]** Removed the ``Query.foo()`` method.")[0][1] == "removed"


def test_change_directive_tags():
    text = ("2.1.0\n=====\n\n"
            "    .. change::\n        :tags: usecase, sql\n        :tickets: 1\n\n"
            "        :class:`.ForeignKeyConstraint` now accepts constraints that remove the limit.\n\n"
            "    .. change::\n        :tags: removed, orm\n\n"
            "        The ``Session.foo()`` method is removed.\n")
    entries = parse_sectioned_changelog(text, "sqlalchemy")
    assert [(e.api, e.kind) for e in entries] == [("Session.foo", "removed")]


def test_code_examples_are_not_items():
    assert apis("select(\n    func.sum(table.c.value).over(range_=FrameClause(3.14, 2.71))\n)") == []
    assert apis(":ref:`engine_insertmanyvalues_monotonic_functions`") == []
    assert apis("### Removed\n\n- `pkg.a`") == [("pkg.a", "removed")]  # a bare name under Removed counts


def test_removed_from_owner():
    assert apis("* 🔥 Remove deprecated `openapi_prefix` and unused `**extra` from `FastAPI`. "
                "PR [#16518](https://github.com/fastapi/fastapi/pull/16518).") == \
        [("FastAPI.openapi_prefix", "removed")]
    # only catch-all arguments named: nothing a call site can be matched to
    assert apis("The deprecated `**extra` keyword arguments have been removed from `Path`, `Query`.") == []


def _usages(tmp_path, code):
    (tmp_path / "main.py").write_text(code)
    return scan_directory(str(tmp_path), "fastapi")


APP = """from fastapi import FastAPI


def lifespan(app: FastAPI):
    yield


app = FastAPI(title="x"{extra})
app.include_router(object())
"""
ROOT_PATH = ChangeEntry(kind="deprecated", api="FastAPI.root_path", version="0.160.0", confidence="medium",
                        description="The `root_path` parameter in `FastAPI()` is deprecated.")
PREFIX = ChangeEntry(kind="removed", api="FastAPI.openapi_prefix", version="0.160.0", confidence="medium",
                     description="Remove deprecated `openapi_prefix` from `FastAPI`.")


def test_parameter_change_only_hits_calls_that_pass_it(tmp_path):
    risks, _ = analyze(_usages(tmp_path, APP.format(extra="")), [ROOT_PATH, PREFIX], "fastapi")
    assert risks == []  # importing, annotating or calling FastAPI without root_path is fine
    risks, _ = analyze(_usages(tmp_path, APP.format(extra=', root_path="/api"')), [ROOT_PATH, PREFIX], "fastapi")
    assert [(r.usage.line, r.severity) for r in risks] == [(8, "deprecated")]
