# Real release notes, for tests

Excerpts of real projects' release notes, so the changelog tests read what
pyupcheck reads in the wild, not text written to suit the parser. Each one
is cut to the versions the tests need and is otherwise unchanged.

| File | Source | License |
|---|---|---|
| `flask_CHANGES.rst` | [pallets/flask `CHANGES.rst`](https://github.com/pallets/flask/blob/main/CHANGES.rst), versions 2.2.5 to 3.0.3 | BSD-3-Clause |
| `numpy_2.0.0-notes.rst` | [numpy/numpy `doc/source/release/2.0.0-notes.rst`](https://github.com/numpy/numpy/blob/main/doc/source/release/2.0.0-notes.rst), up to "Compatibility notes" | BSD-3-Clause |
| `requests_HISTORY.md` | [psf/requests `HISTORY.md`](https://github.com/psf/requests/blob/main/HISTORY.md), the newest releases and 2.30.0 to 2.32.5 | Apache-2.0 |

The tests also wrap sections of these files as GitHub API release objects
and as a releases Atom feed (the two other formats pyupcheck reads); the
text inside them is the same real text.
