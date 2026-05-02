"""Tests for the order_flow / contract_price / net_flow formatters.

These tests pin the formatters against sanitized real responses captured from
the QuantData API. They guard against regressions in shape parsing, cents-vs-
dollars handling, and edge-case behavior (empty payload, missing optional
fields).
"""
from __future__ import annotations

from typing import Any

import pytest

from quantdata_mcp.server import (
    _fmt_contract_price,
    _fmt_net_flow,
    _fmt_order_flow,
)


# ---------------------------------------------------------------------------
# _fmt_net_flow
# ---------------------------------------------------------------------------


class TestFmtNetFlow:
    def test_renders_header_and_all_entries(
        self, net_flow_response: dict[str, Any]
    ) -> None:
        out = _fmt_net_flow(net_flow_response)
        assert out.startswith("Net Flow — Last 5 entries")
        # 5 data rows + header line + blank separator = 7 lines
        assert len([line for line in out.splitlines() if line.strip()]) == 6

    def test_first_entry_values(self, net_flow_response: dict[str, Any]) -> None:
        # First fixture row: [1777608000000, 110000, 356000, 720901]
        # call_cents=110000 -> $1,100; put_cents=356000 -> $3,560; net=-$2,460
        out = _fmt_net_flow(net_flow_response)
        assert "04:00:00" in out  # 1777608000000ms = 2026-05-01 04:00:00 UTC
        assert "+1,100" in out
        assert "+3,560" in out
        assert "-2,460" in out

    def test_last_n_truncation(self, net_flow_response: dict[str, Any]) -> None:
        out = _fmt_net_flow(net_flow_response, last_n=2)
        # Should show only the last 2 rows
        assert "Last 2 entries" in out
        assert "04:03:00" in out
        assert "04:04:00" in out
        assert "04:00:00" not in out

    @pytest.mark.parametrize(
        "data",
        [
            None,
            {},
            {"response": {}},
            {"response": {"netFlow": []}},
        ],
    )
    def test_handles_empty_or_missing(self, data: Any) -> None:
        out = _fmt_net_flow(data)
        assert isinstance(out, str)
        assert "No net flow" in out

    def test_skips_malformed_rows(self) -> None:
        """A row that isn't a 4-item list must not crash; it should be skipped."""
        data = {
            "response": {
                "netFlow": [
                    [1777608000000, 100, 200, 720000],  # ok
                    "not a list",                         # garbage
                    [1, 2],                               # too short
                    [1777608060000, 50, 75, 720000],     # ok
                ]
            }
        }
        out = _fmt_net_flow(data)
        # Both well-formed rows should appear; nothing should crash
        assert "04:00:00" in out
        assert "04:01:00" in out

    def test_does_not_treat_8_item_rows_as_drift(self) -> None:
        """The 8-item branch was a PR #1 mistake — net-flow only ever returns
        4-item rows. We still want graceful handling: read the first 4 fields
        and ignore the rest, which is what the canonical 4-item shape does
        when slicing entry[0..3]."""
        data = {
            "response": {
                "netFlow": [
                    [1777608000000, 100, 200, 720000, 9, 9, 9, 9],
                ]
            }
        }
        out = _fmt_net_flow(data)
        # call=100c -> $1, put=200c -> $2, net=-$1; should NOT use entry[4]
        assert "+1" in out and "+2" in out


# ---------------------------------------------------------------------------
# _fmt_contract_price
# ---------------------------------------------------------------------------


class TestFmtContractPrice:
    def test_renders_header_and_rows(
        self, contract_price_response: dict[str, Any]
    ) -> None:
        out = _fmt_contract_price(contract_price_response)
        assert out.startswith("Contract Price (OHLCV)")
        assert "Time" in out and "Open" in out and "Close" in out and "Volume" in out

    def test_first_entry_values(
        self, contract_price_response: dict[str, Any]
    ) -> None:
        # First fixture row:
        #   [1777611180000, 3700, 3700, 3700, 3700, 2, ...]
        #   1777611180000ms = 2026-05-01 04:53:00 UTC
        #   3700 cents -> $37.00, volume 2
        out = _fmt_contract_price(contract_price_response)
        assert "04:53:00" in out
        assert "$    37.00" in out

    def test_known_close_price(
        self, contract_price_response: dict[str, Any]
    ) -> None:
        # Last fixture row close=3802 cents -> $38.02
        out = _fmt_contract_price(contract_price_response)
        assert "$    38.02" in out

    @pytest.mark.parametrize(
        "data",
        [
            None,
            {},
            {"response": {}},
            {"response": {"optionPriceOverTime": []}},
        ],
    )
    def test_handles_empty_or_missing(self, data: Any) -> None:
        out = _fmt_contract_price(data)
        assert isinstance(out, str)
        assert "No contract price" in out

    def test_skips_malformed_rows(self) -> None:
        """Rows shorter than the OHLCV shape are skipped, not crashed on."""
        data = {
            "response": {
                "optionPriceOverTime": [
                    [1777611180000, 3700, 3700, 3700, 3700, 2],  # ok
                    [1, 2, 3],                                     # too short
                    "garbage",                                      # not a list
                ]
            }
        }
        out = _fmt_contract_price(data)
        assert "$    37.00" in out  # well-formed row rendered

    def test_handles_zero_or_none_prices(self) -> None:
        """Falsy price fields shouldn't blow up division — they map to $0.00."""
        data = {
            "response": {
                "optionPriceOverTime": [
                    [1777611180000, 0, None, 0, 0, 0],
                ]
            }
        }
        out = _fmt_contract_price(data)
        assert "$     0.00" in out


# ---------------------------------------------------------------------------
# _fmt_order_flow
# ---------------------------------------------------------------------------


class TestFmtOrderFlow:
    def test_renders_header(self, order_flow_response: dict[str, Any]) -> None:
        out = _fmt_order_flow(order_flow_response)
        assert "Order Flow" in out
        assert "Ticker" in out and "Strike" in out and "Sentiment" in out

    def test_known_trade_fields(
        self, order_flow_response: dict[str, Any]
    ) -> None:
        # First fixture trade:
        #   tradeTime=1777665597865ms -> 19:59:57 UTC
        #   strikePriceInCents=723500 -> $7,235
        #   contractType=PUT -> "P"
        #   tradeSideCode="B"
        #   premiumInCents=6234000 -> $62,340
        #   size=118
        #   sentimentType=BULLISH
        out = _fmt_order_flow(order_flow_response)
        assert "19:59:57" in out
        assert "$   7,235" in out
        assert "BULLISH" in out
        assert "$    62,340" in out
        assert "118" in out
        # Should produce P (not full "PUT") in the Type column
        # and B (not "B" wrapped) for the side
        assert " P " in out

    def test_call_trades_render_as_C(
        self, order_flow_response: dict[str, Any]
    ) -> None:
        out = _fmt_order_flow(order_flow_response)
        # Two of the three fixture trades are CALLs
        assert " C " in out
        assert "BEARISH" in out

    def test_last_n_truncation(
        self, order_flow_response: dict[str, Any]
    ) -> None:
        out = _fmt_order_flow(order_flow_response, last_n=1)
        assert "Last 1 entries" in out
        # Only the last fixture trade (CALL @ 7175 strike) should be present
        assert "$   7,175" in out
        assert "$   7,235" not in out

    @pytest.mark.parametrize(
        "data",
        [
            None,
            {},
            {"response": {}},
            {"response": {"trades": []}},
        ],
    )
    def test_handles_empty_or_missing(self, data: Any) -> None:
        out = _fmt_order_flow(data)
        assert isinstance(out, str)
        assert "No order flow" in out

    def test_handles_missing_optional_fields(self) -> None:
        """Trades missing optional fields should still render with safe defaults."""
        data = {
            "response": {
                "trades": [
                    {
                        "tradeTime": 1777665597000,
                        "ticker": "SPX",
                        # no strikePriceInCents, no premiumInCents, no sentimentType
                        "contractType": "CALL",
                        "tradeSideCode": "A",
                        "size": 5,
                    }
                ]
            }
        }
        out = _fmt_order_flow(data)
        assert "SPX" in out
        assert "$       0" in out  # missing premium -> $0
        assert "5" in out
        # No exception, no crash
