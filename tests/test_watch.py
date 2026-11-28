"""pyupcheck watch: dependency file changes and new releases, without the network."""

import os

from click.testing import CliRunner

from depshift import cli, output
from depshift.watch import Watcher, dep_file_signature


def write(path, text, tick=[0]):
    path.write_text(text, encoding="utf-8")
    tick[0] += 1  # a distinct mtime each write, however fast the file system
    os.utime(path, ns=(10**18 + tick[0] * 10**9, 10**18 + tick[0] * 10**9))


def fake_check(seen):
    def check(change):
        seen.append((change.name, change.old, change.new))
        return {"package": change.name, "current_version": change.old, "target_version": change.new,
                "risks": [], "breaking_count": 1 if change.name == "flask" else 0,
                "deprecated_count": 0, "safe_count": 0}
    return check


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_signature_covers_dependency_files_only(tmp_path):
    write(tmp_path / "requirements.txt", "a==1\n")
    (tmp_path / "requirements").mkdir()
    write(tmp_path / "requirements" / "dev.txt", "b==1\n")
    write(tmp_path / "uv.lock", "")
    write(tmp_path / "app.py", "import a\n")
    assert sorted(dep_file_signature(str(tmp_path))) == ["requirements.txt", "requirements/dev.txt", "uv.lock"]


def test_change_is_read_once_the_file_settles(tmp_path):
    req = tmp_path / "requirements.txt"
    write(req, "flask==2.2.5\nrequests==2.31.0\nold==1.0\n")
    seen = []
    w = Watcher(str(tmp_path), fake_check(seen))
    assert w.start() == []
    assert w.poll() == []  # nothing changed

    write(req, "flask==3.0.0\nrequests==2.31.0\nnew==1.0\n")
    assert w.poll() == []  # just changed: may still be being written
    (ev,) = w.poll()
    assert ev.kind == "files" and ev.files == ["requirements.txt"]
    assert [(c.name, c.old, c.new) for c in ev.changes] == [("flask", "2.2.5", "3.0.0")]
    assert ev.added == ["new"] and ev.removed == ["old"]
    assert [r["package"] for r in ev.results] == ["flask"] and seen == [("flask", "2.2.5", "3.0.0")]
    assert w.poll() == []  # handled once


def test_a_file_still_changing_waits(tmp_path):
    req = tmp_path / "requirements.txt"
    write(req, "a==1\n")
    w = Watcher(str(tmp_path), fake_check([]))
    w.start()
    write(req, "a==2\n")
    assert w.poll() == []
    write(req, "a==3\n")  # changed again before it settled
    assert w.poll() == []
    (ev,) = w.poll()
    assert [(c.old, c.new) for c in ev.changes] == [("1", "3")]


def test_unreadable_state_keeps_the_last_good_one(tmp_path):
    req = tmp_path / "requirements.txt"
    write(req, "a==1\n")
    from depshift.review import resolved_versions
    calls = {"n": 0}

    def resolve(d):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("half-written lockfile")
        return resolved_versions(d)

    w = Watcher(str(tmp_path), fake_check([]), resolve=resolve)
    w.start()
    write(req, "a==2\n")
    w.poll()
    (err,) = w.poll()
    assert err.kind == "error" and "half-written" in err.message
    write(req, "a==3\n")
    w.poll()
    (ev,) = w.poll()
    assert [(c.old, c.new) for c in ev.changes] == [("1", "3")]  # compared with the last good state


def test_a_failing_check_does_not_stop_the_watch(tmp_path):
    req = tmp_path / "requirements.txt"
    write(req, "a==1\n")

    def check(change):
        raise RuntimeError("PyPI is down")

    w = Watcher(str(tmp_path), check)
    w.start()
    write(req, "a==2\n")
    w.poll()
    (ev,) = w.poll()
    assert ev.results[0]["unchecked"] and "PyPI is down" in ev.results[0]["warnings"][0]


def release_watcher(tmp_path, latest_versions, seen):
    write(tmp_path / "requirements.txt", "flask==2.2.5\nrequests>=2.0\n")
    clock = Clock()
    asked = []

    def latest(name, raw):
        asked.append((name, raw))
        return latest_versions.get(name)

    w = Watcher(str(tmp_path), fake_check(seen), latest=latest, direct=lambda: ["flask", "requests"],
                releases_every=3600, clock=clock)
    return w, clock, asked


def test_new_release_is_checked_once(tmp_path):
    latest = {"flask": "2.3.3", "requests": "2.31.0"}
    seen = []
    w, clock, asked = release_watcher(tmp_path, latest, seen)
    assert w.start() == []  # what's out already is recorded, not reported
    assert ("requests", "requests>=2.0") in asked  # the requirement, for its constraint

    latest["flask"] = "3.0.0"
    clock.t = 1800
    assert w.poll() == []  # not due yet
    clock.t = 3600
    (ev,) = w.poll()
    assert ev.kind == "release"
    assert [(c.name, c.old, c.new) for c in ev.changes] == [("flask", "2.2.5", "3.0.0")]
    assert seen == [("flask", "2.2.5", "3.0.0")]

    clock.t = 7200
    assert w.poll() == []  # the same release is not news twice


def test_release_older_than_what_you_have_is_not_news(tmp_path):
    latest = {"flask": "2.2.0"}
    w, clock, _ = release_watcher(tmp_path, latest, [])
    w.start()
    latest["flask"] = "2.2.4"  # a backport, still older than 2.2.5
    clock.t = 3600
    assert w.poll() == []


def test_upgrading_past_a_release_silences_it(tmp_path):
    latest = {"flask": "2.2.5"}
    seen = []
    w, clock, _ = release_watcher(tmp_path, latest, seen)
    w.start()
    write(tmp_path / "requirements.txt", "flask==3.0.0\nrequests>=2.0\n")
    w.poll()
    w.poll()  # the edit: checked as a file change
    latest["flask"] = "3.0.0"
    clock.t = 3600
    assert w.poll() == []
    assert seen == [("flask", "2.2.5", "3.0.0")]


def test_releases_off_never_asks(tmp_path):
    write(tmp_path / "requirements.txt", "a==1\n")
    w = Watcher(str(tmp_path), fake_check([]), latest=lambda n, r: 1 / 0, direct=lambda: ["a"],
                releases_every=0)
    w.start()
    assert w.poll() == []


def test_cli_starts_and_stops(tmp_path, monkeypatch):
    write(tmp_path / "requirements.txt", "a==1\n")
    monkeypatch.setattr("time.sleep", lambda s: None)
    res = CliRunner().invoke(cli.main, ["watch", "-d", str(tmp_path), "--releases", "0", "--cycles", "2"])
    assert res.exit_code == 0, res.output
    assert "Watching requirements.txt (1 package)." in res.output


def test_cli_prints_release_verdict(capsys):
    from depshift.review import VersionChange
    from depshift.watch import Event
    ok = Event("release", changes=[VersionChange("requests", "2.31.0", "2.32.0", "requests")],
               results=[{"package": "requests", "current_version": "2.31.0", "target_version": "2.32.0",
                         "risks": [], "breaking_count": 0, "deprecated_count": 0, "safe_count": 3}])
    assert output.print_watch_event(ok, ".", quiet=False) is False
    out = capsys.readouterr().out
    assert "New release: requests 2.32.0" in out and "Safe to upgrade" in out
