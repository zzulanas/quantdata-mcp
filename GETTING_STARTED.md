# Getting Started with QuantData MCP

This is the friendly, step-by-step version of the README. If you'd rather skim a reference, the [README](README.md) has the full tool inventory and parameter docs. If you're starting from scratch and want to be making real queries in 15 minutes, you're in the right place.

> Heads up: this is an **unofficial** project. It's not affiliated with QuantData. You'll need an active QuantData subscription to use it.

---

## What you're getting

QuantData MCP is a small Python program that lets Claude (Code or Desktop) pull live and historical options market data from your QuantData account. Once it's wired up, you can ask Claude things like *"where are the biggest gamma walls right now?"* or *"compare yesterday's max pain to today's"* in plain English, and Claude will fetch the data and lay it out for you.

It works for any optionable ticker — SPX, SPY, QQQ, AAPL, TSLA, NVDA, you name it — and covers GEX/DEX/CEX/VEX exposure walls, exposure term structure, net drift, max pain, IV rank, trade-side aggression, open interest, net flow, consolidated order flow, contract OHLCV, and contract statistics.

Here's what a typical conversation looks like:

> **You:** What are the biggest GEX walls right now for SPX?

> **Claude:** *(calls `qd_get_exposure_by_strike`)*

<!-- If you change _fmt_walls in quantdata_mcp/server.py, update the example below to match. -->

```
GEX (Gamma Exposure) — SPX $5,612.40

    Strike    Net ($M)   Call ($M)    Put ($M)    Type
--------------------------------------------------------
   $5,600       +482.13      612.40     -130.27    CALL
   $5,650       -318.55       91.22     -409.77     PUT
   $5,500       -284.10       45.88     -329.98     PUT
   $5,700       +211.74      268.90      -57.16    CALL
   $5,550       -167.42       82.11     -249.53     PUT
   ...
```

> Big call wall sitting at 5,600 — that's where dealers are most short gamma and price tends to act like a magnet. Below 5,500 the put walls take over and dealers flip into negative-gamma territory.

That's the loop. Ask, get data, ask follow-ups.

---

## Before you begin (5-minute checklist)

You need three things:

1. **An active QuantData subscription.** Check at [quantdata.us](https://quantdata.us). The MCP server uses your normal account credentials — there's no separate API key.
2. **Python 3.11 or newer.** Run `python3 --version` in a terminal. If it's 3.11+, you're set.
   - **Mac:** `brew install python` (or grab the installer from [python.org](https://www.python.org/downloads/))
   - **Windows:** [python.org installer](https://www.python.org/downloads/) — make sure to tick "Add Python to PATH" during install
   - **Linux:** Your distro probably has it; otherwise `sudo apt install python3.11` or equivalent
3. **A Claude product to wire it into.** Either:
   - **Claude Code** — Anthropic's CLI / IDE plugin for coding. Best if you live in a terminal already. ([docs](https://claude.com/claude-code))
   - **Claude Desktop** — the regular Claude chat app for Mac/Windows. Best if you just want to chat with the data. ([download](https://claude.ai/download))

Either works fine. Pick whichever you already use.

---

## Step 1: Install Python and the package

The package isn't on PyPI — you install it directly from GitHub. The recommended way is with [uv](https://github.com/astral-sh/uv), a modern Python installer that's much faster than pip and handles isolated environments automatically.

### Install uv (recommended)

**Mac / Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

Close and reopen your terminal so `uv` is on your PATH.

### Install the package

```bash
uv pip install git+https://github.com/zzulanas/quantdata-mcp.git
```

### Or use pip if you'd rather

```bash
pip install git+https://github.com/zzulanas/quantdata-mcp.git
```

### Verify

```bash
quantdata-mcp --help
```

You should see two subcommands: `setup` and `serve`. If your shell can't find `quantdata-mcp`, see the [PATH gotcha](#step-4b-wire-it-into-claude-desktop) section — it's usually a missing entry in your shell PATH.

---

## Step 2: Get your QuantData credentials

QuantData doesn't publish an official API, so the MCP server logs in by reusing the credentials your browser already has. You'll grab two values from your browser's network tab and pass them to `quantdata-mcp setup`. This takes about two minutes.

1. Go to [v3.quantdata.us](https://v3.quantdata.us) and **log in** with your normal account.
2. Open **DevTools**:
   - **Mac Chrome/Brave/Edge:** `Cmd + Option + I`, or right-click anywhere → **Inspect**
   - **Windows Chrome/Brave/Edge:** `F12`, or right-click → **Inspect**
   - **Safari:** Enable Develop menu first (Settings → Advanced → "Show Develop menu"), then `Cmd + Option + I`
3. Click the **Network** tab in DevTools.
4. **Refresh the page.** You'll see a flood of network requests appear.
5. In the filter bar, type `api` to narrow it down. Look for any request to `core-lb-prod.quantdata.us`.
6. Click on one of those requests. In the right-hand pane, find the **Headers** section, then scroll to **Request Headers**.
7. Copy these two values:
   - **`authorization`** — a very long string starting with `eyJ...`. This is your auth token (a JWT).
   - **`x-instance-id`** — a UUID like `a1b2c3d4-e5f6-7890-abcd-ef1234567890`.

Stash both somewhere safe for the next step (a notes app, password manager, whatever). You'll paste them on the command line in a second.

> The README has a screenshot of what this looks like in DevTools — see the [credentials section](README.md#2-get-your-credentials) if you want a visual reference.

> **Tokens expire.** When that happens (usually after a few hours to a day), you'll start getting `401 Unauthorized` errors. Just go back to your browser, grab a fresh `authorization` value, and re-run `quantdata-mcp setup`. As long as your local config at `~/.quantdata-mcp/config.json` exists and its tools still resolve on the server, setup reuses your existing page — it doesn't matter whether the `--instance-id` is the same or different. Refreshing the token takes about 30 seconds.

---

## Step 3: Run setup once

With your two values copied, run:

```bash
quantdata-mcp setup \
  --auth-token "eyJhbGciOi..." \
  --instance-id "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

(Replace the values with the ones you copied. The whole token goes in quotes — it's long, that's fine.)

### What this does

The setup command does several things:

1. **Validates your credentials** by listing your existing QuantData pages.
2. **Creates (or reuses) a hidden "MCP Agentic Page"** on your QuantData account. This page holds 11 chart widgets — one per data type the MCP server queries. You can ignore this page in the QuantData UI; it exists purely as a container the MCP server reads from.
3. **Creates each tool widget** on the page (or skips ones that already exist).
4. **Sets the page filter** to SPX for today's session.
5. **Updates the page layout** so the tools appear as tabs in the UI.
6. **Saves your config** to `~/.quantdata-mcp/config.json` so future `serve` invocations know how to authenticate.

### What success looks like

The setup command prints progress to stderr. A fresh run looks roughly like this (truncated for brevity):

<!-- If you change run_setup in quantdata_mcp/setup.py, update the example below to match. -->

```
Setting up QuantData MCP...
  Validating credentials... OK (3 pages found)
  Creating page... OK (a1b2c3d4e5f6...)
  Creating tool: Exposure by Strike (GEX/DEX/CEX/VEX)... OK (11ab22cd33ef...)
  Creating tool: Net Drift... OK (...)
  Creating tool: IV Rank... OK (...)
  Creating tool: Contract Side Statistics... OK (...)
  Creating tool: Max Pain... OK (...)
  Creating tool: Net Flow... OK (...)
  Creating tool: Order Flow (Consolidated)... OK (...)
  Creating tool: Open Interest by Strike... OK (...)
  Creating tool: Contract Statistics... OK (...)
  Creating tool: Exposure by Expiration... OK (...)
  Creating tool: Contract Price / Time... OK (...)
  Setting page filter: SPX, 2026-05-02... OK
  Updating page layout... OK

  Config saved to ~/.quantdata-mcp/config.json

==================================================
Setup complete!
==================================================
```

If you re-run setup with an existing local config, you'll see `Found existing config (page: ...)` near the top and `Reusing existing page: ...` instead of a fresh `Creating page...` line, with each tool reported as `already exists, skipping`.

### Troubleshooting

| Failure point | Likely cause | Fix |
|---|---|---|
| `Validating credentials... FAILED: ...` | Bad/expired auth token, or the token didn't get fully copied (they're long — easy to truncate) | Re-grab the `authorization` header from DevTools and run setup again |
| `Creating page... FAILED` | Subscription issue or QuantData account permissions | Confirm your subscription is active at quantdata.us; contact QuantData support if it persists |
| `Creating tool: ... FAILED` | Transient API error | Just re-run the setup command — it's idempotent and will reuse the page and any tools that were already created |

Re-running setup is safe: if the local config at `~/.quantdata-mcp/config.json` already exists *and* its tool IDs still resolve on the server, setup reuses that page and only creates any tools that are missing. Otherwise it creates a fresh page. That's exactly what you want when you're refreshing an expired token.

---

## Step 4a: Wire it into Claude Code

If you're using **Claude Desktop**, skip to [Step 4b](#step-4b-wire-it-into-claude-desktop).

Claude Code reads MCP server definitions from one of two places:

- `.mcp.json` in your project root (project-scoped — only available in that project)
- `~/.claude/mcp.json` (global — available everywhere)

Pick whichever you want. For most people, global is more convenient. Add this:

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

If the file already exists with other servers, just add `quantdata` as a new key inside `mcpServers`.

Restart Claude Code (quit and relaunch, or run `/mcp` to reload). Then type `/mcp` in a chat — you should see `quantdata` listed with 13 tools available.

---

## Step 4b: Wire it into Claude Desktop

Claude Desktop reads MCP servers from a single config file:

- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

If the file doesn't exist yet, create it. Add this:

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

If you already have other MCP servers in there, just add `quantdata` alongside them.

**Quit Claude Desktop entirely and relaunch it** (not just close the window — fully quit). The QuantData tools will appear in the tool picker.

### PATH gotcha (the most common Claude Desktop issue)

Claude Desktop on Mac doesn't always inherit your shell PATH, so it might not find `quantdata-mcp` even though it works fine in your terminal. If you see "command not found" errors in Claude Desktop's logs, find the absolute path:

```bash
# Mac / Linux:
which quantdata-mcp
# /Users/you/.local/bin/quantdata-mcp

# Windows (PowerShell or cmd):
where quantdata-mcp
# C:\Users\you\AppData\Local\Programs\Python\Python311\Scripts\quantdata-mcp.exe
```

Then use that full path in the config:

```json
{
  "mcpServers": {
    "quantdata": {
      "command": "/Users/you/.local/bin/quantdata-mcp",
      "args": ["serve"]
    }
  }
}
```

**Or skip the PATH issue entirely** by using `uvx`, which downloads and runs the package on demand:

```json
{
  "mcpServers": {
    "quantdata": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/zzulanas/quantdata-mcp.git", "quantdata-mcp", "serve"]
    }
  }
}
```

This works reliably as long as `uvx` itself is on PATH (it usually is once you install `uv`).

---

## Step 5: Your first query

Open a fresh chat in Claude Code or Claude Desktop and try one of these. Each one teaches you something different about how the system works.

### 1. The everything-at-once overview

> Show me a market snapshot for SPX

This calls `qd_get_market_snapshot`, which fans out and pulls GEX walls, DEX walls, drift, max pain, and trade-side stats in one shot. It's the fastest way to see "what's the market doing right now?" and a great first query to confirm the setup is working.

### 2. Walls on a specific ticker

> What are the biggest GEX walls right now?

Defaults to SPX 0DTE. You'll get something like:

<!-- If you change _fmt_walls in quantdata_mcp/server.py, update the example below to match. -->

```
GEX (Gamma Exposure) — SPX $5,612.40

    Strike    Net ($M)   Call ($M)    Put ($M)    Type
--------------------------------------------------------
   $5,600       +482.13      612.40     -130.27    CALL
   $5,650       -318.55       91.22     -409.77     PUT
   $5,500       -284.10       45.88     -329.98     PUT
   $5,700       +211.74      268.90      -57.16    CALL
   $5,550       -167.42       82.11     -249.53     PUT
   $5,575        -98.21       33.40     -131.61     PUT
   $5,625       +87.04       142.18      -55.14    CALL
   $5,475       -71.88       18.92      -90.80     PUT
   $5,800       +64.50       102.30      -37.80    CALL
   $5,400       -52.16       11.04      -63.20     PUT
```

The "Net" column shows dealer net exposure per 1% move. Big positive numbers = call walls (resistance), big negative numbers = put walls (support). A natural follow-up: *"How does that compare to yesterday at the same time?"* — Claude will use `qd_set_page_date` and re-query.

### 3. Filtering order flow

> Pull up the order flow — just calls with premium over $50K

This shows off the filter parameters. Under the hood Claude is calling `qd_get_order_flow(contract_type="CALL", min_premium=50000)`. You'll see the largest call trades hitting the tape, with side codes like `AA` (above ask, aggressive buy) and `BB` (below bid, aggressive sell).

<!-- If you change _fmt_order_flow in quantdata_mcp/server.py, update the example below to match. -->

```
Order Flow — Last 12 entries (of 4,891 total)

        Time  Ticker      Strike  Type  Side       Premium      Size   Sentiment
----------------------------------------------------------------------------------
    14:32:18    SPX     $5,650     C    AA      $128,400       240     BULLISH
    14:30:55    SPX     $5,600     C     A       $94,200       180     BULLISH
    14:28:11    SPX     $5,700     C    AA       $76,500        95     BULLISH
    14:25:43    SPX     $5,625     C     M       $58,300       110     NEUTRAL
    ...
```

### 4. Time-traveling

> Compare yesterday's max pain to today's

Claude will run `qd_get_max_pain` twice — once with `date=` set to today and once with yesterday. Useful for tracking how dealer hedging zones drift session-over-session.

### 5. Equity options on a non-0DTE expiration

> Show me TSLA gamma walls for the next monthly expiration (May 15)

This is the multi-ticker, non-default-expiration case. Equity options like TSLA don't have daily expirations — you have to point Claude at a real expiration date, like the third Friday of the month. Claude will call `qd_get_exposure_by_strike(ticker="TSLA", expiration_date="2026-05-15", greek_type="GAMMA")`.

If you forget the expiration on an equity ticker, you'll get empty data back — see [Common gotchas](#common-gotchas) below.

---

## Understanding the data: a 90-second primer

You don't need to be a quant to use this, but a few of these terms will make Claude's responses make a lot more sense:

- **GEX (Gamma Exposure)** — Where dealer hedging is most concentrated by strike. Big GEX walls act like price magnets (when dealers are long gamma) or amplifiers (when they're short gamma). Watching where GEX is biggest tells you where price is likely to get stuck or break out.
- **DEX (Delta Exposure)** — The directional skew of dealer positioning. A strongly negative DEX wall at a strike means dealers are net short calls / long puts there.
- **Net Drift** — Cumulative call premium minus cumulative put premium throughout the session. Positive and growing = bullish flow. Negative = bearish. The sign and slope tell you where the money is leaning.
- **Max Pain** — The strike at which the most options expire worthless. Folklore (and a fair bit of empirical evidence) says price tends to gravitate toward max pain into expiration as dealers hedge.
- **IV Rank** — Where current implied volatility sits in its historical range, expressed 0–100%. IVR 80% means IV is near the top of its recent range (options are relatively expensive); IVR 20% means it's near the bottom (relatively cheap).
- **Trade Side (AA / A / M / B / BB)** — How aggressive each trade was relative to the bid/ask spread. **AA** = above ask (aggressive buy), **A** = at ask, **M** = mid, **B** = at bid, **BB** = below bid (aggressive sell). The AA/BB ratio is a quick read on market urgency.

For the full list of tools and what each one returns, see the [Available Tools](README.md#available-tools) table in the README.

---

## Common gotchas

A handful of things bite almost every new user — get these out of the way upfront:

### Equity options need an explicit `expiration_date`

SPX, SPY, and QQQ have **daily** expirations, so the default behavior (expiration = today) gives you 0DTE data. Equity options like AAPL, TSLA, NVDA don't expire daily — you have to specify a real expiration, usually the third Friday of the month.

```
# This works (SPX has 0DTE):
> Show me SPX gamma walls

# This will return empty data:
> Show me TSLA gamma walls

# This works:
> Show me TSLA gamma walls for the May 15 expiration
```

### Trading days only

`date` parameters must be a valid trading day. No weekends. No market holidays. If you ask for July 4th data you'll get an error or empty data — pick the trading day before or after.

### All times are Eastern (ET)

Drift entries, time scrubbing (`time_minutes`), session boundaries — everything is keyed to Eastern Time. `time_minutes=570` means 9:30 AM ET (market open). `time_minutes=960` means 4:00 PM ET (close). Data is keyed by ET session date, so a query for "today" after midnight UTC but before 9:30 AM ET is asking about a session that hasn't started yet.

> Note: timestamps in formatted tool output are shown in ET as of v0.2 (PR #4). Earlier versions render some columns (order flow, net flow, contract price) in UTC — if you're on an older build, mentally add/subtract the offset.

### Tokens expire

When you start seeing 401 errors in Claude's tool responses, your `authorization` token has expired. Fix:

```bash
quantdata-mcp setup \
  --auth-token "NEW_TOKEN_FROM_DEVTOOLS" \
  --instance-id "YOUR_INSTANCE_ID"
```

Reuse is keyed on your local config file (`~/.quantdata-mcp/config.json`), not on `--instance-id`. As long as that file exists and the tools it references still exist on the server, setup will reuse the same page and tools — only the token changes. Using a fresh `--instance-id` is fine too.

### Claude Desktop can't find `quantdata-mcp`

Mac Claude Desktop doesn't always inherit your shell PATH. If you see "command not found" in Claude Desktop logs, switch to a full path or `uvx` invocation as shown in [Step 4b](#step-4b-wire-it-into-claude-desktop).

### Empty data with no error

Almost always one of:
- Querying a non-trading day
- Querying an equity ticker without `expiration_date`
- Querying outside market hours on a date with no aftermarket data
- An expired token (which sometimes manifests as empty data instead of a clean 401)

---

## Where to go next

- **[README](README.md)** — full tool inventory, every parameter, and the architecture overview.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — if you want to add a tool, file a bug, or send a PR.
- **[GitHub Issues](https://github.com/zzulanas/quantdata-mcp/issues)** — for bug reports and feature requests. Include the tool you called, the arguments, and the response (with token redacted) for fastest help.

Once you're comfortable, try chaining queries — *"pull max pain, then check the GEX walls within $50 of it, then show me order flow on those strikes."* That's where the MCP setup really shines: Claude can plan and execute multi-step research that would take you a dozen clicks in the QuantData UI.

Happy trading.
