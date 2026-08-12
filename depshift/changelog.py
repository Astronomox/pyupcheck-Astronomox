"""Fetch changelog and deprecation data for a package version."""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx


