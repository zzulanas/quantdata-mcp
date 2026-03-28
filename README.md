# QuantData MCP Server

MCP server that gives AI agents (Claude Code, etc.) access to real-time and historical SPX 0DTE options market data from [QuantData](https://quantdata.us).

**Available data:** GEX/DEX/CEX/VEX exposure walls, net drift, max pain, IV rank, trade side statistics, open interest, net flow, contract statistics, and more.

## Quick Start

### 1. Install

```bash
# Basic install
pip install git+https://github.com/zzulanas/quantdata-mcp.git

# With browser login support (recommended)
pip install "quantdata-mcp[browser] @ git+https://github.com/zzulanas/quantdata-mcp.git"
playwright install chromium
```

### 2. Set Up (choose one method)

#### Option Zero: Let Claude Do It

If you've already added the MCP server to your config (step 3), just ask Claude:

> *"Log in to QuantData"*

Claude will call the `qd_login` tool, which opens a browser for you to log in. Credentials are captured automatically and setup runs. Done.

> Requires the browser extra: `pip install 'quantdata-mcp[browser]'` + `playwright install chromium`

#### Option A: Browser Login (easiest)

```bash
quantdata-mcp login
```

This opens a browser window to QuantData. Log in normally — your credentials are captured automatically from network requests. The browser closes once captured, then the setup runs.

#### Option B: Manual Setup

Get your credentials from the browser Network tab:

1. Open [v3.quantdata.us](https://v3.quantdata.us) and log in
2. Open DevTools (F12) → Network tab
3. Click any page/chart — look for requests to `core-lb-prod.quantdata.us`
4. From any request's headers, copy:
   - **`authorization`** header value (the JWT, starts with `eyJ...`)
   - **`x-instance-id`** header value (a UUID)

```bash
quantdata-mcp setup \
  --auth-token "eyJhbGci..." \
  --instance-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

### 3. Add to Claude

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

> **Note:** `quantdata-mcp` must be on your system PATH. If you installed into a virtualenv, use the full path instead:
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
| `qd_login` | Open browser to log in and configure (first-time setup or token refresh) |

### Example Usage

Ask Claude Code things like:

- *"What are the biggest GEX walls right now?"*
- *"Show me yesterday's DEX walls at 10:30 AM"*
- *"Pull up the trade side stats — are puts or calls more aggressive today?"*
- *"Compare the GEX profile at open vs close for last Thursday"*

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

The setup creates a page called "MCP Agentic Page" on your QuantData account with 11 tool widgets. Your credentials and tool IDs are stored locally at `~/.quantdata-mcp/config.json`.

## Commands

```bash
quantdata-mcp login              # Browser login + auto-setup (easiest)
quantdata-mcp setup --browser    # Same as login
quantdata-mcp setup --auth-token <TOKEN> --instance-id <ID>  # Manual setup
quantdata-mcp serve              # Start MCP server (used by Claude Code)
```

## Requirements

- Python 3.11+
- Active [QuantData](https://quantdata.us) subscription
- For browser login: `pip install 'quantdata-mcp[browser]'` + `playwright install chromium`

## Troubleshooting

**"Config not found" error:** Run `quantdata-mcp login` or `quantdata-mcp setup` first.

**Auth errors (401):** Your token expired. Re-run `quantdata-mcp login` to get a fresh one. Your existing page and tools will be reused.

**Empty data:** Make sure you have an active QuantData subscription and the market was open on the date you're querying.

**Browser login doesn't capture credentials:** Make sure you actually navigate to a page with charts after logging in. The server needs to see at least one API request to `core-lb-prod.quantdata.us`.

**Playwright not installed:**
```bash
pip install 'quantdata-mcp[browser]'
playwright install chromium
```

## License

MIT
