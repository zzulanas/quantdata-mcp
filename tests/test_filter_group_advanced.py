"""Tests for the field catalog + advanced tree helpers (PR 13b).

Covers:
- ``filter_group_fields`` catalog lookups + render
- The tree normalisation / key-backfill / validation helpers in
  ``server`` (these are the non-trivial bits of
  ``qd_save_filter_group_advanced``).
"""

from __future__ import annotations

from quantdata_mcp.filter_group_fields import (
    ALL_OPERATORS,
    FIELDS_BY_GROUP_TYPE,
    fields_for,
    find_field,
    render_catalog,
)


# ---------------------------------------------------------------------------
# filter_group_fields catalog
# ---------------------------------------------------------------------------

def test_catalog_covers_all_three_group_types() -> None:
    assert set(FIELDS_BY_GROUP_TYPE) == {
        "OPTION_TRADES_UNCONSOLIDATED",
        "OPTION_TRADES_CONSOLIDATED",
        "NEWS_ARTICLES",
    }


def test_trades_catalog_contains_expected_fields() -> None:
    """Spot-check that the high-value trades fields all made it in."""
    fields = {f.name for f in fields_for("OPTION_TRADES_UNCONSOLIDATED")}
    expected = {
        "IS_COMPLEX", "IS_TIED", "IS_FLOOR", "IS_CANCELLED",
        "PREMIUM_IN_CENTS", "SIZE", "VOLUME", "OPEN_INTEREST",
        "GREEK_DELTA", "GREEK_GAMMA",
        "CONTRACT_TYPE", "MONEYNESS_MONEY_TYPE", "SENTIMENT_TYPE",
        "TRADE_SIDE_CODE", "TRADE_TYPE", "EXCHANGE",
        "TICKER", "OSI", "EXPIRATION_DATE",
        "FRACTIONAL_DAYS_TO_EXPIRATION",
    }
    missing = expected - fields
    assert not missing, f"Missing from trades catalog: {missing}"


def test_news_catalog_has_text_fields_with_contains() -> None:
    fields = {f.name: f for f in fields_for("NEWS_ARTICLES")}
    assert "CONTENT" in fields
    assert "CONTAINS" in fields["CONTENT"].operators
    # Ticker tagging works on news too
    assert "TICKER" in fields


def test_find_field_normalises_case() -> None:
    f1 = find_field("OPTION_TRADES_UNCONSOLIDATED", "IS_COMPLEX")
    f2 = find_field("OPTION_TRADES_UNCONSOLIDATED", "is_complex")
    assert f1 is not None
    assert f1 is f2  # same backing dataclass instance


def test_find_field_returns_none_for_unknown() -> None:
    assert find_field("OPTION_TRADES_UNCONSOLIDATED", "NOT_A_REAL_FIELD") is None


def test_enum_values_pinned_for_closed_enums() -> None:
    sentiment = find_field("OPTION_TRADES_UNCONSOLIDATED", "SENTIMENT_TYPE")
    assert sentiment is not None
    assert sentiment.kind == "enum"
    assert set(sentiment.values or ()) == {"BULLISH", "BEARISH", "NEUTRAL"}

    contract = find_field("OPTION_TRADES_UNCONSOLIDATED", "CONTRACT_TYPE")
    assert contract is not None
    assert set(contract.values or ()) == {"CALL", "PUT"}

    side = find_field("OPTION_TRADES_UNCONSOLIDATED", "TRADE_SIDE_CODE")
    assert side is not None
    assert set(side.values or ()) == {"AA", "A", "M", "B", "BB"}


def test_open_enum_kind_for_freeform_lists() -> None:
    """``TRADE_TYPE`` is open-ended (server adds new values), so it's
    ``enum_open`` rather than ``enum``. Common values are still pinned."""
    f = find_field("OPTION_TRADES_UNCONSOLIDATED", "TRADE_TYPE")
    assert f is not None
    assert f.kind == "enum_open"
    # Common values are populated for hint/output but not strictly enforced
    assert "AUTO" in (f.values or ())


def test_numeric_fields_get_full_numeric_operator_set() -> None:
    f = find_field("OPTION_TRADES_UNCONSOLIDATED", "PREMIUM_IN_CENTS")
    assert f is not None
    assert "GREATER_THAN_OR_EQUAL_TO" in f.operators
    assert "LESS_THAN_OR_EQUAL_TO" in f.operators
    assert "DOES_NOT_EQUAL" in f.operators


def test_bool_fields_get_only_equality_operators() -> None:
    f = find_field("OPTION_TRADES_UNCONSOLIDATED", "IS_COMPLEX")
    assert f is not None
    assert set(f.operators) == {"EQUALS", "DOES_NOT_EQUAL"}
    # No GTE/LTE on booleans
    assert "GREATER_THAN" not in f.operators


def test_no_operators_outside_canonical_set() -> None:
    """Catch typos: every operator on every field must be one of the seven
    canonical operators QuantData accepts."""
    for group_type, fields in FIELDS_BY_GROUP_TYPE.items():
        for f in fields:
            for op in f.operators:
                assert op in ALL_OPERATORS, (
                    f"{group_type}/{f.name}: operator {op!r} not canonical"
                )


# ---------------------------------------------------------------------------
# render_catalog output
# ---------------------------------------------------------------------------

def test_render_catalog_groups_by_kind() -> None:
    out = render_catalog("OPTION_TRADES_UNCONSOLIDATED")
    # Section headers for at least the kinds we've defined
    assert "BOOL" in out
    assert "ENUM" in out
    assert "TEXT" in out


def test_render_catalog_kind_filter_narrows_output() -> None:
    full = render_catalog("OPTION_TRADES_UNCONSOLIDATED")
    bool_only = render_catalog("OPTION_TRADES_UNCONSOLIDATED", kind_filter="bool")
    assert len(bool_only) < len(full)
    # Bool-only output mentions IS_COMPLEX but not GREEK_DELTA
    assert "IS_COMPLEX" in bool_only
    assert "GREEK_DELTA" not in bool_only


def test_render_catalog_unknown_group_type_lists_valid_options() -> None:
    out = render_catalog("BOGUS")
    assert "Unknown group type" in out
    for valid in FIELDS_BY_GROUP_TYPE:
        assert valid in out


def test_render_catalog_shows_enum_values() -> None:
    out = render_catalog("OPTION_TRADES_UNCONSOLIDATED", kind_filter="enum")
    assert "BULLISH" in out
    assert "BEARISH" in out
    assert "NEUTRAL" in out


# ---------------------------------------------------------------------------
# Tree-normalisation helpers (defined in server.py — import at test time)
# ---------------------------------------------------------------------------

def test_ensure_keys_backfills_missing_uuids() -> None:
    from quantdata_mcp.server import _ensure_keys

    # Caller forgot every key
    tree = {
        "conjunctionType": "OR",
        "filters": [
            {
                "conjunctionType": "AND",
                "filters": [
                    {"field": "IS_COMPLEX", "operationType": "EQUALS", "value": "false"},
                    {"field": "TICKER", "operationType": "EQUALS", "value": "SPY"},
                ],
            }
        ],
    }
    out = _ensure_keys(tree)
    assert "key" in out
    assert "key" in out["filters"][0]
    for leaf in out["filters"][0]["filters"]:
        assert "key" in leaf
    # All keys are unique
    keys = {out["key"], out["filters"][0]["key"]} | {leaf["key"] for leaf in out["filters"][0]["filters"]}
    assert len(keys) == 4


def test_normalise_tree_in_place_handles_aliases() -> None:
    from quantdata_mcp.server import _normalise_tree_in_place

    tree = {
        "conjunctionType": "OR",
        "filters": [
            {
                "conjunctionType": "AND",
                "filters": [
                    {"field": "is_complex", "op": ">=", "value": False},
                    {"field": "premiumInCents", "op": "gte", "value": 1_000_000},
                ],
            }
        ],
    }
    _normalise_tree_in_place(tree)
    leaf1 = tree["filters"][0]["filters"][0]
    assert leaf1["field"] == "IS_COMPLEX"
    # `op` got promoted to operationType, then normalised
    assert leaf1["operationType"] == "GREATER_THAN_OR_EQUAL_TO"
    assert leaf1["value"] == "false"
    assert "op" not in leaf1

    leaf2 = tree["filters"][0]["filters"][1]
    assert leaf2["field"] == "PREMIUM_IN_CENTS"
    assert leaf2["operationType"] == "GREATER_THAN_OR_EQUAL_TO"
    assert leaf2["value"] == "1000000"


def test_validate_tree_flags_unknown_field() -> None:
    from quantdata_mcp.server import _validate_tree

    tree = {
        "conjunctionType": "OR",
        "filters": [
            {
                "conjunctionType": "AND",
                "filters": [
                    {"field": "NOT_A_REAL_FIELD", "operationType": "EQUALS", "value": "x"},
                ],
            }
        ],
    }
    warnings = _validate_tree(tree, "OPTION_TRADES_UNCONSOLIDATED")
    assert any("not in" in w and "catalog" in w for w in warnings)


def test_validate_tree_flags_invalid_operator_for_bool() -> None:
    from quantdata_mcp.server import _validate_tree

    tree = {
        "conjunctionType": "OR",
        "filters": [
            {
                "conjunctionType": "AND",
                "filters": [
                    # GTE doesn't apply to a boolean
                    {"field": "IS_COMPLEX", "operationType": "GREATER_THAN_OR_EQUAL_TO", "value": "false"},
                ],
            }
        ],
    }
    warnings = _validate_tree(tree, "OPTION_TRADES_UNCONSOLIDATED")
    assert any("IS_COMPLEX" in w for w in warnings)


def test_validate_tree_flags_invalid_enum_value() -> None:
    from quantdata_mcp.server import _validate_tree

    tree = {
        "conjunctionType": "OR",
        "filters": [
            {
                "conjunctionType": "AND",
                "filters": [
                    {"field": "SENTIMENT_TYPE", "operationType": "EQUALS", "value": "MAGENTA"},
                ],
            }
        ],
    }
    warnings = _validate_tree(tree, "OPTION_TRADES_UNCONSOLIDATED")
    assert any("MAGENTA" in w for w in warnings)


def test_validate_tree_accepts_valid_input() -> None:
    from quantdata_mcp.server import _validate_tree

    tree = {
        "conjunctionType": "OR",
        "filters": [
            {
                "conjunctionType": "AND",
                "filters": [
                    {"field": "IS_COMPLEX", "operationType": "EQUALS", "value": "false"},
                    {"field": "PREMIUM_IN_CENTS", "operationType": "GREATER_THAN_OR_EQUAL_TO", "value": "1000000"},
                    {"field": "SENTIMENT_TYPE", "operationType": "EQUALS", "value": "BULLISH,BEARISH"},
                ],
            }
        ],
    }
    warnings = _validate_tree(tree, "OPTION_TRADES_UNCONSOLIDATED")
    assert warnings == []
