# QuantData MCP Server

MCP server that gives AI agents (Claude Code, Claude Desktop, etc.) access to real-time and historical SPX 0DTE options market data from [QuantData](https://quantdata.us).

**Available data:** GEX/DEX/CEX/VEX exposure walls, net drift, max pain, IV rank, trade side statistics, open interest, net flow, contract statistics, and more.

## Quick Start

### 1. Install

You need **Python 3.11+** installed. Check with `python3 --version`.

- **Mac:** `brew install python` (or download from [python.org](https://www.python.org/downloads/))
- **Windows:** Download from [python.org](https://www.python.org/downloads/) (check "Add to PATH" during install)

Then install the package:

```bash
# With pip
pip install git+https://github.com/zzulanas/quantdata-mcp.git

# With uv (faster)
uv pip install git+https://github.com/zzulanas/quantdata-mcp.git
```

> **Don't have uv?** Install it with `curl -LsSf https://astral.sh/uv/install.sh | sh` (Mac/Linux) or `irm https://astral.sh/uv/install.ps1 | iex` (Windows). It's a faster alternative to pip.

### 2. Get Your Credentials

You need two values from your QuantData account. Open your browser:

1. Go to [v3.quantdata.us](https://v3.quantdata.us) and **log in**
2. Open **DevTools** (F12 or right-click → Inspect) → **Network** tab
3. Refresh the page
4. Click on any chart or page on QuantData — you'll see API requests appear
5. Click any request to `core-lb-prod.quantdata.us`
6. In the **Request Headers**, find and copy:
   - **`authorization`** — your auth token (starts with `eyJ...`)
   - **`x-instance-id`** — your instance ID (a UUID like `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
  
Should look like these:
<img width="2093" height="1146" alt="Screenshot 2026-03-27 at 8 49 30 PM 1" src="https://github.com/user-attachments/assets/51856fbb-7f22-458e-8478-8906f8792a54" />


### 3. Run Setup

```bash
quantdata-mcp setup \
  --auth-token "eyJhbGci..." \
  --instance-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

This creates a dedicated page on your QuantData account with 11 data tools and saves your config to `~/.quantdata-mcp/config.json`.

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

### Example Usage

Ask Claude things like:

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
Claude → MCP (stdio) → quantdata-mcp server → QuantData REST API
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

**Empty data:** Make sure you have an active QuantData subscription and the market was open on the date you're querying.

**"No such file or directory" in Claude Desktop:** Use the full path to `quantdata-mcp` (see step 4 above).

## License

MIT
