"""Deep mode end to end without the network: choosing which file of a release
to read, downloading it (faked), extracting both surfaces, caching them, and
following a code-less meta-package to the package it pins."""

import io
import tarfile
import zipfile

import pytest

from depshift import apisurface as api


def wheel(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def sdist(root: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


V1 = {"mypkg/__init__.py": "def old(a, b=1):\n    pass\n\ndef kept(x):\n    pass\n\nclass Thing:\n    def go(self):\n        pass\n"}
V2 = {"mypkg/__init__.py": "def kept(*, x):\n    pass\n\nclass Thing:\n    pass\n"}


class Resp:
    def __init__(self, content=b"", status=200):
        self.content, self.status = content, status

    def raise_for_status(self):
        if self.status >= 400:
            raise OSError(self.status)


@pytest.fixture
def pypi(monkeypatch):
    """A fake PyPI: {(package, version): [file dicts]} and {url: bytes}."""
    state = {"files": {}, "blobs": {}, "requires": {}, "downloads": []}

    def version_info(package, version):
        if (package, version) not in state["files"]:
            raise LookupError("404")
        return {"urls": state["files"][(package, version)],
                "info": {"requires_dist": state["requires"].get((package, version))}}

    def get(url, **kw):
        state["downloads"].append(url)
        return Resp(state["blobs"][url]) if url in state["blobs"] else Resp(status=404)
    monkeypatch.setattr(api, "get_pypi_version_info", version_info)
    monkeypatch.setattr(api, "get_pypi_info", lambda p: {"releases": {}})
    monkeypatch.setattr(api, "http_get", get)
    return state


def publish(state, package, version, blob, filename, **extra):
    url = f"https://files.example/{filename}"
    state["files"].setdefault((package, version), []).append({"filename": filename, "url": url, **extra})
    state["blobs"][url] = blob


def test_diff_between_two_released_wheels(pypi):
    publish(pypi, "mypkg", "1.0", wheel(V1), "mypkg-1.0-py3-none-any.whl")
    publish(pypi, "mypkg", "2.0", wheel(V2), "mypkg-2.0-py3-none-any.whl")
    changes = {(c.kind, c.api) for c in api.get_api_changes("mypkg", "1.0", "2.0")}
    assert ("removed_function", "mypkg.old") in changes
    assert any(api_ == "mypkg.kept" for _, api_ in changes)  # x became keyword-only
    assert any(api_.startswith("mypkg.Thing.go") for _, api_ in changes)


def test_surfaces_are_cached(pypi):
    publish(pypi, "mypkg", "1.0", wheel(V1), "mypkg-1.0-py3-none-any.whl")
    first = api.extract_surface("mypkg", "1.0")
    again = api.extract_surface("mypkg", "1.0")
    assert len(pypi["downloads"]) == 1
    assert again.functions.keys() == first.functions.keys() and again.classes == first.classes
    assert again.functions["mypkg.old"].params == first.functions["mypkg.old"].params


def test_pure_wheel_preferred_then_sdist(pypi):
    publish(pypi, "p", "1", b"", "p-1-cp312-cp312-win_amd64.whl")
    publish(pypi, "p", "1", b"", "p-1-py3-none-any.whl")
    publish(pypi, "p", "1", b"", "p-1.tar.gz")
    assert api._find_sdist_or_wheel_url("p", "1") == ("https://files.example/p-1-py3-none-any.whl", "wheel")
    publish(pypi, "q", "1", b"", "q-1.tar.gz")
    publish(pypi, "q", "1", b"", "q-1.zip")
    assert api._find_sdist_or_wheel_url("q", "1") == ("https://files.example/q-1.tar.gz", "sdist")


def test_wheel_ranking():
    assert [api._wheel_score(n) for n in ("a-1-py3-none-any.whl", "a-1-py2-none-any.whl",
                                          "a-1-cp311-cp311-manylinux_2_17_x86_64.whl",
                                          "a-1-cp311-cp311-macosx_11_0_arm64.whl", "a-1-pp39-pypy39_pp73-win.whl")] \
        == [0, 1, 2, 3, 4]


def test_yanked_only_release_is_still_read_and_huge_files_skipped(pypi):
    publish(pypi, "y", "1", b"", "y-1-py3-none-any.whl", yanked=True)
    assert api._find_sdist_or_wheel_url("y", "1")[1] == "wheel"
    publish(pypi, "big", "1", b"", "big-1-py3-none-any.whl", size=api.MAX_DOWNLOAD_BYTES + 1)
    assert api._find_sdist_or_wheel_url("big", "1") is None


def test_network_failure_is_not_remembered(pypi, monkeypatch):
    calls = []

    def down(*a):
        calls.append(1)
        raise OSError("offline")
    monkeypatch.setattr(api, "get_pypi_version_info", down)
    monkeypatch.setattr(api, "get_pypi_info", down)
    assert api._find_sdist_or_wheel_url("z", "1") is None
    assert api._find_sdist_or_wheel_url("z", "1") is None
    assert len(calls) == 4  # asked again: an outage is not "this release has no files"


def test_missing_files_and_failed_downloads(pypi):
    assert api.extract_surface("nothing", "1") is None
    pypi["files"][("gone", "1")] = [{"filename": "gone-1-py3-none-any.whl", "url": "https://files.example/x"}]
    assert api.extract_surface("gone", "1") is None
    assert api.get_api_changes("gone", "1", "2") is None


def test_sdist_is_read_like_a_wheel(pypi):
    publish(pypi, "mypkg", "1.0", sdist("mypkg-1.0", {"src/" + k: v for k, v in V1.items()}), "mypkg-1.0.tar.gz")
    surface = api.extract_surface("mypkg", "1.0")
    assert surface is not None and "mypkg.old" in surface.functions


def test_meta_package_follows_its_exact_pin(pypi):
    publish(pypi, "meta", "4.0", wheel({"meta-4.0.dist-info/METADATA": "Name: meta\n"}), "meta-4.0-py3-none-any.whl")
    pypi["requires"][("meta", "4.0")] = ["mypkg==1.0", "extra-thing==2; extra == 'cli'", "loose>=1", "bad req !!"]
    publish(pypi, "mypkg", "1.0", wheel(V1), "mypkg-1.0-py3-none-any.whl")
    surface = api.extract_surface("meta", "4.0")
    assert surface is not None and "mypkg.old" in surface.functions and surface.version == "4.0"
    assert api._pinned_deps("meta", "4.0") == [("mypkg", "1.0")]
    assert api._pinned_deps("unknown", "1") == []


def test_return_types_from_annotations(pypi):
    src = {"mypkg/__init__.py": "class Engine:\n    pass\n\ndef create_engine() -> Engine:\n    pass\n\n"
                                "def a() -> int:\n    pass\n"}
    publish(pypi, "mypkg", "1.0", wheel(src), "mypkg-1.0-py3-none-any.whl")
    assert api.get_return_types("mypkg", "1.0", "9.9").get("create_engine") == "Engine"
