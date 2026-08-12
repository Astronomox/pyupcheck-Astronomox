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


