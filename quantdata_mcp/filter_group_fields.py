"""Field / operator / value catalog per filter-group type.

QuantData has no public ``/filter-fields`` endpoint — the web UI hardcodes
its dropdowns client-side. This module reproduces that catalog for the
LLM so it can pick valid fields without guessing, plus give users the same
"what's available?" discovery the UI offers.

The catalog was assembled from:
- Live filter-group DTO inspection (this user's groups + 55 public groups)
- The order_flow / unconsolidated_flow filter scaffolds
- Operator probing live against the API

Field names use SCREAMING_SNAKE_CASE on the wire (matching how they appear
inside filter-group trees). Note this is **distinct** from the camelCase
namespace used in ``metadata.filter`` on tools — see
``quantdata_mcp/filters.py`` for that surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field


# Canonical operators discovered live. Missing from the API: BETWEEN
# (rejected with 400 — see PR 1's ``filters.py`` notes).
ALL_OPERATORS = (
    "EQUALS",
    "DOES_NOT_EQUAL",
    "GREATER_THAN",
    "GREATER_THAN_OR_EQUAL_TO",
    "LESS_THAN",
    "LESS_THAN_OR_EQUAL_TO",
    "CONTAINS",
)

# Operator subsets per field-type bucket — used by ``qd_list_filter_fields``
# output and (lightly) by suggestion logic.
_BOOL_OPS = ("EQUALS", "DOES_NOT_EQUAL")
_NUMERIC_OPS = (
    "EQUALS",
    "DOES_NOT_EQUAL",
    "GREATER_THAN",
    "GREATER_THAN_OR_EQUAL_TO",
    "LESS_THAN",
    "LESS_THAN_OR_EQUAL_TO",
)
_ENUM_OPS = ("EQUALS", "DOES_NOT_EQUAL")
_TEXT_OPS = ("EQUALS", "CONTAINS")


@dataclass(frozen=True)
class FilterField:
    """One filter field with its operators, value type, and (when known)
    its enum values.

    Attributes:
        name: SCREAMING_SNAKE_CASE field name as it appears on the wire.
        kind: ``"bool"`` / ``"int"`` / ``"float"`` / ``"enum"`` /
            ``"enum_open"`` / ``"text"`` / ``"date"``.
        operators: Tuple of canonical operator names accepted for this
            field.
        values: For ``"enum"`` fields, the closed set of valid string
            values. For ``"enum_open"`` fields, common observed values.
            ``None`` for free-text / numeric / date / bool.
        description: One-line LLM-friendly explanation. Skipped from
            output when empty.
    """

    name: str
    kind: str
    operators: tuple[str, ...]
    values: tuple[str, ...] | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# OPTION_TRADES_UNCONSOLIDATED — applies to Net Drift, Net Flow, Order Flow
# (unconsolidated), Contract Statistics, Contract Side Stats, Exposure tools,
# Volatility/IV tools, OI tools, Term Structure, Volatility Drift, Skew, etc.
# Anything that consumes individual option trades.
# ---------------------------------------------------------------------------

_TRADES_FIELDS: tuple[FilterField, ...] = (
    # --- Bool quality flags (the "noise stripping" set) -------------------
    FilterField("IS_COMPLEX", "bool", _BOOL_OPS, description="Multi-leg spread (vs single-leg) trade"),
    FilterField("IS_TIED", "bool", _BOOL_OPS, description="Trade tied to underlying stock"),
    FilterField("IS_FLOOR", "bool", _BOOL_OPS, description="Floor (vs auto/electronic) trade"),
    FilterField("IS_CANCELLED", "bool", _BOOL_OPS, description="Trade was later cancelled"),
    FilterField("IS_PRICE_IMPROVEMENT", "bool", _BOOL_OPS, description="Trade priced better than NBBO"),
    FilterField("IS_UNUSUAL", "bool", _BOOL_OPS, description="Flagged as unusual options activity"),
    FilterField("IS_OPENING_POSITION", "bool", _BOOL_OPS, description="Volume > prior open interest (likely opening)"),
    FilterField("IS_VOLUME_GREATER_THAN_OPEN_INTEREST", "bool", _BOOL_OPS, description="Same as IS_OPENING_POSITION"),
    FilterField("IS_INDEX", "bool", _BOOL_OPS, description="Underlier is an index (SPX, NDX, RUT, ...)"),
    FilterField("IS_ETF", "bool", _BOOL_OPS, description="Underlier is an ETF"),
    FilterField("IS_GOLDEN_SWEEP", "bool", _BOOL_OPS, description="Multi-exchange aggressive sweep (consolidated only)"),
    # --- Numeric: premium / size / OI ------------------------------------
    FilterField("PREMIUM_IN_CENTS", "int", _NUMERIC_OPS, description="Trade premium in cents (1_000_000 = $10K)"),
    FilterField("SIZE", "int", _NUMERIC_OPS, description="Trade size in contracts"),
    FilterField("VOLUME", "int", _NUMERIC_OPS, description="Total daily contract volume"),
    FilterField("OPEN_INTEREST", "int", _NUMERIC_OPS, description="Open interest at trade time"),
    FilterField("DEALER_DIRECTIONAL_OPEN_INTEREST", "int", _NUMERIC_OPS),
    FilterField("BID_ASK_SPREAD_IN_CENTS", "int", _NUMERIC_OPS),
    FilterField("MONEYNESS_DEGREE_IN_CENTS", "int", _NUMERIC_OPS, description="Distance from spot in cents"),
    FilterField("MONEYNESS_DEGREE_IN_PERCENT", "float", _NUMERIC_OPS, description="Distance from spot as % (5.0 = 5%)"),
    FilterField("FRACTIONAL_DAYS_TO_EXPIRATION", "float", _NUMERIC_OPS, description="DTE; <1 = 0DTE"),
    FilterField("STRIKE_PRICE_IN_CENTS", "int", _NUMERIC_OPS),
    FilterField("ASK_PRICE_IN_CENTS", "int", _NUMERIC_OPS),
    FilterField("BID_PRICE_IN_CENTS", "int", _NUMERIC_OPS),
    FilterField("OPTION_PRICE_IN_CENTS", "int", _NUMERIC_OPS),
    FilterField("STOCK_PRICE_IN_CENTS", "int", _NUMERIC_OPS),
    FilterField("IMPLIED_VOLATILITY", "float", _NUMERIC_OPS, description="IV as decimal (0.25 = 25%)"),
    # --- Greeks (numeric) -----------------------------------------------
    FilterField("GREEK_DELTA", "float", _NUMERIC_OPS),
    FilterField("GREEK_GAMMA", "float", _NUMERIC_OPS),
    FilterField("GREEK_THETA", "float", _NUMERIC_OPS),
    FilterField("GREEK_VEGA", "float", _NUMERIC_OPS),
    FilterField("GREEK_RHO", "float", _NUMERIC_OPS),
    FilterField("GREEK_OMEGA", "float", _NUMERIC_OPS),
    FilterField("GREEK_CHARM", "float", _NUMERIC_OPS),
    FilterField("GREEK_VANNA", "float", _NUMERIC_OPS),
    FilterField("GREEK_VOMMA", "float", _NUMERIC_OPS),
    FilterField("GREEK_VETA", "float", _NUMERIC_OPS),
    FilterField("GREEK_COLOR", "float", _NUMERIC_OPS),
    FilterField("GREEK_SPEED", "float", _NUMERIC_OPS),
    FilterField("GREEK_ULTIMA", "float", _NUMERIC_OPS),
    FilterField("GREEK_ZOMMA", "float", _NUMERIC_OPS),
    FilterField("GREEK_SIGMA", "float", _NUMERIC_OPS),
    # --- Closed enums --------------------------------------------------
    FilterField("CONTRACT_TYPE", "enum", _ENUM_OPS, ("CALL", "PUT")),
    FilterField(
        "MONEYNESS_MONEY_TYPE",
        "enum",
        _ENUM_OPS,
        ("IN_THE_MONEY", "OUT_OF_THE_MONEY", "AT_THE_MONEY"),
    ),
    FilterField(
        "SENTIMENT_TYPE",
        "enum",
        _ENUM_OPS,
        ("BULLISH", "BEARISH", "NEUTRAL"),
        description="Server-classified directional sentiment",
    ),
    FilterField(
        "TRADE_SIDE_CODE",
        "enum",
        _ENUM_OPS,
        ("AA", "A", "M", "B", "BB"),
        description="AA=above ask, A=at ask, M=mid, B=at bid, BB=below bid",
    ),
    # --- Open enums (free string list, common values shown) ------------
    FilterField(
        "TRADE_TYPE",
        "enum_open",
        _ENUM_OPS,
        ("AUTO", "ISO", "M2S_FLR", "MULTI_AUTO_COB", "MULTI_FLR_PP"),
        description="Trade routing type — server-defined; multi-value via comma-separated string",
    ),
    FilterField(
        "TRADE_CONSOLIDATION_TYPE",
        "enum_open",
        _ENUM_OPS,
        ("BLOCK", "SWEEP", "SPLIT"),
        description="Consolidated table only — how the trade was rolled up",
    ),
    FilterField("EXCHANGE", "enum_open", _ENUM_OPS, ("CBOE", "BATS", "NASDAQ", "NYSE", "ARCA", "PHLX")),
    FilterField("EXCHANGE_TYPE", "enum_open", _ENUM_OPS, ("CBOE", "BATS", "NASDAQ", "NYSE", "ARCA", "PHLX")),
    FilterField("SECTOR", "enum_open", _ENUM_OPS, description="Open-ended free string per QuantData's data"),
    FilterField("SECTOR_TYPE", "enum_open", _ENUM_OPS),
    FilterField("INDUSTRY", "enum_open", _ENUM_OPS),
    FilterField("INDUSTRY_TYPE", "enum_open", _ENUM_OPS),
    # --- Identifiers / dates -------------------------------------------
    FilterField("TICKER", "text", _TEXT_OPS, description="Underlying ticker — multi-value via comma-separated"),
    FilterField("OSI", "text", _TEXT_OPS, description="OCC option symbol (full OSI string)"),
    FilterField("EXPIRATION_DATE", "date", _NUMERIC_OPS, description="YYYY-MM-DD"),
    FilterField("SESSION_DATE", "date", _NUMERIC_OPS, description="YYYY-MM-DD"),
)


# ---------------------------------------------------------------------------
# OPTION_TRADES_CONSOLIDATED — superset of unconsolidated. Adds the
# consolidation-type field; everything else is shared. We define this as a
# tuple-extension instead of duplicating the whole list.
# ---------------------------------------------------------------------------

_CONSOLIDATED_FIELDS: tuple[FilterField, ...] = _TRADES_FIELDS


# ---------------------------------------------------------------------------
# NEWS_ARTICLES — the only non-trades group type. Different field set:
# ticker tagging, sentiment classification, and CONTAINS searches over
# article text.
# ---------------------------------------------------------------------------

_NEWS_FIELDS: tuple[FilterField, ...] = (
    FilterField(
        "TICKER",
        "text",
        _TEXT_OPS,
        description="Tickers tagged in the article — comma-separated for multiple",
    ),
    FilterField(
        "TOPIC",
        "enum_open",
        _ENUM_OPS,
        description="Server-defined article topic (open-ended)",
    ),
    FilterField(
        "SENTIMENT",
        "enum",
        _ENUM_OPS,
        ("BULLISH", "BEARISH", "NEUTRAL"),
        description="Article-level sentiment classification",
    ),
    FilterField("TITLE", "text", _TEXT_OPS, description="Article headline — supports CONTAINS"),
    FilterField("BODY", "text", _TEXT_OPS, description="Article body — supports CONTAINS"),
    FilterField("CONTENT", "text", _TEXT_OPS, description="Combined title + body — supports CONTAINS"),
    FilterField("PUBLISHED_TIME", "date", _NUMERIC_OPS, description="Epoch ms"),
)


# Public mapping — group_type → ordered tuple of fields.
FIELDS_BY_GROUP_TYPE: dict[str, tuple[FilterField, ...]] = {
    "OPTION_TRADES_UNCONSOLIDATED": _TRADES_FIELDS,
    "OPTION_TRADES_CONSOLIDATED": _CONSOLIDATED_FIELDS,
    "NEWS_ARTICLES": _NEWS_FIELDS,
}


def fields_for(group_type: str) -> tuple[FilterField, ...]:
    """Return the catalog for a given group type. Empty tuple for unknown types."""
    return FIELDS_BY_GROUP_TYPE.get(group_type, ())


def find_field(group_type: str, name: str) -> FilterField | None:
    """Look up a single field by name (post-normalisation) within a group type."""
    upper = name.upper()
    for f in fields_for(group_type):
        if f.name == upper:
            return f
    return None


def render_catalog(group_type: str, *, kind_filter: str | None = None) -> str:
    """Pretty-print the catalog for ``qd_list_filter_fields`` output.

    Groups fields by ``kind`` so the LLM (and humans) can scan for the right
    one quickly. Pass ``kind_filter`` to narrow ("bool", "enum", "float",
    etc.) — useful for big trades catalogs where the full list is long.
    """
    fields = fields_for(group_type)
    if not fields:
        return f"Unknown group type: {group_type}. Valid: {', '.join(FIELDS_BY_GROUP_TYPE)}"

    if kind_filter:
        fields = tuple(f for f in fields if f.kind == kind_filter)
        if not fields:
            return f"No fields of kind={kind_filter!r} in {group_type}."

    # Group by kind for readability
    by_kind: dict[str, list[FilterField]] = {}
    for f in fields:
        by_kind.setdefault(f.kind, []).append(f)

    sections: list[str] = []
    sections.append(f"Filter Fields — {group_type} ({len(fields)} fields)\n")
    KIND_ORDER = ("bool", "int", "float", "enum", "enum_open", "text", "date")
    for kind in KIND_ORDER:
        if kind not in by_kind:
            continue
        sections.append(f"{kind.upper()}")
        for f in by_kind[kind]:
            ops = ", ".join(_short_op(op) for op in f.operators)
            line = f"  {f.name:38s} ops=[{ops}]"
            if f.values:
                vals = ", ".join(f.values[:8])
                if len(f.values) > 8:
                    vals += ", ..."
                line += f"\n    values: {vals}"
            if f.description:
                line += f"\n    {f.description}"
            sections.append(line)
        sections.append("")
    return "\n".join(sections).rstrip()


_SHORT_OP = {
    "EQUALS": "==",
    "DOES_NOT_EQUAL": "!=",
    "GREATER_THAN": ">",
    "GREATER_THAN_OR_EQUAL_TO": ">=",
    "LESS_THAN": "<",
    "LESS_THAN_OR_EQUAL_TO": "<=",
    "CONTAINS": "contains",
}


def _short_op(op: str) -> str:
    return _SHORT_OP.get(op, op)
