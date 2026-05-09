"""Per-tool-type filter builders.

Each ``build_*_filter()`` function takes the same flat kwargs the MCP tool
wrappers accept and returns a ``dict[str, dict | None]`` mapping QuantData
API field names to filter clauses (or ``None`` for "no filter on this
field"). The result is fed straight into ``tool_context(filter_updates=...)``
which drops ``None`` entries before sending the PUT payload.

Centralising the filter shape per tool type lets the persistent
(saved-query, page-tool) layer share one source of truth with the
transient (``qd_get_*``) layer. Adding a new filter happens in one place.
"""

from __future__ import annotations

from typing import Any

from quantdata_mcp._context import _eq, _gte, _lte
from quantdata_mcp.tools import (
    ContractTypeFilter,
    MoneynessType,
    SentimentType,
    TradeSideCodeType,
)


def build_order_flow_filter(
    *,
    # Core filters
    contract_type: ContractTypeFilter | None = None,
    moneyness: list[MoneynessType] | None = None,
    trade_side: list[TradeSideCodeType] | None = None,
    min_premium: float | None = None,
    strikes: list[float] | None = None,
    # Bool flags
    is_unusual: bool | None = None,
    is_golden_sweep: bool | None = None,
    is_opening_position: bool | None = None,
    is_etf: bool | None = None,
    is_index: bool | None = None,
    is_volume_gt_oi: bool | None = None,
    # Numeric thresholds
    min_size: int | None = None,
    min_volume: int | None = None,
    min_open_interest: int | None = None,
    min_iv: float | None = None,
    min_bid_ask_spread: float | None = None,
    min_moneyness_pct: float | None = None,
    min_moneyness_dollars: float | None = None,
    max_dte: float | None = None,
    # Greek thresholds (one operator per field, GTE only)
    min_delta: float | None = None,
    min_gamma: float | None = None,
    min_theta: float | None = None,
    min_vega: float | None = None,
    min_charm: float | None = None,
    min_vanna: float | None = None,
    # Multi-select lists
    sentiment_type: list[SentimentType] | None = None,
    trade_type: list[str] | None = None,
    exchange_type: list[str] | None = None,
    sector: list[str] | None = None,
    industry: list[str] | None = None,
    trade_consolidation_type: list[str] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Build the filter dict for ``OPTIONS_ORDER_FLOW_CONSOLIDATED_TABLE``.

    Also used by ``OPTIONS_ORDER_FLOW_UNCONSOLIDATED_TABLE`` — the
    unconsolidated scaffold doesn't expose ``isGoldenSweep`` or
    ``tradeConsolidationType`` as filter fields, but passing them through
    is safe because ``tool_context`` drops ``None`` entries before
    serialising. The unconsolidated MCP wrapper simply doesn't surface
    those two kwargs, so they default to ``None``.
    """
    return {
        # Core filters
        "contractType": _eq(contract_type.value) if contract_type is not None else None,
        "moneynessMoneyType": _eq([m.value for m in moneyness]) if moneyness else None,
        "tradeSideCodeType": _eq([t.value for t in trade_side]) if trade_side else None,
        "premiumInCents": _gte(int(min_premium * 100)) if min_premium is not None else None,
        "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
        # Bool flags
        "isUnusual": _eq(is_unusual) if is_unusual is not None else None,
        "isGoldenSweep": _eq(is_golden_sweep) if is_golden_sweep is not None else None,
        "isOpeningPosition": _eq(is_opening_position) if is_opening_position is not None else None,
        "isETF": _eq(is_etf) if is_etf is not None else None,
        "isIndex": _eq(is_index) if is_index is not None else None,
        "isVolumeGreaterThanOpenInterest": _eq(is_volume_gt_oi) if is_volume_gt_oi is not None else None,
        # Numeric thresholds
        "size": _gte(min_size) if min_size is not None else None,
        "volume": _gte(min_volume) if min_volume is not None else None,
        "openInterest": _gte(min_open_interest) if min_open_interest is not None else None,
        "impliedVolatility": _gte(min_iv) if min_iv is not None else None,
        "bidAskSpreadInCents": _gte(int(min_bid_ask_spread * 100)) if min_bid_ask_spread is not None else None,
        "moneynessDegreeInPercent": _gte(min_moneyness_pct) if min_moneyness_pct is not None else None,
        "moneynessDegreeInCents": _gte(int(min_moneyness_dollars * 100)) if min_moneyness_dollars is not None else None,
        "fractionalDaysToExpiration": _lte(max_dte) if max_dte is not None else None,
        # Greek thresholds — one operator per field, all GTE.
        "greekDelta": _gte(min_delta) if min_delta is not None else None,
        "greekGamma": _gte(min_gamma) if min_gamma is not None else None,
        "greekTheta": _gte(min_theta) if min_theta is not None else None,
        "greekVega": _gte(min_vega) if min_vega is not None else None,
        "greekCharm": _gte(min_charm) if min_charm is not None else None,
        "greekVanna": _gte(min_vanna) if min_vanna is not None else None,
        # Multi-select lists
        "sentimentType": _eq([s.value for s in sentiment_type]) if sentiment_type else None,
        "tradeType": _eq(trade_type) if trade_type else None,
        "exchangeType": _eq(exchange_type) if exchange_type else None,
        "sectorType": _eq(sector) if sector else None,
        "industryType": _eq(industry) if industry else None,
        "tradeConsolidationType": _eq(trade_consolidation_type) if trade_consolidation_type else None,
    }
