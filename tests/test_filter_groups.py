"""Tests for `quantdata_mcp.filter_groups` — the helper module that
normalises field names, resolves operator aliases, serialises Python
values to QuantData's wire format, and builds filter trees from flat
condition lists.
"""

from __future__ import annotations

import re

import pytest

from quantdata_mcp.filter_groups import (
    GROUP_TYPES,
    build_filter_tree,
    normalise_field,
    normalise_operator,
    serialise_value,
    summarise_filter_tree,
)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# normalise_field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("IS_COMPLEX", "IS_COMPLEX"),
        ("is_complex", "IS_COMPLEX"),
        ("isComplex", "IS_COMPLEX"),
        ("isPriceImprovement", "IS_PRICE_IMPROVEMENT"),
        ("PREMIUM_IN_CENTS", "PREMIUM_IN_CENTS"),
        ("greekDelta", "GREEK_DELTA"),
        ("ticker", "TICKER"),
    ],
)
def test_normalise_field(raw: str, expected: str) -> None:
    assert normalise_field(raw) == expected


def test_normalise_field_rejects_empty() -> None:
    with pytest.raises(ValueError):
        normalise_field("")


# ---------------------------------------------------------------------------
# normalise_operator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("EQUALS", "EQUALS"),
        ("equals", "EQUALS"),
        ("==", "EQUALS"),
        ("=", "EQUALS"),
        ("eq", "EQUALS"),
        ("!=", "DOES_NOT_EQUAL"),
        ("ne", "DOES_NOT_EQUAL"),
        ("neq", "DOES_NOT_EQUAL"),
        (">", "GREATER_THAN"),
        ("gt", "GREATER_THAN"),
        (">=", "GREATER_THAN_OR_EQUAL_TO"),
        ("gte", "GREATER_THAN_OR_EQUAL_TO"),
        ("<", "LESS_THAN"),
        ("lt", "LESS_THAN"),
        ("<=", "LESS_THAN_OR_EQUAL_TO"),
        ("lte", "LESS_THAN_OR_EQUAL_TO"),
        ("contains", "CONTAINS"),
    ],
)
def test_normalise_operator(raw: str, expected: str) -> None:
    assert normalise_operator(raw) == expected


def test_normalise_operator_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown filter operator"):
        normalise_operator("approximately")


# ---------------------------------------------------------------------------
# serialise_value — wire is always strings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (3.14, "3.14"),
        (1_000_000, "1000000"),
        ("SPY", "SPY"),
        (["AA", "A"], "AA,A"),
        (["BULLISH", "BEARISH", "NEUTRAL"], "BULLISH,BEARISH,NEUTRAL"),
        ([], ""),
    ],
)
def test_serialise_value(raw, expected: str) -> None:
    assert serialise_value(raw) == expected


# ---------------------------------------------------------------------------
# build_filter_tree
# ---------------------------------------------------------------------------

def test_empty_conditions_produce_empty_or_root() -> None:
    tree = build_filter_tree([])
    assert tree["conjunctionType"] == "OR"
    assert tree["filters"] == []
    assert _UUID_RE.match(tree["key"])


def test_single_condition_produces_one_and_branch() -> None:
    tree = build_filter_tree([{"field": "IS_COMPLEX", "op": "EQUALS", "value": False}])
    assert tree["conjunctionType"] == "OR"
    assert len(tree["filters"]) == 1
    branch = tree["filters"][0]
    assert branch["conjunctionType"] == "AND"
    assert len(branch["filters"]) == 1
    leaf = branch["filters"][0]
    assert leaf["field"] == "IS_COMPLEX"
    assert leaf["operationType"] == "EQUALS"
    assert leaf["value"] == "false"


def test_multi_condition_collapses_to_one_and_group() -> None:
    """Flat list = one AND group at the root, regardless of how many conditions."""
    tree = build_filter_tree([
        {"field": "IS_COMPLEX", "op": "EQUALS", "value": False},
        {"field": "IS_TIED",    "op": "EQUALS", "value": False},
        {"field": "TICKER",     "op": "==",     "value": "SPY"},
    ])
    branch = tree["filters"][0]
    assert branch["conjunctionType"] == "AND"
    fields = [leaf["field"] for leaf in branch["filters"]]
    assert fields == ["IS_COMPLEX", "IS_TIED", "TICKER"]


def test_friendly_inputs_are_normalised() -> None:
    tree = build_filter_tree([
        {"field": "is_complex",    "op": "==",  "value": False},
        {"field": "premiumInCents", "op": ">=", "value": 1_000_000},
    ])
    leaves = tree["filters"][0]["filters"]
    assert leaves[0]["field"] == "IS_COMPLEX"
    assert leaves[0]["operationType"] == "EQUALS"
    assert leaves[0]["value"] == "false"
    assert leaves[1]["field"] == "PREMIUM_IN_CENTS"
    assert leaves[1]["operationType"] == "GREATER_THAN_OR_EQUAL_TO"
    assert leaves[1]["value"] == "1000000"


def test_every_node_has_unique_uuid_key() -> None:
    tree = build_filter_tree([
        {"field": "IS_COMPLEX", "op": "==", "value": False},
        {"field": "IS_TIED",    "op": "==", "value": False},
    ])
    branch = tree["filters"][0]
    keys = {tree["key"], branch["key"]} | {leaf["key"] for leaf in branch["filters"]}
    assert len(keys) == 4  # all distinct
    for k in keys:
        assert _UUID_RE.match(k)


def test_invalid_condition_dict_raises() -> None:
    with pytest.raises(ValueError, match="must have keys"):
        build_filter_tree([{"field": "X", "op": "=="}])  # missing value
    with pytest.raises(ValueError, match="must be a dict"):
        build_filter_tree(["not a dict"])  # type: ignore[list-item]


def test_invalid_operator_propagates() -> None:
    with pytest.raises(ValueError, match="Unknown filter operator"):
        build_filter_tree([{"field": "X", "op": "approximately", "value": 1}])


# ---------------------------------------------------------------------------
# summarise_filter_tree
# ---------------------------------------------------------------------------

def test_summarise_empty_tree() -> None:
    tree = build_filter_tree([])
    assert "no clauses" in summarise_filter_tree(tree).lower()


def test_summarise_flat_and_group() -> None:
    tree = build_filter_tree([
        {"field": "IS_COMPLEX", "op": "==", "value": False},
        {"field": "IS_TIED",    "op": "==", "value": False},
    ])
    s = summarise_filter_tree(tree)
    assert s == "IS_COMPLEX=false AND IS_TIED=false"


def test_summarise_uses_op_symbols() -> None:
    tree = build_filter_tree([
        {"field": "PREMIUM_IN_CENTS", "op": ">=", "value": 1_000_000},
        {"field": "TRADE_TYPE",       "op": "!=", "value": "AUTO"},
    ])
    s = summarise_filter_tree(tree)
    assert "PREMIUM_IN_CENTS>=1000000" in s
    assert "TRADE_TYPE!=AUTO" in s


# ---------------------------------------------------------------------------
# Group type enum
# ---------------------------------------------------------------------------

def test_group_types_only_three() -> None:
    """Pin the discovered enum surface so a server-side change shows up here."""
    assert GROUP_TYPES == {
        "OPTION_TRADES_UNCONSOLIDATED",
        "OPTION_TRADES_CONSOLIDATED",
        "NEWS_ARTICLES",
    }
