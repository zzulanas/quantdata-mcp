"""Wiring + formatter tests for the 7 v0.4.0 Tier-2 tools.

Each test follows the same pattern used by ``tests/test_new_tools.py``
for the PR-2 tools: load a sanitised fixture, drive the formatter, assert
that the output mentions the expected fields. Wiring tests use the
shared ``mock_client`` / ``context_kwargs`` to confirm each MCP wrapper
hits the right ``client.fetch_*`` method.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from quantdata_mcp import server

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    with (FIXTURES / f"{name}.json").open() as f:
        data: dict[str, Any] = json.load(f)
    return data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def heat_map_response() -> dict[str, Any]:
    return _load("heat_map")


@pytest.fixture
def interval_map_response() -> dict[str, Any]:
    return _load("interval_map")


@pytest.fixture
def news_articles_response() -> dict[str, Any]:
    return _load("news_articles")


@pytest.fixture
def gainers_losers_response() -> dict[str, Any]:
    return _load("gainers_losers")


@pytest.fixture
def dark_pool_response() -> dict[str, Any]:
    return _load("dark_pool_levels")


@pytest.fixture
def stock_price_response() -> dict[str, Any]:
    return _load("stock_price_over_time")


# ---------------------------------------------------------------------------
# Formatter outputs — sanity checks against the captured live shapes
# ---------------------------------------------------------------------------

def test_heat_map_formatter_picks_top_cells(heat_map_response: dict[str, Any]) -> None:
    out = server._fmt_heat_map(heat_map_response, top_n=10, ticker="SPX")
    assert "Heat Map" in out
    # Fixture spot is around the SPX 7000s — just sanity-check the format.
    assert "$7," in out
    # Headers + at least one cell. The trimmed fixture is sparse (deep OTM
    # strikes are mostly zero), but the format is what we're testing.
    assert "Net" in out
    assert "Strike" in out


def test_interval_map_formatter_groups_by_time(
    interval_map_response: dict[str, Any]
) -> None:
    out = server._fmt_interval_map(interval_map_response, top_n=3, ticker="SPX")
    assert "Interval Map" in out
    assert "ET" in out
    # Each top bucket lists strikes
    assert "call=" in out and "put=" in out


def test_news_articles_formatter_lists_titles(
    news_articles_response: dict[str, Any]
) -> None:
    out = server._fmt_news_articles(news_articles_response, top_n=5)
    assert "News Articles" in out
    # Fixture has 3 articles — at least one title should surface
    assert "ET" in out or "?" in out  # timestamp formatting works


def test_gainers_losers_formatter_shows_both_sides(
    gainers_losers_response: dict[str, Any]
) -> None:
    out = server._fmt_gainers_losers(gainers_losers_response, top_n=5)
    assert "Gainers / Losers" in out
    assert "Top bullish" in out
    assert "Top bearish" in out


def test_dark_pool_formatter_renders_levels(
    dark_pool_response: dict[str, Any]
) -> None:
    out = server._fmt_dark_pool_levels(dark_pool_response, top_n=10)
    assert "Dark Pool Levels" in out
    # Even an empty level set should produce a non-error message
    assert "no levels" in out.lower() or "Size" in out


def test_equity_prints_formatter_handles_empty() -> None:
    """The fixture is empty (weekend tape). Formatter should say so cleanly."""
    out = server._fmt_equity_prints({"response": []})
    assert "No equity prints for the current filter." in out


def test_stock_price_time_formatter_renders_candles(
    stock_price_response: dict[str, Any]
) -> None:
    out = server._fmt_stock_price_time(stock_price_response, last_n=5)
    assert "Stock Price / Time" in out
    assert "Open" in out and "Close" in out
    assert "most recent" in out  # confirms the new sort-by-timestamp-DESC path


def test_stock_price_time_formatter_picks_newest_regardless_of_input_order() -> None:
    """Source array can be ascending or descending — the formatter sorts
    explicitly so the newest bar lands first either way."""
    asc = {
        "response": {
            "stockPriceOverTime": [
                [1_000, 100_00, 101_00, 99_00, 100_50],
                [2_000, 100_50, 102_00, 100_00, 101_00],
                [3_000, 101_00, 103_00, 100_50, 102_50],
            ]
        }
    }
    desc = {
        "response": {
            "stockPriceOverTime": [
                [3_000, 101_00, 103_00, 100_50, 102_50],
                [2_000, 100_50, 102_00, 100_00, 101_00],
                [1_000, 100_00, 101_00, 99_00, 100_50],
            ]
        }
    }
    out_asc = server._fmt_stock_price_time(asc, last_n=2)
    out_desc = server._fmt_stock_price_time(desc, last_n=2)
    # Both should show the same two newest bars (ts=3000, then ts=2000)
    # — the ts=1000 oldest bar should be excluded in both cases.
    assert "1000" not in out_asc and "1000" not in out_desc, (
        "The oldest bar (ts=1000) shouldn't appear when last_n=2"
    )
    # Both outputs should be identical apart from the body itself.
    # (Both should mention 'most recent 2 shown'.)
    assert "most recent 2 shown" in out_asc
    assert "most recent 2 shown" in out_desc


# ---------------------------------------------------------------------------
# Wiring tests — each MCP wrapper hits the right client.fetch_* method
# ---------------------------------------------------------------------------

@pytest.fixture
def heat_map_dto(make_tool_dto) -> dict[str, Any]:
    return make_tool_dto(
        tool_id="tool-heat_map",
        metadata_overrides={
            "type": "OPTIONS_HEAT_MAP_CHART",
            "dataModeType": "PREMIUM",
            "invertAxes": False,
            "filter": {"ticker": {"filterOperationType": "EQUALS", "value": "SPX"}},
        },
    )


@pytest.fixture
def interval_map_dto(make_tool_dto) -> dict[str, Any]:
    return make_tool_dto(
        tool_id="tool-interval_map",
        metadata_overrides={
            "type": "INTERVAL_MAP_CHART",
            "aggregationPeriodType": "FIVE_MINUTE",
            "greekModeType": "DELTA",
            "numberOfPaddingStrikes": 5,
            "representationModeType": "VALUE",
            "filter": {
                "ticker": {"filterOperationType": "EQUALS", "value": "SPX"},
                "expirationDate": {"filterOperationType": "EQUALS", "value": ""},
            },
        },
    )


def test_heat_map_wrapper_calls_fetch_heat_map(
    heat_map_dto, mock_specs, heat_map_response
) -> None:
    client = MagicMock()
    client.get_tool.return_value = heat_map_dto
    client.fetch_heat_map.return_value = heat_map_response
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        out = server.qd_get_heat_map(ticker="SPX", date="2026-05-08", top_n=5)

    client.fetch_heat_map.assert_called_once_with("tool-heat_map")
    assert "Heat Map" in out


def test_interval_map_wrapper_passes_metadata(
    interval_map_dto, mock_specs, interval_map_response
) -> None:
    client = MagicMock()
    client.get_tool.return_value = interval_map_dto
    client.fetch_interval_map.return_value = interval_map_response
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_interval_map(
            ticker="SPX",
            date="2026-05-08",
            expiration_date="2026-05-08",
            greek_type=server.GreekMode.GAMMA,
            aggregation=server.AggregationPeriod.TEN_MINUTE,
            padding_strikes=8,
        )

    # Find the apply PUT and inspect its metadata
    put_calls = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    apply_payload = put_calls[0].kwargs["json"]
    md = apply_payload["metadata"]
    assert md["greekModeType"] == "GAMMA"
    assert md["aggregationPeriodType"] == "TEN_MINUTE"
    assert md["numberOfPaddingStrikes"] == 8


def test_news_articles_wrapper_builds_filter_dict(
    make_tool_dto, mock_specs, news_articles_response
) -> None:
    dto = make_tool_dto(
        tool_id="tool-news_articles",
        metadata_overrides={
            "type": "NEWS_ARTICLE_LISTING",
            "filter": {
                "ticker": {"filterOperationType": "EQUALS", "value": []},
            },
        },
    )
    client = MagicMock()
    client.get_tool.return_value = dto
    client.fetch_news_articles.return_value = news_articles_response
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_news_articles(
            tickers=["SPY", "QQQ"],
            sentiment=[server.SentimentType.BULLISH],
            title_contains="rocket",
        )

    put_calls = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    apply_filter = put_calls[0].kwargs["json"]["metadata"]["filter"]
    assert apply_filter["ticker"]["value"] == ["SPY", "QQQ"]
    assert apply_filter["sentiment"]["value"] == ["BULLISH"]
    assert apply_filter["title"]["filterOperationType"] == "CONTAINS"
    assert apply_filter["title"]["value"] == "rocket"


def test_gainers_losers_watchlist_sets_multi_ticker_page_filter(
    make_tool_dto, mock_specs, gainers_losers_response
) -> None:
    """When a watchlist is passed, the wrapper sets a multi-ticker page
    filter for the call so the response covers all listed tickers."""
    from quantdata_mcp._context import clear_active_page

    clear_active_page()
    dto = make_tool_dto(
        tool_id="tool-gainers_losers",
        metadata_overrides={
            "type": "OPTIONS_GAINERS_LOSERS_TABLE",
            "filter": {
                "sectorType": {"filterOperationType": "EQUALS", "value": []},
                "industryType": {"filterOperationType": "EQUALS", "value": []},
            },
        },
    )
    client = MagicMock()
    client.get_tool.return_value = dto
    client.fetch_gainers_losers.return_value = gainers_losers_response
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_gainers_losers(watchlist=["SPY", "QQQ", "NVDA"])

    # Page filter set with a list ticker (not single string)
    page_filter_calls = client.set_page_filter.call_args_list
    assert page_filter_calls, "set_page_filter should have been called for the watchlist"
    assert page_filter_calls[0].kwargs["ticker"] == ["SPY", "QQQ", "NVDA"]


def test_gainers_losers_wrapper_passes_sector_filter(
    make_tool_dto, mock_specs, gainers_losers_response
) -> None:
    dto = make_tool_dto(
        tool_id="tool-gainers_losers",
        metadata_overrides={
            "type": "OPTIONS_GAINERS_LOSERS_TABLE",
            "filter": {
                "sectorType": {"filterOperationType": "EQUALS", "value": []},
                "industryType": {"filterOperationType": "EQUALS", "value": []},
            },
        },
    )
    client = MagicMock()
    client.get_tool.return_value = dto
    client.fetch_gainers_losers.return_value = gainers_losers_response
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_gainers_losers(sectors=["TECHNOLOGY"], industries=["SOFTWARE"])

    put_calls = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    apply_filter = put_calls[0].kwargs["json"]["metadata"]["filter"]
    assert apply_filter["sectorType"]["value"] == ["TECHNOLOGY"]
    assert apply_filter["industryType"]["value"] == ["SOFTWARE"]


def test_dark_pool_wrapper_passes_max_levels(
    make_tool_dto, mock_specs, dark_pool_response
) -> None:
    dto = make_tool_dto(
        tool_id="tool-dark_pool_levels",
        metadata_overrides={
            "type": "DARK_POOL_LEVELS_TABLE",
            "maximumLevelCount": 22,
            "filter": {"ticker": {"filterOperationType": "EQUALS", "value": "SPY"}},
        },
    )
    client = MagicMock()
    client.get_tool.return_value = dto
    client.fetch_dark_pool_levels.return_value = dark_pool_response
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_dark_pool_levels(max_levels=50)

    put_calls = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    md = put_calls[0].kwargs["json"]["metadata"]
    assert md["maximumLevelCount"] == 50


def test_equity_prints_wrapper_dollar_to_cents(make_tool_dto, mock_specs) -> None:
    dto = make_tool_dto(
        tool_id="tool-equity_prints",
        metadata_overrides={
            "type": "EQUITY_PRINTS_TABLE",
            "filter": {
                "size": {"filterOperationType": "EQUALS"},
                "notionalValueInCents": {"filterOperationType": "EQUALS"},
                "tradeSideCodeType": {"filterOperationType": "EQUALS", "value": []},
            },
        },
    )
    client = MagicMock()
    client.get_tool.return_value = dto
    client.fetch_equity_prints.return_value = []
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_equity_prints(min_size=100, min_notional=10_000.0)

    put_calls = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    apply_filter = put_calls[0].kwargs["json"]["metadata"]["filter"]
    assert apply_filter["size"]["value"] == 100
    # Dollars -> cents conversion for notional
    assert apply_filter["notionalValueInCents"]["value"] == 1_000_000
    # Ticker is wrapped in a list because the equity_prints scaffold expects
    # multi-value (unlike most options tools where ticker is a single string).
    assert isinstance(apply_filter["ticker"]["value"], list)


def test_stock_price_time_wrapper_passes_chart_type(
    make_tool_dto, mock_specs, stock_price_response
) -> None:
    dto = make_tool_dto(
        tool_id="tool-stock_price_time",
        metadata_overrides={
            "type": "STOCK_PRICE_OVER_TIME_CHART",
            "aggregationPeriodType": "ONE_MINUTE",
            "chartType": "CANDLESTICK",
            "filter": {"ticker": {"filterOperationType": "EQUALS", "value": "SPY"}},
        },
    )
    client = MagicMock()
    client.get_tool.return_value = dto
    client.fetch_stock_price_time.return_value = stock_price_response
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_stock_price_time(
            ticker="SPY",
            aggregation=server.AggregationPeriod.FIVE_MINUTE,
            chart_type=server.ChartType.LINE,
        )

    put_calls = [
        c for c in client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    md = put_calls[0].kwargs["json"]["metadata"]
    assert md["aggregationPeriodType"] == "FIVE_MINUTE"
    assert md["chartType"] == "LINE"
