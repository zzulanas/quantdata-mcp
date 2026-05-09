"""Helpers for building and summarising QuantData filter groups.

QuantData's filter groups are server-side, persistent, **named** filter sets
that attach to tools via a tool's ``filterGroupIds`` array. Once attached, the
group is AND'd onto every fetch alongside the tool's ``metadata.filter`` —
the user gets persistent custom filtering that survives sessions and is
visible in the QuantData web UI.

The on-the-wire representation is a *tree* of conjunctions:

    filter:                                     # root, conjunctionType=OR
      filters:                                  # OR alternatives (rare to have >1)
        - conjunctionType=AND                   # AND group of leaves
          filters:
            - {key, field, operationType, value}   # a leaf clause
            - {key, field, operationType, value}
            ...

For the LLM-friendly path we collapse this to a flat list of
``{field, op, value}`` dicts (one AND-group at the root). The advanced /
nested-OR shape is reserved for a future ``advanced_tree`` parameter.

Field names are SCREAMING_SNAKE_CASE on the wire (``IS_COMPLEX``,
``GREEK_DELTA``, ``PREMIUM_IN_CENTS``). We accept friendlier variants
(``is_complex``, ``isComplex``) and normalise.

Values on the wire are always strings — booleans become ``"true"`` /
``"false"``, numbers stringified, lists joined by commas (``"AA,A"``).
"""

from __future__ import annotations

import re
import uuid
from typing import Any


# Canonical filter-group type enum. Three values exist on the API today; the
# server returns 400 for anything else.
GROUP_TYPES = frozenset({
    "OPTION_TRADES_UNCONSOLIDATED",
    "OPTION_TRADES_CONSOLIDATED",
    "NEWS_ARTICLES",
})

# Operator aliases — accept compact forms because LLMs (and humans) prefer
# `>=` over `GREATER_THAN_OR_EQUAL_TO`.
_OPERATOR_ALIASES = {
    "==": "EQUALS",
    "=": "EQUALS",
    "eq": "EQUALS",
    "equals": "EQUALS",
    "!=": "DOES_NOT_EQUAL",
    "ne": "DOES_NOT_EQUAL",
    "neq": "DOES_NOT_EQUAL",
    "does_not_equal": "DOES_NOT_EQUAL",
    ">": "GREATER_THAN",
    "gt": "GREATER_THAN",
    "greater_than": "GREATER_THAN",
    ">=": "GREATER_THAN_OR_EQUAL_TO",
    "gte": "GREATER_THAN_OR_EQUAL_TO",
    "greater_than_or_equal_to": "GREATER_THAN_OR_EQUAL_TO",
    "<": "LESS_THAN",
    "lt": "LESS_THAN",
    "less_than": "LESS_THAN",
    "<=": "LESS_THAN_OR_EQUAL_TO",
    "lte": "LESS_THAN_OR_EQUAL_TO",
    "less_than_or_equal_to": "LESS_THAN_OR_EQUAL_TO",
    "contains": "CONTAINS",
}


def normalise_field(field: str) -> str:
    """Convert a friendly field name to SCREAMING_SNAKE_CASE.

    Accepts:
        - ``"IS_COMPLEX"``  → ``"IS_COMPLEX"``  (no change)
        - ``"is_complex"``  → ``"IS_COMPLEX"``
        - ``"isComplex"``   → ``"IS_COMPLEX"``
        - ``"isPriceImprovement"`` → ``"IS_PRICE_IMPROVEMENT"``
    """
    if not field:
        raise ValueError("filter field cannot be empty")
    # Already SCREAMING_SNAKE (with or without underscores — e.g. "TICKER" or "IS_COMPLEX")
    if field.isupper():
        return field
    # snake_case → SCREAMING_SNAKE
    if "_" in field:
        return field.upper()
    # camelCase → SCREAMING_SNAKE (insert underscores before uppercase letters)
    return re.sub(r"(?<!^)(?=[A-Z])", "_", field).upper()


def normalise_operator(op: str) -> str:
    """Resolve a friendly operator to the canonical API form.

    Accepts the canonical name verbatim, common aliases (``"="``, ``"=="``,
    ``"gte"``, ``">="``, ``"eq"``, ...), or any case variant. Unknown
    operators raise ``ValueError`` so the caller surfaces the typo immediately
    rather than the API rejecting at PUT time.
    """
    norm = op.strip().lower()
    if norm.upper() in {
        "EQUALS", "DOES_NOT_EQUAL",
        "GREATER_THAN", "GREATER_THAN_OR_EQUAL_TO",
        "LESS_THAN", "LESS_THAN_OR_EQUAL_TO",
        "CONTAINS",
    }:
        return op.upper()
    if norm in _OPERATOR_ALIASES:
        return _OPERATOR_ALIASES[norm]
    raise ValueError(
        f"Unknown filter operator: {op!r}. Use one of "
        "EQUALS / DOES_NOT_EQUAL / GREATER_THAN / GREATER_THAN_OR_EQUAL_TO / "
        "LESS_THAN / LESS_THAN_OR_EQUAL_TO / CONTAINS, or aliases like "
        "'==', '!=', '>=', '<=', '>', '<', 'eq', 'gte', 'contains'."
    )


def serialise_value(value: Any) -> str:
    """Convert a Python value to the wire representation.

    The QuantData API expects all filter values as strings — booleans as
    ``"true"`` / ``"false"``, numbers stringified, lists joined by commas
    (e.g. ``["AA", "A"]`` → ``"AA,A"``).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value)


def build_filter_tree(conditions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a filter tree from a flat list of ``{field, op, value}`` dicts.

    Produces a single OR root containing one AND group, which covers the
    common "all of these conditions must hold" case. For OR alternatives
    (``A or (B and C)``) callers should use the advanced tree path — not yet
    exposed.

    Each node gets a fresh UUID ``key`` because the API rejects payloads
    without them.

    Empty conditions produce an empty OR root, which the API accepts and
    treats as "no additional filtering."
    """
    if not conditions:
        return {
            "key": str(uuid.uuid4()),
            "conjunctionType": "OR",
            "filters": [],
        }

    leaves = []
    for c in conditions:
        if not isinstance(c, dict):
            raise ValueError(f"each condition must be a dict, got {type(c).__name__}: {c!r}")
        if "field" not in c or "op" not in c or "value" not in c:
            raise ValueError(
                f"condition must have keys 'field', 'op', 'value' — got {sorted(c.keys())}"
            )
        leaves.append({
            "key": str(uuid.uuid4()),
            "field": normalise_field(c["field"]),
            "operationType": normalise_operator(c["op"]),
            "value": serialise_value(c["value"]),
        })

    return {
        "key": str(uuid.uuid4()),
        "conjunctionType": "OR",
        "filters": [
            {
                "key": str(uuid.uuid4()),
                "conjunctionType": "AND",
                "filters": leaves,
            }
        ],
    }


def summarise_filter_tree(tree: dict[str, Any]) -> str:
    """Render a filter tree into a single-line human/LLM-readable summary.

    Examples:
        ``IS_COMPLEX=false AND IS_TIED=false``
        ``IS_COMPLEX=false AND TICKER=SPY AND PREMIUM_IN_CENTS>=1000000``
        ``(A AND B) OR (C AND D)`` for nested trees.

    Used by ``qd_list_filter_groups`` and ``qd_get_filter_group`` so the LLM
    can present the active filter clauses without dumping the raw tree.
    """
    return _summarise_node(tree)


_OP_SYMBOL = {
    "EQUALS": "=",
    "DOES_NOT_EQUAL": "!=",
    "GREATER_THAN": ">",
    "GREATER_THAN_OR_EQUAL_TO": ">=",
    "LESS_THAN": "<",
    "LESS_THAN_OR_EQUAL_TO": "<=",
    "CONTAINS": " contains ",
}


def _summarise_node(node: dict[str, Any]) -> str:
    if not node:
        return "(empty)"
    # Leaf
    if "field" in node:
        op = _OP_SYMBOL.get(node.get("operationType", ""), node.get("operationType", "?"))
        return f"{node.get('field', '?')}{op}{node.get('value', '?')}"
    # Inner node
    children = node.get("filters") or []
    if not children:
        return "(no clauses)"
    sep = " AND " if node.get("conjunctionType") == "AND" else " OR "
    parts = [_summarise_node(c) for c in children]
    if len(parts) == 1:
        return parts[0]
    # Wrap in parens when nesting an inner expression
    if node.get("conjunctionType") == "OR":
        return sep.join(f"({p})" if " AND " in p else p for p in parts)
    return sep.join(parts)


# ---------------------------------------------------------------------------
# Tree traversal + mutation helpers (used by surgical clause-edit MCP tools)
# ---------------------------------------------------------------------------

# Sentinel for "argument not provided" so callers can pass ``None`` /
# ``""`` / ``False`` as legitimate new values to ``update_leaf``.
_SENTINEL_UNSET: Any = object()


def is_leaf(node: dict[str, Any]) -> bool:
    """A leaf node has a ``field`` key; an inner node has ``filters``."""
    return isinstance(node, dict) and "field" in node


def is_branch(node: dict[str, Any]) -> bool:
    """An inner conjunction node (AND/OR group of children)."""
    return isinstance(node, dict) and "conjunctionType" in node and "filters" in node


def find_default_and_branch(tree: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first AND-group child of the OR root, or ``None`` if there
    isn't one yet. The "default" branch for new clauses goes here.
    """
    for child in tree.get("filters") or []:
        if is_branch(child) and child.get("conjunctionType") == "AND":
            return child
    return None


def ensure_default_and_branch(tree: dict[str, Any]) -> dict[str, Any]:
    """Like :func:`find_default_and_branch` but creates the AND-group if
    missing. Mutates ``tree`` in place. Returns the AND-group dict.
    """
    branch = find_default_and_branch(tree)
    if branch is not None:
        return branch
    # Need to create it. Append a fresh AND-group to the OR root.
    new_branch = {
        "key": str(uuid.uuid4()),
        "conjunctionType": "AND",
        "filters": [],
    }
    tree.setdefault("filters", []).append(new_branch)
    return new_branch


def find_branch_by_key(tree: dict[str, Any], branch_key: str) -> dict[str, Any] | None:
    """Search the tree for a branch (inner conjunction node) with the given
    UUID key. Used to target a specific OR alternative for new clauses.
    """
    if not isinstance(tree, dict):
        return None
    if is_branch(tree) and tree.get("key") == branch_key:
        return tree
    for child in tree.get("filters") or []:
        found = find_branch_by_key(child, branch_key)
        if found is not None:
            return found
    return None


def find_leaves(
    tree: dict[str, Any],
    *,
    key: str | None = None,
    field: str | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return all matching leaves as (parent_branch, leaf) pairs.

    Pass ``key`` to match a leaf's UUID exactly; pass ``field`` to match
    every leaf with that ``field`` value (``field`` is normalised). Pass
    both to match leaves satisfying both. Returns ``[]`` if nothing matches.
    """
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    norm_field = normalise_field(field) if field else None

    def walk(parent: dict[str, Any] | None, node: dict[str, Any]) -> None:
        if is_leaf(node) and parent is not None:
            if key is not None and node.get("key") != key:
                return
            if norm_field is not None and node.get("field") != norm_field:
                return
            out.append((parent, node))
            return
        if is_branch(node):
            for child in node.get("filters") or []:
                walk(node, child)

    walk(None, tree)
    return out


def remove_leaves(
    tree: dict[str, Any],
    *,
    key: str | None = None,
    field: str | None = None,
) -> int:
    """Remove all matching leaves in place. Returns count removed.

    Cleans up: if removing leaves leaves an AND-branch empty, the empty
    branch is also pruned so the tree doesn't accumulate dead nodes.
    """
    matches = find_leaves(tree, key=key, field=field)
    if not matches:
        return 0
    # Group by parent so we can mutate each parent's filters list once.
    parents: dict[int, dict[str, Any]] = {id(p): p for p, _ in matches}
    leaves_by_parent: dict[int, list[dict[str, Any]]] = {}
    for parent, leaf in matches:
        leaves_by_parent.setdefault(id(parent), []).append(leaf)
    for pid, parent in parents.items():
        to_drop = leaves_by_parent[pid]
        parent["filters"] = [c for c in parent.get("filters") or [] if c not in to_drop]

    # Prune any branches that became empty.
    def prune_empty(node: dict[str, Any]) -> bool:
        """Return True if ``node`` should be removed by its parent."""
        if not is_branch(node):
            return False
        node["filters"] = [
            c for c in node.get("filters") or [] if not prune_empty(c)
        ]
        # Don't prune the root — empty roots are valid ("no clauses").
        return False

    prune_empty(tree)
    return len(matches)


def add_leaf(
    tree: dict[str, Any],
    *,
    field: str,
    op: str,
    value: Any,
    branch_key: str | None = None,
) -> dict[str, Any]:
    """Add a leaf to the specified branch (defaults to the first AND-group at
    the root, creating one if needed). Returns the new leaf dict.
    """
    if branch_key is not None:
        branch = find_branch_by_key(tree, branch_key)
        if branch is None:
            raise ValueError(f"branch {branch_key!r} not found in filter tree")
    else:
        branch = ensure_default_and_branch(tree)

    leaf = {
        "key": str(uuid.uuid4()),
        "field": normalise_field(field),
        "operationType": normalise_operator(op),
        "value": serialise_value(value),
    }
    branch.setdefault("filters", []).append(leaf)
    return leaf


def update_leaf(
    leaf: dict[str, Any],
    *,
    new_field: str | None = None,
    new_op: str | None = None,
    new_value: Any = _SENTINEL_UNSET,
) -> dict[str, Any]:
    """Mutate a single leaf in place. Pass only the fields you want to change.

    ``new_value`` can legitimately be ``None`` / ``""`` / ``False``, so a
    sentinel object distinguishes "unset" from "explicitly set to None."
    """
    if new_field is not None:
        leaf["field"] = normalise_field(new_field)
    if new_op is not None:
        leaf["operationType"] = normalise_operator(new_op)
    if new_value is not _SENTINEL_UNSET:
        leaf["value"] = serialise_value(new_value)
    return leaf
