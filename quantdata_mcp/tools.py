"""Tool type definitions and registry — no hardcoded IDs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ToolType(str, Enum):
    EXPOSURE_BY_STRIKE = "OPTIONS_EXPOSURE_BY_STRIKE_CHART"
    NET_DRIFT = "OPTIONS_NET_DRIFT_CHART"
    IV_RANK = "OPTIONS_IV_RANK_CHART"
    CONTRACT_SIDE_STATS = "OPTIONS_CONTRACT_TRADE_SIDE_STATISTICS_CHART"
    MAX_PAIN = "OPTIONS_MAX_PAIN_CHART"
    NET_FLOW = "OPTIONS_NET_FLOW_CHART"
    ORDER_FLOW_CONSOLIDATED = "OPTIONS_ORDER_FLOW_CONSOLIDATED_TABLE"
    OI_BY_STRIKE = "OPTIONS_OPEN_INTEREST_BY_STRIKE_CHART"
    CONTRACT_STATISTICS = "OPTIONS_CONTRACT_STATISTICS_CHART"
    EXPOSURE_BY_EXPIRATION = "OPTIONS_EXPOSURE_BY_EXPIRATION_CHART"
    CONTRACT_PRICE_TIME = "OPTIONS_CONTRACT_PRICE_OVER_TIME_CHART"
    # PR 2 — 8 new Tier-1 tools
    VOLATILITY_SKEW = "OPTIONS_VOLATILITY_SKEW_CHART"
    TERM_STRUCTURE = "OPTIONS_TERM_STRUCTURE_CHART"
    VOLATILITY_DRIFT = "OPTIONS_VOLATILITY_DRIFT_CHART"
    MAX_PAIN_OVER_TIME = "OPTIONS_MAX_PAIN_OVER_TIME_CHART"
    OI_CHANGE = "OPTIONS_OPEN_INTEREST_CHANGE_TABLE"
    OI_BY_EXPIRATION = "OPTIONS_OPEN_INTEREST_BY_EXPIRATION_CHART"
    OI_OVER_TIME = "OPTIONS_OPEN_INTEREST_OVER_TIME_CHART"
    ORDER_FLOW_UNCONSOLIDATED = "OPTIONS_ORDER_FLOW_UNCONSOLIDATED_TABLE"
    # v0.4.0 — 7 new Tier-2 tools (broader market context: heat map, news,
    # gainers/losers, dark pool, equity prints, stock OHLC, interval map).
    HEAT_MAP = "OPTIONS_HEAT_MAP_CHART"
    INTERVAL_MAP = "INTERVAL_MAP_CHART"
    NEWS_ARTICLES = "NEWS_ARTICLE_LISTING"
    GAINERS_LOSERS = "OPTIONS_GAINERS_LOSERS_TABLE"
    DARK_POOL_LEVELS = "DARK_POOL_LEVELS_TABLE"
    EQUITY_PRINTS = "EQUITY_PRINTS_TABLE"
    STOCK_PRICE_TIME = "STOCK_PRICE_OVER_TIME_CHART"


class GreekMode(str, Enum):
    GAMMA = "GAMMA"
    DELTA = "DELTA"
    CHARM = "CHARM"
    VANNA = "VANNA"


class DataMode(str, Enum):
    PREMIUM = "PREMIUM"
    TRADE_COUNT = "TRADE_COUNT"
    VOLUME = "VOLUME"


class MoneynessType(str, Enum):
    OTM = "OUT_OF_THE_MONEY"
    ITM = "IN_THE_MONEY"
    ATM = "AT_THE_MONEY"


class TradeSideCodeType(str, Enum):
    AA = "AA"   # Above Ask (aggressive buy)
    A = "A"     # At Ask
    M = "M"     # Midpoint
    B = "B"     # At Bid
    BB = "BB"   # Below Bid (aggressive sell)


class RepresentationMode(str, Enum):
    PER_ONE_PERCENT_MOVE = "PER_ONE_PERCENT_MOVE"
    PER_ONE_DOLLAR_MOVE = "PER_ONE_DOLLAR_MOVE"
    RAW = "RAW"


class AggregationPeriod(str, Enum):
    ONE_MINUTE = "ONE_MINUTE"
    FIVE_MINUTE = "FIVE_MINUTE"
    TEN_MINUTE = "TEN_MINUTE"
    FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
    THIRTY_MINUTE = "THIRTY_MINUTE"
    ONE_HOUR = "ONE_HOUR"


class ContractTypeFilter(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class SentimentType(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class ChartType(str, Enum):
    CANDLESTICK = "CANDLESTICK"
    LINE = "LINE"


@dataclass(frozen=True)
class ToolDefinition:
    """Template for a tool — no ID until setup creates it."""

    canonical_name: str
    tool_type: ToolType
    endpoint: str
    label: str


@dataclass(frozen=True)
class ToolSpec:
    """A tool instance with a live ID."""

    tool_id: str
    tool_type: ToolType
    endpoint: str
    label: str


# The 19 tools to create during setup (11 from PR 0, 8 added in PR 2)
TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "exposure_by_strike": ToolDefinition(
        canonical_name="exposure_by_strike",
        tool_type=ToolType.EXPOSURE_BY_STRIKE,
        endpoint="options/exposure/strike",
        label="Exposure by Strike (GEX/DEX/CEX/VEX)",
    ),
    "net_drift": ToolDefinition(
        canonical_name="net_drift",
        tool_type=ToolType.NET_DRIFT,
        endpoint="options/net-drift",
        label="Net Drift",
    ),
    "iv_rank": ToolDefinition(
        canonical_name="iv_rank",
        tool_type=ToolType.IV_RANK,
        endpoint="options/iv-rank",
        label="IV Rank",
    ),
    "contract_side_stats": ToolDefinition(
        canonical_name="contract_side_stats",
        tool_type=ToolType.CONTRACT_SIDE_STATS,
        endpoint="options/contract/statistics/trade-side",
        label="Contract Side Statistics",
    ),
    "max_pain": ToolDefinition(
        canonical_name="max_pain",
        tool_type=ToolType.MAX_PAIN,
        endpoint="options/max-pain",
        label="Max Pain",
    ),
    "net_flow": ToolDefinition(
        canonical_name="net_flow",
        tool_type=ToolType.NET_FLOW,
        endpoint="options/net-flow",
        label="Net Flow",
    ),
    "order_flow": ToolDefinition(
        canonical_name="order_flow",
        tool_type=ToolType.ORDER_FLOW_CONSOLIDATED,
        endpoint="options/order-flow/consolidated",
        label="Order Flow (Consolidated)",
    ),
    "oi_by_strike": ToolDefinition(
        canonical_name="oi_by_strike",
        tool_type=ToolType.OI_BY_STRIKE,
        endpoint="options/open-interest/strike",
        label="Open Interest by Strike",
    ),
    "contract_statistics": ToolDefinition(
        canonical_name="contract_statistics",
        tool_type=ToolType.CONTRACT_STATISTICS,
        endpoint="options/contract/statistics",
        label="Contract Statistics",
    ),
    "exposure_by_expiration": ToolDefinition(
        canonical_name="exposure_by_expiration",
        tool_type=ToolType.EXPOSURE_BY_EXPIRATION,
        endpoint="options/exposure/expiration",
        label="Exposure by Expiration",
    ),
    "contract_price_time": ToolDefinition(
        canonical_name="contract_price_time",
        tool_type=ToolType.CONTRACT_PRICE_TIME,
        endpoint="options/contract/price/time",
        label="Contract Price / Time",
    ),
    # ----- PR 2: 8 new Tier-1 tools -----
    "volatility_skew": ToolDefinition(
        canonical_name="volatility_skew",
        tool_type=ToolType.VOLATILITY_SKEW,
        endpoint="options/volatility-skew",
        label="Volatility Skew",
    ),
    "term_structure": ToolDefinition(
        canonical_name="term_structure",
        tool_type=ToolType.TERM_STRUCTURE,
        endpoint="options/term-structure",
        label="Term Structure",
    ),
    "volatility_drift": ToolDefinition(
        canonical_name="volatility_drift",
        tool_type=ToolType.VOLATILITY_DRIFT,
        endpoint="options/volatility-drift",
        label="Volatility Drift",
    ),
    "max_pain_over_time": ToolDefinition(
        canonical_name="max_pain_over_time",
        tool_type=ToolType.MAX_PAIN_OVER_TIME,
        endpoint="options/max-pain/time",
        label="Max Pain / Time",
    ),
    "oi_change": ToolDefinition(
        canonical_name="oi_change",
        tool_type=ToolType.OI_CHANGE,
        endpoint="options/open-interest/change",
        label="Open Interest Change",
    ),
    "oi_by_expiration": ToolDefinition(
        canonical_name="oi_by_expiration",
        tool_type=ToolType.OI_BY_EXPIRATION,
        endpoint="options/open-interest/expiration",
        label="Open Interest by Expiration",
    ),
    "oi_over_time": ToolDefinition(
        canonical_name="oi_over_time",
        tool_type=ToolType.OI_OVER_TIME,
        endpoint="options/open-interest/time",
        label="Open Interest / Time",
    ),
    "unconsolidated_flow": ToolDefinition(
        canonical_name="unconsolidated_flow",
        tool_type=ToolType.ORDER_FLOW_UNCONSOLIDATED,
        endpoint="options/order-flow/unconsolidated",
        label="Order Flow (Unconsolidated)",
    ),
    # v0.4.0 — 7 new Tier-2 tools
    "heat_map": ToolDefinition(
        canonical_name="heat_map",
        tool_type=ToolType.HEAT_MAP,
        endpoint="options/heat-map",
        label="Heat Map",
    ),
    "interval_map": ToolDefinition(
        canonical_name="interval_map",
        tool_type=ToolType.INTERVAL_MAP,
        endpoint="interval-map",
        label="Interval Map",
    ),
    "news_articles": ToolDefinition(
        canonical_name="news_articles",
        tool_type=ToolType.NEWS_ARTICLES,
        endpoint="news/articles",
        label="News Articles",
    ),
    "gainers_losers": ToolDefinition(
        canonical_name="gainers_losers",
        tool_type=ToolType.GAINERS_LOSERS,
        endpoint="options/gainers-losers",
        label="Gainers / Losers",
    ),
    "dark_pool_levels": ToolDefinition(
        canonical_name="dark_pool_levels",
        tool_type=ToolType.DARK_POOL_LEVELS,
        endpoint="equities/dark-pool/levels",
        label="Dark Pool Levels",
    ),
    "equity_prints": ToolDefinition(
        canonical_name="equity_prints",
        tool_type=ToolType.EQUITY_PRINTS,
        endpoint="equities/prints",
        label="Equity Prints",
    ),
    "stock_price_time": ToolDefinition(
        canonical_name="stock_price_time",
        tool_type=ToolType.STOCK_PRICE_TIME,
        endpoint="equity/price/time",
        label="Stock Price / Time",
    ),
}


def build_tool_specs(tool_ids: dict[str, str]) -> dict[str, ToolSpec]:
    """Merge tool definitions with user-specific IDs from config."""
    specs: dict[str, ToolSpec] = {}
    for name, defn in TOOL_DEFINITIONS.items():
        tid = tool_ids.get(name)
        if tid:
            specs[name] = ToolSpec(
                tool_id=tid,
                tool_type=defn.tool_type,
                endpoint=defn.endpoint,
                label=defn.label,
            )
    return specs
