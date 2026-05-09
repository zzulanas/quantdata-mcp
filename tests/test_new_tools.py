"""Wiring + filter + formatter tests for the 8 PR-2 Tier-1 tools.

Each new tool gets:
- A "happy-path wiring" test that asserts the captured PUT /tool payload
  carries the expected metadata + filter shape.
- A non-trivial filter test (e.g. ``min_pct_change`` on oi_change,
  ``min_delta`` / ``max_delta`` on term_structure, ``expirations`` on
  volatility_skew).
- For the formatters with fixture data, an output-shape test that pins the
  rendered string against a sanitized real response.

The unconsolidated_flow tests are a focused smoke pass over the filter
machinery rather than a full repeat of every PR-1 order_flow test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from quantdata_mcp import server
from quantdata_mcp.server import (
    _fmt_max_pain_over_time,
    _fmt_oi_by_expiration,
    _fmt_oi_change,
    _fmt_oi_over_time,
    _fmt_term_structure,
    _fmt_volatility_drift,
    _fmt_volatility_skew,
)
from quantdata_mcp.tools import (
    ChartType,
    ContractTypeFilter,
    MoneynessType,
    SentimentType,
    ToolSpec,
    ToolType,
)

# ---------------------------------------------------------------------------
# Helpers shared across wiring tests
# ---------------------------------------------------------------------------


def _apply_filter(client: MagicMock) -> dict[str, Any]:
    """Return the filter dict from the FIRST PUT /tool (apply) payload."""
    puts = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert puts, "Expected at least one PUT /tool"
    return puts[0].kwargs["json"]["metadata"]["filter"]


def _apply_metadata(client: MagicMock) -> dict[str, Any]:
    """Return the full metadata dict from the FIRST PUT /tool (apply) payload."""
    puts = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert puts, "Expected at least one PUT /tool"
    return puts[0].kwargs["json"]["metadata"]


def _make_dto(tool_id: str, tool_type: str, filter_scaffold: dict[str, Any], extra_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a minimal DTO with the given filter scaffold + optional metadata."""
    metadata: dict[str, Any] = {"filter": dict(filter_scaffold), "type": tool_type}
    if extra_metadata:
        metadata.update(extra_metadata)
    return {
        "id": tool_id,
        "userId": "user-123",
        "pageId": "page-abc",
        "type": tool_type,
        "metadata": metadata,
        "createdTime": 1_700_000_000_000,
        "lastUpdatedTime": 1_700_000_000_000,
    }


def _patch_server(client: MagicMock, specs: dict[str, ToolSpec]):
    """Return a context-manager that patches the server's lazy accessors."""
    return (
        patch.object(server, "_get_client", lambda: client),
        patch.object(server, "_get_specs", lambda: specs),
        patch.object(server, "_get_page_id", lambda: "page-abc"),
    )


def _call(client: MagicMock, specs: dict[str, ToolSpec], func, **kwargs):
    p1, p2, p3 = _patch_server(client, specs)
    with p1, p2, p3:
        return func(**kwargs)


# ---------------------------------------------------------------------------
# Per-tool fixtures (filter scaffolds + DTOs)
# ---------------------------------------------------------------------------

VOL_SKEW_SCAFFOLD: dict[str, Any] = {
    "contractType": {"filterOperationType": "EQUALS", "value": []},
    "expirationDate": {"filterOperationType": "EQUALS", "value": []},
    "ticker": {"filterOperationType": "EQUALS", "value": "SPY"},
}

TERM_STRUCTURE_SCAFFOLD: dict[str, Any] = {
    "contractType": {"filterOperationType": "EQUALS", "value": []},
    "daysUntilExpiration": {"filterOperationType": "EQUALS"},
    "deltaMax": {"filterOperationType": "LESS_THAN_OR_EQUAL_TO", "value": 1.0},
    "deltaMin": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO", "value": 0.0},
    "expirationDate": {"filterOperationType": "EQUALS", "value": []},
    "moneynessMoneyType": {"filterOperationType": "EQUALS", "value": ["AT_THE_MONEY"]},
    "strikePriceInCents": {"filterOperationType": "EQUALS", "value": []},
    "ticker": {"filterOperationType": "EQUALS", "value": "SPY"},
}

VOL_DRIFT_SCAFFOLD: dict[str, Any] = {
    "expirationDate": {"filterOperationType": "EQUALS"},
    "ticker": {"filterOperationType": "EQUALS", "value": "SPY"},
}

MPOT_SCAFFOLD: dict[str, Any] = {
    "ticker": {"filterOperationType": "EQUALS", "value": "SPY"},
}

OI_CHANGE_SCAFFOLD: dict[str, Any] = {
    "changeInOpenInterest": {"filterOperationType": "EQUALS"},
    "contractType": {"filterOperationType": "EQUALS"},
    "createdTime": {"filterOperationType": "EQUALS"},
    "currentOpenInterest": {"filterOperationType": "EQUALS"},
    "expirationDate": {"filterOperationType": "EQUALS"},
    "percentChangeInOpenInterest": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "previousOpenInterest": {"filterOperationType": "EQUALS"},
    "sessionDate": {"filterOperationType": "EQUALS"},
    "strikePriceInCents": {"filterOperationType": "EQUALS"},
    "ticker": {"filterOperationType": "EQUALS", "value": []},
}

OI_BY_EXP_SCAFFOLD: dict[str, Any] = {
    "strikePriceInCents": {"filterOperationType": "EQUALS"},
    "ticker": {"filterOperationType": "EQUALS", "value": "SPY"},
}

OI_OVER_TIME_SCAFFOLD: dict[str, Any] = {
    "expirationDate": {"filterOperationType": "EQUALS"},
    "strikePriceInCents": {"filterOperationType": "EQUALS"},
    "ticker": {"filterOperationType": "EQUALS", "value": "SPY"},
}

UNCONSOLIDATED_FLOW_SCAFFOLD: dict[str, Any] = {
    # All EQUALS scaffolds (44 keys; isGoldenSweep + tradeConsolidationType
    # are intentionally absent on the unconsolidated table).
    "askPriceInCents": {"filterOperationType": "EQUALS"},
    "bidAskSpreadInCents": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "bidPriceInCents": {"filterOperationType": "EQUALS"},
    "contractType": {"filterOperationType": "EQUALS"},
    "exchangeType": {"filterOperationType": "EQUALS", "value": []},
    "expirationDate": {"filterOperationType": "EQUALS"},
    "fractionalDaysToExpiration": {"filterOperationType": "LESS_THAN_OR_EQUAL_TO"},
    "greekCharm": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekColor": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekDelta": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekGamma": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekOmega": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekRho": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekSigma": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekSpeed": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekTheta": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekUltima": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekVanna": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekVega": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekVeta": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekVomma": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "greekZomma": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "impliedVolatility": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "industryType": {"filterOperationType": "EQUALS", "value": []},
    "isETF": {"filterOperationType": "EQUALS"},
    "isIndex": {"filterOperationType": "EQUALS"},
    "isOpeningPosition": {"filterOperationType": "EQUALS"},
    "isUnusual": {"filterOperationType": "EQUALS"},
    "isVolumeGreaterThanOpenInterest": {"filterOperationType": "EQUALS"},
    "moneynessMoneyType": {"filterOperationType": "EQUALS", "value": []},
    "moneynessDegreeInCents": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "moneynessDegreeInPercent": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "openInterest": {"filterOperationType": "EQUALS"},
    "optionPriceInCents": {"filterOperationType": "EQUALS"},
    "premiumInCents": {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO"},
    "sectorType": {"filterOperationType": "EQUALS", "value": []},
    "sentimentType": {"filterOperationType": "EQUALS", "value": []},
    "size": {"filterOperationType": "EQUALS"},
    "stockPriceInCents": {"filterOperationType": "EQUALS"},
    "strikePriceInCents": {"filterOperationType": "EQUALS"},
    "ticker": {"filterOperationType": "EQUALS", "value": []},
    "tradeSideCodeType": {"filterOperationType": "EQUALS", "value": []},
    "tradeType": {"filterOperationType": "EQUALS", "value": []},
    "volume": {"filterOperationType": "EQUALS"},
}


def _new_specs(name: str, tool_type: ToolType, endpoint: str) -> dict[str, ToolSpec]:
    return {
        name: ToolSpec(
            tool_id=f"tool-{name}",
            tool_type=tool_type,
            endpoint=endpoint,
            label=name,
        )
    }


def _make_client(dto: dict[str, Any], fetch_method: str, fetch_return: Any) -> MagicMock:
    client = MagicMock()
    client.get_tool.return_value = dto
    client.set_page_filter.return_value = True
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})
    getattr(client, fetch_method).return_value = fetch_return
    return client


# ---------------------------------------------------------------------------
# qd_get_volatility_skew
# ---------------------------------------------------------------------------


class TestVolatilitySkewWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-volatility_skew",
            "OPTIONS_VOLATILITY_SKEW_CHART",
            VOL_SKEW_SCAFFOLD,
        )
        client = _make_client(
            dto, "fetch_volatility_skew",
            {"response": {"stockPriceInCents": 720000, "volatilitySkew": {}}},
        )
        specs = _new_specs("volatility_skew", ToolType.VOLATILITY_SKEW, "options/volatility-skew")
        return client, specs

    def test_default_invocation_keeps_scaffold(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_volatility_skew, ticker="SPX")
        f = _apply_filter(client)
        assert f["ticker"] == {"filterOperationType": "EQUALS", "value": "SPX"}
        # contractType / expirationDate untouched (still empty list scaffold)
        assert f["contractType"] == {"filterOperationType": "EQUALS", "value": []}
        assert f["expirationDate"] == {"filterOperationType": "EQUALS", "value": []}

    def test_contract_type_emits_list(self) -> None:
        """The skew scaffold expects a LIST under contractType, not a single string."""
        client, specs = self._setup()
        _call(client, specs, server.qd_get_volatility_skew, contract_type=ContractTypeFilter.PUT)
        f = _apply_filter(client)
        assert f["contractType"] == {"filterOperationType": "EQUALS", "value": ["PUT"]}

    def test_expirations_list_emits_equals(self) -> None:
        client, specs = self._setup()
        _call(
            client, specs, server.qd_get_volatility_skew,
            expirations=["2026-05-15", "2026-06-19"],
        )
        f = _apply_filter(client)
        assert f["expirationDate"] == {
            "filterOperationType": "EQUALS",
            "value": ["2026-05-15", "2026-06-19"],
        }


# ---------------------------------------------------------------------------
# qd_get_term_structure
# ---------------------------------------------------------------------------


class TestTermStructureWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-term_structure",
            "OPTIONS_TERM_STRUCTURE_CHART",
            TERM_STRUCTURE_SCAFFOLD,
        )
        client = _make_client(
            dto, "fetch_term_structure",
            {"response": {"stockPriceInCents": 720000, "expectedMove": {}, "termStructure": {}}},
        )
        specs = _new_specs("term_structure", ToolType.TERM_STRUCTURE, "options/term-structure")
        return client, specs

    def test_default_passes_ticker(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_term_structure, ticker="QQQ")
        f = _apply_filter(client)
        assert f["ticker"] == {"filterOperationType": "EQUALS", "value": "QQQ"}
        # The server-side default moneynessMoneyType=["AT_THE_MONEY"] must not
        # be overwritten when the caller doesn't pass `moneyness`.
        assert f["moneynessMoneyType"]["value"] == ["AT_THE_MONEY"]

    def test_min_max_delta_use_separate_fields(self) -> None:
        """deltaMin / deltaMax are SEPARATE fields, so a true range works."""
        client, specs = self._setup()
        _call(
            client, specs, server.qd_get_term_structure,
            min_delta=0.20, max_delta=0.80,
        )
        f = _apply_filter(client)
        assert f["deltaMin"] == {
            "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
            "value": 0.20,
        }
        assert f["deltaMax"] == {
            "filterOperationType": "LESS_THAN_OR_EQUAL_TO",
            "value": 0.80,
        }

    def test_moneyness_override(self) -> None:
        client, specs = self._setup()
        _call(
            client, specs, server.qd_get_term_structure,
            moneyness=[MoneynessType.OTM, MoneynessType.ITM],
        )
        f = _apply_filter(client)
        assert f["moneynessMoneyType"]["value"] == ["OUT_OF_THE_MONEY", "IN_THE_MONEY"]


# ---------------------------------------------------------------------------
# qd_get_volatility_drift
# ---------------------------------------------------------------------------


class TestVolatilityDriftWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-volatility_drift",
            "OPTIONS_VOLATILITY_DRIFT_CHART",
            VOL_DRIFT_SCAFFOLD,
        )
        client = _make_client(
            dto, "fetch_volatility_drift",
            {"response": {"volatilityDrift": {}}},
        )
        specs = _new_specs("volatility_drift", ToolType.VOLATILITY_DRIFT, "options/volatility-drift")
        return client, specs

    def test_ticker_lands_in_filter(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_volatility_drift, ticker="AAPL", expiration_date="2026-06-19")
        f = _apply_filter(client)
        assert f["ticker"] == {"filterOperationType": "EQUALS", "value": "AAPL"}
        assert f["expirationDate"] == {
            "filterOperationType": "EQUALS",
            "value": "2026-06-19",
        }


# ---------------------------------------------------------------------------
# qd_get_max_pain_over_time
# ---------------------------------------------------------------------------


class TestMaxPainOverTimeWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-max_pain_over_time",
            "OPTIONS_MAX_PAIN_OVER_TIME_CHART",
            MPOT_SCAFFOLD,
        )
        client = _make_client(
            dto, "fetch_max_pain_over_time",
            {"response": {"stockPriceInCents": 720000, "maxPainStrikePricesInCents": {}}},
        )
        specs = _new_specs("max_pain_over_time", ToolType.MAX_PAIN_OVER_TIME, "options/max-pain/time")
        return client, specs

    def test_ticker_filter_applied(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_max_pain_over_time, ticker="SPY")
        f = _apply_filter(client)
        assert f["ticker"] == {"filterOperationType": "EQUALS", "value": "SPY"}


# ---------------------------------------------------------------------------
# qd_get_oi_change
# ---------------------------------------------------------------------------


class TestOiChangeWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-oi_change",
            "OPTIONS_OPEN_INTEREST_CHANGE_TABLE",
            OI_CHANGE_SCAFFOLD,
            extra_metadata={
                "tableMetadata": {
                    "sort": {"field": "CHANGE_IN_OPEN_INTEREST", "sortDirectionType": "DESCENDING"}
                }
            },
        )
        client = _make_client(
            dto, "fetch_oi_change",
            {"response": []},
        )
        specs = _new_specs("oi_change", ToolType.OI_CHANGE, "options/open-interest/change")
        return client, specs

    def test_ticker_emits_list(self) -> None:
        """oi_change ticker scaffold takes a LIST."""
        client, specs = self._setup()
        _call(client, specs, server.qd_get_oi_change, ticker="SPX")
        f = _apply_filter(client)
        assert f["ticker"] == {"filterOperationType": "EQUALS", "value": ["SPX"]}

    def test_min_pct_change_emits_gte(self) -> None:
        """The headline filter for oi_change."""
        client, specs = self._setup()
        _call(client, specs, server.qd_get_oi_change, min_pct_change=50.0)
        f = _apply_filter(client)
        assert f["percentChangeInOpenInterest"] == {
            "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
            "value": 50.0,
        }

    def test_contract_type_and_strikes(self) -> None:
        client, specs = self._setup()
        _call(
            client, specs, server.qd_get_oi_change,
            contract_type=ContractTypeFilter.PUT,
            strikes=[5600.0, 5700.0],
        )
        f = _apply_filter(client)
        assert f["contractType"] == {"filterOperationType": "EQUALS", "value": "PUT"}
        assert f["strikePriceInCents"]["value"] == [560_000, 570_000]


# ---------------------------------------------------------------------------
# qd_get_oi_by_expiration
# ---------------------------------------------------------------------------


class TestOiByExpirationWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-oi_by_expiration",
            "OPTIONS_OPEN_INTEREST_BY_EXPIRATION_CHART",
            OI_BY_EXP_SCAFFOLD,
        )
        client = _make_client(
            dto, "fetch_oi_by_expiration",
            {"response": {"expirationDatesToPutCallOpenInterest": {}}},
        )
        specs = _new_specs("oi_by_expiration", ToolType.OI_BY_EXPIRATION, "options/open-interest/expiration")
        return client, specs

    def test_strikes_filter(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_oi_by_expiration, strikes=[5600.0])
        f = _apply_filter(client)
        assert f["strikePriceInCents"]["value"] == [560_000]


# ---------------------------------------------------------------------------
# qd_get_oi_over_time
# ---------------------------------------------------------------------------


class TestOiOverTimeWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-oi_over_time",
            "OPTIONS_OPEN_INTEREST_OVER_TIME_CHART",
            OI_OVER_TIME_SCAFFOLD,
            extra_metadata={"chartType": "LINE"},
        )
        client = _make_client(
            dto, "fetch_oi_over_time",
            {"response": {"sessionDatesToPutCallOpenInterest": {}}},
        )
        specs = _new_specs("oi_over_time", ToolType.OI_OVER_TIME, "options/open-interest/time")
        return client, specs

    def test_chart_type_lands_in_metadata(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_oi_over_time, chart_type=ChartType.CANDLESTICK)
        md = _apply_metadata(client)
        assert md["chartType"] == "CANDLESTICK"

    def test_chart_type_omitted_by_default(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_oi_over_time)
        md = _apply_metadata(client)
        # The DTO seed had chartType=LINE; the apply payload preserves the
        # snapshot so an omitted kwarg leaves the user's saved value alone.
        assert md.get("chartType") == "LINE"


# ---------------------------------------------------------------------------
# qd_get_unconsolidated_flow — focused smoke pass over filter machinery
# ---------------------------------------------------------------------------


class TestUnconsolidatedFlowWiring:
    def _setup(self):
        dto = _make_dto(
            "tool-unconsolidated_flow",
            "OPTIONS_ORDER_FLOW_UNCONSOLIDATED_TABLE",
            UNCONSOLIDATED_FLOW_SCAFFOLD,
            extra_metadata={
                "tableMetadata": {
                    "sort": {"field": "PREMIUM_IN_CENTS", "sortDirectionType": "DESCENDING"}
                }
            },
        )
        client = _make_client(
            dto, "fetch_unconsolidated_flow",
            {"response": {"trades": []}},
        )
        specs = _new_specs(
            "unconsolidated_flow",
            ToolType.ORDER_FLOW_UNCONSOLIDATED,
            "options/order-flow/unconsolidated",
        )
        return client, specs

    def test_bool_flag_filter(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_unconsolidated_flow, is_unusual=True)
        f = _apply_filter(client)
        assert f["isUnusual"] == {"filterOperationType": "EQUALS", "value": True}

    def test_gte_threshold_filter(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_unconsolidated_flow, min_premium=50_000)
        f = _apply_filter(client)
        assert f["premiumInCents"] == {
            "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
            "value": 5_000_000,
        }

    def test_multi_select_filter(self) -> None:
        client, specs = self._setup()
        _call(
            client, specs, server.qd_get_unconsolidated_flow,
            sentiment_type=[SentimentType.BULLISH, SentimentType.BEARISH],
        )
        f = _apply_filter(client)
        assert f["sentimentType"]["value"] == ["BULLISH", "BEARISH"]

    def test_min_delta_emits_gte(self) -> None:
        client, specs = self._setup()
        _call(client, specs, server.qd_get_unconsolidated_flow, min_delta=0.30)
        f = _apply_filter(client)
        assert f["greekDelta"] == {
            "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
            "value": 0.30,
        }

    def test_no_is_golden_sweep_kwarg(self) -> None:
        """The unconsolidated scaffold has no isGoldenSweep field. The wrapper
        must NOT expose it as a kwarg (signature regression guard)."""
        import inspect
        sig = inspect.signature(server.qd_get_unconsolidated_flow)
        assert "is_golden_sweep" not in sig.parameters

    def test_default_invocation_preserves_scaffold(self) -> None:
        """Calling with no filter kwargs leaves the scaffold untouched."""
        client, specs = self._setup()
        _call(client, specs, server.qd_get_unconsolidated_flow)
        f = _apply_filter(client)
        # Spot-check several keys remain in their seed shape
        for key in (
            "isUnusual", "size", "premiumInCents",
            "greekDelta", "sentimentType", "tradeType", "sectorType",
        ):
            scaffold_val = UNCONSOLIDATED_FLOW_SCAFFOLD[key]
            assert f[key] == scaffold_val, (
                f"Filter key {key} should be the empty scaffold but got {f[key]!r}"
            )


# ---------------------------------------------------------------------------
# Formatter tests (output shape + edge cases)
# ---------------------------------------------------------------------------


class TestFmtVolatilitySkew:
    def test_renders_header_and_strikes(self, volatility_skew_response: dict[str, Any]) -> None:
        out = _fmt_volatility_skew(volatility_skew_response, ticker="SPY")
        assert out.startswith("Volatility Skew — SPY")
        assert "Expiration:" in out
        assert "Call IV" in out and "Put IV" in out

    def test_handles_empty(self) -> None:
        assert "No volatility skew" in _fmt_volatility_skew(None)
        assert "No volatility skew" in _fmt_volatility_skew({})

    def test_call_only_filter(self, volatility_skew_response: dict[str, Any]) -> None:
        """contract_type='CALL' should hide rows that have no CALL IV."""
        out = _fmt_volatility_skew(volatility_skew_response, contract_type="CALL", ticker="SPY")
        # Smoke: still renders without crashing
        assert "Volatility Skew" in out


class TestFmtTermStructure:
    def test_renders_table(self, term_structure_response: dict[str, Any]) -> None:
        out = _fmt_term_structure(term_structure_response, ticker="SPY")
        assert out.startswith("Term Structure")
        assert "Expiration" in out
        assert "Call EM ($)" in out and "Put EM ($)" in out

    def test_handles_empty(self) -> None:
        assert "No term structure" in _fmt_term_structure(None)


class TestFmtVolatilityDrift:
    def test_renders_table(self, volatility_drift_response: dict[str, Any]) -> None:
        out = _fmt_volatility_drift(volatility_drift_response)
        assert out.startswith("Volatility Drift")
        assert "ARV" in out and "Spot" in out

    def test_handles_empty(self) -> None:
        assert "No volatility drift" in _fmt_volatility_drift(None)
        assert "No volatility drift" in _fmt_volatility_drift({"response": {"volatilityDrift": {}}})

    def test_handles_missing_iv_gracefully(self) -> None:
        """Earliest entries lack 'iv' — must render '—' instead of crashing."""
        data = {
            "response": {
                "volatilityDrift": {
                    "1778273400000": {"arv": 0.0, "stockPriceInCents": 720000},
                }
            }
        }
        out = _fmt_volatility_drift(data)
        assert "—" in out  # the IV column for the iv-less entry


class TestFmtMaxPainOverTime:
    def test_renders_table(self, max_pain_over_time_response: dict[str, Any]) -> None:
        out = _fmt_max_pain_over_time(max_pain_over_time_response, ticker="SPY")
        assert out.startswith("Max Pain by Expiration")
        assert "Max Pain" in out

    def test_handles_empty(self) -> None:
        assert "No max pain over time" in _fmt_max_pain_over_time(None)


class TestFmtOiChange:
    def test_renders_table(self, oi_change_response: dict[str, Any]) -> None:
        out = _fmt_oi_change(oi_change_response)
        assert out.startswith("Open Interest Change")
        assert "Strike" in out and "%Chg" in out

    def test_top_n_truncation(self, oi_change_response: dict[str, Any]) -> None:
        out = _fmt_oi_change(oi_change_response, top_n=2)
        assert "Top 2" in out

    def test_handles_empty(self) -> None:
        assert "No OI change" in _fmt_oi_change(None)
        assert "No OI change" in _fmt_oi_change({"response": []})

    def test_handles_non_list_response(self) -> None:
        """If the response shape ever changes to a dict, surface a diagnostic
        instead of crashing."""
        out = _fmt_oi_change({"response": {"unexpected": "dict"}})
        assert "unexpected response shape" in out


class TestFmtOiByExpiration:
    def test_renders_table(self, oi_by_expiration_response: dict[str, Any]) -> None:
        out = _fmt_oi_by_expiration(oi_by_expiration_response, ticker="SPY")
        assert out.startswith("Open Interest by Expiration")
        assert "Call OI" in out and "Put OI" in out and "P/C" in out

    def test_handles_empty(self) -> None:
        assert "No OI by expiration" in _fmt_oi_by_expiration(None)


class TestFmtOiOverTime:
    def test_renders_table(self, oi_over_time_response: dict[str, Any]) -> None:
        out = _fmt_oi_over_time(oi_over_time_response, ticker="SPY")
        assert out.startswith("Open Interest / Time")
        assert "Session" in out

    def test_last_n_truncation(self, oi_over_time_response: dict[str, Any]) -> None:
        out = _fmt_oi_over_time(oi_over_time_response, last_n=2, ticker="SPY")
        assert "last 2 of" in out

    def test_handles_empty(self) -> None:
        assert "No OI over time" in _fmt_oi_over_time(None)
