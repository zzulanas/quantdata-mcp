# QuantData MCP Server

MCP server that gives AI agents (Claude Code, etc.) access to real-time and historical SPX 0DTE options market data from [QuantData](https://quantdata.us).

**Available data:** GEX/DEX/CEX/VEX exposure walls, net drift, max pain, IV rank, trade side statistics, open interest, net flow, contract statistics, and more.

## Quick Start

### 1. Install

```bash
pip install quantdata-mcp
# or
uv pip install quantdata-mcp
```

### 2. Get Your Credentials

You need two values from your QuantData account:

**Auth Token (JWT):**
1. Open [v3.quantdata.us](https://v3.quantdata.us) and log in
2. Open browser DevTools (F12) -> Network tab
3. Click on any page/chart — look for API requests to `core-lb-prod.quantdata.us`
4. Click any request, find the `authorization` header in Request Headers
5. Copy the full value (starts with `eyJ...`)

**Instance ID:**
1. In the same request, find the `x-instance-id` header
2. Copy the UUID value (format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)

### 3. Run Setup

```bash
quantdata-mcp setup \
  --auth-token "eyJhbGci..." \
  --instance-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

This creates a dedicated page on your QuantData account with 11 data tools, and saves your config to `~/.quantdata-mcp/config.json`.

### 4. Add to Claude Code

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

## Available Tools

| Tool | Description |
|------|-------------|
| `qd_get_market_snapshot` | Full overview: GEX + DEX walls, drift, max pain, trade stats |
| `qd_get_exposure_by_strike` | GEX/DEX/CEX/VEX wall data (switchable greek type) |
| `qd_get_net_drift` | Cumulative call vs put premium flow |
| `qd_get_trade_side_stats` | Trade aggression: AA/A/M/B/BB breakdown for calls/puts |
| `qd_get_max_pain` | Max pain strike + distance from current price |
| `qd_get_iv_rank` | Implied volatility rank vs historical range |
| `qd_get_net_flow` | Call/put premium flow over time |
| `qd_get_oi_by_strike` | Open interest distribution with near-ATM filtering |
| `qd_get_contract_statistics` | Total premium, trade count, volume by call/put |
| `qd_set_page_date` | Switch to a historical date for analysis |

### Historical Data

All tools support historical analysis. Use `qd_set_page_date` to switch to a past trading day, then call any tool:

```
> Set the date to 2026-03-26 and show me the GEX walls at 10:00 AM

> qd_set_page_date(date="2026-03-26")
> qd_get_exposure_by_strike(greek_type="GAMMA", time_minutes=600)
```

Time scrubbing: `time_minutes` = minutes from midnight (570 = 9:30 AM, 720 = 12:00 PM, 960 = 4:00 PM).

## How It Works

QuantData doesn't have an official API. This server uses reverse-engineered REST endpoints from their web app. Each user has "tools" (chart widgets) on "pages" — the setup command creates a dedicated page with all 11 data types so the MCP server can query them.

**Architecture:**
```
Claude Code → MCP (stdio) → quantdata-mcp server → QuantData REST API
```

## Requirements

- Python 3.11+
- Active [QuantData](https://quantdata.us) subscription
- The auth token may expire periodically — re-run `quantdata-mcp setup` with a fresh token if you get auth errors

## Troubleshooting

**"Config not found" error:** Run `quantdata-mcp setup` first.

**Auth errors (401):** Your token expired. Get a new one from the Network tab and re-run setup. Your existing page and tools will be reused.

**Empty data:** Make sure you have an active QuantData subscription and the market was open on the date you're querying.

**Token refresh:** Re-run setup with the new token — it will reuse your existing page and tools:
```bash
quantdata-mcp setup --auth-token "NEW_TOKEN" --instance-id "SAME_ID"
```

## License

MIT
