"""
QuantData MCP Server — Exposes all QuantData Agentic Page tools via MCP.

Provides real-time and historical options market data (GEX/DEX/CEX/VEX walls,
net drift, max pain, IV rank, trade side stats, contract stats, OI, net flow)
to any MCP client (e.g., Claude Code).

Usage:
    quantdata-mcp serve
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

from quantdata_mcp._context import (
    AUTH_ERROR_MESSAGE,
    _eq,
    _gte,
    _lte,
    format_error,
    page_filter_context,
    tool_context,
)
from quantdata_mcp.client import QuantDataAuthError, QuantDataClient
from quantdata_mcp.config import Config, config_exists, load_config, save_config
from quantdata_mcp.filter_groups import (
    GROUP_TYPES,
    add_leaf,
    build_filter_tree,
    find_leaves,
    remove_leaves,
    summarise_filter_tree,
    update_leaf,
)
from quantdata_mcp.filters import build_order_flow_filter
from quantdata_mcp.tools import (
    TOOL_DEFINITIONS,
    AggregationPeriod,
    ChartType,
    ContractTypeFilter,
    DataMode,
    GreekMode,
    MoneynessType,
    RepresentationMode,
    SentimentType,
    ToolSpec,
    TradeSideCodeType,
    build_tool_specs,
)

# stdout is reserved for MCP JSON-RPC, so all logging goes to stderr.
_log = logging.getLogger("quantdata_mcp.server")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# MCP Server + lazy-loaded config/client
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "quantdata",
    instructions=(
        "QuantData MCP server providing real-time and historical options market data. "
        "Supports any optionable ticker (SPX, SPY, QQQ, AAPL, TSLA, etc.) and any trading date.\n\n"
        "FILTERING RULES — read before querying:\n"
        "1. session_date MUST be a valid trading day (not weekends or market holidays like Good Friday). "
        "Before querying a historical date, verify it was a trading day.\n"
        "2. expiration_date MUST match a real options chain for that ticker. "
        "session_date and expiration_date are independent — they can differ.\n"
        "3. SPX, SPY, and QQQ have DAILY expirations (Mon–Fri), so the default "
        "expiration (= session_date, i.e. 0DTE) works for them.\n"
        "4. Equity options (AAPL, TSLA, etc.) only have weekly (Fridays) or monthly "
        "(3rd Friday) expirations — you MUST set expiration_date explicitly or you will get empty data. "
        "Not all Fridays have weeklies; monthlies are the safest bet.\n\n"
        "DEFAULTS: ticker=SPX, date=today, expiration_date=same as date (0DTE).\n"
        "All prices are in dollars. Exposure values are in millions. "
        "Drift values are cumulative premium flows. "
        "Use qd_get_market_snapshot for a comprehensive overview, "
        "or individual tools for specific data points."
    ),
)

_client: QuantDataClient | None = None
_config: Config | None = None
_specs: dict[str, ToolSpec] = {}


def _is_configured() -> bool:
    """Check if the server has been set up."""
    return config_exists() and bool(_config or _try_load_config())


def _try_load_config() -> Config | None:
    """Attempt to load config, return None if missing."""
    try:
        return load_config()
    except FileNotFoundError:
        return None


_load_lock = threading.Lock()


def _auto_register_missing_tools(client: QuantDataClient, config: Config) -> bool:
    """Create QuantData tool instances for any TOOL_DEFINITIONS missing from config.

    Closes the upgrade gap: when a release adds new tool definitions, existing
    users would otherwise have to re-run ``quantdata-mcp setup``. This helper
    creates the missing instances on first server load, persists each new ID to
    ``config.json`` immediately (so a partial failure leaves a consistent on-disk
    state), and best-effort refreshes the page layout so the new tools appear
    as tabs in the QuantData UI.

    Returns True if any new tools were created.
    """
    if not config.page_id:
        # Fresh install with no page — caller hasn't run `setup` yet, so we
        # can't auto-create. Surface that via the existing config-missing path.
        return False

    missing = [name for name in TOOL_DEFINITIONS if name not in config.tools]
    if not missing:
        return False

    _log.info(
        "Auto-registering %d new tool(s) on page %s...",
        len(missing),
        config.page_id[:8],
    )

    created_any = False
    for name in missing:
        defn = TOOL_DEFINITIONS[name]
        result = client.create_tool(page_id=config.page_id, tool_type=defn.tool_type.value)
        if not result:
            _log.warning("Failed to create tool '%s' — will retry on next start.", name)
            continue
        tool_id = result.get("response", {}).get("toolDTO", {}).get("id", "")
        if not tool_id:
            _log.warning("Tool '%s' created but no ID in response.", name)
            continue
        config.tools[name] = tool_id
        # Persist after every successful create so a mid-loop failure leaves a
        # consistent config (no orphan tool IDs in QuantData without a record).
        save_config(config)
        _log.info("  Registered '%s' (%s...)", name, tool_id[:8])
        created_any = True

    if created_any:
        # Best-effort: rebuild the page layout to surface the new tabs in the
        # QuantData web UI. Failures here are non-fatal — the data plane works
        # regardless of whether the tabs are visible.
        try:
            tab_tools = [
                (tid, TOOL_DEFINITIONS[name].label, TOOL_DEFINITIONS[name].tool_type.value)
                for name, tid in config.tools.items()
                if name in TOOL_DEFINITIONS
            ]
            client.update_page_layout(config.page_id, tab_tools)
        except Exception as e:  # pragma: no cover - best effort
            _log.warning("Page layout refresh failed (tools still usable): %s", e)

    return created_any


def _load() -> tuple[QuantDataClient, Config, dict[str, ToolSpec]]:
    """Lazy-init client, config, and tool specs.

    Auto-registers any new ``TOOL_DEFINITIONS`` missing from the on-disk config
    so users don't have to re-run ``setup`` after upgrading the package.
    """
    global _client, _config, _specs
    with _load_lock:
        if _client is None:
            if not config_exists():
                raise RuntimeError(
                    "Not configured yet. Please run "
                    "`quantdata-mcp setup --auth-token <TOKEN> --instance-id <INSTANCE_ID>` "
                    "before starting the server (see README for credential lookup)."
                )
            _config = load_config()
            _client = QuantDataClient(
                auth_token=_config.auth_token,
                instance_id=_config.instance_id,
                max_retries=2,
                retry_delay=0.5,
            )
            _auto_register_missing_tools(_client, _config)
            _specs = build_tool_specs(_config.tools)
    assert _config is not None
    return _client, _config, _specs


def _get_client() -> QuantDataClient:
    c, _, _ = _load()
    return c


def _get_page_id() -> str:
    _, cfg, _ = _load()
    return cfg.page_id


def _get_specs() -> dict[str, ToolSpec]:
    _, _, specs = _load()
    return specs


def _today() -> str:
    """Return today's date in YYYY-MM-DD (Eastern Time, since market data is keyed by ET)."""
    et = ZoneInfo("America/New_York")
    return datetime.now(et).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Formatting helpers — make output LLM-friendly
# ---------------------------------------------------------------------------

GREEK_LABELS = {
    "GAMMA": "GEX (Gamma Exposure)",
    "DELTA": "DEX (Delta Exposure)",
    "CHARM": "CEX (Charm Exposure)",
    "VANNA": "VEX (Vanna Exposure)",
}


def _fmt_walls(data: dict[str, Any] | None, greek_type: str, top_n: int = 10, ticker: str = "SPX") -> str:
    """Format exposure-by-strike data into a readable wall table."""
    if not data or "response" not in data:
        return f"No {GREEK_LABELS.get(greek_type, greek_type)} data available."

    resp = data["response"]
    price_cents = resp.get("stockPriceInCents", 0)
    price = price_cents / 100
    label = GREEK_LABELS.get(greek_type, greek_type)

    # The strike map is nested: expDate -> strike(cents) -> {CALL, PUT}
    exp_map = resp.get("expirationDateToStrikePriceInCentsToContractExposureMap", {})
    if not exp_map:
        return f"No {label} strike data available. {ticker} price: ${price:,.2f}"

    # Flatten all expirations
    walls: list[dict[str, Any]] = []
    for _exp, strike_map in exp_map.items():
        for strike_str, exposure in strike_map.items():
            strike = int(strike_str) / 100
            call_val = exposure.get("CALL", 0)
            put_val = exposure.get("PUT", 0)
            net = call_val + put_val
            walls.append(
                {
                    "strike": strike,
                    "call": call_val / 1_000_000,
                    "put": put_val / 1_000_000,
                    "net": net / 1_000_000,
                }
            )

    # Sort by absolute net exposure
    walls.sort(key=lambda w: abs(w["net"]), reverse=True)
    walls = walls[:top_n]

    lines = [f"{label} — {ticker} ${price:,.2f}", ""]
    lines.append(
        f"{'Strike':>10}  {'Net ($M)':>10}  {'Call ($M)':>10}  {'Put ($M)':>10}  {'Type':>6}"
    )
    lines.append("-" * 56)
    for w in walls:
        wtype = "CALL" if w["net"] > 0 else "PUT"
        lines.append(
            f"${w['strike']:>8,.0f}  {w['net']:>+10.2f}  {w['call']:>10.2f}  {w['put']:>10.2f}  {wtype:>6}"
        )

    return "\n".join(lines)


def _fmt_drift(data: dict[str, Any] | None, last_n: int = 10) -> str:
    """Format net drift data into readable entries."""
    if not data or "response" not in data:
        return "No net drift data available."

    resp = data["response"]
    drift_array = resp.get("netDrift", [])
    if not drift_array:
        return "No net drift entries."

    # Filter to regular market session only (9:30 AM ET).
    # The API returns data from overnight/pre-market which skews the cumulative.
    # Use ZoneInfo so DST transitions are handled correctly (EDT vs EST).
    et = ZoneInfo("America/New_York")
    today_et = datetime.now(et).date()
    market_open_ms = int(
        datetime(today_et.year, today_et.month, today_et.day, 9, 30, 0, tzinfo=et).timestamp() * 1000
    )
    session_entries = [t for t in drift_array if t[0] >= market_open_ms]
    if not session_entries:
        session_entries = drift_array  # fallback for historical dates

    entries = session_entries[-last_n:]

    # Compute running totals from session entries only
    total_call = sum(t[1] for t in session_entries) / 100
    total_put = sum(t[4] for t in session_entries) / 100
    total_net = total_call - total_put
    total_dir = "BULLISH" if total_net > 1000 else "BEARISH" if total_net < -1000 else "NEUTRAL"

    lines = [f"Net Drift — Last {len(entries)} entries (of {len(session_entries)} session)", ""]
    lines.append(f"{'Time (ET)':>12}  {'Call ($)':>12}  {'Put ($)':>12}  {'Net ($)':>12}  {'Price':>10}")
    lines.append("-" * 66)

    for entry in entries:
        ts = entry[0]
        call_prem = entry[1] / 100
        put_prem = entry[4] / 100
        net = call_prem - put_prem
        spx = entry[7] / 100 if len(entry) > 7 else 0

        try:
            t = datetime.fromtimestamp(ts / 1000, tz=et).strftime("%H:%M:%S")
        except (OSError, ValueError):
            t = str(ts)

        lines.append(
            f"{t:>12}  {call_prem:>+12,.0f}  {put_prem:>+12,.0f}  {net:>+12,.0f}  ${spx:>8,.2f}"
        )

    lines.append("")
    lines.append(
        f"Cumulative: Call ${total_call / 1_000_000:+.2f}M, Put ${total_put / 1_000_000:+.2f}M, "
        f"Net ${total_net / 1_000_000:+.2f}M => {total_dir}"
    )

    return "\n".join(lines)


def _fmt_max_pain(data: dict[str, Any] | None) -> str:
    """Format max pain data."""
    if not data or "response" not in data:
        return "No max pain data available."

    resp = data["response"]
    mp_cents = resp.get("strikePriceInCentsWithMaxPain", 0)
    price_cents = resp.get("stockPriceInCents", 0)
    mp = mp_cents / 100
    price = price_cents / 100

    if mp == 0:
        return "Max pain data unavailable."

    distance = price - mp
    dist_pct = (distance / price) * 100 if price else 0
    direction = "above" if distance > 0 else "below"

    lines = [
        f"Max Pain: ${mp:,.0f}",
        f"Price: ${price:,.2f}",
        f"Distance: {abs(distance):,.2f} pts ({abs(dist_pct):.2f}%) {direction} max pain",
    ]
    if abs(dist_pct) < 0.3:
        lines.append("Note: Price is very close to max pain — expect pinning pressure.")
    return "\n".join(lines)


def _fmt_iv_rank(data: dict[str, Any] | None, date: str | None = None) -> str:
    """Format IV rank data."""
    if not data or "response" not in data:
        return "No IV rank data available."

    resp = data["response"]
    session_data = resp.get("sessionDateToIVRankData", {})

    target_date = date or _today()
    iv_data = session_data.get(target_date, {})
    if not iv_data and session_data:
        target_date = max(session_data.keys())
        iv_data = session_data.get(target_date, {})

    if not iv_data:
        return "No IV rank data for this session."

    contract_data = iv_data.get("contractTypeToIVData", {})

    lines = [f"IV Rank — {target_date}", ""]

    for ct in ("CALL", "PUT"):
        cd = contract_data.get(ct, {})
        if not cd:
            continue
        last_iv = cd.get("lastIV", 0)
        min_iv = cd.get("windowMinIV", 0)
        max_iv = cd.get("windowMaxIV", 0)
        ivr = ((last_iv - min_iv) / (max_iv - min_iv) * 100) if max_iv > min_iv else 0
        level = "LOW" if ivr < 30 else "HIGH" if ivr > 70 else "NORMAL"
        lines.append(
            f"  {ct}: IVR {ivr:.1f}% ({level}) — IV {last_iv:.4f}, range [{min_iv:.4f}, {max_iv:.4f}]"
        )

    return "\n".join(lines)


def _fmt_trade_side_stats(data: dict[str, Any] | None) -> str:
    """Format trade side statistics."""
    if not data or "response" not in data:
        return "No trade side statistics available."

    resp = data["response"]
    stats_map = resp.get("contractTypeOptionsContractTradeSideStatisticsSumMap", {})
    if not stats_map:
        return "No trade side statistics data."

    lines = ["Contract Side Statistics (Trade Aggression)", ""]
    lines.append(
        f"{'Side':>6}  {'AA':>12}  {'A':>12}  {'M':>12}  {'B':>12}  {'BB':>12}  {'Aggr%':>6}"
    )
    lines.append("-" * 78)

    for ct in ("CALL", "PUT"):
        side = stats_map.get(ct, {})
        aa = side.get("AA", 0)
        a = side.get("A", 0)
        m = side.get("M", 0)
        b = side.get("B", 0)
        bb = side.get("BB", 0)
        total = aa + a + m + b + bb
        aggr = (aa + a) / total * 100 if total > 0 else 0

        def _fmt_val(v: float) -> str:
            if abs(v) >= 1_000_000:
                return f"${v / 1_000_000:.1f}M"
            elif abs(v) >= 1_000:
                return f"${v / 1_000:.0f}K"
            return f"${v:.0f}"

        lines.append(
            f"{ct:>6}  {_fmt_val(aa):>12}  {_fmt_val(a):>12}  {_fmt_val(m):>12}  "
            f"{_fmt_val(b):>12}  {_fmt_val(bb):>12}  {aggr:>5.1f}%"
        )

    lines.append("")
    lines.append("AA=Above Ask (aggressive buy), BB=Below Bid (aggressive sell)")
    lines.append("Aggr% = (AA + A) / Total — higher = more aggressive buying")

    return "\n".join(lines)


def _fmt_net_flow(data: dict[str, Any] | None, last_n: int = 10) -> str:
    """Format net flow data.

    Canonical shape (from /api/options/net-flow/{tool_id}):
        response.netFlow -> list of 4-item arrays
            [timestamp_ms, call_premium_cents, put_premium_cents, stock_price_cents]

    Note: Net Flow and Net Drift are different endpoints with different shapes.
    This formatter only handles Net Flow's 4-item rows. For Net Drift's 8-item
    rows, use _fmt_drift. PR #1 added an 8-item branch here that was dead code —
    the API never returns 8-item rows on the net-flow endpoint.
    """
    if not data or "response" not in data:
        return "No net flow data available."

    resp = data["response"]
    flow_array = resp.get("netFlow")
    if flow_array is None:
        return f"No net flow data — unexpected response shape. Available keys: {list(resp.keys())}"
    if not flow_array:
        return "No net flow entries."

    entries = flow_array[-last_n:]
    et = ZoneInfo("America/New_York")

    rendered: list[str] = []
    for entry in entries:
        # Defensive: skip rows that don't match the documented 4-item shape
        if not isinstance(entry, (list, tuple)) or len(entry) < 4:
            continue
        ts, call_cents, put_cents, _price_cents = entry[0], entry[1], entry[2], entry[3]
        call_flow = call_cents / 100
        put_flow = put_cents / 100
        net = call_flow - put_flow
        try:
            t = datetime.fromtimestamp(ts / 1000, tz=et).strftime("%H:%M:%S")
        except (OSError, ValueError):
            t = str(ts)
        rendered.append(
            f"  {t}  Call: ${call_flow:>+10,.0f}  Put: ${put_flow:>+10,.0f}  Net: ${net:>+10,.0f}"
        )

    lines = [f"Net Flow — Last {len(rendered)} entries (Time ET)", ""]
    lines.extend(rendered)

    return "\n".join(lines)


def _fmt_oi_by_strike(data: dict[str, Any] | None, near_strike: float | None = None, ticker: str = "SPX") -> str:
    """Format open interest by strike."""
    if not data or "response" not in data:
        return "No OI data available."

    resp = data["response"]
    # OI structure: flat map of strike(cents) -> {callOpenInterest, putOpenInterest}
    oi_map = resp.get("strikePricesInCentsToPutCallOpenInterest", {})
    if not oi_map:
        return "No OI strike data."

    all_strikes: list[dict[str, Any]] = []
    for strike_str, oi in oi_map.items():
        strike = int(strike_str) / 100
        if near_strike and abs(strike - near_strike) > 50:
            continue
        call_oi = oi.get("callOpenInterest", 0)
        put_oi = oi.get("putOpenInterest", 0)
        all_strikes.append(
            {"strike": strike, "call": call_oi, "put": put_oi, "total": call_oi + put_oi}
        )

    all_strikes.sort(key=lambda s: s["total"], reverse=True)
    top = all_strikes[:15]

    price_cents = resp.get("stockPriceInCents", 0)
    price = price_cents / 100

    lines = [f"Open Interest by Strike — {ticker} ${price:,.2f}", ""]
    lines.append(
        f"{'Strike':>10}  {'Call OI':>10}  {'Put OI':>10}  {'Total OI':>10}  {'P/C Ratio':>10}"
    )
    lines.append("-" * 58)

    for s in top:
        pc = s["put"] / s["call"] if s["call"] > 0 else 0
        lines.append(
            f"${s['strike']:>8,.0f}  {s['call']:>10,}  {s['put']:>10,}  {s['total']:>10,}  {pc:>10.2f}"
        )

    return "\n".join(lines)


def _fmt_contract_stats(data: dict[str, Any] | None) -> str:
    """Format contract statistics."""
    if not data or "response" not in data:
        return "No contract statistics available."

    resp = data["response"]

    lines = ["Contract Statistics", ""]

    # Try common response keys
    for key, label in [
        ("contractTypeToTotalPremium", "Total Premium"),
        ("contractTypeToTradeCount", "Trade Count"),
        ("contractTypeToVolume", "Volume"),
    ]:
        section = resp.get(key, {})
        if section:
            call_val = section.get("CALL", 0)
            put_val = section.get("PUT", 0)
            if "Premium" in label:
                lines.append(f"  {label}: Call ${call_val / 100:,.0f}, Put ${put_val / 100:,.0f}")
            else:
                lines.append(f"  {label}: Call {call_val:,}, Put {put_val:,}")

    if len(lines) == 2:
        # Fallback: dump what we got
        for k, v in resp.items():
            if k != "stockPriceInCents":
                lines.append(f"  {k}: {v}")

    return "\n".join(lines)


def _fmt_exposure_by_expiration(data: dict[str, Any] | None, greek_type: str, ticker: str = "SPX") -> str:
    """Format exposure-by-expiration data into a term structure table."""
    if not data or "response" not in data:
        return f"No {GREEK_LABELS.get(greek_type, greek_type)} expiration data available."

    resp = data["response"]
    price_cents = resp.get("stockPriceInCents", 0)
    price = price_cents / 100
    label = GREEK_LABELS.get(greek_type, greek_type)

    # The expiration map: expDate -> strike(cents) -> {CALL, PUT}
    exp_map = resp.get("expirationDateToStrikePriceInCentsToContractExposureMap", {})
    if not exp_map:
        return f"No {label} expiration data available. {ticker} price: ${price:,.2f}"

    # Aggregate by expiration date
    exp_totals: list[dict[str, Any]] = []
    for exp_date, strike_map in exp_map.items():
        total_call = 0.0
        total_put = 0.0
        for _strike_str, exposure in strike_map.items():
            total_call += exposure.get("CALL", 0)
            total_put += exposure.get("PUT", 0)
        net = total_call + total_put
        exp_totals.append({
            "expiration": exp_date,
            "call": total_call / 1_000_000,
            "put": total_put / 1_000_000,
            "net": net / 1_000_000,
        })

    # Sort by expiration date
    exp_totals.sort(key=lambda e: e["expiration"])

    lines = [f"{label} by Expiration (Term Structure) — {ticker} ${price:,.2f}", ""]
    lines.append(
        f"{'Expiration':>12}  {'Net ($M)':>10}  {'Call ($M)':>10}  {'Put ($M)':>10}"
    )
    lines.append("-" * 50)
    for e in exp_totals:
        lines.append(
            f"{e['expiration']:>12}  {e['net']:>+10.2f}  {e['call']:>10.2f}  {e['put']:>10.2f}"
        )

    return "\n".join(lines)


def _fmt_contract_price(data: dict[str, Any] | None) -> str:
    """Format contract price OHLCV data.

    Canonical shape (from /api/options/contract/price/time/{tool_id}):
        response.optionPriceOverTime -> list of arrays of length >= 6:
            [timestamp_ms, open_cents, high_cents, low_cents, close_cents, volume, ...]

    Trailing entries beyond index 5 are bid/ask/stock price snapshots in cents
    that we don't render here. All price fields are cents — divide by 100.
    """
    if not data or "response" not in data:
        return "No contract price data available."

    resp = data["response"]
    price_data = resp.get("optionPriceOverTime")

    if price_data is None:
        return f"No contract price data — unexpected response shape. Available keys: {list(resp.keys())}"
    if not price_data:
        return "No contract price entries."

    et = ZoneInfo("America/New_York")
    rendered: list[str] = []
    for entry in price_data:
        # Defensive: skip rows that don't match the documented OHLCV shape
        if not isinstance(entry, (list, tuple)) or len(entry) < 6:
            continue
        ts = entry[0]
        o = (entry[1] or 0) / 100
        h = (entry[2] or 0) / 100
        lo = (entry[3] or 0) / 100
        cl = (entry[4] or 0) / 100
        vol = entry[5] or 0
        try:
            t = datetime.fromtimestamp(ts / 1000, tz=et).strftime("%H:%M:%S")
        except (OSError, ValueError):
            t = str(ts)
        rendered.append(
            f"{t:>12}  ${o:>9.2f}  ${h:>9.2f}  ${lo:>9.2f}  ${cl:>9.2f}  {vol:>10,}"
        )

    lines = ["Contract Price (OHLCV)", ""]
    lines.append(
        f"{'Time (ET)':>12}  {'Open':>10}  {'High':>10}  {'Low':>10}  {'Close':>10}  {'Volume':>10}"
    )
    lines.append("-" * 72)
    lines.extend(rendered)

    return "\n".join(lines)


def _fmt_order_flow(data: dict[str, Any] | None, last_n: int = 20) -> str:
    """Format consolidated order flow data.

    Canonical shape (from /api/options/order-flow/consolidated/{tool_id}):
        response.trades -> list of trade dicts. Relevant fields:
            tradeTime          (int, ms since epoch)
            ticker             (str)
            strikePriceInCents (int, cents)
            contractType       ("CALL" | "PUT")
            tradeSideCode      ("AA" | "A" | "M" | "B" | "BB")
            premiumInCents     (int, cents)
            size               (int)
            sentimentType      ("BULLISH" | "BEARISH" | ...)

    All price fields use the *InCents naming convention — divide by 100.
    There is no array-style entry shape and no need for cents-vs-dollars
    heuristics; the API is consistent.
    """
    if not data or "response" not in data:
        return "No order flow data available."

    resp = data["response"]
    trades = resp.get("trades")

    if trades is None:
        return f"No order flow data — unexpected response shape. Available keys: {list(resp.keys())}"
    if not trades:
        return "No order flow entries."

    entries = trades[-last_n:] if len(trades) > last_n else trades
    et = ZoneInfo("America/New_York")

    rendered: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("tradeTime", 0)
        tkr = entry.get("ticker", "")
        strike = (entry.get("strikePriceInCents") or 0) / 100
        ct = entry.get("contractType", "")
        side = entry.get("tradeSideCode", "")
        prem = (entry.get("premiumInCents") or 0) / 100
        size = entry.get("size", 0)
        sent = entry.get("sentimentType", "")
        try:
            t = datetime.fromtimestamp(ts / 1000, tz=et).strftime("%H:%M:%S")
        except (OSError, ValueError, TypeError):
            t = str(ts)
        ct_short = "C" if ct == "CALL" else "P" if ct == "PUT" else str(ct)
        rendered.append(
            f"{t:>12}  {str(tkr):>6}  ${strike:>8,.0f}  {ct_short:>4}  {str(side):>4}  "
            f"${prem:>10,.0f}  {size:>8,}  {str(sent):>10}"
        )

    lines = [f"Order Flow — Last {len(rendered)} entries (of {len(trades)} total)", ""]
    lines.append(
        f"{'Time (ET)':>12}  {'Ticker':>6}  {'Strike':>10}  {'Type':>4}  {'Side':>4}  "
        f"{'Premium':>12}  {'Size':>8}  {'Sentiment':>10}"
    )
    lines.append("-" * 82)
    lines.extend(rendered)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatters — PR 2 (volatility surface, OI series, unconsolidated flow)
# ---------------------------------------------------------------------------


def _fmt_volatility_skew(
    data: dict[str, Any] | None,
    contract_type: str | None = None,
    near_n: int = 12,
    ticker: str = "SPX",
) -> str:
    """Format volatility skew — IV per strike around the spot, by expiration.

    Canonical shape (from /api/options/volatility-skew/{tool_id}):
        response.stockPriceInCents
        response.volatilitySkew -> {expDate: {strike_cents: {CALL: {iv}, PUT: {iv}}}}

    Args:
        data: Raw API response.
        contract_type: Optional 'CALL' or 'PUT' to filter the table to one side.
        near_n: Show the ``near_n`` strikes closest to the spot (per expiration).
        ticker: Ticker symbol (for the header).
    """
    if not data or "response" not in data:
        return "No volatility skew data available."

    resp = data["response"]
    price_cents = resp.get("stockPriceInCents", 0)
    price = price_cents / 100
    skew = resp.get("volatilitySkew")
    if not skew:
        return f"No volatility skew data — {ticker} ${price:,.2f}"

    lines = [f"Volatility Skew — {ticker} ${price:,.2f}", ""]
    for exp_date, strike_map in sorted(skew.items()):
        if not isinstance(strike_map, dict):
            continue
        rows: list[tuple[float, float | None, float | None]] = []
        for strike_str, ct_map in strike_map.items():
            try:
                strike = int(strike_str) / 100
            except (TypeError, ValueError):
                continue
            if not isinstance(ct_map, dict):
                continue
            call_iv = (ct_map.get("CALL") or {}).get("iv")
            put_iv = (ct_map.get("PUT") or {}).get("iv")
            rows.append((strike, call_iv, put_iv))
        if not rows:
            continue
        rows.sort(key=lambda r: abs(r[0] - price))
        rows = rows[:near_n]
        rows.sort(key=lambda r: r[0])

        lines.append(f"  Expiration: {exp_date}")
        lines.append(f"  {'Strike':>10}  {'Call IV':>10}  {'Put IV':>10}")
        lines.append("  " + "-" * 34)
        for strike, civ, piv in rows:
            if contract_type == "CALL" and civ is None:
                continue
            if contract_type == "PUT" and piv is None:
                continue
            civ_str = f"{civ:.4f}" if civ is not None else "—"
            piv_str = f"{piv:.4f}" if piv is not None else "—"
            lines.append(f"  ${strike:>8,.0f}  {civ_str:>10}  {piv_str:>10}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _fmt_term_structure(data: dict[str, Any] | None, ticker: str = "SPX") -> str:
    """Format term structure — expected move per expiration.

    Canonical shape (from /api/options/term-structure/{tool_id}):
        response.stockPriceInCents
        response.expectedMove -> {expDate: {CALL: cents, PUT: cents}}
        response.termStructure -> nested IV grid (deeper-dive; not rendered here)
    """
    if not data or "response" not in data:
        return "No term structure data available."

    resp = data["response"]
    price_cents = resp.get("stockPriceInCents", 0)
    price = price_cents / 100
    em = resp.get("expectedMove")
    if not em:
        return f"No term structure data — {ticker} ${price:,.2f}"

    rows: list[dict[str, Any]] = []
    today_str = _today()
    for exp_date, sides in em.items():
        if not isinstance(sides, dict):
            continue
        call_cents = sides.get("CALL", 0) or 0
        put_cents = sides.get("PUT", 0) or 0
        # rough DTE: count days between today and exp_date (string compare is fine for ISO dates)
        try:
            dte = (
                datetime.strptime(exp_date, "%Y-%m-%d").date()
                - datetime.strptime(today_str, "%Y-%m-%d").date()
            ).days
        except (ValueError, TypeError):
            dte = -1
        rows.append(
            {
                "expiration": exp_date,
                "dte": dte,
                "call": call_cents / 100,
                "put": put_cents / 100,
            }
        )

    rows.sort(key=lambda r: r["expiration"])

    lines = [f"Term Structure (Expected Move) — {ticker} ${price:,.2f}", ""]
    lines.append(
        f"{'Expiration':>12}  {'DTE':>5}  {'Call EM ($)':>12}  {'Put EM ($)':>12}"
    )
    lines.append("-" * 47)
    for r in rows:
        dte_str = f"{r['dte']:>5}" if r["dte"] >= 0 else "  n/a"
        lines.append(
            f"{r['expiration']:>12}  {dte_str}  {r['call']:>12,.2f}  {r['put']:>12,.2f}"
        )
    lines.append("")
    lines.append(
        "Note: Per-strike IV grid available in `response.termStructure` for deeper analysis."
    )
    return "\n".join(lines)


def _fmt_volatility_drift(data: dict[str, Any] | None, last_n: int = 10) -> str:
    """Format volatility drift — ARV / IV / spot over time.

    Canonical shape (from /api/options/volatility-drift/{tool_id}):
        response.volatilityDrift -> {timestamp_ms_str: {arv, iv?, stockPriceInCents}}

    The earliest entries may lack ``iv`` (warm-up period); we render those as '—'.
    """
    if not data or "response" not in data:
        return "No volatility drift data available."

    resp = data["response"]
    vd = resp.get("volatilityDrift")
    if not vd:
        return "No volatility drift entries."

    # Keys are stringified ms timestamps; sort numerically.
    try:
        items = sorted(vd.items(), key=lambda kv: int(kv[0]))
    except (TypeError, ValueError):
        items = list(vd.items())

    tail = items[-last_n:]
    et = ZoneInfo("America/New_York")

    lines = [f"Volatility Drift — Last {len(tail)} entries (of {len(items)})", ""]
    lines.append(
        f"{'Time (ET)':>12}  {'ARV':>10}  {'IV':>10}  {'Spot':>10}"
    )
    lines.append("-" * 48)
    for ts_str, payload in tail:
        if not isinstance(payload, dict):
            continue
        try:
            ts = int(ts_str)
            t = datetime.fromtimestamp(ts / 1000, tz=et).strftime("%H:%M:%S")
        except (TypeError, ValueError, OSError):
            t = str(ts_str)
        arv = payload.get("arv")
        iv = payload.get("iv")
        spot_cents = payload.get("stockPriceInCents", 0) or 0
        spot = spot_cents / 100
        arv_str = f"{arv:.4f}" if arv is not None else "—"
        iv_str = f"{iv:.4f}" if iv is not None else "—"
        lines.append(f"{t:>12}  {arv_str:>10}  {iv_str:>10}  ${spot:>8,.2f}")

    return "\n".join(lines)


def _fmt_max_pain_over_time(data: dict[str, Any] | None, ticker: str = "SPX") -> str:
    """Format Max Pain over Time — max-pain strike per expiration.

    Canonical shape (from /api/options/max-pain/time/{tool_id}):
        response.stockPriceInCents
        response.maxPainStrikePricesInCents -> {expDate: strike_cents}
    """
    if not data or "response" not in data:
        return "No max pain over time data available."

    resp = data["response"]
    price = (resp.get("stockPriceInCents", 0) or 0) / 100
    mp_map = resp.get("maxPainStrikePricesInCents")
    if not mp_map:
        return f"No max pain over time data — {ticker} ${price:,.2f}"

    rows = []
    for exp_date, strike_cents in mp_map.items():
        rows.append((exp_date, (strike_cents or 0) / 100))
    rows.sort(key=lambda r: r[0])

    lines = [f"Max Pain by Expiration — {ticker} ${price:,.2f}", ""]
    lines.append(f"{'Expiration':>12}  {'Max Pain':>10}  {'Distance':>12}")
    lines.append("-" * 40)
    for exp, strike in rows:
        dist = price - strike
        sign = "+" if dist > 0 else ""
        lines.append(f"{exp:>12}  ${strike:>8,.0f}  {sign}{dist:>+10,.2f}")
    return "\n".join(lines)


def _fmt_oi_change(
    data: dict[str, Any] | None, top_n: int = 15
) -> str:
    """Format Open Interest Change — top movers in OI vs prior session.

    Canonical shape (from /api/options/open-interest/change/{tool_id}):
        response -> list of dicts with strike, contractType, expirationDate,
        previousOpenInterest, currentOpenInterest, changeInOpenInterest,
        percentChangeInOpenInterest, sessionDate, ticker
    """
    if not data or "response" not in data:
        return "No OI change data available."

    rows = data["response"]
    if not isinstance(rows, list):
        return f"No OI change data — unexpected response shape ({type(rows).__name__})."
    if not rows:
        return "No OI change entries."

    # The API already sorts by tableMetadata.sort, but be defensive: sort by absolute change DESC.
    sortable = [r for r in rows if isinstance(r, dict)]
    sortable.sort(
        key=lambda r: abs(r.get("changeInOpenInterest") or 0), reverse=True
    )
    top = sortable[:top_n]

    lines = [f"Open Interest Change — Top {len(top)} (of {len(sortable)})", ""]
    lines.append(
        f"{'Ticker':>6}  {'Strike':>10}  {'Type':>4}  {'Exp':>11}  "
        f"{'Prev OI':>10}  {'Curr OI':>10}  {'Change':>10}  {'%Chg':>10}"
    )
    lines.append("-" * 86)
    for r in top:
        tkr = r.get("ticker", "")
        strike = (r.get("strikePriceInCents") or 0) / 100
        ct = r.get("contractType", "")
        ct_short = "C" if ct == "CALL" else "P" if ct == "PUT" else str(ct)
        exp = r.get("expirationDate", "")
        prev = r.get("previousOpenInterest", 0) or 0
        curr = r.get("currentOpenInterest", 0) or 0
        chg = r.get("changeInOpenInterest", 0) or 0
        pct = r.get("percentChangeInOpenInterest", 0) or 0
        lines.append(
            f"{str(tkr):>6}  ${strike:>8,.0f}  {ct_short:>4}  {exp:>11}  "
            f"{prev:>10,}  {curr:>10,}  {chg:>+10,}  {pct:>9.1f}%"
        )
    return "\n".join(lines)


def _fmt_oi_by_expiration(
    data: dict[str, Any] | None, ticker: str = "SPX"
) -> str:
    """Format Open Interest by Expiration — call/put OI summed per expiration.

    Canonical shape (from /api/options/open-interest/expiration/{tool_id}):
        response.expirationDatesToPutCallOpenInterest ->
            {expDate: {callOpenInterest, putOpenInterest}}
    """
    if not data or "response" not in data:
        return "No OI by expiration data available."

    resp = data["response"]
    m = resp.get("expirationDatesToPutCallOpenInterest")
    if not m:
        return "No OI by expiration entries."

    rows = []
    for exp_date, oi in m.items():
        if not isinstance(oi, dict):
            continue
        c = oi.get("callOpenInterest", 0) or 0
        p = oi.get("putOpenInterest", 0) or 0
        rows.append((exp_date, c, p))
    rows.sort(key=lambda r: r[0])

    lines = [f"Open Interest by Expiration — {ticker}", ""]
    lines.append(
        f"{'Expiration':>12}  {'Call OI':>12}  {'Put OI':>12}  {'Total':>12}  {'P/C':>6}"
    )
    lines.append("-" * 62)
    for exp, c, p in rows:
        total = c + p
        pc = p / c if c > 0 else 0
        lines.append(
            f"{exp:>12}  {c:>12,}  {p:>12,}  {total:>12,}  {pc:>6.2f}"
        )
    return "\n".join(lines)


def _fmt_oi_over_time(
    data: dict[str, Any] | None, last_n: int = 20, ticker: str = "SPX"
) -> str:
    """Format Open Interest Over Time — call/put OI per session date.

    Canonical shape (from /api/options/open-interest/time/{tool_id}):
        response.sessionDatesToPutCallOpenInterest ->
            {sessionDate: {callOpenInterest, putOpenInterest}}
    """
    if not data or "response" not in data:
        return "No OI over time data available."

    resp = data["response"]
    m = resp.get("sessionDatesToPutCallOpenInterest")
    if not m:
        return "No OI over time entries."

    rows = []
    for session_date, oi in m.items():
        if not isinstance(oi, dict):
            continue
        c = oi.get("callOpenInterest", 0) or 0
        p = oi.get("putOpenInterest", 0) or 0
        rows.append((session_date, c, p))
    rows.sort(key=lambda r: r[0])
    tail = rows[-last_n:]

    lines = [
        f"Open Interest / Time — {ticker} (last {len(tail)} of {len(rows)} sessions)",
        "",
    ]
    lines.append(
        f"{'Session':>12}  {'Call OI':>12}  {'Put OI':>12}  {'Total':>12}  {'P/C':>6}"
    )
    lines.append("-" * 62)
    for session, c, p in tail:
        total = c + p
        pc = p / c if c > 0 else 0
        lines.append(
            f"{session:>12}  {c:>12,}  {p:>12,}  {total:>12,}  {pc:>6.2f}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def qd_get_exposure_by_strike(
    greek_type: GreekMode = GreekMode.GAMMA,
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    time_minutes: int | None = None,
    representation_mode: RepresentationMode = RepresentationMode.PER_ONE_PERCENT_MOVE,
    is_net: bool = True,
) -> str:
    """Get GEX/DEX/CEX/VEX wall data — top exposure levels by strike price.

    Shows where the biggest gamma/delta/charm/vanna walls are, indicating
    key support/resistance levels.

    Args:
        greek_type: GAMMA (GEX), DELTA (DEX), CHARM (CEX), or VANNA (VEX)
        ticker: Ticker symbol (default: SPX). Any optionable ticker works (SPY, QQQ, AAPL, etc.)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE; set differently for non-0DTE)
        time_minutes: Minutes from midnight for historical playback (570=9:30AM, 960=4PM)
        representation_mode: PER_1PCT (per 1% move, default), PER_1USD (per $1 move), or RAW
        is_net: True for net (call+put combined), False for gross (separate). Default: True.
    """
    try:
        with tool_context(
            "exposure_by_strike",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates={
                "greekModeType": greek_type.value,
                "representationModeType": representation_mode.value,
                "isNet": is_net,
            },
            time_minutes=time_minutes,
        ) as ctx:
            data = ctx.client.fetch_strike_data(ctx.tool_spec.tool_id)
        return _fmt_walls(data, greek_type.value, ticker=ticker)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"{greek_type.value} walls", e)


@mcp.tool()
def qd_get_net_drift(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessType] | None = None,
    strikes: list[float] | None = None,
    aggregation: AggregationPeriod = AggregationPeriod.ONE_MINUTE,
    confidence_visible: bool | None = None,
    last_n: int = 10,
) -> str:
    """Get net drift data — cumulative call vs put premium flow.

    Net drift shows whether money is flowing into calls (bullish) or puts (bearish).
    Positive net = more call premium, negative = more put premium.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
        moneyness: Filter by moneyness — OTM, ITM, ATM. Pass a list to combine. Default: all.
        strikes: Filter to specific strike prices in dollars (e.g. [5600.0, 5700.0]). Default: all.
        aggregation: Time aggregation period — ONE_MIN (default), FIVE_MIN, TEN_MIN, FIFTEEN_MIN, THIRTY_MIN, ONE_HOUR.
        confidence_visible: Toggle the confidence band overlay on the drift chart (server-side metadata only).
        last_n: Number of recent entries to show (default: 10)
    """
    metadata_updates: dict[str, Any] = {"aggregationPeriodType": aggregation.value}
    if confidence_visible is not None:
        metadata_updates["confidenceVisible"] = confidence_visible
    try:
        with tool_context(
            "net_drift",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates=metadata_updates,
            filter_updates={
                "moneynessMoneyType": _eq([m.value for m in moneyness]) if moneyness else None,
                "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
            },
        ) as ctx:
            data = ctx.client.fetch_net_drift(ctx.tool_spec.tool_id)
        return _fmt_drift(data, last_n)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("net drift", e)


@mcp.tool()
def qd_get_trade_side_stats(
    data_mode: DataMode = DataMode.PREMIUM,
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessType] | None = None,
    strikes: list[float] | None = None,
) -> str:
    """Get contract side statistics — trade aggression breakdown.

    Shows how aggressively traders are buying/selling calls and puts.
    AA (Above Ask) = aggressive buying, BB (Below Bid) = aggressive selling.

    Args:
        data_mode: PREMIUM (dollar value), TRADE_COUNT, or VOLUME
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
        moneyness: Filter by moneyness — OTM, ITM, ATM. Pass a list to combine. Default: all.
        strikes: Filter to specific strike prices in dollars (e.g. [5600.0]). Default: all.
    """
    try:
        with tool_context(
            "contract_side_stats",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates={"dataModeType": data_mode.value},
            filter_updates={
                "moneynessMoneyType": _eq([m.value for m in moneyness]) if moneyness else None,
                "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
            },
        ) as ctx:
            data = ctx.client.fetch_trade_side_stats(ctx.tool_spec.tool_id)
        return _fmt_trade_side_stats(data)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("trade side stats", e)


@mcp.tool()
def qd_get_max_pain(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
) -> str:
    """Get max pain strike — the price where option holders lose the most.

    Price tends to gravitate toward max pain near expiration.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
    """
    try:
        with tool_context(
            "max_pain",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            needs_tool=False,
        ) as ctx:
            data = ctx.client.fetch_max_pain(ctx.tool_spec.tool_id)
        return _fmt_max_pain(data)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("max pain", e)


@mcp.tool()
def qd_get_iv_rank(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    lookback_period: int = 365,
    maturity: int = 30,
    contract_type: list[ContractTypeFilter] | None = None,
) -> str:
    """Get IV rank — where current implied volatility sits in its historical range.

    Low IVR (<30%) = options are cheap, good for buying.
    High IVR (>70%) = options are expensive, need larger moves for profit.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
        lookback_period: Number of days to look back for IVR calculation (default: 365)
        maturity: Target DTE for IV curve (default: 30)
        contract_type: Filter to CALL, PUT, or both. Default: both (None).
    """
    try:
        with tool_context(
            "iv_rank",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates={
                "lookBackPeriod": lookback_period,
                "maturity": maturity,
            },
            filter_updates={
                "contractType": _eq([ct.value for ct in contract_type])
                if contract_type
                else None,
            },
        ) as ctx:
            data = ctx.client.fetch_iv_rank(ctx.tool_spec.tool_id)
        return _fmt_iv_rank(data, date)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("IV rank", e)


@mcp.tool()
def qd_get_net_flow(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessType] | None = None,
    trade_side: list[TradeSideCodeType] | None = None,
    strikes: list[float] | None = None,
    aggregation: AggregationPeriod = AggregationPeriod.ONE_MINUTE,
    data_mode: DataMode = DataMode.PREMIUM,
    last_n: int = 10,
) -> str:
    """Get net flow data — call/put premium flow over time.

    Similar to net drift but shows raw premium flow rather than cumulative.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
        moneyness: Filter by moneyness — OTM, ITM, ATM. Pass a list to combine. Default: all.
        trade_side: Filter by trade side — AA (Above Ask), A (At Ask), M (Mid), B (At Bid), BB (Below Bid). Default: all.
        strikes: Filter to specific strike prices in dollars (e.g. [5600.0]). Default: all.
        aggregation: Time aggregation period — ONE_MIN (default), FIVE_MIN, TEN_MIN, FIFTEEN_MIN, THIRTY_MIN, ONE_HOUR.
        data_mode: PREMIUM (dollar value, default) or VOLUME.
        last_n: Number of recent entries to show (default: 10)
    """
    try:
        with tool_context(
            "net_flow",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates={
                "aggregationPeriodType": aggregation.value,
                "dataModeType": data_mode.value,
            },
            filter_updates={
                "moneynessMoneyType": _eq([m.value for m in moneyness]) if moneyness else None,
                "tradeSideCodeType": _eq([t.value for t in trade_side]) if trade_side else None,
                "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
            },
        ) as ctx:
            data = ctx.client.fetch_net_flow(ctx.tool_spec.tool_id)
        return _fmt_net_flow(data, last_n)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("net flow", e)


@mcp.tool()
def qd_get_oi_by_strike(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    near_strike: float | None = None,
) -> str:
    """Get open interest by strike — put/call OI distribution.

    High OI strikes act as magnets/barriers. Put/Call ratio shows market positioning.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
        near_strike: Filter to strikes within $50 of this price
    """
    try:
        with tool_context(
            "oi_by_strike",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            needs_tool=False,
        ) as ctx:
            data = ctx.client.fetch_oi_by_strike(ctx.tool_spec.tool_id)
        return _fmt_oi_by_strike(data, near_strike, ticker=ticker)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("OI by strike", e)


@mcp.tool()
def qd_get_contract_statistics(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessType] | None = None,
    trade_side: list[TradeSideCodeType] | None = None,
    strikes: list[float] | None = None,
) -> str:
    """Get contract statistics — total premium, trade count, volume by call/put.

    Overview of the day's options activity levels.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
        moneyness: Filter by moneyness — OTM, ITM, ATM. Pass a list to combine. Default: all.
        trade_side: Filter by trade side — AA (Above Ask), A (At Ask), M (Mid), B (At Bid), BB (Below Bid). Default: all.
        strikes: Filter to specific strike prices in dollars (e.g. [5600.0]). Default: all.
    """
    try:
        with tool_context(
            "contract_statistics",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates={
                "moneynessMoneyType": _eq([m.value for m in moneyness]) if moneyness else None,
                "tradeSideCodeType": _eq([t.value for t in trade_side]) if trade_side else None,
                "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
            },
        ) as ctx:
            data = ctx.client.fetch_contract_statistics(ctx.tool_spec.tool_id)
        return _fmt_contract_stats(data)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("contract statistics", e)


@mcp.tool()
def qd_get_exposure_by_expiration(
    greek_type: GreekMode = GreekMode.GAMMA,
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    representation_mode: RepresentationMode = RepresentationMode.PER_ONE_PERCENT_MOVE,
    is_net: bool = True,
    strikes: list[float] | None = None,
) -> str:
    """Get greek exposure by expiration date — term structure view.

    Shows how gamma/delta/charm/vanna exposure is distributed across
    expiration dates, revealing where the most hedging activity is concentrated.

    Args:
        greek_type: GAMMA (GEX), DELTA (DEX), CHARM (CEX), or VANNA (VEX)
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE)
        representation_mode: PER_1PCT (per 1% move, default), PER_1USD (per $1 move), or RAW
        is_net: True for net (call+put combined), False for gross (separate). Default: True.
        strikes: Filter to specific strike prices in dollars (e.g. [5600.0, 5700.0]). Default: all.
    """
    try:
        with tool_context(
            "exposure_by_expiration",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates={
                "greekModeType": greek_type.value,
                "representationModeType": representation_mode.value,
                "isNet": is_net,
            },
            filter_updates={
                "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
            },
        ) as ctx:
            data = ctx.client.fetch_exposure_by_expiration(ctx.tool_spec.tool_id)
        return _fmt_exposure_by_expiration(data, greek_type.value, ticker=ticker)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("exposure by expiration", e)


@mcp.tool()
def qd_get_contract_price(
    strike: float,
    contract_type: ContractTypeFilter = ContractTypeFilter.CALL,
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    aggregation: AggregationPeriod = AggregationPeriod.ONE_MINUTE,
) -> str:
    """Get OHLCV price data for a specific options contract.

    Shows intraday price action for a single call or put contract at a given strike.

    Args:
        strike: Strike price in dollars (e.g. 5600.0)
        contract_type: CALL or PUT (default: CALL)
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE)
        aggregation: Time aggregation period — ONE_MIN (default), FIVE_MIN, TEN_MIN, FIFTEEN_MIN, THIRTY_MIN, ONE_HOUR.
    """
    session_date = date or _today()
    exp = expiration_date or session_date
    try:
        with tool_context(
            "contract_price_time",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates={"aggregationPeriodType": aggregation.value},
            filter_updates={
                "contractType": _eq(contract_type.value),
                "strikePriceInCents": _eq(int(strike * 100)),
                "expirationDate": _eq(exp),
                "ticker": _eq(ticker),
            },
        ) as ctx:
            data = ctx.client.fetch_contract_price_time(ctx.tool_spec.tool_id)
        return _fmt_contract_price(data)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("contract price", e)


@mcp.tool()
def qd_get_order_flow(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    # ----- Existing filters (kept verbatim for backwards compatibility) -----
    contract_type: ContractTypeFilter | None = None,
    moneyness: list[MoneynessType] | None = None,
    trade_side: list[TradeSideCodeType] | None = None,
    min_premium: float | None = None,
    strikes: list[float] | None = None,
    # ----- Bool flag filters (snake_case, "is_" prefix preserved) -----
    is_unusual: bool | None = None,
    is_golden_sweep: bool | None = None,
    is_opening_position: bool | None = None,
    is_etf: bool | None = None,
    is_index: bool | None = None,
    is_volume_gt_oi: bool | None = None,
    # ----- Threshold filters (GTE / LTE) -----
    min_size: int | None = None,
    min_volume: int | None = None,
    min_open_interest: int | None = None,
    min_iv: float | None = None,
    min_bid_ask_spread: float | None = None,
    min_moneyness_pct: float | None = None,
    min_moneyness_dollars: float | None = None,
    max_dte: float | None = None,
    # ----- Greek thresholds (GTE only — the API allows one operator per field) -----
    min_delta: float | None = None,
    min_gamma: float | None = None,
    min_theta: float | None = None,
    min_vega: float | None = None,
    min_charm: float | None = None,
    min_vanna: float | None = None,
    # ----- Multi-select list filters -----
    sentiment_type: list[SentimentType] | None = None,
    trade_type: list[str] | None = None,
    exchange_type: list[str] | None = None,
    sector: list[str] | None = None,
    industry: list[str] | None = None,
    trade_consolidation_type: list[str] | None = None,
    # ----- Output control -----
    last_n: int = 20,
) -> str:
    """Get consolidated order flow — individual large trades with full detail.

    The most filter-rich tool. Shows individual option trades with strike, type,
    side (aggression), premium, size, sentiment, and full greeks. Supports
    40+ filters covering bool flags, numeric thresholds, greek floors/ceilings,
    and multi-select lists.

    For open-ended list filters (``trade_type``, ``exchange_type``, ``sector``,
    ``industry``, ``trade_consolidation_type``) valid values come from
    QuantData's data — common examples:

    * ``trade_type``: ``AUTO``, ``M2S_FLR``, ``MULTI_AUTO_COB``
    * ``sentiment_type``: ``BULLISH``, ``BEARISH``, ``NEUTRAL``

    Example: bullish sweeps in tech, $10K+ premium, opening positions::

        qd_get_order_flow(
            ticker="SPY", is_unusual=True, is_opening_position=True,
            sentiment_type=[SentimentType.BULLISH], min_premium=10000,
            trade_type=["AUTO"], sector=["TECHNOLOGY"], last_n=20,
        )

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE)
        contract_type: Filter to CALL or PUT only. Default: both.
        moneyness: Filter by moneyness — OTM, ITM, ATM. Pass a list to combine.
        trade_side: Filter by trade side — AA (Above Ask), A (At Ask), M (Mid), B (At Bid), BB (Below Bid).
        min_premium: Minimum premium in dollars (e.g. 10000 for $10K+).
        strikes: Filter to specific strike prices in dollars (e.g. [5600.0]).
        is_unusual: Only trades flagged as unusual activity.
        is_golden_sweep: Only "golden sweep" trades (large multi-exchange sweeps).
        is_opening_position: Only opening positions (volume > prior open interest).
        is_etf: Only ETF underliers.
        is_index: Only index underliers (SPX, NDX, etc.).
        is_volume_gt_oi: Only trades where contract volume exceeds open interest.
        min_size: Minimum trade size (contracts).
        min_volume: Minimum daily contract volume.
        min_open_interest: Minimum open interest.
        min_iv: Minimum implied volatility (decimal, e.g. 0.25 for 25%).
        min_bid_ask_spread: Minimum bid-ask spread in dollars.
        min_moneyness_pct: Minimum moneyness as a percentage (e.g. 5.0 for >=5% OTM/ITM).
        min_moneyness_dollars: Minimum moneyness in dollars (converted to cents internally).
        max_dte: Maximum days-to-expiration (fractional). Use 0 for 0DTE only.
        min_delta: Minimum delta (e.g. 0.30 to skip far-OTM noise). The API
            accepts only one operator per greek field, so a delta range is
            not directly supported — use ``min_delta`` to floor and pair with
            ``moneyness`` if you need an upper bound.
        min_gamma: Minimum gamma.
        min_theta: Minimum theta (typically negative — pass e.g. -0.05 to skip the most decayed).
        min_vega: Minimum vega.
        min_charm: Minimum charm.
        min_vanna: Minimum vanna.
        sentiment_type: Filter by sentiment classification (BULLISH/BEARISH/NEUTRAL list).
        trade_type: Free-form trade type codes (e.g. ["AUTO", "M2S_FLR"]).
        exchange_type: Free-form exchange codes.
        sector: Free-form sector codes (e.g. ["TECHNOLOGY", "FINANCE"]).
        industry: Free-form industry codes.
        trade_consolidation_type: Free-form consolidation type codes.
        last_n: Number of recent entries to show (default: 20).
    """
    filter_updates = build_order_flow_filter(
        contract_type=contract_type,
        moneyness=moneyness,
        trade_side=trade_side,
        min_premium=min_premium,
        strikes=strikes,
        is_unusual=is_unusual,
        is_golden_sweep=is_golden_sweep,
        is_opening_position=is_opening_position,
        is_etf=is_etf,
        is_index=is_index,
        is_volume_gt_oi=is_volume_gt_oi,
        min_size=min_size,
        min_volume=min_volume,
        min_open_interest=min_open_interest,
        min_iv=min_iv,
        min_bid_ask_spread=min_bid_ask_spread,
        min_moneyness_pct=min_moneyness_pct,
        min_moneyness_dollars=min_moneyness_dollars,
        max_dte=max_dte,
        min_delta=min_delta,
        min_gamma=min_gamma,
        min_theta=min_theta,
        min_vega=min_vega,
        min_charm=min_charm,
        min_vanna=min_vanna,
        sentiment_type=sentiment_type,
        trade_type=trade_type,
        exchange_type=exchange_type,
        sector=sector,
        industry=industry,
        trade_consolidation_type=trade_consolidation_type,
    )
    try:
        with tool_context(
            "order_flow",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates=filter_updates,
        ) as ctx:
            data = ctx.client.fetch_consolidated_flow(ctx.tool_spec.tool_id)
        return _fmt_order_flow(data, last_n)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("order flow", e)


@mcp.tool()
def qd_get_market_snapshot(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
) -> str:
    """Get a comprehensive market snapshot — GEX walls, DEX walls, net drift, max pain, trade side stats, and contract stats.

    Best tool for a quick overview of the current market state. Calls multiple
    data sources and formats them into a single readable report.

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
    """
    try:
        sections: list[str] = []

        # All six section fetches share ONE page-filter scope -- the page
        # filter is applied once on enter and restored once on exit, instead
        # of once per inner tool_context (which would be 6 applies + 6
        # restores for a non-default ticker).
        with page_filter_context(
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
        ):
            # GEX walls (snapshot+restore the exposure tool's metadata for us).
            with tool_context(
                "exposure_by_strike",
                ticker=ticker,
                date=date,
                expiration_date=expiration_date,
                metadata_updates={"greekModeType": "GAMMA"},
                skip_page_filter=True,
            ) as ctx:
                gex_data = ctx.client.fetch_strike_data(ctx.tool_spec.tool_id)
                sections.append(_fmt_walls(gex_data, "GAMMA", ticker=ticker))

            # DEX walls -- separate context so the previous one restores cleanly.
            with tool_context(
                "exposure_by_strike",
                ticker=ticker,
                date=date,
                expiration_date=expiration_date,
                metadata_updates={"greekModeType": "DELTA"},
                skip_page_filter=True,
            ) as ctx:
                dex_data = ctx.client.fetch_strike_data(ctx.tool_spec.tool_id)
                sections.append(_fmt_walls(dex_data, "DELTA", ticker=ticker))

            # Remaining sections only need the page filter -- no per-tool
            # metadata mutations -- so we use needs_tool=False to skip the
            # GET/PUT pair.
            with tool_context(
                "net_drift",
                ticker=ticker,
                date=date,
                expiration_date=expiration_date,
                needs_tool=False,
                skip_page_filter=True,
            ) as ctx:
                drift_data = ctx.client.fetch_net_drift(ctx.tool_spec.tool_id)
                sections.append(_fmt_drift(drift_data, last_n=5))

            with tool_context(
                "max_pain",
                ticker=ticker,
                date=date,
                expiration_date=expiration_date,
                needs_tool=False,
                skip_page_filter=True,
            ) as ctx:
                mp_data = ctx.client.fetch_max_pain(ctx.tool_spec.tool_id)
                sections.append(_fmt_max_pain(mp_data))

            with tool_context(
                "contract_side_stats",
                ticker=ticker,
                date=date,
                expiration_date=expiration_date,
                needs_tool=False,
                skip_page_filter=True,
            ) as ctx:
                tss_data = ctx.client.fetch_trade_side_stats(ctx.tool_spec.tool_id)
                sections.append(_fmt_trade_side_stats(tss_data))

            with tool_context(
                "contract_statistics",
                ticker=ticker,
                date=date,
                expiration_date=expiration_date,
                needs_tool=False,
                skip_page_filter=True,
            ) as ctx:
                cs_data = ctx.client.fetch_contract_statistics(ctx.tool_spec.tool_id)
                sections.append(_fmt_contract_stats(cs_data))

        divider = "\n" + "=" * 56 + "\n"
        return divider.join(sections)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("market snapshot", e)


@mcp.tool()
def qd_set_page_date(
    date: str,
    ticker: str = "SPX",
    expiration_date: str | None = None,
) -> str:
    """Change the session date, ticker, and/or expiration for historical analysis.

    Sets the QuantData page filter so subsequent tool calls return data
    for that session. Useful for switching tickers or analyzing non-0DTE expirations.

    Args:
        date: Session date in YYYY-MM-DD format
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE; set differently for weeklies/monthlies)
    """
    try:
        c = _get_client()
        ok = c.set_page_filter(
            _get_page_id(),
            session_date=date,
            ticker=ticker,
            expiration_date=expiration_date,
        )
        exp_label = expiration_date or date
        if ok:
            return f"Page set to {ticker} on {date} (expiration: {exp_label}). All subsequent tool calls will return data for this session."
        return f"Failed to set page filter."
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return f"Error setting page filter: {e}"



# ---------------------------------------------------------------------------
# PR 2 — Tier-1 expansion tools
# ---------------------------------------------------------------------------


@mcp.tool()
def qd_get_volatility_skew(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    contract_type: ContractTypeFilter | None = None,
    expirations: list[str] | None = None,
    near_n: int = 12,
) -> str:
    """Get the implied-volatility skew curve — IV across strikes per expiration.

    Shows how IV varies with strike (the "smile" or "smirk"). A steep put-side
    skew often signals downside hedging demand; flat skew suggests calmer
    positioning.

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Page-filter expiration date YYYY-MM-DD (default: same as date for 0DTE)
        contract_type: Restrict the rendered table to CALL or PUT only. Default: both.
        expirations: List of expiration dates (YYYY-MM-DD) to include — the API
            scaffold expects an EQUALS list. Default: all expirations the
            QuantData backend deems relevant for the page filter.
        near_n: Show this many strikes nearest the spot per expiration (default: 12).
    """
    filter_updates: dict[str, dict[str, Any] | None] = {
        "contractType": _eq([contract_type.value]) if contract_type is not None else None,
        "expirationDate": _eq(expirations) if expirations else None,
        "ticker": _eq(ticker),
    }
    try:
        with tool_context(
            "volatility_skew",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates=filter_updates,
        ) as ctx:
            data = ctx.client.fetch_volatility_skew(ctx.tool_spec.tool_id)
        return _fmt_volatility_skew(
            data,
            contract_type=contract_type.value if contract_type else None,
            near_n=near_n,
            ticker=ticker,
        )
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("volatility skew", e)


@mcp.tool()
def qd_get_term_structure(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    contract_type: ContractTypeFilter | None = None,
    expirations: list[str] | None = None,
    moneyness: list[MoneynessType] | None = None,
    strikes: list[float] | None = None,
    days_until_expiration: int | None = None,
    min_delta: float | None = None,
    max_delta: float | None = None,
) -> str:
    """Get the IV / expected-move term structure across expirations.

    Shows expected move per expiration and the underlying per-strike IV grid.
    Useful for identifying volatility skew across time (front-month rich vs
    back-month rich, etc.).

    The QuantData backend defaults the ``moneynessMoneyType`` filter to
    ``["AT_THE_MONEY"]`` server-side; pass ``moneyness`` only to override.

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Page-filter expiration date (default: same as date)
        contract_type: Restrict to CALL or PUT only. Default: both.
        expirations: List of expiration dates (YYYY-MM-DD) to include.
        moneyness: Filter by moneyness — OTM, ITM, ATM. Pass a list to combine.
            Default: ATM (server-side default).
        strikes: Filter to specific strike prices in dollars (e.g. [560.0]).
        days_until_expiration: Filter to a specific DTE bucket (rare; usually
            you want ``expirations`` instead).
        min_delta: Minimum delta floor (GTE on ``deltaMin``). Default: 0.
        max_delta: Maximum delta ceiling (LTE on ``deltaMax``). Default: 1.
            Unlike most filter pairs, ``deltaMin`` and ``deltaMax`` are
            SEPARATE fields on the term-structure scaffold, so a true delta
            range works here.
    """
    filter_updates: dict[str, dict[str, Any] | None] = {
        "contractType": _eq([contract_type.value]) if contract_type is not None else None,
        "expirationDate": _eq(expirations) if expirations else None,
        "moneynessMoneyType": _eq([m.value for m in moneyness]) if moneyness else None,
        "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
        "daysUntilExpiration": _eq(days_until_expiration) if days_until_expiration is not None else None,
        "deltaMin": _gte(min_delta) if min_delta is not None else None,
        "deltaMax": _lte(max_delta) if max_delta is not None else None,
        "ticker": _eq(ticker),
    }
    try:
        with tool_context(
            "term_structure",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates=filter_updates,
        ) as ctx:
            data = ctx.client.fetch_term_structure(ctx.tool_spec.tool_id)
        return _fmt_term_structure(data, ticker=ticker)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("term structure", e)


@mcp.tool()
def qd_get_volatility_drift(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    last_n: int = 10,
) -> str:
    """Get realized vs implied volatility over time (ARV vs IV vs spot).

    Volatility drift compares actual realized volatility (ARV) to implied
    volatility (IV) at each timestamp — useful for spotting regime changes
    (e.g. IV richening into an event, or RV catching up after a big move).

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE)
        last_n: Number of recent timestamps to render (default: 10).
    """
    try:
        with tool_context(
            "volatility_drift",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates={
                "expirationDate": _eq(expiration_date) if expiration_date else None,
                "ticker": _eq(ticker),
            },
        ) as ctx:
            data = ctx.client.fetch_volatility_drift(ctx.tool_spec.tool_id)
        return _fmt_volatility_drift(data, last_n)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("volatility drift", e)


@mcp.tool()
def qd_get_max_pain_over_time(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
) -> str:
    """Get the max-pain strike for each expiration of the current chain.

    Same idea as ``qd_get_max_pain`` but rendered as a per-expiration table.
    Useful for spotting which expirations have a max-pain strike near vs far
    from the current spot.

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE)
    """
    try:
        with tool_context(
            "max_pain_over_time",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates={"ticker": _eq(ticker)},
        ) as ctx:
            data = ctx.client.fetch_max_pain_over_time(ctx.tool_spec.tool_id)
        return _fmt_max_pain_over_time(data, ticker=ticker)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("max pain over time", e)


@mcp.tool()
def qd_get_oi_change(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    contract_type: ContractTypeFilter | None = None,
    strikes: list[float] | None = None,
    expirations: list[str] | None = None,
    min_pct_change: float | None = None,
    top_n: int = 15,
) -> str:
    """Get the day's biggest OI changes — strikes that gained / lost the most open interest.

    Big OI changes often signal positioning shifts (e.g. a put-side build-up
    suggests hedging demand; a call-side dump suggests profit-taking).

    Example — strikes that gained at least 50% OI today, top 10::

        qd_get_oi_change(ticker="SPY", min_pct_change=50.0, top_n=10)

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE)
        contract_type: Filter to CALL or PUT only. Default: both.
        strikes: Filter to specific strike prices in dollars (e.g. [560.0]).
        expirations: Filter to specific expiration dates.
        min_pct_change: Minimum percent change in OI vs prior session
            (e.g. ``50.0`` for >=50% change). The headline filter for this tool.
        top_n: Number of rows to display, sorted by absolute OI change DESC (default: 15).
    """
    filter_updates: dict[str, dict[str, Any] | None] = {
        "contractType": _eq(contract_type.value) if contract_type is not None else None,
        "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
        "expirationDate": _eq(expirations) if expirations else None,
        "percentChangeInOpenInterest": _gte(min_pct_change) if min_pct_change is not None else None,
        "ticker": _eq([ticker]),
    }
    try:
        with tool_context(
            "oi_change",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates=filter_updates,
        ) as ctx:
            data = ctx.client.fetch_oi_change(ctx.tool_spec.tool_id)
        return _fmt_oi_change(data, top_n=top_n)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("OI change", e)


@mcp.tool()
def qd_get_oi_by_expiration(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    strikes: list[float] | None = None,
) -> str:
    """Get total call/put open interest summed per expiration.

    Quickly identifies which expirations carry the most positioning — useful for
    spotting key dates (monthly opex, FOMC week, etc.).

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Page-filter expiration date YYYY-MM-DD (default: same as date)
        strikes: Filter to specific strike prices in dollars (e.g. [560.0]).
    """
    try:
        with tool_context(
            "oi_by_expiration",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates={
                "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
                "ticker": _eq(ticker),
            },
        ) as ctx:
            data = ctx.client.fetch_oi_by_expiration(ctx.tool_spec.tool_id)
        return _fmt_oi_by_expiration(data, ticker=ticker)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("OI by expiration", e)


@mcp.tool()
def qd_get_oi_over_time(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    strikes: list[float] | None = None,
    chart_type: ChartType | None = None,
    last_n: int = 20,
) -> str:
    """Get call/put open interest per session date — track OI build-up over time.

    Useful for confirming whether a position has been steadily accumulated
    (gradual build) vs slammed on in one session (event-driven).

    Args:
        ticker: Ticker symbol (default: SPX). Any optionable ticker works.
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE)
        strikes: Filter to specific strike prices in dollars (e.g. [560.0]).
        chart_type: Server-side chart rendering hint — CANDLESTICK or LINE
            (defaults to whatever the user has configured in the QuantData UI).
        last_n: Number of recent sessions to render (default: 20).
    """
    metadata_updates: dict[str, Any] = {}
    if chart_type is not None:
        metadata_updates["chartType"] = chart_type.value
    try:
        with tool_context(
            "oi_over_time",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            metadata_updates=metadata_updates or None,
            filter_updates={
                "expirationDate": _eq(expiration_date) if expiration_date else None,
                "strikePriceInCents": _eq([int(s * 100) for s in strikes]) if strikes else None,
                "ticker": _eq(ticker),
            },
        ) as ctx:
            data = ctx.client.fetch_oi_over_time(ctx.tool_spec.tool_id)
        return _fmt_oi_over_time(data, last_n=last_n, ticker=ticker)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("OI over time", e)


@mcp.tool()
def qd_get_unconsolidated_flow(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    # ----- Existing filters (mirror order_flow) -----
    contract_type: ContractTypeFilter | None = None,
    moneyness: list[MoneynessType] | None = None,
    trade_side: list[TradeSideCodeType] | None = None,
    min_premium: float | None = None,
    strikes: list[float] | None = None,
    # ----- Bool flag filters (snake_case, "is_" prefix) -----
    is_unusual: bool | None = None,
    is_opening_position: bool | None = None,
    is_etf: bool | None = None,
    is_index: bool | None = None,
    is_volume_gt_oi: bool | None = None,
    # ----- Threshold filters (GTE / LTE) -----
    min_size: int | None = None,
    min_volume: int | None = None,
    min_open_interest: int | None = None,
    min_iv: float | None = None,
    min_bid_ask_spread: float | None = None,
    min_moneyness_pct: float | None = None,
    min_moneyness_dollars: float | None = None,
    max_dte: float | None = None,
    # ----- Greek thresholds (GTE only) -----
    min_delta: float | None = None,
    min_gamma: float | None = None,
    min_theta: float | None = None,
    min_vega: float | None = None,
    min_charm: float | None = None,
    min_vanna: float | None = None,
    # ----- Multi-select list filters -----
    sentiment_type: list[SentimentType] | None = None,
    trade_type: list[str] | None = None,
    exchange_type: list[str] | None = None,
    sector: list[str] | None = None,
    industry: list[str] | None = None,
    # ----- Output control -----
    last_n: int = 20,
) -> str:
    """Get unconsolidated order flow — every individual trade, no sweep / block rollup.

    Mirrors :func:`qd_get_order_flow`'s filter set with two API-driven
    differences: there is no ``is_golden_sweep`` flag (the unconsolidated
    scaffold doesn't expose it), and rows are sorted by premium DESC by default
    (vs trade time DESC for the consolidated view).

    Use this when you want raw per-trade detail — e.g. to inspect every leg of
    a sweep, or to verify large block trades aren't being merged with smaller
    nearby trades.

    Example — bullish $50K+ trades on SPY today::

        qd_get_unconsolidated_flow(
            ticker="SPY", min_premium=50000,
            sentiment_type=[SentimentType.BULLISH], last_n=20,
        )

    Args:
        ticker: Ticker symbol (default: SPX).
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date)
        contract_type: Filter to CALL or PUT only.
        moneyness: Filter by moneyness — OTM, ITM, ATM (list to combine).
        trade_side: Filter by trade side — AA, A, M, B, BB.
        min_premium: Minimum premium in dollars (e.g. 10000 for $10K+).
        strikes: Filter to specific strike prices in dollars.
        is_unusual: Only trades flagged as unusual activity.
        is_opening_position: Only opening positions (volume > prior OI).
        is_etf: Only ETF underliers.
        is_index: Only index underliers (SPX, NDX, etc.).
        is_volume_gt_oi: Only trades where contract volume exceeds OI.
        min_size: Minimum trade size (contracts).
        min_volume: Minimum daily contract volume.
        min_open_interest: Minimum open interest.
        min_iv: Minimum implied volatility (decimal, e.g. 0.25 for 25%).
        min_bid_ask_spread: Minimum bid-ask spread in dollars.
        min_moneyness_pct: Minimum moneyness as a percent.
        min_moneyness_dollars: Minimum moneyness in dollars.
        max_dte: Maximum days-to-expiration (fractional). Use 0 for 0DTE only.
        min_delta: Minimum delta floor.
        min_gamma: Minimum gamma floor.
        min_theta: Minimum theta floor (typically negative).
        min_vega: Minimum vega floor.
        min_charm: Minimum charm floor.
        min_vanna: Minimum vanna floor.
        sentiment_type: Filter by sentiment classification.
        trade_type: Free-form trade type codes (e.g. ["AUTO", "M2S_FLR"]).
        exchange_type: Free-form exchange codes.
        sector: Free-form sector codes.
        industry: Free-form industry codes.
        last_n: Number of trades to render (default: 20).
    """
    # Reuses the consolidated-flow builder. The unconsolidated scaffold lacks
    # `isGoldenSweep` and `tradeConsolidationType` — we just don't surface them
    # as MCP args, so they default to None and `tool_context` drops them.
    filter_updates = build_order_flow_filter(
        contract_type=contract_type,
        moneyness=moneyness,
        trade_side=trade_side,
        min_premium=min_premium,
        strikes=strikes,
        is_unusual=is_unusual,
        is_opening_position=is_opening_position,
        is_etf=is_etf,
        is_index=is_index,
        is_volume_gt_oi=is_volume_gt_oi,
        min_size=min_size,
        min_volume=min_volume,
        min_open_interest=min_open_interest,
        min_iv=min_iv,
        min_bid_ask_spread=min_bid_ask_spread,
        min_moneyness_pct=min_moneyness_pct,
        min_moneyness_dollars=min_moneyness_dollars,
        max_dte=max_dte,
        min_delta=min_delta,
        min_gamma=min_gamma,
        min_theta=min_theta,
        min_vega=min_vega,
        min_charm=min_charm,
        min_vanna=min_vanna,
        sentiment_type=sentiment_type,
        trade_type=trade_type,
        exchange_type=exchange_type,
        sector=sector,
        industry=industry,
    )
    try:
        with tool_context(
            "unconsolidated_flow",
            ticker=ticker,
            date=date,
            expiration_date=expiration_date,
            filter_updates=filter_updates,
        ) as ctx:
            data = ctx.client.fetch_unconsolidated_flow(ctx.tool_spec.tool_id)
        # Same response shape as consolidated flow — reuse the formatter.
        return _fmt_order_flow(data, last_n)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("unconsolidated flow", e)


# ---------------------------------------------------------------------------
# Filter groups — server-side persistent named filter sets
# ---------------------------------------------------------------------------
#
# QuantData filter groups are first-class persistent objects. Each group has a
# tree of conditions (typically one AND-group of leaves) and a `type` from the
# enum {OPTION_TRADES_UNCONSOLIDATED, OPTION_TRADES_CONSOLIDATED, NEWS_ARTICLES}.
# Groups attach to tools via the tool DTO's `filterGroupIds` array — once
# attached, the group is AND'd onto every fetch alongside `metadata.filter`.

# Tool types to probe when resolving a filter-group name → ID. The QuantData
# listing endpoint is per-tool-type and the server's index doesn't uniformly
# expose every group on every tool type (likely propagation lag), so we walk
# a curated set of representative tools and union the results.
_RESOLVE_PROBE_TOOL_TYPES = (
    "OPTIONS_NET_DRIFT_CHART",                  # surfaces OPTION_TRADES_UNCONSOLIDATED reliably
    "OPTIONS_ORDER_FLOW_UNCONSOLIDATED_TABLE",  # ditto, plus its own variants
    "OPTIONS_ORDER_FLOW_CONSOLIDATED_TABLE",   # OPTION_TRADES_CONSOLIDATED
    "OPTIONS_NET_FLOW_CHART",                   # extra coverage for trades
    "NEWS_ARTICLE_LISTING",                     # NEWS_ARTICLES (future-proof)
)

_UUID_RE = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    __import__("re").IGNORECASE,
)


def _resolve_filter_group(
    ref: str, *, hint_group_type: str | None = None
) -> dict[str, Any] | None:
    """Resolve a filter-group reference (UUID or name) to a full DTO.

    If ``ref`` is UUID-shaped, fetches by ID directly. Otherwise enumerates
    the user's groups across multiple representative tool types and matches
    by name. Uses several probe types because the listing endpoint's index
    doesn't always include every group under every tool type immediately
    after creation.
    """
    client = _get_client()
    if _UUID_RE.match(ref):
        return client.get_filter_group(ref)

    seen_ids: set[str] = set()
    for tool_type in _RESOLVE_PROBE_TOOL_TYPES:
        groups = client.list_filter_groups(tool_type)
        for g in groups:
            gid = g.get("id", "")
            if gid in seen_ids:
                continue
            seen_ids.add(gid)
            if g.get("name") == ref:
                return g
        if hint_group_type:
            # If caller hinted a type, we can stop after the first matching
            # probe. Otherwise we walk the full list to be thorough.
            break
    return None


def _resolve_tool_id(tool_name_or_id: str) -> str:
    """Accept either a canonical tool name (``"net_drift"``) from the spec
    registry or a raw tool ID. Returns the ID."""
    specs = _get_specs()
    if tool_name_or_id in specs:
        return specs[tool_name_or_id].tool_id
    return tool_name_or_id


def _summarise_group(g: dict[str, Any]) -> str:
    """One-line render of a filter group for list output."""
    name = g.get("name", "?")
    gid = g.get("id", "")[:8]
    gtype = g.get("type", "?")
    pub = "🌐" if g.get("isPublic") else "🔒"
    desc = (g.get("description") or "").splitlines()[0][:60]
    summary = summarise_filter_tree(g.get("filter") or {})
    if len(summary) > 100:
        summary = summary[:97] + "..."
    return f"{pub} {name!r} [{gid}…] type={gtype}\n   filter: {summary}" + (
        f"\n   {desc}" if desc else ""
    )


@mcp.tool()
def qd_list_filter_groups(tool_type: str = "order_flow") -> str:
    """List the user's saved filter groups applicable to a given tool type.

    Filter groups are server-side, persistent, named filter sets that can be
    attached to one or more canonical tools. Once attached, the group is
    AND'd onto every fetch from that tool.

    Args:
        tool_type: Either a canonical name (``"order_flow"``,
            ``"unconsolidated_flow"``, ``"net_drift"``, ``"net_flow"``, etc.)
            or a raw QuantData tool type (``"OPTIONS_ORDER_FLOW_..."``).
            Default: ``"order_flow"``.
    """
    try:
        # Resolve canonical name → tool type via TOOL_DEFINITIONS
        defn = TOOL_DEFINITIONS.get(tool_type)
        full_type = defn.tool_type.value if defn else tool_type
        groups = _get_client().list_filter_groups(full_type)
        if not groups:
            return f"No filter groups for tool type {full_type}."
        lines = [f"Filter Groups for {full_type} ({len(groups)} total)\n"]
        for g in groups:
            lines.append(_summarise_group(g))
        return "\n\n".join(lines)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"list_filter_groups({tool_type})", e)


@mcp.tool()
def qd_search_public_filter_groups(
    group_type: str = "OPTION_TRADES_UNCONSOLIDATED",
    query: str | None = None,
    top_n: int = 20,
) -> str:
    """Browse community / public filter groups for inspiration or cloning.

    Args:
        group_type: One of ``OPTION_TRADES_UNCONSOLIDATED``,
            ``OPTION_TRADES_CONSOLIDATED``, ``NEWS_ARTICLES``. Default:
            ``OPTION_TRADES_UNCONSOLIDATED`` (the most populated category).
        query: Optional case-insensitive substring filter applied to the
            group's name + description. ``None`` returns everything.
        top_n: Cap the number of rendered groups (default 20).
    """
    if group_type not in GROUP_TYPES:
        return (
            f"Unknown group_type {group_type!r}. Use one of: "
            f"{', '.join(sorted(GROUP_TYPES))}"
        )
    try:
        groups = _get_client().list_public_filter_groups(group_type)
        if query:
            q = query.lower()
            groups = [
                g for g in groups
                if q in (g.get("name", "") + " " + (g.get("description") or "")).lower()
            ]
        if not groups:
            return (
                f"No public filter groups for {group_type}"
                + (f" matching {query!r}" if query else "") + "."
            )
        groups = groups[:top_n]
        lines = [f"Public Filter Groups — {group_type} ({len(groups)} shown)\n"]
        for g in groups:
            lines.append(_summarise_group(g))
        return "\n\n".join(lines)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error("search_public_filter_groups", e)


@mcp.tool()
def qd_get_filter_group(group_id_or_name: str) -> str:
    """Show the full details of a single filter group by ID or name.

    Resolves a name → ID by enumerating the user's groups across all 3
    group types. Pass a UUID to skip the lookup.
    """
    try:
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        return _summarise_group(g)
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"get_filter_group({group_id_or_name!r})", e)


@mcp.tool()
def qd_save_filter_group(
    name: str,
    conditions: list[dict[str, Any]],
    group_type: str = "OPTION_TRADES_UNCONSOLIDATED",
    description: str = "",
    public: bool = False,
) -> str:
    """Create a new filter group and populate it with the given conditions.

    Conditions are a flat list of ``{field, op, value}`` dicts; the server-side
    representation becomes one AND-group at the root. Examples::

        # Cleaner directional signal — exclude noise trades
        qd_save_filter_group(
            name="exclude_tied_complex",
            conditions=[
                {"field": "IS_COMPLEX", "op": "EQUALS", "value": False},
                {"field": "IS_TIED",    "op": "EQUALS", "value": False},
                {"field": "IS_FLOOR",   "op": "EQUALS", "value": False},
            ],
            description="Cleaner directional signal — Net Drift / Net Flow",
        )

        # Premium SPY sweeps with a delta floor
        qd_save_filter_group(
            name="spy_directional_premium",
            conditions=[
                {"field": "TICKER",          "op": "==",  "value": "SPY"},
                {"field": "PREMIUM_IN_CENTS", "op": ">=", "value": 1_000_000},
                {"field": "GREEK_DELTA",      "op": ">=", "value": 0.30},
            ],
        )

    Field names accept any case (``IS_COMPLEX`` / ``is_complex`` / ``isComplex``).
    Operators accept aliases (``"=="``, ``"!="``, ``">="``, ``"<="``, ``"gte"``,
    ``"contains"``, etc.).

    Args:
        name: Display name for the group (no uniqueness enforcement on the
            server side, but the LLM should pick distinctive names).
        conditions: Flat list of ``{field, op, value}`` dicts.
        group_type: One of ``OPTION_TRADES_UNCONSOLIDATED`` (default),
            ``OPTION_TRADES_CONSOLIDATED``, ``NEWS_ARTICLES``.
        description: Free-text description (shown in the QuantData UI).
        public: When True, the group is visible in
            ``qd_search_public_filter_groups`` for other users.
    """
    if group_type not in GROUP_TYPES:
        return f"Unknown group_type {group_type!r}. Valid: {', '.join(sorted(GROUP_TYPES))}"
    try:
        tree = build_filter_tree(conditions)
    except ValueError as e:
        return f"Invalid condition: {e}"
    try:
        client = _get_client()
        dto = client.create_filter_group(
            name=name, group_type=group_type, description=description, is_public=public,
        )
        if dto is None:
            return "Failed to create filter group (see server logs)."
        # The create returns an empty filter — populate it via PUT.
        dto["filter"] = tree
        updated = client.update_filter_group(dto)
        if updated is None:
            return f"Created group {dto.get('id', '')[:8]}... but failed to populate conditions."
        return (
            f"Saved filter group {name!r} ({updated.get('id', '')[:8]}...). "
            f"Filter: {summarise_filter_tree(tree)}"
        )
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"save_filter_group({name!r})", e)


@mcp.tool()
def qd_update_filter_group(
    group_id_or_name: str,
    name: str | None = None,
    conditions: list[dict[str, Any]] | None = None,
    description: str | None = None,
    public: bool | None = None,
) -> str:
    """Update fields on an existing filter group. Only provided args are
    changed; ``None`` leaves the field as-is. Replaces the entire condition
    list when ``conditions`` is provided (no per-clause add/remove).
    """
    try:
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        if name is not None:
            g["name"] = name
        if description is not None:
            g["description"] = description
        if public is not None:
            g["isPublic"] = public
        if conditions is not None:
            try:
                g["filter"] = build_filter_tree(conditions)
            except ValueError as e:
                return f"Invalid condition: {e}"
        updated = _get_client().update_filter_group(g)
        if updated is None:
            return "Failed to update filter group (see server logs)."
        return f"Updated. {_summarise_group(updated)}"
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"update_filter_group({group_id_or_name!r})", e)


@mcp.tool()
def qd_delete_filter_group(group_id_or_name: str) -> str:
    """Delete a filter group permanently. Detaches it from any tools that
    referenced it (server-side cascade, not a client-side sweep)."""
    try:
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        ok = _get_client().delete_filter_group(g["id"])
        return (
            f"Deleted {g.get('name', '?')!r}." if ok
            else f"Failed to delete {g.get('name', '?')!r} (see server logs)."
        )
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"delete_filter_group({group_id_or_name!r})", e)


@mcp.tool()
def qd_apply_filter_group(tool_name: str, group_id_or_name: str) -> str:
    """Attach a saved filter group to one of the canonical MCP tools.

    Once attached, the saved filter is AND'd onto every fetch from that tool
    automatically — no need to pass it through subsequent ``qd_get_*`` calls.
    Idempotent: re-applying the same group is a no-op.

    Args:
        tool_name: Canonical tool name (``"net_drift"``, ``"order_flow"``,
            ``"unconsolidated_flow"``, etc.) or a raw tool ID.
        group_id_or_name: Filter group UUID or name.
    """
    try:
        specs = _get_specs()
        if tool_name not in specs and not _UUID_RE.match(tool_name):
            return f"Unknown tool {tool_name!r}. Available: {', '.join(sorted(specs))}"
        tool_id = _resolve_tool_id(tool_name)
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        ok = _get_client().attach_filter_group_to_tool(tool_id, g["id"])
        if not ok:
            return "Attach failed (see server logs)."
        return f"Attached {g.get('name', '?')!r} to {tool_name}."
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(
            f"apply_filter_group({tool_name!r}, {group_id_or_name!r})", e
        )


@mcp.tool()
def qd_detach_filter_group(tool_name: str, group_id_or_name: str) -> str:
    """Remove a previously-attached filter group from a tool. Idempotent."""
    try:
        specs = _get_specs()
        if tool_name not in specs and not _UUID_RE.match(tool_name):
            return f"Unknown tool {tool_name!r}. Available: {', '.join(sorted(specs))}"
        tool_id = _resolve_tool_id(tool_name)
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        ok = _get_client().detach_filter_group_from_tool(tool_id, g["id"])
        if not ok:
            return "Detach failed (see server logs)."
        return f"Detached {g.get('name', '?')!r} from {tool_name}."
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(
            f"detach_filter_group({tool_name!r}, {group_id_or_name!r})", e
        )


@mcp.tool()
def qd_clone_public_filter_group(
    public_group_id: str,
    new_name: str | None = None,
) -> str:
    """Copy a public/community filter group into the user's account.

    Useful workflow: discover groups via ``qd_search_public_filter_groups``,
    pick one whose strategy you like, clone it under your own name, then
    ``qd_apply_filter_group`` it to a canonical tool.

    Args:
        public_group_id: UUID of a public group (from
            ``qd_search_public_filter_groups`` output).
        new_name: Override the cloned group's name. Defaults to
            ``"Copy of <original name>"``.
    """
    try:
        client = _get_client()
        src = client.get_filter_group(public_group_id)
        if src is None:
            return f"Filter group {public_group_id!r} not found."
        target_name = new_name or f"Copy of {src.get('name', 'unnamed')}"
        new_dto = client.create_filter_group(
            name=target_name,
            group_type=src.get("type", "OPTION_TRADES_UNCONSOLIDATED"),
            description=f"Cloned from public group {src.get('name', '?')!r}",
            is_public=False,
        )
        if new_dto is None:
            return "Failed to create cloned group."
        # Copy the filter tree but regenerate keys to avoid collisions.
        new_dto["filter"] = _regenerate_keys(src.get("filter") or {})
        updated = client.update_filter_group(new_dto)
        if updated is None:
            return f"Cloned group {target_name!r} created but failed to copy filter tree."
        return f"Cloned as {target_name!r} ({updated.get('id', '')[:8]}...)."
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"clone_public_filter_group({public_group_id!r})", e)


# ---------------------------------------------------------------------------
# Surgical clause edits — add / remove / update individual conditions
# without re-listing the whole filter. Mirrors the QuantData web UI's
# per-clause +/× behaviour so users can edit either surface and the other
# stays in sync.
# ---------------------------------------------------------------------------


@mcp.tool()
def qd_add_filter_clause(
    group_id_or_name: str,
    field: str,
    op: str,
    value: Any,
    branch_key: str | None = None,
) -> str:
    """Append a single clause to a saved filter group.

    By default the clause is added to the first AND-group at the root
    (the typical "all of these conditions must hold" location). Pass
    ``branch_key`` to target a specific OR alternative when the group has
    nested branches.

    Args:
        group_id_or_name: UUID or name of the filter group.
        field: Field name (any case — ``IS_COMPLEX`` / ``is_complex`` /
            ``isComplex`` all normalise).
        op: Operator. Accepts canonical (``EQUALS``, ``GREATER_THAN_OR_EQUAL_TO``)
            or aliases (``==``, ``>=``, ``gte``, ``contains``, ``!=``).
        value: Python value — booleans, numbers, lists all serialised
            correctly to QuantData's wire format.
        branch_key: Optional UUID of a specific AND-branch to target. Look
            it up via ``qd_get_filter_group`` if the group has multiple
            OR alternatives.

    Example::

        # Bump an existing "clean signal" group with a $5K premium floor
        qd_add_filter_clause(
            "clean_signal", "PREMIUM_IN_CENTS", ">=", 500_000,
        )
    """
    try:
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        tree = g.get("filter") or {"key": "", "conjunctionType": "OR", "filters": []}
        try:
            new_leaf = add_leaf(tree, field=field, op=op, value=value, branch_key=branch_key)
        except ValueError as e:
            return f"Add failed: {e}"
        g["filter"] = tree
        updated = _get_client().update_filter_group(g)
        if updated is None:
            return "Update failed (see server logs)."
        return (
            f"Added clause to {g.get('name', '?')!r}: "
            f"{new_leaf['field']}{_OP_SYMBOL_FOR(new_leaf['operationType'])}{new_leaf['value']}\n"
            f"Filter now: {summarise_filter_tree(updated.get('filter') or {})}"
        )
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"add_filter_clause({group_id_or_name!r}, {field!r})", e)


@mcp.tool()
def qd_remove_filter_clause(
    group_id_or_name: str,
    field: str | None = None,
    clause_key: str | None = None,
) -> str:
    """Remove one or more clauses from a saved filter group.

    Pass ``field`` to remove every clause matching that field name (the
    common case — e.g. "drop the premium threshold"). Pass ``clause_key``
    to target a single clause by UUID when there are multiple matching
    clauses on the same field. Pass both to require both match.

    Args:
        group_id_or_name: UUID or name of the filter group.
        field: Field name to match (any case). Removes all matches.
        clause_key: UUID of a specific leaf — find it via
            ``qd_get_filter_group``.
    """
    if field is None and clause_key is None:
        return "Pass at least one of `field` or `clause_key`."
    try:
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        tree = g.get("filter") or {}
        n = remove_leaves(tree, key=clause_key, field=field)
        if n == 0:
            target = field or clause_key
            return f"No clauses matching {target!r} in {g.get('name', '?')!r}."
        g["filter"] = tree
        updated = _get_client().update_filter_group(g)
        if updated is None:
            return "Update failed (see server logs)."
        return (
            f"Removed {n} clause(s) from {g.get('name', '?')!r}.\n"
            f"Filter now: {summarise_filter_tree(updated.get('filter') or {})}"
        )
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"remove_filter_clause({group_id_or_name!r})", e)


@mcp.tool()
def qd_update_filter_clause(
    group_id_or_name: str,
    field: str,
    new_op: str | None = None,
    new_value: Any = None,
    clause_key: str | None = None,
) -> str:
    """Edit a single clause in place — change its operator or value without
    touching the rest of the filter.

    Targets the clause by ``field`` (the common case). Pass ``clause_key``
    when there are multiple clauses on the same field. Errors if the match
    is ambiguous (multiple leaves match without a key).

    Either or both of ``new_op`` and ``new_value`` must be provided.

    Args:
        group_id_or_name: UUID or name.
        field: Field name of the clause to edit (any case).
        new_op: Replacement operator. ``None`` keeps the existing operator.
        new_value: Replacement value. ``None`` is treated as "no change"
            here — pass ``new_op="!=", new_value=None`` if you literally
            need a null value (rare on this API).
        clause_key: UUID of a specific leaf if multiple clauses share the
            same field.

    Example::

        # Tighten the premium floor on a saved group from $5K to $50K
        qd_update_filter_clause(
            "clean_signal", "PREMIUM_IN_CENTS", new_value=5_000_000,
        )
    """
    if new_op is None and new_value is None:
        return "Pass at least one of `new_op` or `new_value`."
    try:
        g = _resolve_filter_group(group_id_or_name)
        if g is None:
            return f"Filter group {group_id_or_name!r} not found."
        tree = g.get("filter") or {}
        matches = find_leaves(tree, key=clause_key, field=field)
        if not matches:
            return f"No clause matching field {field!r} in {g.get('name', '?')!r}."
        if len(matches) > 1 and clause_key is None:
            keys = [leaf.get("key", "?")[:8] for _, leaf in matches]
            return (
                f"Multiple clauses match field {field!r} in "
                f"{g.get('name', '?')!r}. Pass clause_key=one of: {keys}"
            )
        _, leaf = matches[0]
        # Only forward the kwargs the caller actually provided. ``None``
        # means "keep the existing value" at the MCP-tool level — the
        # underlying API doesn't have a concept of null filter values
        # anyway, so this is the right behaviour.
        update_kwargs: dict[str, Any] = {}
        if new_op is not None:
            update_kwargs["new_op"] = new_op
        if new_value is not None:
            update_kwargs["new_value"] = new_value
        update_leaf(leaf, **update_kwargs)
        g["filter"] = tree
        updated = _get_client().update_filter_group(g)
        if updated is None:
            return "Update failed (see server logs)."
        return (
            f"Updated clause in {g.get('name', '?')!r}: "
            f"{leaf['field']}{_OP_SYMBOL_FOR(leaf['operationType'])}{leaf['value']}\n"
            f"Filter now: {summarise_filter_tree(updated.get('filter') or {})}"
        )
    except QuantDataAuthError:
        return AUTH_ERROR_MESSAGE
    except Exception as e:
        return format_error(f"update_filter_clause({group_id_or_name!r}, {field!r})", e)


# Re-export the operator-symbol map for the surgical-edit tools' output and
# the sentinel for "value not provided" semantics in qd_update_filter_clause.
def _OP_SYMBOL_FOR(op: str) -> str:
    return {
        "EQUALS": "=",
        "DOES_NOT_EQUAL": "!=",
        "GREATER_THAN": ">",
        "GREATER_THAN_OR_EQUAL_TO": ">=",
        "LESS_THAN": "<",
        "LESS_THAN_OR_EQUAL_TO": "<=",
        "CONTAINS": " contains ",
    }.get(op, op)


def _regenerate_keys(tree: dict[str, Any]) -> dict[str, Any]:
    """Walk a filter tree and replace every node's ``key`` UUID with a fresh
    one. The server doesn't strictly require this for clones, but reusing
    keys across groups is messy and risks future server-side dedupe weirdness.
    """
    if not isinstance(tree, dict):
        return tree
    import uuid as _uuid
    out = dict(tree)
    out["key"] = str(_uuid.uuid4())
    if "filters" in out and isinstance(out["filters"], list):
        out["filters"] = [_regenerate_keys(c) for c in out["filters"]]
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
