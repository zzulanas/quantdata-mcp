"""Pytest config — common fixture loader for the formatter tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture by basename (without .json suffix)."""
    path = FIXTURES_DIR / f"{name}.json"
    with path.open() as f:
        data: dict[str, Any] = json.load(f)
    return data


@pytest.fixture
def order_flow_response() -> dict[str, Any]:
    """Sanitized sample of /api/options/order-flow/consolidated/{tool_id}."""
    return _load_fixture("order_flow")


@pytest.fixture
def contract_price_response() -> dict[str, Any]:
    """Sanitized sample of /api/options/contract/price/time/{tool_id}."""
    return _load_fixture("contract_price")


@pytest.fixture
def net_flow_response() -> dict[str, Any]:
    """Sanitized sample of /api/options/net-flow/{tool_id}."""
    return _load_fixture("net_flow")
