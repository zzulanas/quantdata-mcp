"""Sanity checks that catch packaging / version drift between
``quantdata_mcp.__version__`` and the version declared in ``pyproject.toml``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import quantdata_mcp


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


def _pyproject_version() -> str:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def test_package_exposes_version() -> None:
    assert isinstance(quantdata_mcp.__version__, str)
    assert quantdata_mcp.__version__  # non-empty


def test_version_matches_pyproject() -> None:
    assert quantdata_mcp.__version__ == _pyproject_version(), (
        "quantdata_mcp.__version__ is out of sync with pyproject.toml — "
        "bump both together."
    )
