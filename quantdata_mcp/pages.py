"""User-managed page primitives.

This module owns:
- The page record schema and helpers to read/write pages from
  ``Config.pages``.
- Validation (page-name uniqueness, lookup, etc.).
- A registry mapping canonical tool names to their ``client.fetch_*``
  method + formatter, used by ``qd_run_page`` to fetch every tool on a
  page in one batch.

Pages are *parallel* workspaces — they sit alongside the canonical MCP
Agentic Page (where the 26 ``qd_get_*`` tools target). Each user-managed
page has its own QuantData ``page_id``, its own page filter, and its own
subset of tool instances. The ``URL`` field lets the user open the page
in their QuantData browser tab and watch it evolve in sync with whatever
the LLM is doing.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Callable

from quantdata_mcp.config import Config


# Format we expose for QuantData browser-side viewing.
QUANTDATA_PAGE_URL_TEMPLATE = "https://v3.quantdata.us/page/{page_id}"

# Page-name validation: keep names short, snake-case-friendly, easy to type.
PAGE_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def page_url(page_id: str) -> str:
    """Compose the user-facing browser URL for a QuantData page."""
    return QUANTDATA_PAGE_URL_TEMPLATE.format(page_id=page_id)


def is_valid_page_name(name: str) -> bool:
    """Page names are lowercase alphanumeric + underscore, max 64 chars."""
    return bool(PAGE_NAME_RE.match(name))


def find_page(config: Config, name: str) -> dict[str, Any] | None:
    """Look up a user-managed page by name. Returns ``None`` if missing."""
    for page in config.pages:
        if page.get("name") == name:
            return page
    return None


def page_index(config: Config, name: str) -> int:
    """Index of the page in ``config.pages``, or -1 if missing."""
    for i, page in enumerate(config.pages):
        if page.get("name") == name:
            return i
    return -1


def make_page_record(
    *,
    name: str,
    label: str,
    page_id: str,
    ticker: str | None = None,
    session_date: str | None = None,
    expiration_date: str | None = None,
) -> dict[str, Any]:
    """Build a page record for ``config.pages``.

    The record schema is intentionally a plain ``dict`` (not a dataclass)
    so callers can extend without a schema migration. Documented keys:

    - ``name`` — short identifier (snake_case, ``[a-z0-9_]+``)
    - ``label`` — human-readable display name
    - ``page_id`` — QuantData page UUID
    - ``url`` — pre-composed browser URL the user can open
    - ``filter`` — saved page filter as ``{ticker, session_date, expiration_date}``
    - ``tools`` — list of ``{canonical_name, tool_id, label}`` records
    - ``created_at`` — ISO timestamp
    """
    return {
        "name": name,
        "label": label,
        "page_id": page_id,
        "url": page_url(page_id),
        "filter": {
            "ticker": ticker,
            "session_date": session_date,
            "expiration_date": expiration_date,
        },
        "tools": [],
        "created_at": datetime.now(UTC).isoformat(),
    }


def make_tool_record(
    canonical_name: str, tool_id: str, label: str
) -> dict[str, Any]:
    """Build a tool record for a page's ``tools`` list."""
    return {
        "canonical_name": canonical_name,
        "tool_id": tool_id,
        "label": label,
    }


# ---------------------------------------------------------------------------
# Tool-fetcher registry — maps canonical tool name → (fetch_method,
# formatter, supports_ticker_kwarg). Used by ``qd_run_page`` to iterate
# the page's tools and build a concatenated output. Only includes tool
# types that make sense in a workspace-style "give me everything on this
# page" view — heavy / specialty tools (heat_map, interval_map, news,
# equity_prints) are intentionally excluded so qd_run_page output stays
# bounded.
# ---------------------------------------------------------------------------


# Each entry: (client_method_name, formatter_attr_on_server, formatter_kwargs)
# The formatter is referenced by name on the server module so we don't
# create an import cycle (server imports pages, not the other way).
#
# ``formatter_kwargs`` is a dict of extra args to pass to the formatter
# beyond ``data``. Two special keys are recognised by ``qd_run_page``:
#
#   ``_pass_ticker``: bool — if True, the page's active ticker is passed
#       as the formatter's ``ticker`` kwarg.
#
# All other keys in the dict are passed as-is. This lets us encode
# formatters that need extra positional args (like ``greek_type`` on
# ``_fmt_walls``) without an import cycle.
RUNNABLE_TOOLS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "exposure_by_strike":   ("fetch_strike_data",          "_fmt_walls",                    {"greek_type": "GAMMA", "_pass_ticker": True}),
    "exposure_by_expiration": ("fetch_exposure_by_expiration", "_fmt_exposure_by_expiration", {"greek_type": "GAMMA", "_pass_ticker": True}),
    "net_drift":            ("fetch_net_drift",            "_fmt_drift",                    {}),
    "net_flow":             ("fetch_net_flow",             "_fmt_net_flow",                 {}),
    "max_pain":             ("fetch_max_pain",             "_fmt_max_pain",                 {}),
    "iv_rank":              ("fetch_iv_rank",              "_fmt_iv_rank",                  {}),
    "contract_side_stats":  ("fetch_trade_side_stats",     "_fmt_trade_side_stats",         {}),
    "contract_statistics":  ("fetch_contract_statistics",  "_fmt_contract_stats",           {}),
    "oi_by_strike":         ("fetch_oi_by_strike",         "_fmt_oi_by_strike",             {"_pass_ticker": True}),
    "max_pain_over_time":   ("fetch_max_pain_over_time",   "_fmt_max_pain_over_time",       {"_pass_ticker": True}),
    "oi_change":            ("fetch_oi_change",            "_fmt_oi_change",                {}),
    "oi_by_expiration":     ("fetch_oi_by_expiration",     "_fmt_oi_by_expiration",         {"_pass_ticker": True}),
    "oi_over_time":         ("fetch_oi_over_time",         "_fmt_oi_over_time",             {"_pass_ticker": True}),
    "stock_price_time":     ("fetch_stock_price_time",     "_fmt_stock_price_time",         {}),
}


def runnable_canonical_names() -> tuple[str, ...]:
    """Tool names that ``qd_run_page`` knows how to fetch + format."""
    return tuple(RUNNABLE_TOOLS.keys())


def fetcher_for(
    canonical_name: str,
) -> tuple[str, str, dict[str, Any]] | None:
    """Look up the ``(client_method, formatter_attr, formatter_kwargs)``
    triple for a canonical tool name. Returns ``None`` if the tool isn't
    runnable on a page (e.g. order_flow — too filter-rich to make sense
    in a batch view; heavy tools like heat_map are excluded for
    output-size reasons)."""
    return RUNNABLE_TOOLS.get(canonical_name)
