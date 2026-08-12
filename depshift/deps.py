"""Parse project dependency declarations from requirements.txt, pyproject.toml, setup.cfg, setup.py, and conda environment.yml."""

import ast
import os
import re
from dataclasses import dataclass
from typing import List, Optional

