"""CLI entry point — dispatch between 'serve' and 'setup'."""

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

    # setup
    setup_p = sub.add_parser("setup", help="Set up page and tools for a new user")
    setup_p.add_argument("--auth-token", required=True, help="QuantData JWT auth token")
    setup_p.add_argument("--instance-id", required=True, help="QuantData instance ID (x-instance-id header)")

    args = parser.parse_args()

    if args.command == "serve":
        from quantdata_mcp.server import mcp
        mcp.run(transport="stdio")

    elif args.command == "setup":
        from quantdata_mcp.setup import run_setup
        run_setup(args.auth_token, args.instance_id)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
