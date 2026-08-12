"""Extract and diff the public API surface between two versions of a package.

This is the accuracy core of pyupcheck. Instead of guessing breaking changes
from changelog prose, we download both versions, extract their actual public
API (modules, classes, functions, and signatures), and compute a precise diff:
what was removed, what changed signature, what parameters disappeared.
"""

import ast
import io

import tarfile
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import httpx

from depshift.cache import cache_get, cache_set
from depshift.changelog import get_pypi_info


@dataclass
