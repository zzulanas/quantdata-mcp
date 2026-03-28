"""Setup wizard — creates the agentic page and 11 tools for a new user."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from quantdata_mcp.client import QuantDataClient
from quantdata_mcp.config import Config, config_exists, load_config, save_config
from quantdata_mcp.tools import TOOL_DEFINITIONS


def run_setup(auth_token: str, instance_id: str) -> None:
    """Create page, tools, and save config."""
    print("Setting up QuantData MCP...")

    # Check existing config
    existing_config: Config | None = None
    if config_exists():
        try:
            existing_config = load_config()
            print(f"  Found existing config (page: {existing_config.page_id[:12]}...)")
        except Exception:
            existing_config = None

    client = QuantDataClient(
        auth_token=auth_token,
        instance_id=instance_id,
        max_retries=2,
        retry_delay=0.5,
    )

    # Step 1: Validate credentials
    print("  Validating credentials...", end=" ")
    try:
        pages = client.get_pages()
        print(f"OK ({len(pages)} pages found)")
    except Exception as e:
        print(f"FAILED: {e}")
        print("\nCheck your auth token and instance ID.")
        sys.exit(1)

    # Step 2: Create or reuse page
    page_id = ""
    if existing_config and existing_config.page_id:
        # Verify the page still exists
        tool_check = client.get_tool(
            next(iter(existing_config.tools.values()), "nonexistent")
        )
        if tool_check:
            page_id = existing_config.page_id
            print(f"  Reusing existing page: {page_id[:12]}...")
        else:
            print("  Existing page/tools not found, creating new...")

    if not page_id:
        print("  Creating page...", end=" ")
        page = client.create_page(
            name="MCP Agentic Page",
            description=f"Created by quantdata-mcp setup on {datetime.now(UTC).strftime('%Y-%m-%d')}",
        )
        if not page:
            print("FAILED")
            sys.exit(1)
        page_id = page.get("id", "")
        print(f"OK ({page_id[:12]}...)")

    # Step 3: Create tools
    tool_ids: dict[str, str] = {}
    if existing_config and existing_config.tools and existing_config.page_id == page_id:
        tool_ids = dict(existing_config.tools)

    for name, defn in TOOL_DEFINITIONS.items():
        if name in tool_ids:
            print(f"  Tool '{defn.label}' already exists, skipping")
            continue

        print(f"  Creating tool: {defn.label}...", end=" ")
        result = client.create_tool(
            page_id=page_id,
            tool_type=defn.tool_type.value,
        )
        if result:
            resp = result.get("response", {}).get("toolDTO", result)
            tid = resp.get("id", "")
            if tid:
                tool_ids[name] = tid
                print(f"OK ({tid[:12]}...)")
            else:
                print(f"OK (unexpected response shape)")
                tool_ids[name] = str(result)
        else:
            print("FAILED")

    # Step 4: Set page filter (SPX, today)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    print(f"  Setting page filter: SPX, {today}...", end=" ")
    client.set_page_filter(page_id, session_date=today, ticker="SPX")
    print("OK")

    # Step 5: Update page layout with all tools as tabs
    print("  Updating page layout...", end=" ")
    tab_tools = []
    for name, tid in tool_ids.items():
        defn = TOOL_DEFINITIONS.get(name)
        if defn:
            tab_tools.append((tid, defn.label, defn.tool_type.value))
    client.update_page_layout(page_id, tab_tools)
    print("OK")

    # Step 6: Save config
    config = Config(
        auth_token=auth_token,
        instance_id=instance_id,
        page_id=page_id,
        tools=tool_ids,
    )
    save_config(config)
    print(f"\n  Config saved to ~/.quantdata-mcp/config.json")

    # Print next steps
    print(f"\n{'=' * 50}")
    print("Setup complete! Add to your Claude Code .mcp.json:")
    print()
    print('  {')
    print('    "mcpServers": {')
    print('      "quantdata": {')
    print('        "command": "quantdata-mcp",')
    print('        "args": ["serve"]')
    print("      }")
    print("    }")
    print("  }")
    print()
    print("Then restart Claude Code.")
    print(f"{'=' * 50}")

    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up QuantData MCP server")
    parser.add_argument("--auth-token", required=True, help="QuantData JWT auth token")
    parser.add_argument("--instance-id", required=True, help="QuantData instance ID")
    args = parser.parse_args()
    run_setup(args.auth_token, args.instance_id)


if __name__ == "__main__":
    main()
