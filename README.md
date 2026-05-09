# QuantData MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/quantdata-mcp.svg)](https://pypi.org/project/quantdata-mcp/)
[![PyPI downloads](https://img.shields.io/pypi/dm/quantdata-mcp.svg)](https://pypi.org/project/quantdata-mcp/)

## THIS IS AN UNOFFICIAL MCP SERVER I AM NOT AFFILIATED WITH QUANTDATA IN ANY WAY

MCP server that gives AI agents (Claude Code, Claude Desktop, etc.) access to real-time and historical options market data from [QuantData](https://quantdata.us).

> 📖 New to this? Start with the [Getting Started Guide](GETTING_STARTED.md) for a step-by-step walkthrough.

**Supports any optionable ticker** — SPX, SPY, QQQ, AAPL, TSLA, and more. Not just 0DTE.

**Available data:** GEX/DEX/CEX/VEX exposure walls, exposure term structure, net drift, max pain (instantaneous + per-expiration), IV rank, IV skew, IV term structure, volatility drift (ARV vs IV), trade side statistics, open interest (per strike, per expiration, over time, day-over-day change), net flow, consolidated + unconsolidated order flow, contract OHLCV, and contract statistics.

**Documentation:** see [GETTING_STARTED.md](GETTING_STARTED.md) for a click-by-click walkthrough.

## Quick Start

### 1. Install

You need **Python 3.11+** installed. Check with `python3 --version`.

- **Mac:** `brew install python` (or download from [python.org](https://www.python.org/downloads/))
- **Windows:** Download from [python.org](https://www.python.org/downloads/) (check "Add to PATH" during install)

Install from PyPI:

```bash
# With pip
pip install quantdata-mcp

# With uv (faster)
uv pip install quantdata-mcp

# Or install as a global tool with uv
uv tool install quantdata-mcp
```

> **Don't have uv?** Install it with `curl -LsSf https://astral.sh/uv/install.sh | sh` (Mac/Linux) or `irm https://astral.sh/uv/install.ps1 | iex` (Windows). It's a faster alternative to pip.

> **Want the bleeding edge?** Install from GitHub: `pip install git+https://github.com/zzulanas/quantdata-mcp.git`

### 2. Get Your Credentials

You need two values from your QuantData account. Open your browser:

1. Go to [v3.quantdata.us](https://v3.quantdata.us) and **log in**
2. Open **DevTools** (F12 or right-click → Inspect) → **Network** tab
3. Refresh the page
4. Click on any chart or page on QuantData — you'll see API requests appear
5. Click any request to `core-lb-prod.quantdata.us`, or filter by /api in the top
6. In the **Request Headers**, find and copy:
   - **`authorization`** — your auth token (starts with `eyJ...`)
   - **`x-instance-id`** — your instance ID (a UUID like `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
  
Should look like these:
<img width="2093" height="1146" alt="Screenshot 2026-03-27 at 8 49 30 PM 1" src="https://github.com/user-attachments/assets/51856fbb-7f22-458e-8478-8906f8792a54" />


### 3. Run Setup

```bash
quantdata-mcp setup \
  --auth-token "eyJhbGci..." \
  --instance-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

This creates a dedicated page on your QuantData account with 19 data tools and saves your config to `~/.quantdata-mcp/config.json`.

> **Upgrading from a previous install?** Just upgrade the package — the server auto-registers any new tool definitions (e.g. the 8 Tier-1 additions in this release) on first start. Existing tool IDs and page layout are preserved. Re-running `quantdata-mcp setup` is no longer required when a release adds new tools; only run it again if you need to refresh an expired auth token.

### 4. Add to Claude

#### Claude Code

Add to your project's `.mcp.json` (or global `~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "quantdata": {
      "command": "quantdata-mcp",
      "args": ["serve"]
    }
  }
}
```

Restart Claude Code. You should see `quantdata` in your MCP servers.

#### Claude Desktop

Add to your Claude Desktop config file:

- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "quantdata": {
      "command": "quantdata-mcp",
      "args": ["serve"]
    }
  }
}
```

> **Note:** `quantdata-mcp` must be on your system PATH. If it's not found, use the full path:
> ```bash
> which quantdata-mcp   # find the path
> ```
> ```json
> { "command": "/Users/you/.local/bin/quantdata-mcp", "args": ["serve"] }
> ```
>
> Or use `uvx` to run without worrying about PATH:
> ```json
> {
>   "command": "uvx",
>   "args": ["--from", "git+https://github.com/zzulanas/quantdata-mcp.git", "quantdata-mcp", "serve"]
> }
> ```

Restart Claude Desktop. The QuantData tools will appear in your tool list.

## Available Tools

### Market Overview

| Tool | Description |
|------|-------------|
| `qd_get_market_snapshot` | Full overview: GEX + DEX walls, drift, max pain, trade stats |
| `qd_set_page_date` | Switch ticker, session date, and/or expiration for analysis |

### Exposure (Greeks)

| Tool | Description | Key Settings |
|------|-------------|--------------|
| `qd_get_exposure_by_strike` | GEX/DEX/CEX/VEX wall data by strike | `greek_type`, `representation_mode` (per 1%, per $1, raw), `is_net`, `time_minutes` |
| `qd_get_exposure_by_expiration` | Greek exposure term structure across expirations | `greek_type`, `representation_mode`, `is_net`, `strikes` filter |

### Premium Flow

| Tool | Description | Key Settings |
|------|-------------|--------------|
| `qd_get_net_drift` | Cumulative call vs put premium flow | `aggregation` (1min–1hr), `moneyness`, `strikes` filter |
| `qd_get_net_flow` | Call/put premium flow over time | `aggregation`, `data_mode` (premium/volume), `moneyness`, `trade_side`, `strikes` |

### Order Flow & Trade Stats

| Tool | Description | Key Settings |
|------|-------------|--------------|
| `qd_get_order_flow` | Consolidated order flow — individual large trades (40+ filters) | bool flags (`is_unusual`, `is_golden_sweep`, `is_opening_position`, ...), thresholds (`min_premium`, `min_size`, `min_volume`, `min_iv`, `max_dte`, ...), greek floors (`min_delta`, `min_gamma`, `min_theta`, ...), multi-selects (`sentiment_type`, `trade_type`, `sector`, `industry`, ...) |
| `qd_get_unconsolidated_flow` | Raw per-trade order flow — every trade, no sweep/block rollup. Mirrors `qd_get_order_flow`'s 40+ filters (no `is_golden_sweep`; sorted by premium DESC). | Same filter set as `qd_get_order_flow` minus `is_golden_sweep` and `trade_consolidation_type` |
| `qd_get_trade_side_stats` | Trade aggression: AA/A/M/B/BB breakdown | `data_mode`, `moneyness`, `strikes` |
| `qd_get_contract_statistics` | Total premium, trade count, volume by call/put | `moneyness`, `trade_side`, `strikes` |

`qd_get_order_flow` is the most filter-rich tool — it now exposes the full
QuantData order-flow filter set (40+ flat kwargs grouped into bool flags,
GTE/LTE thresholds, greek thresholds, and multi-select lists). Example —
bullish opening-position sweeps in tech with $10K+ premium:

```python
qd_get_order_flow(
    ticker="SPY", is_unusual=True, is_opening_position=True,
    sentiment_type=[SentimentType.BULLISH], min_premium=10000,
    trade_type=["AUTO"], sector=["TECHNOLOGY"], last_n=20,
)
```

### Volatility & Pricing

| Tool | Description | Key Settings |
|------|-------------|--------------|
| `qd_get_iv_rank` | IV rank vs historical range | `lookback_period`, `maturity`, `contract_type` |
| `qd_get_volatility_skew` | IV across strikes per expiration (the "smile" / "smirk") | `contract_type`, `expirations`, `near_n` |
| `qd_get_term_structure` | Expected move + IV grid across expirations | `contract_type`, `expirations`, `moneyness`, `min_delta`, `max_delta` |
| `qd_get_volatility_drift` | Realized vs implied volatility (ARV vs IV) over time | `last_n` |
| `qd_get_contract_price` | OHLCV price data for a specific contract | `strike` (required), `contract_type`, `aggregation` |

### Open Interest & Max Pain

| Tool | Description | Key Settings |
|------|-------------|--------------|
| `qd_get_max_pain` | Max pain strike + distance from current price | — |
| `qd_get_max_pain_over_time` | Max pain strike for each expiration of the chain | — |
| `qd_get_oi_by_strike` | Open interest distribution with near-ATM filtering | `near_strike` |
| `qd_get_oi_by_expiration` | Total call/put OI summed per expiration | `strikes` |
| `qd_get_oi_over_time` | Call/put OI per session date — track build-up over time | `strikes`, `chart_type`, `last_n` |
| `qd_get_oi_change` | Day-over-day OI change — biggest gainers / losers | `min_pct_change`, `contract_type`, `strikes`, `expirations`, `top_n` |

### Common Parameters

All tools accept these parameters for ticker/date control:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ticker` | Any optionable symbol (SPX, SPY, QQQ, AAPL, TSLA, etc.) | `SPX` |
| `date` | Session date YYYY-MM-DD | Today |
| `expiration_date` | Expiration date YYYY-MM-DD | Same as `date` (0DTE) |

### Filter Parameters

Tools that support filtering accept these optional parameters:

| Parameter | Values | Description |
|-----------|--------|-------------|
| `moneyness` | `OTM`, `ITM`, `ATM` | Filter by moneyness (pass a list to combine) |
| `trade_side` | `AA`, `A`, `M`, `B`, `BB` | Filter by trade aggression |
| `strikes` | Dollar values, e.g. `[5600.0]` | Filter to specific strikes |
| `contract_type` | `CALL`, `PUT` | Filter to calls or puts only |
| `min_premium` | Dollar amount, e.g. `50000` | Minimum premium threshold (order flow only) |

### Example Usage

Ask Claude things like:

- *"What are the biggest GEX walls right now?"*
- *"Show me yesterday's DEX walls at 10:30 AM"*
- *"Pull up the trade side stats — are puts or calls more aggressive today?"*
- *"Compare the GEX profile at open vs close for last Thursday"*
- *"Show me AAPL gamma exposure for the April 17 monthly expiration"*
- *"What's the OTM-only net drift for SPX today?"*
- *"Show me the order flow — just calls with premium over $50K"*
- *"Get the OHLCV for the SPX 6600 call today"*
- *"What's the IV rank with a 30-day lookback?"*
- *"Show the GEX term structure across all expirations"*

### Multi-Ticker Support

All tools work with any optionable ticker. Just pass `ticker="AAPL"` (or whatever symbol).

**Important:** SPX, SPY, and QQQ have **daily** expirations (0DTE works by default). For equity options like AAPL or TSLA, you **must** set `expiration_date` to a valid expiration (e.g. monthly 3rd Friday) or you'll get empty data.

```
> Show me TSLA GEX walls for the April 17 monthly
> qd_get_exposure_by_strike(ticker="TSLA", expiration_date="2026-04-17")
```

### Historical Data

All tools support historical analysis. Either pass `date=` to any tool, or use `qd_set_page_date` to switch context:

```
> Set the date to 2026-03-26 and show me the GEX walls at 10:00 AM

> qd_set_page_date(date="2026-03-26")
> qd_get_exposure_by_strike(greek_type="GAMMA", time_minutes=600)
```

Time scrubbing: `time_minutes` = minutes from midnight (570 = 9:30 AM, 720 = 12:00 PM, 960 = 4:00 PM).

**Note:** `date` must be a valid trading day (not weekends or market holidays).

## How It Works

QuantData doesn't have an official API. This server uses reverse-engineered REST endpoints from their web app. Each user has "tools" (chart widgets) on "pages" — the setup command creates a dedicated page with all 19 data types so the MCP server can query them.

**Architecture:**
```
Claude --> MCP (stdio) --> quantdata-mcp server --> QuantData REST API
```

Your credentials and tool IDs are stored locally at `~/.quantdata-mcp/config.json`.

## Commands

```bash
quantdata-mcp setup --auth-token <TOKEN> --instance-id <ID>  # One-time setup
quantdata-mcp serve                                           # Start MCP server (used by Claude)
```

## Requirements

- Python 3.11+
- Active [QuantData](https://quantdata.us) subscription

## Troubleshooting

**"Config not found" error:** Run `quantdata-mcp setup` first.

**Auth errors (401):** Your token expired. Get a new one from the Network tab and re-run setup. Your existing page and tools will be reused:
```bash
quantdata-mcp setup --auth-token "NEW_TOKEN" --instance-id "SAME_ID"
```

**Empty data:** Make sure you have an active QuantData subscription and the market was open on the date you're querying. For non-index tickers (AAPL, TSLA), make sure you set `expiration_date` to a valid options expiration.

**"No such file or directory" in Claude Desktop:** Use the full path to `quantdata-mcp` (see step 4 above).

## Related Projects

[fortress-mcp](https://github.com/citychip/fortress-mcp) is a companion MCP server for traders who use [Interactive Brokers](https://www.interactivebrokers.com) and manage options portfolios (PMCC, PCS, Jade Lizard strategies). It connects Claude Desktop to a live IBKR account via the [Fortress Dashboard](https://github.com/citychip/options-portfolio-strategy-dashboard-2026).

The two servers are **complementary** — quantdata-mcp provides market-wide options structure data; fortress-mcp provides portfolio-level context:

| | quantdata-mcp | fortress-mcp |
|---|---|---|
| **Data source** | QuantData API (market-wide) | Fortress Dashboard (live IBKR portfolio) |
| **What Claude can do** | GEX walls, order flow, IV skew, max pain for any ticker | Check positions, P&L, stop-loss signals, roll candidates, pre-trade gates |
| **Portfolio awareness** | None | Full |
| **QuantData depth** | Full real-time API | Summary only (parsed daily report files) |

Running both together lets Claude combine live market structure with portfolio context in a single prompt — for example: *"Check my MSFT PMCC stop-loss status and show me the current GEX walls."*

```json
{
  "mcpServers": {
    "quantdata": {
      "command": "quantdata-mcp",
      "args": ["serve"]
    },
    "fortress-dashboard": {
      "command": "python3",
      "args": ["/path/to/fortress-mcp/fortress_mcp.py"],
      "env": {
        "FORTRESS_API_URL": "http://YOUR_VPS_IP:8080",
        "FORTRESS_API_TOKEN": "YOUR_64_CHAR_TOKEN"
      }
    }
  }
}
```

---

## Contributing

Contributions are welcome — bug reports, new tools wrapping additional QuantData widgets, formatter improvements, and docs all help. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev environment setup, the recipe for adding a new MCP tool, and the conventions to follow when sending a PR.

## License

MIT — see [LICENSE](LICENSE).
