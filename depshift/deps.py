"""Parse project dependency declarations.

Supported: requirements*.txt (with -r/-c includes), pyproject.toml (PEP 621,
PEP 735 dependency-groups, Poetry, PDM, uv, pixi), Pipfile, setup.cfg,
setup.py, conda environment.yml and pixi.toml; and the pyproject.toml of
each member of a uv workspace.
"""

import ast
import configparser
import glob
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial

from ._toml import MISSING_TOML_HINT, tomllib


@dataclass
class Dependency:
    name: str
    pinned_version: str | None  # exact version if pinned with ==
    raw: str
    source: str  # which file it came from


_REQ_LINE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(===|==|>=|<=|~=|!=|>|<)?\s*([\w.*+!-]+)?"
)
# `#egg=name` fragment on VCS / URL requirements
_EGG = re.compile(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize_name(name: str) -> str:
    """PEP 503 normalized project name (Flask_SQLAlchemy -> flask-sqlalchemy)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement_line(line: str, source: str) -> Dependency | None:
    raw = line.strip()
    egg = _EGG.search(raw)
    line = re.split(r"\s#|^#", raw)[0].strip()
    if not line:
        return None
    if egg and line.startswith(("git+", "hg+", "svn+", "bzr+", "http://", "https://", "-e ")):
        return Dependency(name=normalize_name(egg.group(1)), pinned_version=None, raw=line, source=source)
    if line.startswith(("-", "git+", "hg+", "svn+", "bzr+", "http://", "https://", "./", "../", "/",
                        "file:", ".")):
        return None
    # drop environment markers and per-requirement options
    line = line.split(";")[0].split(" --")[0].strip()
    if re.match(r"^[A-Za-z0-9][\w.-]*\s*(?:\[[^\]]*\])?\s*@\s*\S", line):
        # "name @ git+https://..." / "name @ file:///...": installed from
        # there, not from PyPI. A namesake on PyPI is a different package
        return None
    m = _REQ_LINE.match(line)
    if not m:
        return None
    name, op, ver = m.group(1), m.group(2), m.group(3)
    pinned = ver if op in ("==", "===") and ver and "*" not in ver else None
    return Dependency(name=normalize_name(name), pinned_version=pinned, raw=line, source=source)


def _logical_lines(text: str) -> Iterable[str]:
    """Join backslash-continued lines, as pip does."""
    buf = ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        yield buf + line
        buf = ""
    if buf:
        yield buf


def parse_requirements_txt(path: str, _seen: set[str] | None = None) -> list[Dependency]:
    """Parse a pip requirements file, following -r / -c includes."""
    seen = _seen if _seen is not None else set()
    real = os.path.realpath(path)
    if real in seen:
        return []
    seen.add(real)

    deps: list[Dependency] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return deps
    for line in _logical_lines(text):
        stripped = line.strip()
        inc = re.match(r"^(?:-r|--requirement|-c|--constraint)[\s=]+(\S+)", stripped)
        if inc:
            target = os.path.join(os.path.dirname(path), inc.group(1))
            if os.path.isfile(target):
                deps.extend(parse_requirements_txt(target, seen))
            continue
        d = parse_requirement_line(stripped, source=os.path.basename(path))
        if d:
            deps.append(d)
    return deps


def parse_pyproject_toml(path: str) -> list[Dependency]:
    if tomllib is None:
        import warnings

        warnings.warn(
            f"Skipping {path}: dependencies cannot be read. {MISSING_TOML_HINT}",
            RuntimeWarning,
            stacklevel=2,
        )
        return []
    deps = []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        import warnings

        warnings.warn(
            f"Could not parse {path}: {exc}", RuntimeWarning, stacklevel=2
        )
        return []

    raw_deps: list[str] = []

    def add_list(items):
        for item in items or []:
            if isinstance(item, str):  # PEP 735 groups may hold {include-group=...}
                raw_deps.append(item)

    project = data.get("project", {}) or {}
    add_list(project.get("dependencies"))
    for group in (project.get("optional-dependencies", {}) or {}).values():
        add_list(group)

    # PEP 735 dependency groups, following {include-group = "..."} entries
    groups = data.get("dependency-groups", {}) or {}

    def expand_group(name, stack):
        if name in stack:
            return  # include cycle
        for item in groups.get(name, []) or []:
            if isinstance(item, str):
                raw_deps.append(item)
            elif isinstance(item, dict) and isinstance(item.get("include-group"), str):
                expand_group(item["include-group"], stack | {name})

    for group_name in groups:
        expand_group(group_name, frozenset())

    tool = data.get("tool", {}) or {}

    # PDM and uv dev dependencies
    for group in ((tool.get("pdm", {}) or {}).get("dev-dependencies", {}) or {}).values():
        add_list(group)
    add_list((tool.get("uv", {}) or {}).get("dev-dependencies"))

    # poetry style: main deps, legacy dev-dependencies, and groups
    poetry = tool.get("poetry", {}) or {}
    poetry_tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    for group in (poetry.get("group", {}) or {}).values():
        poetry_tables.append((group or {}).get("dependencies", {}))
    for table in poetry_tables:
        raw_deps.extend(_poetry_style(table))

    pixi = tool.get("pixi")
    if isinstance(pixi, dict):
        from .conda import pixi_lock_names, pixi_requirements
        lock = os.path.join(os.path.dirname(path), "pixi.lock")
        raw_deps.extend(pixi_requirements(pixi, pixi_lock_names(lock) if os.path.isfile(lock) else None))

    # [tool.uv.sources]: names installed from a folder, the workspace, git or a URL
    elsewhere = {normalize_name(n) for n, src in (((tool.get("uv") or {}).get("sources") or {}).items())
                 if isinstance(src, dict) and any(k in src for k in ("path", "workspace", "git", "url"))}
    for raw in raw_deps:
        d = parse_requirement_line(raw, source=os.path.basename(path))
        if d and d.name not in elsewhere:
            deps.append(d)
    return deps


def _poetry_style(table) -> list[str]:
    """Convert a Poetry/Pipfile {name: spec} table to requirement strings."""
    out = []
    for name, spec in (table or {}).items():
        if name.lower() == "python":
            continue
        if isinstance(spec, dict):
            if any(k in spec for k in ("path", "git", "url", "file")):
                continue  # installed from a folder, repository or URL, not PyPI
            spec = spec.get("version", "*")
        if isinstance(spec, str):
            spec = spec.strip()
            if spec in ("*", ""):
                out.append(name)
            elif spec[0].isdigit():
                out.append(f"{name}=={spec}")  # poetry bare version means exact
            elif spec.startswith(("^", "~")) and not spec.startswith("~="):
                out.append(f"{name}>={spec.lstrip('^~')}")  # caret/tilde: lower bound
            else:
                out.append(f"{name}{spec}")
        else:
            out.append(name)
    return out


def parse_pipfile(path: str) -> list[Dependency]:
    """Parse a Pipenv Pipfile ([packages] and [dev-packages])."""
    if tomllib is None:
        return []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []
    deps = []
    for section in ("packages", "dev-packages"):
        for raw in _poetry_style(data.get(section, {})):
            d = parse_requirement_line(raw, source="Pipfile")
            if d:
                deps.append(d)
    return deps


def parse_setup_cfg(path: str) -> list[Dependency]:
    """Parse dependencies from setup.cfg [options] install_requires."""
    deps = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(path, encoding="utf-8")
        sections = {
            "options": ["install_requires", "setup_requires"],
            "options.extras_require": None,
        }
        raw_lines: list[str] = []
        for section, keys in sections.items():
            if not cfg.has_section(section):
                continue
            if keys is None:
                for key in cfg.options(section):
                    raw_lines.extend(cfg.get(section, key).splitlines())
            else:
                for key in keys:
                    if cfg.has_option(section, key):
                        raw_lines.extend(cfg.get(section, key).splitlines())
        for line in raw_lines:
            d = parse_requirement_line(line, source="setup.cfg")
            if d:
                deps.append(d)
    except Exception:
        pass
    return deps


def parse_setup_py(path: str) -> list[Dependency]:
    """Best-effort AST parse of setup.py to extract install_requires."""
    deps = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source)
    except Exception:
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_setup = (isinstance(func, ast.Name) and func.id == "setup") or \
                   (isinstance(func, ast.Attribute) and func.attr == "setup")
        if not is_setup:
            continue
        for kw in node.keywords:
            if kw.arg not in ("install_requires", "setup_requires", "extras_require"):
                continue
            target = kw.value
            # flatten extras_require dict values
            if isinstance(target, ast.Dict):
                for v in target.values:
                    if isinstance(v, ast.List):
                        for elt in v.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                d = parse_requirement_line(elt.value, "setup.py")
                                if d:
                                    deps.append(d)
            elif isinstance(target, ast.List):
                for elt in target.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        d = parse_requirement_line(elt.value, "setup.py")
                        if d:
                            deps.append(d)
    return deps


def parse_conda_env(path: str) -> list[Dependency]:
    """Parse conda environment.yml for pip dependencies."""
    deps = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        in_pip = False
        in_deps = False
        deps_indent = 0
        pip_indent = 0
        for line in lines:
            stripped = line.split("#")[0].strip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            if stripped == "dependencies:":
                in_deps, deps_indent = True, indent
                continue
            if stripped == "- pip:":
                in_pip = True
                pip_indent = indent
                continue
            if in_deps and not in_pip and indent <= deps_indent and not stripped.startswith("-"):
                in_deps = False
            if in_deps and not in_pip and stripped.startswith("-"):
                # conda spec: [channel::]name[=version[=build]]
                spec = stripped.lstrip("- ").strip().split("::")[-1]
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==?|>=|<=|>|<)?\s*([\w.*+!]+)?", spec)
                if m and m.group(1).lower() not in ("python", "pip"):
                    from .conda import pypi_name
                    ver = m.group(3) if m.group(2) in ("=", "==") and m.group(3) and "*" not in m.group(3) else None
                    deps.append(Dependency(name=normalize_name(pypi_name(m.group(1))), pinned_version=ver,
                                           raw=spec, source="environment.yml"))
                continue
            if in_pip:
                # Only lines indented deeper than "- pip:" belong to the pip list.
                if indent > pip_indent and stripped.startswith("-"):
                    raw = stripped.lstrip("- ").strip()
                    d = parse_requirement_line(raw, "environment.yml")
                    if d:
                        deps.append(d)
                else:
                    in_pip = False
    except OSError:
        pass
    return deps


def requirement_files(directory: str) -> list[str]:
    """All pip requirement files: requirements*.txt, requirements/*.txt, *.in."""
    patterns = ["requirements*.txt", "requirements*.in", "requirements/*.txt",
                "requirements/*.in", "reqs/*.txt"]
    out: list[str] = []
    for pat in patterns:
        for path in sorted(glob.glob(os.path.join(directory, pat))):
            if os.path.isfile(path) and path not in out:
                out.append(path)
    # conventional main file first so its pins win on duplicates
    main = os.path.join(directory, "requirements.txt")
    if main in out:
        out.remove(main)
        out.insert(0, main)
    return out


def _read_toml(path: str) -> dict:
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def workspace_patterns(pyproject: dict) -> tuple[list[str], list[str]]:
    """(members, exclude) globs of a uv workspace ([tool.uv.workspace])."""
    ws = ((pyproject.get("tool") or {}).get("uv") or {}).get("workspace") or {}
    if not isinstance(ws, dict):
        return [], []

    def strings(key):
        items = ws.get(key) or []
        return [x.strip("/") for x in items if isinstance(x, str) and x.strip("/")] if isinstance(items, list) else []
    return strings("members"), strings("exclude")


def is_workspace_member(rel_dir: str, members: list[str], exclude: list[str]) -> bool:
    """Does a project-relative folder ('backend', 'packages/api') match the
    workspace's member globs and none of its excludes?"""
    from fnmatch import fnmatchcase
    if not rel_dir or rel_dir.startswith(("/", "..")) or "/../" in f"/{rel_dir}/":
        return False
    return (any(fnmatchcase(rel_dir, m) for m in members)
            and not any(fnmatchcase(rel_dir, x) for x in exclude))


def _local_paths(pyproject: dict) -> list[str]:
    """Folders the project installs dependencies from: Poetry ``path =``,
    uv ``[tool.uv.sources]`` paths, and ``name @ file:...`` references
    (PDM writes ``file:///${PROJECT_ROOT}/...``)."""
    out: list[str] = []
    tool = pyproject.get("tool") or {}
    poetry = tool.get("poetry") or {}
    tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
    tables += [(g or {}).get("dependencies") or {} for g in (poetry.get("group") or {}).values()]
    for table in tables:
        for spec in table.values():
            if isinstance(spec, dict) and isinstance(spec.get("path"), str):
                out.append(spec["path"])
    for src in ((tool.get("uv") or {}).get("sources") or {}).values():
        if isinstance(src, dict) and isinstance(src.get("path"), str):
            out.append(src["path"])
    project = pyproject.get("project") or {}
    reqs = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        reqs += list(group or [])
    for raw in reqs:
        m = re.search(r"@\s*file:(?://)?(\S+)", raw) if isinstance(raw, str) else None
        if m:
            out.append(m.group(1).replace("${PROJECT_ROOT}", ".").lstrip("/") if "${PROJECT_ROOT}" in m.group(1)
                       else m.group(1))
    return out


def workspace_members(directory: str) -> list[str]:
    """The member folders of the uv workspace rooted here, relative to it:
    folders matching ``[tool.uv.workspace] members`` (minus ``exclude``)
    that have a pyproject.toml. Never a folder outside the project, also
    not through a symlink."""
    root_toml = _read_toml(os.path.join(directory, "pyproject.toml"))
    members, exclude = workspace_patterns(root_toml)
    root = os.path.realpath(directory)
    found = []
    # local path dependencies inside the project are its members too (a
    # Poetry, PDM or Hatch monorepo has no workspace table)
    for rel in _local_paths(root_toml):
        path = os.path.join(directory, rel)
        real = os.path.realpath(path)
        rel_norm = os.path.relpath(real, root).replace(os.sep, "/")
        if (not os.path.isabs(rel) and real != root and real.startswith(root + os.sep)
                and os.path.isfile(os.path.join(real, "pyproject.toml")) and rel_norm not in found):
            found.append(rel_norm)
    if not members:
        return found
    for pattern in members:
        for path in sorted(glob.glob(os.path.join(directory, pattern))):
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            real = os.path.realpath(path)
            if (os.path.isfile(os.path.join(path, "pyproject.toml")) and real != root
                    and real.startswith(root + os.sep) and is_workspace_member(rel, members, exclude)
                    and rel not in found):
                found.append(rel)
    return found


def parse_pixi_toml(path: str) -> list[Dependency]:
    """Conda and PyPI dependencies declared in pixi.toml."""
    from .conda import read_pixi_toml
    return [d for d in (parse_requirement_line(r, "pixi.toml") for r in read_pixi_toml(path)) if d]


def discover_dependencies(directory: str) -> list[Dependency]:
    """Find and parse all dependency files in a directory."""
    deps: list[Dependency] = []
    seen = set()

    seen_req: set[str] = set()  # requirement files reached through -r: read once
    parsers: list[tuple[str, Callable[[str], list[Dependency]]]] = [
        (p, partial(parse_requirements_txt, _seen=seen_req)) for p in requirement_files(directory)]
    parsers += [
        (os.path.join(directory, "pyproject.toml"), parse_pyproject_toml),
        (os.path.join(directory, "Pipfile"), parse_pipfile),
        (os.path.join(directory, "setup.cfg"), parse_setup_cfg),
        (os.path.join(directory, "setup.py"), parse_setup_py),
        (os.path.join(directory, "environment.yml"), parse_conda_env),
        (os.path.join(directory, "environment.yaml"), parse_conda_env),
        (os.path.join(directory, "pixi.toml"), parse_pixi_toml),
    ]

    for path, parser in parsers:
        if not os.path.isfile(path):
            continue
        for d in parser(path):
            if d.name not in seen:
                seen.add(d.name)
                deps.append(d)

    # a uv workspace: each member's dependencies are the project's too. The
    # members themselves (installed from the workspace) aren't on PyPI
    members = workspace_members(directory)
    own = {normalize_name(str((_read_toml(os.path.join(directory, m, "pyproject.toml")).get("project") or {})
                               .get("name") or "")) for m in members}
    for member in members:
        for d in parse_pyproject_toml(os.path.join(directory, member, "pyproject.toml")):
            d.source = f"{member}/pyproject.toml"
            if d.name not in seen and d.name not in own:
                seen.add(d.name)
                deps.append(d)
    return [d for d in deps if d.name not in own]

