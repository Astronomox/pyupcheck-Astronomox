"""Notebooks: cells scanned as one module, and rewritten in place."""

import json

from depshift import notebook
from depshift.changelog import parse_changelog_text
from depshift.rewrite import apply_edits, plan_migration
from depshift.scanner import scan_notebook
from test_rewrite import FLASK_NOTE, FLASK_NEW, FLASK_OLD, surfaces


def write_nb(tmp_path, *cells, name="a.ipynb"):
    """Cells as strings; a ("markdown", text) tuple makes a markdown cell."""
    out = []
    for c in cells:
        kind, text = c if isinstance(c, tuple) else ("code", c)
        out.append({"cell_type": kind, "metadata": {}, "source": text.splitlines(keepends=True),
                    **({"outputs": [], "execution_count": None} if kind == "code" else {})})
    path = tmp_path / name
    path.write_text(json.dumps({"cells": out, "metadata": {"k": 1}, "nbformat": 4,
                                "nbformat_minor": 5}, indent=1) + "\n", encoding="utf-8")
    return path


def test_import_in_one_cell_followed_into_later_cells(tmp_path):
    path = write_nb(tmp_path, "import numpy as np\n%matplotlib inline", ("markdown", "# hi"),
                    "%%time\nx = 1\ny = np.float_(3)", "!pip list\nz = np.asfarray([1])")
    found = {(u.cell, u.cell_line, u.attr_chain) for u in scan_notebook(str(path), "numpy")}
    assert (3, 3, "numpy.float_") in found  # line within its cell, counting the %%time line
    assert (4, 2, "numpy.asfarray") in found  # cell numbers count markdown cells too
    assert all(u.code.startswith(f"[cell {u.cell}] ") for u in scan_notebook(str(path), "numpy"))


def test_line_is_the_notebook_file_line(tmp_path):
    path = write_nb(tmp_path, "import numpy as np", "np.float_(1)", "x = 2\nnp.float_(1)")
    usages = [u for u in scan_notebook(str(path), "numpy") if u.attr_chain == "numpy.float_"]
    # the same call on line 1 of one cell and line 2 of another: two findings
    assert [(u.cell, u.cell_line) for u in usages] == [(2, 1), (3, 2)]
    file_lines = path.read_text().splitlines()
    assert all(file_lines[u.line - 1].strip().startswith('"np.float_(1)') for u in usages)


def test_shell_magic_and_unparseable_cells_are_skipped(tmp_path):
    path = write_nb(tmp_path, "import numpy as np", "%%bash\necho np.float_", "np.float_(",
                    "files = !ls\nnp?\nnp.float_?? ", "for i in x:\n    %time f()\nnp.int_")
    found = {(u.cell, u.attr_chain) for u in scan_notebook(str(path), "numpy")}
    assert found == {(1, "numpy"), (5, "numpy.int_")}


def test_round_trip_is_exact(tmp_path):
    path = write_nb(tmp_path, "import a\n%time a.f()\n", "%%writefile x.py\nprint(1)", "a.g()")
    nb = notebook.load(str(path))
    assert notebook.restore(nb, nb.source) == json.loads(path.read_text())


def test_not_a_notebook(tmp_path):
    (tmp_path / "x.ipynb").write_text("{broken", encoding="utf-8")
    assert notebook.load(str(tmp_path / "x.ipynb")) is None
    assert scan_notebook(str(tmp_path / "x.ipynb"), "numpy") == []


def migrate_nb(path, old, new, changelog, pkg, **kw):
    changes, new_surface = surfaces(old, new)
    entries = parse_changelog_text(changelog, "2.0")
    plan = plan_migration(scan_notebook(str(path), pkg), changes, new_surface, pkg, entries, **kw)
    nb = notebook.load(str(path))
    after = apply_edits(nb.source, [e for f in plan.fixes for e in f.edits])
    return plan, notebook.restore(nb, after)


def test_rewrite_lands_in_the_right_cell(tmp_path):
    path = write_nb(tmp_path, "import numpy as np\n%matplotlib inline", ("markdown", "# x"),
                    "%%time\ndtype = np.float_\n!echo done")
    plan, data = migrate_nb(path, {"numpy": "float_ = float\nfloat64 = float"},
                            {"numpy": "float64 = float"},
                            "- ``np.float_`` has been removed, use ``np.float64`` instead.", "numpy")
    assert [(f.cell, f.line) for f in plan.fixes] == [(3, 2)]
    cells = ["".join(c["source"]) for c in data["cells"]]
    assert cells == ["import numpy as np\n%matplotlib inline", "# x",
                     "%%time\ndtype = np.float64\n!echo done"]
    assert data["metadata"] == {"k": 1}


def test_new_import_goes_into_the_import_cell(tmp_path):
    path = write_nb(tmp_path, "import flask", "x = flask.Markup('a')")
    plan, data = migrate_nb(path, FLASK_OLD, FLASK_NEW, FLASK_NOTE, "flask",
                            verify_external=lambda d: True)
    assert ["".join(c["source"]) for c in data["cells"]] == \
        ["import flask\nimport markupsafe", "x = markupsafe.Markup('a')"]


def test_manual_items_carry_the_cell(tmp_path):
    path = write_nb(tmp_path, "import numpy as np", "y = np.gone(1)")
    plan, _ = migrate_nb(path, {"numpy": "def gone(x): pass"}, {"numpy": "x = 1"},
                         "- ``np.gone`` has been removed.", "numpy")
    assert [(m.cell, m.line) for m in plan.manual] == [(2, 1)]


def test_terminal_output_keeps_cell_prefix_and_cleans_notes(capsys):
    from depshift.cli import _print_terminal_result
    risk = {"file": "a.ipynb", "line": 27, "code": "[cell 3] x = np.float_(3)", "severity": "breaking",
            "change_description": "Removed ``np.float_`` [bold]. :pr:`12`", "api": "numpy.float_"}
    _print_terminal_result({"package": "numpy", "current_version": "1.26.4", "target_version": "2.0.0",
                            "risks": [risk], "breaking_count": 1, "deprecated_count": 0, "safe_count": 0},
                           quiet=False)
    out = capsys.readouterr().out
    assert "[cell 3] x = np.float_(3)" in out
    assert "Removed `np.float_` [bold]." in out and ":pr:" not in out
