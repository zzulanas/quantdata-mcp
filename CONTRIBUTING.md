# Contributing to QuantData MCP

Thanks for your interest in contributing! This server is reverse-engineered against [QuantData](https://quantdata.us)'s internal REST API, so contributions that improve resilience, add new tools, or smooth out the developer experience are all welcome.

This guide covers how the project is organized, how to set up a dev environment, the recipe for adding a new MCP tool, and the conventions to follow when sending a PR.

---

## Code of conduct

Be kind, be useful, assume good faith. Bug reports and PRs are equally valuable.

---

## Ways to contribute

- **Bug reports** — open an [issue](https://github.com/zzulanas/quantdata-mcp/issues) with the failing tool call, what you expected, and the error returned. If the QuantData API shape has shifted, include a sample response payload (with credentials redacted).
- **New tools** — wrap an additional QuantData chart/widget as an MCP tool (see [Adding a new tool](#adding-a-new-tool) below).
- **Output formatting** — the `_fmt_*` helpers in `server.py` turn raw API responses into LLM-friendly text. PRs that make output denser, clearer, or more accurate are great.
- **Docs** — clarifications, additional ticker examples, troubleshooting entries.
- **Tests** — there are no tests yet. A small fixture-based test suite would be a high-leverage first contribution.

---

## Project layout

```
quantdata_mcp/
  __init__.py        package version
  __main__.py        CLI dispatcher: `quantdata-mcp serve` / `setup`
  config.py          load/save ~/.quantdata-mcp/config.json (auth, page id, tool ids)
  tools.py           ToolType / ToolDefinition registry — the 11 widgets created during setup
  setup.py           setup wizard: validates creds → creates page + tools → saves config
  client.py          QuantDataClient — wraps the QuantData REST API (auth, retries, fetches)
  server.py          FastMCP server — defines @mcp.tool() functions and output formatters
```

**Mental model:** during `setup`, we create a hidden page on the user's QuantData account containing one widget per data type. At runtime, each MCP tool call mutates that page's filters (date / ticker / expiration / moneyness / strike), fetches the data, then restores the filters. The tool IDs are user-specific UUIDs persisted to `~/.quantdata-mcp/config.json`.

If you change anything in `tools.py`, existing users will need to re-run `quantdata-mcp setup` to provision the new tool — bear that in mind for backwards compatibility.

---

## Dev environment

Requirements: **Python 3.11+** and [uv](https://docs.astral.sh/uv/) (recommended) or pip.

```bash
# Clone
git clone https://github.com/zzulanas/quantdata-mcp.git
cd quantdata-mcp

# Install in editable mode with uv
uv venv
source .venv/bin/activate
uv pip install -e .

# Or with pip
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
```

You'll need an active [QuantData](https://quantdata.us) account to actually call the API. Pull your `authorization` token and `x-instance-id` header out of the network tab (see the [README](README.md#2-get-your-credentials) for the click-by-click) and run setup once:

```bash
quantdata-mcp setup --auth-token "eyJhbGci..." --instance-id "xxxx-xxxx-..."
```

To keep your dev config separate from your real one, point at a different config dir:

```bash
export QUANTDATA_MCP_CONFIG_DIR=$PWD/.dev-config
quantdata-mcp setup --auth-token "..." --instance-id "..."
```

`config.py` honors that env var (it falls back to `~/.quantdata-mcp` otherwise).

---

## Running the server locally

The server speaks MCP over stdio, so the easiest way to drive it during development is to wire it into Claude Code or Claude Desktop pointed at your editable checkout:

```json
{
  "mcpServers": {
    "quantdata-dev": {
      "command": "/absolute/path/to/quantdata-mcp/.venv/bin/quantdata-mcp",
      "args": ["serve"],
      "env": {
        "QUANTDATA_MCP_CONFIG_DIR": "/absolute/path/to/quantdata-mcp/.dev-config"
      }
    }
  }
}
```

Restart your client and you should see `quantdata-dev` listed alongside any other MCP servers.

For raw protocol debugging, you can drive the server by hand with the [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector quantdata-mcp serve
```

### Logging

`stdout` is reserved for the MCP JSON-RPC transport — never `print()` to stdout from server code, or you'll corrupt the protocol stream. `client.py` already routes its `logging` handler to `stderr`; mirror that pattern in any new code:

```python
import logging
import sys

logger = logging.getLogger(__name__)
if not logger.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(h)
```

To see logs from your client, raise the level (`logger.setLevel(logging.DEBUG)`) and check the client's MCP-server logs.

---

## Adding a new tool

Every tool that wraps a QuantData widget follows the same shape. Here's the recipe.

### 1. Register the widget type

Add an entry to `quantdata_mcp/tools.py`:

```python
class ToolType(str, Enum):
    ...
    MY_NEW_CHART = "OPTIONS_MY_NEW_CHART"   # exact backend identifier

TOOL_DEFINITIONS = {
    ...
    "my_new_tool": ToolDefinition(
        canonical_name="my_new_tool",
        tool_type=ToolType.MY_NEW_CHART,
        endpoint="options/my-new-endpoint",
        label="My New Tool",
    ),
}
```

`endpoint` is the REST path stem; the client appends `/{tool_id}` when fetching. `label` shows up as the tab name on the QuantData page.

### 2. Add a fetch method to the client (if needed)

If the new endpoint behaves like the existing ones (`GET options/<endpoint>/<tool_id>`), the generic `fetch_tool_data(tool_spec)` works without changes. Otherwise add a dedicated method to `QuantDataClient`:

```python
def fetch_my_new_data(self, tool_id: str) -> dict[str, Any] | None:
    try:
        response = self._make_request("GET", f"options/my-new-endpoint/{tool_id}", timeout=30)
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch my new data: {e}")
        return None
```

### 3. Write a formatter

Formatters live alphabetically below the existing `_fmt_*` helpers in `server.py`. Keep them text-only and aimed at LLM consumption — wide aligned columns are fine, but avoid ANSI escapes or anything that won't render in a chat surface:

```python
def _fmt_my_new_data(data: dict[str, Any] | None, ticker: str = "SPX") -> str:
    if not data or "response" not in data:
        return "No data available."
    resp = data["response"]
    # ...build readable output...
    return "\n".join(lines)
```

### 4. Expose the MCP tool

Add the `@mcp.tool()` function. Mirror the existing pattern: apply page filter → apply tool filter (if any) → fetch → restore filters in `finally`:

```python
@mcp.tool()
def qd_get_my_new_data(
    ticker: str = "SPX",
    date: str | None = None,
    expiration_date: str | None = None,
) -> str:
    """One-line summary the LLM will read.

    Longer description: when to call this, what the output means.

    Args:
        ticker: Ticker symbol (default: SPX).
        date: Session date YYYY-MM-DD (default: today).
        expiration_date: Expiration YYYY-MM-DD (default: 0DTE).
    """
    try:
        c = _get_client()
        changed = _apply_page_filter(date, ticker, expiration_date)
        tool = _get_specs()["my_new_tool"]
        try:
            data = c.fetch_my_new_data(tool.tool_id)
        finally:
            _restore_page_filter(changed)
        return _fmt_my_new_data(data, ticker=ticker)
    except Exception as e:
        return f"Error fetching my new data: {e}"
```

The docstring is the **tool description the LLM sees**. Be explicit about defaults, units (dollars vs cents, premium vs volume), and any ticker-specific gotchas (SPX/SPY/QQQ have daily expirations; equities don't).

### 5. Re-run setup against your dev config

`setup.py` walks `TOOL_DEFINITIONS` and creates any missing tools, so:

```bash
quantdata-mcp setup --auth-token "..." --instance-id "..."
```

picks up your new entry and provisions the widget on your page. Existing users will need to do the same after the change ships.

### 6. Smoke test

Drive the new tool through your MCP client and verify both the happy path (default args) and at least one filtered call (e.g. with `expiration_date` set, or with `moneyness=["OTM"]` if applicable).

---

## Conventions

### Style

- Format with [ruff](https://docs.astral.sh/ruff/) if you have it (`ruff format .`); otherwise match the surrounding style — 4-space indent, double quotes, `from __future__ import annotations` at the top of every module.
- Type hints everywhere. The codebase targets Python 3.11+, so use the modern syntax: `dict[str, Any]`, `list[float]`, `str | None`, etc.
- Default to no comments. Add one only when the *why* is non-obvious (e.g. workarounds, hidden constraints).

### Output for LLMs

- All `_fmt_*` helpers return plain text. Keep tables narrow (≤ 80 chars where possible) and prefer a short header + aligned columns.
- Always include the ticker and price in the output where relevant — the LLM may not retain context between tool calls.
- Prefer human-readable scaling: `$1.2M` over `$1,234,567`, `$5.40` over `540` cents. Convert `*InCents` fields explicitly (`/100`).

### Error handling

- Each `@mcp.tool()` function wraps its body in `try / except Exception` and returns a `f"Error ...: {e}"` string. Errors are part of the LLM's context — don't raise.
- Inside `client.py`, log the error and return `None`/`False`. Let callers decide how to surface it.

### Filter restore

If you mutate metadata or filters on a tool, restore them in a `finally`. Defaults are scattered through `server.py`; capture the original via `c.get_tool(tool_id)` first when in doubt.

### Commits & PRs

- Use Conventional Commits-ish prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`. Match the existing log style — past commits are good examples.
- One logical change per PR. If you're adding a tool *and* refactoring shared helpers, split it.
- Update the README's "Available Tools" table whenever you add or remove an MCP tool.
- If your change requires existing users to re-run `quantdata-mcp setup`, call that out in the PR description.

---

## Reverse-engineered API caveats

QuantData has no public API. Endpoints, response shapes, and filter keys can change without notice. If a tool starts returning empty data:

1. Open `https://v3.quantdata.us` in your browser, refresh the relevant chart, and inspect the request in DevTools → Network.
2. Compare the request URL, headers, and JSON body to what `client.py` sends.
3. Compare the response shape to what the formatter expects.
4. Patch the affected `fetch_*` method or `_fmt_*` formatter.

When you discover a shape change, document it in your PR — it helps future debuggers.

---

## Releasing

Maintainers only:

1. Bump `__version__` in `quantdata_mcp/__init__.py` and `version` in `pyproject.toml`.
2. Tag the commit: `git tag vX.Y.Z && git push --tags`.
3. Users install with `uv pip install git+https://github.com/zzulanas/quantdata-mcp.git@vX.Y.Z`.

---

## Questions

Open a [discussion](https://github.com/zzulanas/quantdata-mcp/discussions) or an issue. Thanks for contributing!
