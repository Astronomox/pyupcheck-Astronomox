"""Map PyPI distribution names to the module names you actually import.

A distribution's install name often differs from its import name
(``pip install pyyaml`` -> ``import yaml``). Every scan needs the import
names, so this resolves them from, in order:

1. installed package metadata (``top_level.txt`` / ``RECORD``)
2. a table of well-known mismatches
3. the PEP 503 normalized name with ``-``/``.`` turned into ``_``
"""

import re
from typing import Dict, List, Optional

# well-known distribution -> import name mismatches (keys are normalized)
KNOWN_IMPORT_NAMES: Dict[str, List[str]] = {
    "pyyaml": ["yaml"],
    "beautifulsoup4": ["bs4"],
    "pillow": ["PIL"],
    "scikit-learn": ["sklearn"],
    "scikit-image": ["skimage"],
    "python-dateutil": ["dateutil"],
    "opencv-python": ["cv2"],
    "opencv-python-headless": ["cv2"],
    "opencv-contrib-python": ["cv2"],
    "opencv-contrib-python-headless": ["cv2"],
    "python-dotenv": ["dotenv"],
    "pymysql": ["pymysql"],
    "mysqlclient": ["MySQLdb"],
    "mysql-connector-python": ["mysql"],
    "psycopg2-binary": ["psycopg2"],
    "psycopg-binary": ["psycopg"],
    "pyjwt": ["jwt"],
    "python-jose": ["jose"],
    "pycryptodome": ["Crypto"],
    "pycryptodomex": ["Cryptodome"],
    "pyopenssl": ["OpenSSL"],
    "attrs": ["attr", "attrs"],
    "protobuf": ["google.protobuf"],
    "google-cloud-storage": ["google.cloud.storage"],
    "google-cloud-bigquery": ["google.cloud.bigquery"],
    "google-api-python-client": ["googleapiclient"],
    "grpcio": ["grpc"],
    "msgpack-python": ["msgpack"],
    "python-magic": ["magic"],
    "python-multipart": ["multipart"],
    "python-slugify": ["slugify"],
    "python-json-logger": ["pythonjsonlogger"],
    "pyserial": ["serial"],
    "pyusb": ["usb"],
    "pywin32": ["win32api", "win32con", "win32com", "pywintypes"],
    "pygobject": ["gi"],
    "pyqt5": ["PyQt5"],
    "pyqt6": ["PyQt6"],
    "pyside2": ["PySide2"],
    "pyside6": ["PySide6"],
    "discord.py": ["discord"],
    "py-cpuinfo": ["cpuinfo"],
    "ruamel.yaml": ["ruamel.yaml"],
    "setuptools": ["setuptools", "pkg_resources"],
    "typing-extensions": ["typing_extensions"],
    "email-validator": ["email_validator"],
    "faiss-cpu": ["faiss"],
    "faiss-gpu": ["faiss"],
    "tensorflow-cpu": ["tensorflow"],
    "tensorflow-gpu": ["tensorflow"],
    "torch": ["torch"],
    "pytorch-lightning": ["pytorch_lightning"],
    "lightning": ["lightning"],
    "sentence-transformers": ["sentence_transformers"],
    "python-telegram-bot": ["telegram"],
    "pytelegrambotapi": ["telebot"],
    "python-docx": ["docx"],
    "python-pptx": ["pptx"],
    "pypdf2": ["PyPDF2"],
    "pymupdf": ["fitz", "pymupdf"],
    "ffmpeg-python": ["ffmpeg"],
    "pyzmq": ["zmq"],
    "pymongo": ["pymongo", "bson", "gridfs"],
    "dnspython": ["dns"],
    "gitpython": ["git"],
    "markdown": ["markdown"],
    "jinja2": ["jinja2"],
    "markupsafe": ["markupsafe"],
    "websocket-client": ["websocket"],
    "djangorestframework": ["rest_framework"],
    "django-cors-headers": ["corsheaders"],
    "django-filter": ["django_filters"],
    "django-environ": ["environ"],
    "flask-sqlalchemy": ["flask_sqlalchemy"],
    "sqlalchemy-utils": ["sqlalchemy_utils"],
    "apache-airflow": ["airflow"],
    "azure-storage-blob": ["azure.storage.blob"],
    "azure-identity": ["azure.identity"],
    "llama-index": ["llama_index"],
    "tree-sitter": ["tree_sitter"],
    "pysocks": ["socks"],
    "uvicorn": ["uvicorn"],
}


def normalize_dist_name(name: str) -> str:
    """PEP 503 normalization: lowercase, runs of -_. collapse to '-'."""
    return re.sub(r"[-_.]+", "-", name).lower()


# keys are written as projects spell them; look them up by normalized name
KNOWN_IMPORT_NAMES = {normalize_dist_name(k): v for k, v in KNOWN_IMPORT_NAMES.items()}


def _namespace_expand(top: str, files: List[str]) -> List[str]:
    """Expand a namespace package root into the packages it actually ships.

    ``google`` (no ``google/__init__.py``) with files under
    ``google/protobuf/`` becomes ``google.protobuf``. Regular packages and
    modules are returned unchanged.
    """
    prefix = top.replace(".", "/") + "/"
    if f"{prefix}__init__.py" in files or not any(f.startswith(prefix) for f in files):
        return [top]
    children: List[str] = []
    for f in files:
        if not f.startswith(prefix):
            continue
        child = f[len(prefix):].split("/", 1)[0]
        if child.endswith((".py", ".pyi")):
            child = child.rsplit(".", 1)[0]
        elif child.endswith((".so", ".pyd")):
            child = child.split(".", 1)[0]
        if child.isidentifier() and child != "__pycache__" and f"{top}.{child}" not in children:
            children.append(f"{top}.{child}")
    out: List[str] = []
    for c in children:
        out.extend(_namespace_expand(c, files))
    return out or [top]


def _from_installed_metadata(dist_name: str) -> List[str]:
    """Import names shipped by an installed distribution (namespace-aware)."""
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover
        return []
    try:
        dist = metadata.distribution(dist_name)
    except Exception:
        return []

    files = [str(f).replace("\\", "/") for f in (dist.files or [])]

    names: List[str] = []
    try:
        top_level = dist.read_text("top_level.txt")
    except Exception:
        top_level = None
    if top_level:
        names = [n.strip().replace("/", ".") for n in top_level.splitlines() if n.strip()]

    if not names:
        # derive from the installed file list (RECORD)
        for f in files:
            parts = f.split("/")
            first = parts[0]
            if first.endswith((".dist-info", ".egg-info", ".data")) or first in ("..", "__pycache__"):
                continue
            if len(parts) == 1:
                if first.endswith((".py", ".pyi")):
                    mod = first.rsplit(".", 1)[0]
                elif ".cpython-" in first or first.endswith((".so", ".pyd")):
                    mod = first.split(".", 1)[0]
                else:
                    continue
            else:
                mod = first
            if mod.isidentifier() and mod not in names:
                names.append(mod)

    expanded: List[str] = []
    for n in names:
        for e in _namespace_expand(n, files):
            if e not in expanded:
                expanded.append(e)
    return [n for n in expanded if n and not n.startswith("_")] or expanded


def _drop_namespace_parents(names: List[str]) -> List[str]:
    """Remove a bare parent (``google``) when a child (``google.protobuf``) is known.

    Scanning the parent would attribute every ``google.*`` import to this one
    distribution.
    """
    return [n for n in names if not any(o.startswith(n + ".") for o in names)]


def import_names_for(dist_name: str) -> List[str]:
    """Return the import name(s) for a distribution, best guess first.

    Always returns at least one name.
    """
    names: List[str] = []

    for n in _from_installed_metadata(dist_name):
        if n not in names:
            names.append(n)

    norm = normalize_dist_name(dist_name)
    for n in KNOWN_IMPORT_NAMES.get(norm, []):
        if n not in names:
            names.append(n)

    names = _drop_namespace_parents(names)

    guess = re.sub(r"[-.]", "_", dist_name)
    if not names:
        names.append(guess)
        lower = guess.lower()
        if lower != guess:
            names.append(lower)
    return names


def import_names_from_archive(paths: List[str]) -> List[str]:
    """Guess top-level import names from archive paths (``yaml/loader.py``)
    or dotted module paths (``yaml.loader``)."""
    out: List[str] = []
    for p in paths:
        p = p.replace("\\", "/")
        if p.endswith((".py", ".pyi")):
            p = p.rsplit(".", 1)[0]
        top = p.replace("/", ".").split(".", 1)[0]
        if top and top.isidentifier() and top not in out:
            out.append(top)
    return out


def primary_import_name(dist_name: str) -> Optional[str]:
    names = import_names_for(dist_name)
    return names[0] if names else None
