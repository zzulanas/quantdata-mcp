"""Shared pytest fixtures for the QuantData MCP test suite.

Combines:
- Fixture-file loaders for the formatter tests (sanitized live API responses)
- Mock client / mock specs for the tool_context refactor tests
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from quantdata_mcp.tools import ToolSpec, ToolType

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture-file loaders (formatter tests)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Mock client / specs (tool_context refactor tests)
# ---------------------------------------------------------------------------

def _make_tool_dto(
    tool_id: str = "tool-net-drift",
    metadata_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a representative tool DTO with realistic, NON-default metadata.

    The test suite uses non-default values to prove that snapshot/restore
    actually preserves user customizations (rather than blindly writing
    hardcoded "GAMMA" / "ONE_MINUTE" defaults).
    """
    metadata: dict[str, Any] = {
        "greekModeType": "DELTA",                       # not the GAMMA default
        "representationModeType": "PER_ONE_DOLLAR_MOVE",  # not PER_ONE_PERCENT_MOVE
        "aggregationPeriodType": "FIVE_MINUTE",          # not ONE_MINUTE
        "dataModeType": "VOLUME",                        # not PREMIUM
        "isNet": False,                                  # not True
        "lookBackPeriod": 90,                            # not 365
        "maturity": 7,                                   # not 30
        "filter": {
            "moneynessMoneyType": {
                "filterOperationType": "EQUALS",
                "value": ["IN_THE_MONEY"],
            },
        },
    }
    if metadata_overrides:
        metadata.update(metadata_overrides)
    return {
        "id": tool_id,
        "userId": "user-123",
        "pageId": "page-abc",
        "type": "OPTIONS_NET_DRIFT_CHART",
        "metadata": metadata,
        "createdTime": 1_700_000_000_000,
        "lastUpdatedTime": 1_700_000_000_000,
    }


@pytest.fixture
def tool_dto() -> dict[str, Any]:
    """A fresh tool DTO for each test."""
    return _make_tool_dto()


@pytest.fixture
def make_tool_dto():
    """Factory so tests can build tool DTOs with custom overrides."""
    return _make_tool_dto


@pytest.fixture
def mock_client(tool_dto: dict[str, Any]) -> MagicMock:
    """A ``QuantDataClient`` mock pre-wired to return ``tool_dto`` from ``get_tool``."""
    client = MagicMock()
    client.get_tool.return_value = tool_dto
    client.set_page_filter.return_value = True
    client.set_tool_time.return_value = True
    client.reset_to_live.return_value = True
    client.update_tool_metadata.return_value = {"ok": True}
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})
    # Sample fetch return values
    client.fetch_net_drift.return_value = {"response": {"netDrift": []}}
    client.fetch_strike_data.return_value = {"response": {}}
    return client


@pytest.fixture
def mock_specs() -> dict[str, ToolSpec]:
    """Spec registry covering every tool key the server uses."""
    keys = [
        ("exposure_by_strike", ToolType.EXPOSURE_BY_STRIKE, "options/exposure/strike"),
        ("net_drift", ToolType.NET_DRIFT, "options/net-drift"),
        ("iv_rank", ToolType.IV_RANK, "options/iv-rank"),
        ("contract_side_stats", ToolType.CONTRACT_SIDE_STATS, "options/contract/statistics/trade-side"),
        ("max_pain", ToolType.MAX_PAIN, "options/max-pain"),
        ("net_flow", ToolType.NET_FLOW, "options/net-flow"),
        ("order_flow", ToolType.ORDER_FLOW_CONSOLIDATED, "options/order-flow/consolidated"),
        ("oi_by_strike", ToolType.OI_BY_STRIKE, "options/open-interest/strike"),
        ("contract_statistics", ToolType.CONTRACT_STATISTICS, "options/contract/statistics"),
        ("exposure_by_expiration", ToolType.EXPOSURE_BY_EXPIRATION, "options/exposure/expiration"),
        ("contract_price_time", ToolType.CONTRACT_PRICE_TIME, "options/contract/price/time"),
    ]
    return {
        name: ToolSpec(tool_id=f"tool-{name}", tool_type=t, endpoint=ep, label=name)
        for name, t, ep in keys
    }


@pytest.fixture
def context_kwargs(mock_client: MagicMock, mock_specs: dict[str, ToolSpec]):
    """Reusable kwargs to pass straight into ``tool_context``."""
    return {
        "get_client": lambda: mock_client,
        "get_specs": lambda: mock_specs,
        "get_page_id": lambda: "page-abc",
    }
