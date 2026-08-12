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


