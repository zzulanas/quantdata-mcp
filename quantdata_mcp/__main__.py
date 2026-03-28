"""CLI entry point — dispatch between 'serve', 'setup', and 'login'."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="quantdata-mcp",
        description="QuantData MCP Server — Options market data for AI agents",
    )
    sub = parser.add_subparsers(dest="command")

    # serve
    sub.add_parser("serve", help="Start the MCP server (stdio transport)")

    # setup (manual)
    setup_p = sub.add_parser("setup", help="Set up page and tools for a new user")
    setup_p.add_argument("--auth-token", help="QuantData JWT auth token")
    setup_p.add_argument("--instance-id", help="QuantData instance ID (x-instance-id header)")
    setup_p.add_argument(
        "--browser", action="store_true",
        help="Open browser to capture credentials automatically (requires: pip install 'quantdata-mcp[browser]')",
    )

    # login (shortcut for --browser setup)
    sub.add_parser(
        "login",
        help="Open browser to log in and set up automatically (requires: pip install 'quantdata-mcp[browser]')",
    )

    args = parser.parse_args()

    if args.command == "serve":
        from quantdata_mcp.server import mcp
        mcp.run(transport="stdio")

    elif args.command == "login":
        _browser_setup()

    elif args.command == "setup":
        if args.browser:
            _browser_setup()
        elif args.auth_token and args.instance_id:
            from quantdata_mcp.setup import run_setup
            run_setup(args.auth_token, args.instance_id)
        else:
            print("Provide --auth-token and --instance-id, or use --browser to capture automatically.")
            print()
            print("  quantdata-mcp setup --auth-token <TOKEN> --instance-id <ID>")
            print("  quantdata-mcp setup --browser")
            print("  quantdata-mcp login   # shortcut for --browser")
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


def _browser_setup() -> None:
    """Capture credentials via browser, then run setup."""
    from quantdata_mcp.browser_auth import capture_credentials
    from quantdata_mcp.setup import run_setup

    auth_token, instance_id = capture_credentials()
    run_setup(auth_token, instance_id)


if __name__ == "__main__":
    main()
