"""Direct tests for the per-tool-type filter builders in
``quantdata_mcp.filters``. Most filter semantics are covered transitively
by ``tests/test_order_flow_filters.py`` (which exercises the builder
through the MCP wrapper), but the builders are also a public API for
the saved-query / page-tool layers — so we pin a few representative
cases at the builder level here.
"""

from __future__ import annotations

from quantdata_mcp.filters import build_order_flow_filter
from quantdata_mcp.tools import (
    ContractTypeFilter,
    MoneynessType,
    SentimentType,
    TradeSideCodeType,
)


def test_no_kwargs_returns_all_none_values() -> None:
    """With no kwargs, every entry in the returned dict is None.

    `tool_context` drops None entries before serialising, so an empty
    builder call produces zero filter clauses on the wire.
    """
    out = build_order_flow_filter()
    assert all(v is None for v in out.values())
    # All 31 expected fields are present (so callers / mergers see the
    # full key surface even when nothing is set).
    assert len(out) == 31


def test_eq_clause_for_bool_flag() -> None:
    out = build_order_flow_filter(is_unusual=True)
    assert out["isUnusual"] == {"filterOperationType": "EQUALS", "value": True}


def test_gte_clause_for_threshold() -> None:
    out = build_order_flow_filter(min_size=100)
    assert out["size"] == {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO", "value": 100}


def test_lte_clause_for_max_dte() -> None:
    out = build_order_flow_filter(max_dte=14.0)
    assert out["fractionalDaysToExpiration"] == {
        "filterOperationType": "LESS_THAN_OR_EQUAL_TO",
        "value": 14.0,
    }


def test_dollar_to_cents_conversion() -> None:
    out = build_order_flow_filter(min_premium=10_000.0)
    assert out["premiumInCents"]["value"] == 1_000_000  # 10_000 dollars → 1M cents


def test_enum_list_unwraps_values() -> None:
    out = build_order_flow_filter(
        sentiment_type=[SentimentType.BULLISH, SentimentType.BEARISH],
        contract_type=ContractTypeFilter.CALL,
        moneyness=[MoneynessType.OTM],
        trade_side=[TradeSideCodeType.AA],
    )
    assert out["sentimentType"]["value"] == ["BULLISH", "BEARISH"]
    assert out["contractType"]["value"] == "CALL"
    assert out["moneynessMoneyType"]["value"] == ["OUT_OF_THE_MONEY"]
    assert out["tradeSideCodeType"]["value"] == ["AA"]


def test_freeform_list_passes_through() -> None:
    out = build_order_flow_filter(sector=["TECHNOLOGY", "FINANCE"])
    assert out["sectorType"]["value"] == ["TECHNOLOGY", "FINANCE"]


def test_unconsolidated_subset_no_golden_sweep_arg() -> None:
    """Calling without `is_golden_sweep` (the unconsolidated case) leaves
    that key as None — the consumer never sends it to the API."""
    out = build_order_flow_filter(is_unusual=True)
    assert out["isGoldenSweep"] is None
    assert out["tradeConsolidationType"] is None
