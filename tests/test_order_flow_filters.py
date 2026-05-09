"""End-to-end wiring tests for the expanded ``qd_get_order_flow`` filter set.

These tests assert that each new kwarg lands as the right filter clause in the
PUT /tool payload sent to the API. They do NOT exercise the upstream
QuantData service — the mock client just records the payloads.

Coverage strategy:
- One representative bool-flag test (covers the EQUALS-with-bool shape).
- One EQUALS test for a multi-select list (sentiment_type).
- One GTE test (min_size).
- One LTE test (max_dte).
- One float->cents conversion test (min_premium, min_bid_ask_spread).
- One greek delta floor test (min_delta — the API allows only one operator
  per greek field, so there is no BETWEEN/range form).
- One open-ended list test for free-form codes (trade_type, sector).
- A "kitchen sink" test that combines several filters at once and asserts
  none of them clobber each other.
- A None-default test that asserts NO new filter keys appear when callers
  don't pass any of the new kwargs (backwards-compat guarantee).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from quantdata_mcp import server
from quantdata_mcp.tools import (
    ContractTypeFilter,
    MoneynessType,
    SentimentType,
    ToolSpec,
    ToolType,
)

# ---------------------------------------------------------------------------
# Local fixtures: an order_flow tool DTO with the full 46-key filter scaffold
# ---------------------------------------------------------------------------

ORDER_FLOW_FILTER_SCAFFOLD: dict[str, Any] = {
    # Numeric / threshold fields — empty EQUALS scaffolding mimics what the
    # QuantData UI returns when no filter is set.
    "askPriceInCents": {"filterOperationType": "EQUALS"},
    "bidAskSpreadInCents": {"filterOperationType": "EQUALS"},
    "bidPriceInCents": {"filterOperationType": "EQUALS"},
    "fractionalDaysToExpiration": {"filterOperationType": "EQUALS"},
    "greekCharm": {"filterOperationType": "EQUALS"},
    "greekColor": {"filterOperationType": "EQUALS"},
    "greekDelta": {"filterOperationType": "EQUALS"},
    "greekGamma": {"filterOperationType": "EQUALS"},
    "greekOmega": {"filterOperationType": "EQUALS"},
    "greekRho": {"filterOperationType": "EQUALS"},
    "greekSigma": {"filterOperationType": "EQUALS"},
    "greekSpeed": {"filterOperationType": "EQUALS"},
    "greekTheta": {"filterOperationType": "EQUALS"},
    "greekUltima": {"filterOperationType": "EQUALS"},
    "greekVanna": {"filterOperationType": "EQUALS"},
    "greekVega": {"filterOperationType": "EQUALS"},
    "greekVeta": {"filterOperationType": "EQUALS"},
    "greekVomma": {"filterOperationType": "EQUALS"},
    "greekZomma": {"filterOperationType": "EQUALS"},
    "impliedVolatility": {"filterOperationType": "EQUALS"},
    "moneynessDegreeInCents": {"filterOperationType": "EQUALS"},
    "moneynessDegreeInPercent": {"filterOperationType": "EQUALS"},
    "openInterest": {"filterOperationType": "EQUALS"},
    "optionPriceInCents": {"filterOperationType": "EQUALS"},
    "premiumInCents": {"filterOperationType": "EQUALS"},
    "size": {"filterOperationType": "EQUALS"},
    "stockPriceInCents": {"filterOperationType": "EQUALS"},
    "strikePriceInCents": {"filterOperationType": "EQUALS"},
    "volume": {"filterOperationType": "EQUALS"},
    # Bool flags
    "isETF": {"filterOperationType": "EQUALS"},
    "isGoldenSweep": {"filterOperationType": "EQUALS"},
    "isIndex": {"filterOperationType": "EQUALS"},
    "isOpeningPosition": {"filterOperationType": "EQUALS"},
    "isUnusual": {"filterOperationType": "EQUALS"},
    "isVolumeGreaterThanOpenInterest": {"filterOperationType": "EQUALS"},
    # Categorical / multi-select fields
    "contractType": {"filterOperationType": "EQUALS"},
    "exchangeType": {"filterOperationType": "EQUALS"},
    "expirationDate": {"filterOperationType": "EQUALS"},
    "industryType": {"filterOperationType": "EQUALS"},
    "moneynessMoneyType": {"filterOperationType": "EQUALS"},
    "sectorType": {"filterOperationType": "EQUALS"},
    "sentimentType": {"filterOperationType": "EQUALS"},
    "ticker": {"filterOperationType": "EQUALS"},
    "tradeConsolidationType": {"filterOperationType": "EQUALS"},
    "tradeSideCodeType": {"filterOperationType": "EQUALS"},
    "tradeType": {"filterOperationType": "EQUALS"},
}


@pytest.fixture
def order_flow_dto() -> dict[str, Any]:
    """Return an order_flow tool DTO with the full 46-key filter scaffold."""
    return {
        "id": "tool-order_flow",
        "userId": "user-123",
        "pageId": "page-abc",
        "type": "OPTIONS_ORDER_FLOW_CONSOLIDATED_TABLE",
        "metadata": {
            "filter": dict(ORDER_FLOW_FILTER_SCAFFOLD),
        },
        "createdTime": 1_700_000_000_000,
        "lastUpdatedTime": 1_700_000_000_000,
    }


@pytest.fixture
def of_client(order_flow_dto: dict[str, Any]) -> MagicMock:
    """A QuantDataClient mock pre-wired for order_flow tests."""
    client = MagicMock()
    client.get_tool.return_value = order_flow_dto
    client.set_page_filter.return_value = True
    client.fetch_consolidated_flow.return_value = {"response": {"trades": []}}
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})
    return client


@pytest.fixture
def of_specs() -> dict[str, ToolSpec]:
    return {
        "order_flow": ToolSpec(
            tool_id="tool-order_flow",
            tool_type=ToolType.ORDER_FLOW_CONSOLIDATED,
            endpoint="options/order-flow/consolidated",
            label="order_flow",
        ),
    }


def _apply_filter(client: MagicMock) -> dict[str, Any]:
    """Return the filter dict from the FIRST PUT /tool (apply) payload."""
    puts = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert puts, "Expected at least one PUT /tool"
    return puts[0].kwargs["json"]["metadata"]["filter"]


def _call_order_flow(client: MagicMock, specs: dict[str, ToolSpec], **kwargs: Any) -> None:
    """Helper that patches the server module accessors and calls the tool."""
    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_order_flow(**kwargs)


# ---------------------------------------------------------------------------
# Bool flags
# ---------------------------------------------------------------------------

def test_is_unusual_emits_equals_bool(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, is_unusual=True)
    assert _apply_filter(of_client)["isUnusual"] == {
        "filterOperationType": "EQUALS",
        "value": True,
    }


def test_is_volume_gt_oi_maps_to_long_key(of_client, of_specs) -> None:
    """The snake-case ``is_volume_gt_oi`` must serialize to the long API key."""
    _call_order_flow(of_client, of_specs, is_volume_gt_oi=True)
    f = _apply_filter(of_client)
    assert f["isVolumeGreaterThanOpenInterest"]["value"] is True


def test_bool_flags_only_set_when_provided(of_client, of_specs) -> None:
    """Calling with no new kwargs must NOT override the existing scaffold."""
    _call_order_flow(of_client, of_specs)
    f = _apply_filter(of_client)
    # Untouched scaffold entries must keep their EQUALS-with-no-value shape.
    for key in ("isUnusual", "isGoldenSweep", "isOpeningPosition"):
        assert f[key] == {"filterOperationType": "EQUALS"}


# ---------------------------------------------------------------------------
# GTE thresholds
# ---------------------------------------------------------------------------

def test_min_size_emits_gte(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, min_size=50)
    assert _apply_filter(of_client)["size"] == {
        "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
        "value": 50,
    }


def test_min_premium_converts_dollars_to_cents(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, min_premium=10_000.0)
    assert _apply_filter(of_client)["premiumInCents"] == {
        "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
        "value": 1_000_000,
    }


def test_min_bid_ask_spread_converts_to_cents(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, min_bid_ask_spread=0.05)
    assert _apply_filter(of_client)["bidAskSpreadInCents"]["value"] == 5


def test_min_iv_emits_decimal_gte(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, min_iv=0.25)
    assert _apply_filter(of_client)["impliedVolatility"]["value"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# LTE thresholds
# ---------------------------------------------------------------------------

def test_max_dte_emits_lte(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, max_dte=7.0)
    assert _apply_filter(of_client)["fractionalDaysToExpiration"] == {
        "filterOperationType": "LESS_THAN_OR_EQUAL_TO",
        "value": 7.0,
    }


# ---------------------------------------------------------------------------
# Greek thresholds
# ---------------------------------------------------------------------------

def test_min_delta_only_emits_gte(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, min_delta=0.30)
    assert _apply_filter(of_client)["greekDelta"] == {
        "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
        "value": 0.30,
    }


def test_min_gamma_emits_gte(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, min_gamma=0.001)
    assert _apply_filter(of_client)["greekGamma"]["filterOperationType"] == "GREATER_THAN_OR_EQUAL_TO"


# ---------------------------------------------------------------------------
# Multi-select lists
# ---------------------------------------------------------------------------

def test_sentiment_type_list_emits_equals(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, sentiment_type=[SentimentType.BULLISH])
    assert _apply_filter(of_client)["sentimentType"] == {
        "filterOperationType": "EQUALS",
        "value": ["BULLISH"],
    }


def test_sentiment_type_combined_values(of_client, of_specs) -> None:
    _call_order_flow(
        of_client,
        of_specs,
        sentiment_type=[SentimentType.BULLISH, SentimentType.BEARISH],
    )
    assert _apply_filter(of_client)["sentimentType"]["value"] == ["BULLISH", "BEARISH"]


def test_trade_type_freeform_list(of_client, of_specs) -> None:
    """``trade_type`` is ``list[str]`` — passes through verbatim."""
    _call_order_flow(of_client, of_specs, trade_type=["AUTO", "M2S_FLR"])
    assert _apply_filter(of_client)["tradeType"] == {
        "filterOperationType": "EQUALS",
        "value": ["AUTO", "M2S_FLR"],
    }


def test_sector_and_industry_freeform_lists(of_client, of_specs) -> None:
    _call_order_flow(of_client, of_specs, sector=["TECHNOLOGY"], industry=["SOFTWARE"])
    f = _apply_filter(of_client)
    assert f["sectorType"]["value"] == ["TECHNOLOGY"]
    assert f["industryType"]["value"] == ["SOFTWARE"]


# ---------------------------------------------------------------------------
# Backwards compatibility + combined "kitchen sink"
# ---------------------------------------------------------------------------

def test_existing_kwargs_still_work(of_client, of_specs) -> None:
    """Legacy kwargs (contract_type, moneyness, trade_side, min_premium,
    strikes) keep their original shape so prior callers don't break."""
    _call_order_flow(
        of_client,
        of_specs,
        contract_type=ContractTypeFilter.CALL,
        moneyness=[MoneynessType.OTM],
        min_premium=5000,
        strikes=[5600.0],
    )
    f = _apply_filter(of_client)
    assert f["contractType"]["value"] == "CALL"
    assert f["moneynessMoneyType"]["value"] == ["OUT_OF_THE_MONEY"]
    assert f["premiumInCents"]["value"] == 500_000
    assert f["strikePriceInCents"]["value"] == [560_000]


def test_kitchen_sink_combination(of_client, of_specs) -> None:
    """Many filters together — none should clobber each other."""
    _call_order_flow(
        of_client,
        of_specs,
        ticker="SPY",
        is_unusual=True,
        is_opening_position=True,
        sentiment_type=[SentimentType.BULLISH],
        min_premium=10_000,
        trade_type=["AUTO"],
        sector=["TECHNOLOGY"],
        min_delta=0.30,
        max_dte=14.0,
    )
    f = _apply_filter(of_client)
    assert f["isUnusual"]["value"] is True
    assert f["isOpeningPosition"]["value"] is True
    assert f["sentimentType"]["value"] == ["BULLISH"]
    assert f["premiumInCents"]["value"] == 1_000_000
    assert f["tradeType"]["value"] == ["AUTO"]
    assert f["sectorType"]["value"] == ["TECHNOLOGY"]
    assert f["greekDelta"] == {
        "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
        "value": 0.30,
    }
    assert f["fractionalDaysToExpiration"]["value"] == 14.0


# ---------------------------------------------------------------------------
# qd_get_net_drift gained a confidence_visible toggle in this PR — quick
# wiring check so the small addition doesn't regress.
# ---------------------------------------------------------------------------

def test_net_drift_confidence_visible_lands_in_metadata(
    mock_client, mock_specs
) -> None:
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_net_drift(ticker="SPX", confidence_visible=True)

    puts = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert puts
    assert puts[0].kwargs["json"]["metadata"]["confidenceVisible"] is True


def test_net_drift_confidence_visible_omitted_by_default(
    mock_client, mock_specs
) -> None:
    """When the kwarg is not passed, the metadata key must not appear."""
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_net_drift(ticker="SPX")

    puts = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert puts
    assert "confidenceVisible" not in puts[0].kwargs["json"]["metadata"]


def test_no_filters_preserves_scaffold(of_client, of_specs) -> None:
    """Calling with no filter kwargs emits the original scaffold unchanged.

    This is the strongest backwards-compat guarantee: a default invocation
    must look exactly like what the UI's "no filters" state writes.
    """
    _call_order_flow(of_client, of_specs)
    f = _apply_filter(of_client)
    # Spot-check several scaffold keys remain untouched.
    for key in (
        "isUnusual", "isGoldenSweep", "size", "premiumInCents",
        "greekDelta", "sentimentType", "tradeType", "sectorType",
    ):
        assert f[key] == {"filterOperationType": "EQUALS"}, (
            f"Filter key {key} should be the empty scaffold but got {f[key]!r}"
        )
