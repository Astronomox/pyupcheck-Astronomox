"""The GitHub token pyupcheck uses: an explicit one, GH_TOKEN, else the
GitHub CLI's (gh auth token), never a gh inside the project; and how
anonymous rate limiting is remembered for the rest of the run."""

import stat
import sys

import pytest

from depshift import changelog as cl

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake gh is a POSIX shell script")

TOKEN = "gho_" + "a" * 36


def fake_gh(folder, body):
    folder.mkdir(parents=True, exist_ok=True)
    gh = folder / "gh"
    gh.write_text("#!/bin/sh\n" + body + "\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return gh


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(cl, "_found_token", [])
    monkeypatch.delenv("PYUPCHECK_NO_GH_AUTH", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


def test_the_cli_token_is_used_and_asked_once(tmp_path, monkeypatch, fresh):
    log = tmp_path / "calls"
    fake_gh(tmp_path / "bin", f'echo "$@" >> {log}\necho {TOKEN}')
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")  # gh lives outside the folder being checked
    assert cl.github_token_for(None) == TOKEN
    assert cl.github_token_for(None) == TOKEN
    assert log.read_text().splitlines() == ["auth token --hostname github.com"]  # once per run
    assert cl.github_token_for("explicit") == "explicit"


def test_gh_token_env_comes_before_the_cli(tmp_path, monkeypatch, fresh):
    fake_gh(tmp_path / "bin", "exit 99")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("GH_TOKEN", "from-env")
    assert cl.github_token_for(None) == "from-env"


def test_a_gh_inside_the_project_is_never_run(tmp_path, monkeypatch, fresh):
    project = tmp_path / "project"
    marker = tmp_path / "ran"
    fake_gh(project / "tools", f"touch {marker}\necho {TOKEN}")
    monkeypatch.setenv("PATH", str(project / "tools"))
    monkeypatch.chdir(project)
    assert cl.gh_cli_token() is None and not marker.exists()
    cl._found_token.clear()
    monkeypatch.chdir(tmp_path)
    assert cl.gh_cli_token(project_dir=str(project)) is None and not marker.exists()


@pytest.mark.parametrize("body", ["echo 'not a token!'", "exit 1", "sleep 30"])
def test_unusable_answers_give_no_token(body, tmp_path, monkeypatch, fresh):
    fake_gh(tmp_path / "bin", body)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    if body.startswith("sleep"):
        import subprocess
        real = subprocess.run

        def quick(*a, **k):
            k["timeout"] = 0.2
            return real(*a, **k)
        monkeypatch.setattr(subprocess, "run", quick)
    assert cl.gh_cli_token() is None


def test_lookup_can_be_turned_off(tmp_path, monkeypatch, fresh):
    marker = tmp_path / "ran"
    fake_gh(tmp_path / "bin", f"touch {marker}\necho {TOKEN}")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("PYUPCHECK_NO_GH_AUTH", "1")
    assert cl.gh_cli_token() is None and not marker.exists()


def test_no_gh_installed(monkeypatch, fresh, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert cl.gh_cli_token() is None


def test_rate_limit_is_remembered_for_the_run(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, status, text=""):
            self.status_code, self.text = status, text

        def raise_for_status(self):
            raise OSError(f"HTTP {self.status_code}")

    def get(url, **kw):
        calls.append(url)
        return Resp(403) if "api.github.com" in url else Resp(404)
    monkeypatch.setattr(cl, "http_get", get)
    with pytest.raises(OSError):
        cl.fetch_github_releases("a", "one")
    assert cl._api_exhausted
    with pytest.raises(RuntimeError, match="rate limit"):
        cl.fetch_github_releases("b", "two")
    assert [u for u in calls if "api.github.com" in u] == [cl.GITHUB_RELEASES_API.format(owner="a", repo="one")
                                                         + "?per_page=50&page=1"]
    assert any("b/two/releases.atom" in u for u in calls)  # the feed was still tried
