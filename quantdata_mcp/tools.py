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


class GreekMode(str, Enum):
    GAMMA = "GAMMA"
    DELTA = "DELTA"
    CHARM = "CHARM"
    VANNA = "VANNA"


class DataMode(str, Enum):
    PREMIUM = "PREMIUM"
    TRADE_COUNT = "TRADE_COUNT"
    VOLUME = "VOLUME"


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


# The 11 tools to create during setup
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
