"""Trivial unit tests for the filter clause helpers in ``_context``.

The helpers (``_eq``, ``_gte``, ``_lte``, ``_contains``) just emit small
dicts in a specific shape. These tests pin that shape so the rest of the
codebase can rely on it.
"""

from __future__ import annotations

from quantdata_mcp._context import _contains, _eq, _gte, _lte


def test_eq_helper_shape() -> None:
    assert _eq("AAPL") == {"filterOperationType": "EQUALS", "value": "AAPL"}
    assert _eq(["AA", "BB"]) == {
        "filterOperationType": "EQUALS",
        "value": ["AA", "BB"],
    }


def test_gte_helper_shape() -> None:
    assert _gte(100) == {
        "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
        "value": 100,
    }
    assert _gte(0.25) == {
        "filterOperationType": "GREATER_THAN_OR_EQUAL_TO",
        "value": 0.25,
    }


def test_lte_helper_shape() -> None:
    assert _lte(7.0) == {
        "filterOperationType": "LESS_THAN_OR_EQUAL_TO",
        "value": 7.0,
    }
    assert _lte(0) == {
        "filterOperationType": "LESS_THAN_OR_EQUAL_TO",
        "value": 0,
    }


def test_contains_helper_shape() -> None:
    assert _contains("SPX") == {
        "filterOperationType": "CONTAINS",
        "value": "SPX",
    }
