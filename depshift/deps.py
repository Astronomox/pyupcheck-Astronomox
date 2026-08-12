"""Parse project dependency declarations from requirements.txt, pyproject.toml, setup.cfg, setup.py, and conda environment.yml."""

import ast
import os
import re
from dataclasses import dataclass
from typing import List, Optional

try:
    import tomllib  # py311+
except ImportError:
    tomllib = None

try:
    import configparser
except ImportError:
    configparser = None


@dataclass
class Dependency:
    name: str
    pinned_version: Optional[str]  # exact version if pinned with ==
    raw: str
    source: str  # which file it came from


_REQ_LINE = re.compile(
    r"^\s*([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(==|>=|<=|~=|!=|>|<)?\s*([\w.*+!-]+)?"
)


def parse_requirement_line(line: str, source: str) -> Optional[Dependency]:
    line = line.split("#")[0].strip()
    if not line or line.startswith(("-", "git+", "http://", "https://", "./", "file:")):
        return None
    m = _REQ_LINE.match(line)
    if not m:
        return None
    name, op, ver = m.group(1), m.group(2), m.group(3)
    pinned = ver if op == "==" else None
    return Dependency(name=name.lower(), pinned_version=pinned, raw=line, source=source)


def parse_requirements_txt(path: str) -> List[Dependency]:
    deps = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                d = parse_requirement_line(line, source=os.path.basename(path))
                if d:
                    deps.append(d)
    except OSError:
        pass
    return deps


def parse_pyproject_toml(path: str) -> List[Dependency]:
    if tomllib is None:
        return []
    deps = []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []

    raw_deps: List[str] = []
    project = data.get("project", {})
    raw_deps.extend(project.get("dependencies", []) or [])
    for group in (project.get("optional-dependencies", {}) or {}).values():
        raw_deps.extend(group or [])

    # poetry style
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies", {}) or {}).items():
        if name.lower() == "python":
            continue
        if isinstance(spec, str):
            raw_deps.append(f"{name}{'' if spec == '*' else spec}")
        else:
            raw_deps.append(name)

    for raw in raw_deps:
        d = parse_requirement_line(raw, source=os.path.basename(path))
        if d:
            deps.append(d)
    return deps


def parse_setup_cfg(path: str) -> List[Dependency]:
    """Parse dependencies from setup.cfg [options] install_requires."""
    if configparser is None:
        return []
    deps = []
    try:
        cfg = configparser.ConfigParser()
        cfg.read(path, encoding="utf-8")
        sections = {
            "options": ["install_requires", "setup_requires"],
            "options.extras_require": None,
        }
        raw_lines: List[str] = []
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


