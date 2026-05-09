# Changelog

All notable changes to `quantdata-mcp` are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semver](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-05-09

A "full-UI-parity" release. The MCP can now drive almost every primitive
the QuantData web UI exposes — filter groups, surgical clause edits,
nested OR/AND trees, intraday time scrubbing, and a sticky page filter
that lets context persist across calls. The 19 canonical tools also got
~30 new flat-kwarg filters and 8 new chart tools.

### Added — new MCP tools

**Tier-1 chart tools (8 new):**
- `qd_get_volatility_skew` — IV per strike per expiration
- `qd_get_term_structure` — Expected move + IV across expirations
- `qd_get_volatility_drift` — ARV vs IV vs spot over time
- `qd_get_max_pain_over_time` — Max-pain strike per expiration
- `qd_get_oi_change` — Day-over-day OI movers (`min_pct_change` headline filter)
- `qd_get_oi_by_expiration` — Call/put OI per expiration
- `qd_get_oi_over_time` — Call/put OI per session
- `qd_get_unconsolidated_flow` — Per-trade tape (40+ filters mirroring `qd_get_order_flow`)

**Filter groups — full lifecycle (15 tools):**
- Discovery: `qd_list_filter_groups`, `qd_search_public_filter_groups`, `qd_get_filter_group`, `qd_list_filter_fields`
- Create: `qd_save_filter_group` (flat AND), `qd_save_filter_group_advanced` (raw OR/AND tree), `qd_clone_public_filter_group`
- Edit: `qd_update_filter_group`, `qd_add_filter_clause`, `qd_remove_filter_clause`, `qd_update_filter_clause`, `qd_add_or_branch`
- Lifecycle: `qd_apply_filter_group`, `qd_detach_filter_group`, `qd_delete_filter_group`

Filter groups are server-side, persistent, named filter sets that attach
to tools via `filterGroupIds`. Once attached, they auto-apply on every
fetch alongside the tool's `metadata.filter`. Visible and editable in the
QuantData web UI — edit either surface and the other stays in sync.

**Time scrubber (2 tools):**
- `qd_set_tool_time(tool, time)` — scrub a tool to a specific moment of the trading day; persists until reset
- `qd_reset_to_live(tool)` — drop the scrubber, return to live data

Accepts `"9:30"`, `"9:30 AM"`, `"16:00"`, `"4 PM"`, or integer minutes from midnight.

### Added — flat-kwarg filter expansion on `qd_get_order_flow`

40+ new filter kwargs covering bool flags (`is_unusual`, `is_golden_sweep`, `is_opening_position`, `is_etf`, `is_index`, `is_volume_gt_oi`), numeric thresholds (`min_size`, `min_volume`, `min_open_interest`, `min_iv`, `min_bid_ask_spread`, `min_moneyness_pct`, `min_moneyness_dollars`, `max_dte`), greek floors (`min_delta`, `min_gamma`, `min_theta`, `min_vega`, `min_charm`, `min_vanna`), and multi-select lists (`sentiment_type`, `trade_type`, `exchange_type`, `sector`, `industry`, `trade_consolidation_type`).

Same filter set is mirrored on `qd_get_unconsolidated_flow` (minus `is_golden_sweep` and `trade_consolidation_type`, which the unconsolidated scaffold doesn't expose).

### Added — operational nicety

- `qd_get_net_drift` got a `confidence_visible` kwarg.
- New filter helpers in `_context.py`: `_gte`, `_lte`, `_contains`.
- New module `quantdata_mcp/filters.py` with `build_order_flow_filter` (shared between consolidated + unconsolidated wrappers).
- New module `quantdata_mcp/filter_groups.py` with tree-mutation helpers (`add_leaf`, `remove_leaves`, `update_leaf`, `find_leaves`, `find_branch_by_key`, etc.).
- New module `quantdata_mcp/filter_group_fields.py` with the field/operator/value catalog (`OPTION_TRADES_UNCONSOLIDATED`, `OPTION_TRADES_CONSOLIDATED`, `NEWS_ARTICLES`).

### Changed

- **Page filter is now sticky.** Calls no longer reset the page filter to `today/SPX` on exit. Explicit context (set via `qd_set_page_date` or any tool with explicit `ticker`/`date` args) persists across subsequent calls until you change it. Calls with omitted `ticker`/`date` now inherit from the active page cache. ([#16](https://github.com/zzulanas/quantdata-mcp/pull/16))
- **Auto-registration on server load.** Adding a new tool definition in a release no longer forces existing users to re-run `quantdata-mcp setup`. The server detects missing instances on first load and creates them. ([#9](https://github.com/zzulanas/quantdata-mcp/pull/9))
- Wrapper signatures: `ticker: str = "SPX"` → `ticker: str | None = None` across all 22 `qd_get_*` wrappers. None inherits the active page; falls through to `"SPX"` only when the cache is empty.
- `qd_get_order_flow` dropped `max_delta` — verified live that the QuantData API rejects `BETWEEN` operators (400). Each greek field accepts only one operator, so the filter set is GTE-only on greeks.

### Fixed

- The `is_unusual=true` filter now correctly returns zero rows on tickers where no trades are flagged unusual (was a real-data correctness verification, not a code change).

### Notes for upgraders

- **Just upgrade the package** (`uv tool install --reinstall quantdata-mcp` or `pip install --upgrade quantdata-mcp`). The server auto-registers the 8 new chart tools on first load — no setup re-run required.
- **Backwards-compatibility note:** the wrapper-default change (`ticker: str = "SPX"` → `None`) means calls without an explicit `ticker` now inherit context. On a fresh session this still resolves to SPX, so existing scripts behave identically. Scripts that called `qd_get_*` between explicit `qd_set_page_date(ticker="X")` calls will now respect the X context — likely the desired behavior.

## [0.2.0] — earlier 2026

First public PyPI release. See git history for details.

[0.3.0]: https://github.com/zzulanas/quantdata-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/zzulanas/quantdata-mcp/releases/tag/v0.2.0
