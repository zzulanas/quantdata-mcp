"""
QuantData MCP Server — Exposes all QuantData Agentic Page tools via MCP.

Provides real-time and historical options market data (GEX/DEX/CEX/VEX walls,
net drift, max pain, IV rank, trade side stats, contract stats, OI, net flow)
to any MCP client (e.g., Claude Code).

Usage:
    quantdata-mcp serve
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone, timedelta
from enum import Enum
from typing import Any

from mcp.server.fastmcp import FastMCP

from quantdata_mcp.client import QuantDataClient
from quantdata_mcp.config import Config, config_exists, load_config
from quantdata_mcp.tools import GreekMode, MoneynessType, ToolSpec, TradeSideCodeType, build_tool_specs

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


def _load() -> tuple[QuantDataClient, Config, dict[str, ToolSpec]]:
    """Lazy-init client, config, and tool specs."""
    global _client, _config, _specs
    if _client is None:
        if not config_exists():
            raise RuntimeError(
                "Not configured yet. Please call the qd_login tool first to open a browser and log in to QuantData."
            )
        _config = load_config()
        _specs = build_tool_specs(_config.tools)
        _client = QuantDataClient(
            auth_token=_config.auth_token,
            instance_id=_config.instance_id,
            max_retries=2,
            retry_delay=0.5,
        )
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
    et = timezone(timedelta(hours=-4))  # EDT (summer); close enough for date boundary
    return datetime.now(et).strftime("%Y-%m-%d")


def _apply_page_filter(
    date: str | None = None,
    ticker: str = "SPX",
    expiration_date: str | None = None,
) -> dict[str, str]:
    """Set page filter. Always sets it to ensure correct ticker/date. Returns what was set."""
    c = _get_client()
    session_date = date or _today()
    c.set_page_filter(
        _get_page_id(),
        session_date=session_date,
        ticker=ticker,
        expiration_date=expiration_date,
    )
    return {"date": session_date, "ticker": ticker}


def _restore_page_filter(changed: dict[str, str]) -> None:
    """Restore page filter to today/SPX if we changed away from defaults."""
    today = _today()
    if changed.get("date") != today or changed.get("ticker") != "SPX":
        _get_client().set_page_filter(_get_page_id(), session_date=today, ticker="SPX")


def _apply_tool_filter(
    tool_id: str,
    moneyness: list[str] | None = None,
    trade_side: list[str] | None = None,
    strikes: list[int] | None = None,
) -> dict[str, Any] | None:
    """Apply tool-level filters (moneyness, trade side, strikes).

    Returns the original filter dict for restore, or None if no filters were needed.
    """
    if not moneyness and not trade_side and not strikes:
        return None

    c = _get_client()
    tool_dto = c.get_tool(tool_id)
    if tool_dto is None:
        raise RuntimeError(f"Failed to fetch tool {tool_id} for filtering")

    original_filter = tool_dto.get("metadata", {}).get("filter", {})
    new_filter = dict(original_filter)

    if moneyness is not None:
        new_filter["moneynessMoneyType"] = {
            "filterOperationType": "EQUALS",
            "value": moneyness,
        }
    if trade_side is not None:
        new_filter["tradeSideCodeType"] = {
            "filterOperationType": "EQUALS",
            "value": trade_side,
        }
    if strikes is not None:
        new_filter["strikePriceInCents"] = {
            "filterOperationType": "EQUALS",
            "value": strikes,
        }

    c.update_tool_metadata(tool_id, {"filter": new_filter})
    return original_filter


def _restore_tool_filter(tool_id: str, original_filter: dict[str, Any] | None) -> None:
    """Restore tool filter to its original state. No-op if original_filter is None."""
    if original_filter is None:
        return
    _get_client().update_tool_metadata(tool_id, {"filter": original_filter})


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

    entries = drift_array[-last_n:]

    # Compute running totals
    total_call = sum(t[1] for t in drift_array) / 100
    total_put = sum(t[4] for t in drift_array) / 100
    total_net = total_call - total_put
    total_dir = "BULLISH" if total_net > 1000 else "BEARISH" if total_net < -1000 else "NEUTRAL"

    lines = [f"Net Drift — Last {len(entries)} entries (of {len(drift_array)} total)", ""]
    lines.append(f"{'Time':>12}  {'Call ($)':>12}  {'Put ($)':>12}  {'Net ($)':>12}  {'Price':>10}")
    lines.append("-" * 66)

    for entry in entries:
        ts = entry[0]
        call_prem = entry[1] / 100
        put_prem = entry[4] / 100
        net = call_prem - put_prem
        spx = entry[7] / 100 if len(entry) > 7 else 0

        # Convert epoch ms to time string
        try:
            t = datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%H:%M:%S")
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
    """Format net flow data."""
    if not data or "response" not in data:
        return "No net flow data available."

    resp = data["response"]
    # Net flow structure: response.netFlow array similar to net drift
    flow_array = resp.get("netFlow", [])
    if not flow_array:
        return "No net flow entries."

    entries = flow_array[-last_n:]

    lines = [f"Net Flow — Last {len(entries)} entries", ""]

    for entry in entries:
        if isinstance(entry, (list, tuple)) and len(entry) >= 5:
            ts = entry[0]
            call_flow = entry[1] / 100
            put_flow = entry[4] / 100 if len(entry) > 4 else 0
            net = call_flow - put_flow
            try:
                t = datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%H:%M:%S")
            except (OSError, ValueError):
                t = str(ts)
            lines.append(
                f"  {t}  Call: ${call_flow:>+10,.0f}  Put: ${put_flow:>+10,.0f}  Net: ${net:>+10,.0f}"
            )
        elif isinstance(entry, dict):
            lines.append(f"  {entry}")

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


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


class GreekTypeEnum(str, Enum):
    GAMMA = "GAMMA"
    DELTA = "DELTA"
    CHARM = "CHARM"
    VANNA = "VANNA"


class DataModeEnum(str, Enum):
    PREMIUM = "PREMIUM"
    TRADE_COUNT = "TRADE_COUNT"
    VOLUME = "VOLUME"


class MoneynessEnum(str, Enum):
    OTM = "OUT_OF_THE_MONEY"
    ITM = "IN_THE_MONEY"
    ATM = "AT_THE_MONEY"


class TradeSideEnum(str, Enum):
    AA = "AA"   # Above Ask (aggressive buy)
    A = "A"     # At Ask
    M = "M"     # Midpoint
    B = "B"     # At Bid
    BB = "BB"   # Below Bid (aggressive sell)


@mcp.tool()
def qd_get_exposure_by_strike(
    greek_type: GreekTypeEnum = GreekTypeEnum.GAMMA,
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    time_minutes: int | None = None,
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
    """
    try:
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["exposure_by_strike"]

        # Set greek mode on the tool
        greek_mode = GreekMode(greek_type.value)
        c.update_tool_metadata(tool.tool_id, {"greekModeType": greek_mode.value})

        if time_minutes is not None:
            c.set_tool_time(tool.tool_id, time_minutes)

        data = c.fetch_strike_data(tool.tool_id)

        if time_minutes is not None:
            c.reset_to_live(tool.tool_id)

        # Restore to GAMMA for next caller
        if greek_type.value != "GAMMA":
            c.update_tool_metadata(tool.tool_id, {"greekModeType": "GAMMA"})

        _restore_page_filter(changed)
        return _fmt_walls(data, greek_type.value, ticker=ticker)
    except Exception as e:
        return f"Error fetching {greek_type.value} walls: {e}"


@mcp.tool()
def qd_get_net_drift(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessEnum] | None = None,
    strikes: list[float] | None = None,
    last_n: int = 10,
) -> str:
    """Get net drift data — cumulative call vs put premium flow.

    Net drift shows whether money is flowing into calls (bullish) or puts (bearish).
    Positive net = more call premium, negative = more put premium.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
        moneyness: Filter by moneyness — OTM, ITM, ATM. Pass a list to combine (e.g. ["OTM", "ATM"]). Default: all.
        strikes: Filter to specific strike prices in dollars (e.g. [5600.0, 5700.0]). Default: all.
        last_n: Number of recent entries to show (default: 10)
    """
    try:
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["net_drift"]
        original_filter = _apply_tool_filter(
            tool.tool_id,
            moneyness=[m.value for m in moneyness] if moneyness else None,
            strikes=[int(s * 100) for s in strikes] if strikes else None,
        )
        try:
            data = c.fetch_net_drift(tool.tool_id)
        finally:
            _restore_tool_filter(tool.tool_id, original_filter)
        _restore_page_filter(changed)
        return _fmt_drift(data, last_n)
    except Exception as e:
        return f"Error fetching net drift: {e}"


@mcp.tool()
def qd_get_trade_side_stats(
    data_mode: DataModeEnum = DataModeEnum.PREMIUM,
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessEnum] | None = None,
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
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["contract_side_stats"]

        # Set data mode
        c.update_tool_metadata(tool.tool_id, {"dataModeType": data_mode.value})

        original_filter = _apply_tool_filter(
            tool.tool_id,
            moneyness=[m.value for m in moneyness] if moneyness else None,
            strikes=[int(s * 100) for s in strikes] if strikes else None,
        )
        try:
            data = c.fetch_trade_side_stats(tool.tool_id)
        finally:
            _restore_tool_filter(tool.tool_id, original_filter)
        _restore_page_filter(changed)
        return _fmt_trade_side_stats(data)
    except Exception as e:
        return f"Error fetching trade side stats: {e}"


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
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["max_pain"]
        data = c.fetch_max_pain(tool.tool_id)
        _restore_page_filter(changed)
        return _fmt_max_pain(data)
    except Exception as e:
        return f"Error fetching max pain: {e}"


@mcp.tool()
def qd_get_iv_rank(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
) -> str:
    """Get IV rank — where current implied volatility sits in its historical range.

    Low IVR (<30%) = options are cheap, good for buying.
    High IVR (>70%) = options are expensive, need larger moves for profit.

    Args:
        ticker: Ticker symbol (default: SPX)
        date: Session date YYYY-MM-DD (default: today)
        expiration_date: Expiration date YYYY-MM-DD (default: same as date for 0DTE). Required for non-0DTE tickers like AAPL/TSLA — use a valid expiration (e.g. monthly 3rd Friday)
    """
    try:
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["iv_rank"]
        data = c.fetch_iv_rank(tool.tool_id)
        _restore_page_filter(changed)
        return _fmt_iv_rank(data, date)
    except Exception as e:
        return f"Error fetching IV rank: {e}"


@mcp.tool()
def qd_get_net_flow(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessEnum] | None = None,
    trade_side: list[TradeSideEnum] | None = None,
    strikes: list[float] | None = None,
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
        last_n: Number of recent entries to show (default: 10)
    """
    try:
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["net_flow"]
        original_filter = _apply_tool_filter(
            tool.tool_id,
            moneyness=[m.value for m in moneyness] if moneyness else None,
            trade_side=[t.value for t in trade_side] if trade_side else None,
            strikes=[int(s * 100) for s in strikes] if strikes else None,
        )
        try:
            data = c.fetch_net_flow(tool.tool_id)
        finally:
            _restore_tool_filter(tool.tool_id, original_filter)
        _restore_page_filter(changed)
        return _fmt_net_flow(data, last_n)
    except Exception as e:
        return f"Error fetching net flow: {e}"


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
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["oi_by_strike"]
        data = c.fetch_oi_by_strike(tool.tool_id)
        _restore_page_filter(changed)
        return _fmt_oi_by_strike(data, near_strike, ticker=ticker)
    except Exception as e:
        return f"Error fetching OI by strike: {e}"


@mcp.tool()
def qd_get_contract_statistics(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
    moneyness: list[MoneynessEnum] | None = None,
    trade_side: list[TradeSideEnum] | None = None,
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
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["contract_statistics"]
        original_filter = _apply_tool_filter(
            tool.tool_id,
            moneyness=[m.value for m in moneyness] if moneyness else None,
            trade_side=[t.value for t in trade_side] if trade_side else None,
            strikes=[int(s * 100) for s in strikes] if strikes else None,
        )
        try:
            data = c.fetch_contract_statistics(tool.tool_id)
        finally:
            _restore_tool_filter(tool.tool_id, original_filter)
        _restore_page_filter(changed)
        return _fmt_contract_stats(data)
    except Exception as e:
        return f"Error fetching contract statistics: {e}"


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
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)

        sections: list[str] = []

        # GEX walls
        tool_exp = _get_specs()["exposure_by_strike"]
        c.update_tool_metadata(tool_exp.tool_id, {"greekModeType": "GAMMA"})
        gex_data = c.fetch_strike_data(tool_exp.tool_id)
        sections.append(_fmt_walls(gex_data, "GAMMA", ticker=ticker))

        # DEX walls
        c.update_tool_metadata(tool_exp.tool_id, {"greekModeType": "DELTA"})
        dex_data = c.fetch_strike_data(tool_exp.tool_id)
        sections.append(_fmt_walls(dex_data, "DELTA", ticker=ticker))

        # Restore to GAMMA
        c.update_tool_metadata(tool_exp.tool_id, {"greekModeType": "GAMMA"})

        # Net drift
        drift_data = c.fetch_net_drift(_get_specs()["net_drift"].tool_id)
        sections.append(_fmt_drift(drift_data, last_n=5))

        # Max pain
        mp_data = c.fetch_max_pain(_get_specs()["max_pain"].tool_id)
        sections.append(_fmt_max_pain(mp_data))

        # Trade side stats
        tss_data = c.fetch_trade_side_stats(_get_specs()["contract_side_stats"].tool_id)
        sections.append(_fmt_trade_side_stats(tss_data))

        # Contract stats
        cs_data = c.fetch_contract_statistics(_get_specs()["contract_statistics"].tool_id)
        sections.append(_fmt_contract_stats(cs_data))

        _restore_page_filter(changed)

        divider = "\n" + "=" * 56 + "\n"
        return divider.join(sections)
    except Exception as e:
        return f"Error fetching market snapshot: {e}"


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
    except Exception as e:
        return f"Error setting page filter: {e}"



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
