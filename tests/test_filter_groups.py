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
    add_leaf,
    build_filter_tree,
    ensure_default_and_branch,
    find_branch_by_key,
    find_default_and_branch,
    find_leaves,
    is_branch,
    is_leaf,
    normalise_field,
    normalise_operator,
    remove_leaves,
    serialise_value,
    summarise_filter_tree,
    update_leaf,
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


# ---------------------------------------------------------------------------
# Tree traversal + mutation (PR 13a — surgical clause edits)
# ---------------------------------------------------------------------------

def test_is_leaf_and_is_branch_classification() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    assert is_branch(tree) is True
    branch = tree["filters"][0]
    assert is_branch(branch) is True
    leaf = branch["filters"][0]
    assert is_leaf(leaf) is True
    assert is_branch(leaf) is False


def test_find_default_and_branch_returns_first_and_group() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    branch = find_default_and_branch(tree)
    assert branch is not None
    assert branch["conjunctionType"] == "AND"


def test_find_default_and_branch_returns_none_for_empty_tree() -> None:
    tree = build_filter_tree([])
    assert find_default_and_branch(tree) is None


def test_ensure_default_and_branch_creates_when_missing() -> None:
    tree = build_filter_tree([])
    branch = ensure_default_and_branch(tree)
    assert branch["conjunctionType"] == "AND"
    assert branch in tree["filters"]


def test_find_branch_by_key_walks_nested_tree() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    branch = tree["filters"][0]
    found = find_branch_by_key(tree, branch["key"])
    assert found is branch
    assert find_branch_by_key(tree, "nonexistent-key") is None


def test_add_leaf_appends_to_default_branch() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    new_leaf = add_leaf(tree, field="IS_COMPLEX", op="==", value=False)
    branch = tree["filters"][0]
    assert new_leaf in branch["filters"]
    assert branch["filters"][-1]["field"] == "IS_COMPLEX"
    assert branch["filters"][-1]["value"] == "false"


def test_add_leaf_creates_branch_if_tree_empty() -> None:
    tree = build_filter_tree([])
    add_leaf(tree, field="TICKER", op="==", value="SPY")
    assert len(tree["filters"]) == 1
    branch = tree["filters"][0]
    assert branch["conjunctionType"] == "AND"
    assert len(branch["filters"]) == 1
    assert branch["filters"][0]["field"] == "TICKER"


def test_add_leaf_targets_specific_branch_by_key() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    branch_a = tree["filters"][0]
    # Manually add a second AND-branch to simulate an OR alternative
    import uuid
    branch_b = {
        "key": str(uuid.uuid4()),
        "conjunctionType": "AND",
        "filters": [],
    }
    tree["filters"].append(branch_b)

    add_leaf(tree, field="QQQ_FILTER", op="==", value=True, branch_key=branch_b["key"])
    assert any(leaf["field"] == "QQQ_FILTER" for leaf in branch_b["filters"])
    assert not any(leaf["field"] == "QQQ_FILTER" for leaf in branch_a["filters"])


def test_add_leaf_unknown_branch_key_raises() -> None:
    tree = build_filter_tree([])
    with pytest.raises(ValueError, match="not found"):
        add_leaf(tree, field="X", op="==", value=1, branch_key="bogus-key")


def test_find_leaves_by_field() -> None:
    tree = build_filter_tree([
        {"field": "IS_COMPLEX", "op": "==", "value": False},
        {"field": "IS_TIED",    "op": "==", "value": False},
        {"field": "TICKER",     "op": "==", "value": "SPY"},
    ])
    found = find_leaves(tree, field="IS_COMPLEX")
    assert len(found) == 1
    parent, leaf = found[0]
    assert leaf["field"] == "IS_COMPLEX"
    assert parent["conjunctionType"] == "AND"


def test_find_leaves_by_field_normalises() -> None:
    """Lookup accepts friendly case forms — same normalisation as adds."""
    tree = build_filter_tree([{"field": "IS_COMPLEX", "op": "==", "value": False}])
    assert len(find_leaves(tree, field="is_complex")) == 1
    assert len(find_leaves(tree, field="isComplex")) == 1


def test_find_leaves_by_key() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    leaf = tree["filters"][0]["filters"][0]
    found = find_leaves(tree, key=leaf["key"])
    assert len(found) == 1
    assert found[0][1] is leaf


def test_remove_leaves_drops_matched_clauses() -> None:
    tree = build_filter_tree([
        {"field": "IS_COMPLEX", "op": "==", "value": False},
        {"field": "IS_TIED",    "op": "==", "value": False},
        {"field": "TICKER",     "op": "==", "value": "SPY"},
    ])
    n = remove_leaves(tree, field="IS_TIED")
    assert n == 1
    fields = [leaf["field"] for leaf in tree["filters"][0]["filters"]]
    assert fields == ["IS_COMPLEX", "TICKER"]


def test_remove_leaves_returns_zero_when_no_match() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    assert remove_leaves(tree, field="NOT_A_REAL_FIELD") == 0
    # Tree unchanged
    assert tree["filters"][0]["filters"][0]["field"] == "TICKER"


def test_remove_leaves_handles_multiple_matching_fields() -> None:
    """Two clauses on the same field — both get removed by a single call."""
    tree = build_filter_tree([
        {"field": "TICKER", "op": "==", "value": "SPY"},
        {"field": "TICKER", "op": "==", "value": "QQQ"},
    ])
    assert remove_leaves(tree, field="TICKER") == 2
    assert tree["filters"][0]["filters"] == []


def test_update_leaf_mutates_in_place() -> None:
    tree = build_filter_tree([{"field": "PREMIUM_IN_CENTS", "op": ">=", "value": 500_000}])
    leaf = tree["filters"][0]["filters"][0]
    update_leaf(leaf, new_value=5_000_000)
    assert leaf["value"] == "5000000"
    assert leaf["operationType"] == "GREATER_THAN_OR_EQUAL_TO"  # unchanged


def test_update_leaf_changes_operator() -> None:
    tree = build_filter_tree([{"field": "TICKER", "op": "==", "value": "SPY"}])
    leaf = tree["filters"][0]["filters"][0]
    update_leaf(leaf, new_op="!=")
    assert leaf["operationType"] == "DOES_NOT_EQUAL"
    assert leaf["value"] == "SPY"  # unchanged


def test_update_leaf_handles_explicit_false_value() -> None:
    """Sentinel-based 'unset' detection lets ``False`` / ``""`` / ``0`` be
    legitimate new values without being mistaken for "no change"."""
    tree = build_filter_tree([{"field": "IS_COMPLEX", "op": "==", "value": True}])
    leaf = tree["filters"][0]["filters"][0]
    update_leaf(leaf, new_value=False)
    assert leaf["value"] == "false"
