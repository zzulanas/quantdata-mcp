"""Config management — loads/saves user credentials and tool IDs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


CONFIG_DIR = Path(os.environ.get("QUANTDATA_MCP_CONFIG_DIR", Path.home() / ".quantdata-mcp"))
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class Config:
    auth_token: str
    instance_id: str
    page_id: str = ""
    tools: dict[str, str] = field(default_factory=dict)  # canonical name -> tool UUID
    # v0.5.0 — user-managed named pages. Each entry is a dict with keys:
    #   {name, label, page_id, url, filter, tools, created_at}
    # See ``quantdata_mcp.server`` for the schema documented in
    # ``qd_create_page`` and friends. Stored as plain dicts (not
    # nested dataclasses) so callers can extend without schema migrations.
    pages: list[dict] = field(default_factory=list)


def config_exists() -> bool:
    return CONFIG_PATH.exists()


def load_config() -> Config:
    """Load config from disk. Raises FileNotFoundError if missing."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config not found at {CONFIG_PATH}. Run: quantdata-mcp setup --auth-token <TOKEN> --instance-id <ID>"
        )
    data = json.loads(CONFIG_PATH.read_text())
    return Config(
        auth_token=data["auth_token"],
        instance_id=data["instance_id"],
        page_id=data.get("page_id", ""),
        tools=data.get("tools", {}),
        pages=data.get("pages", []),
    )


def save_config(config: Config) -> None:
    """Save config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "auth_token": config.auth_token,
                "instance_id": config.instance_id,
                "page_id": config.page_id,
                "tools": config.tools,
                "pages": config.pages,
            },
            indent=2,
        )
    )
    CONFIG_PATH.chmod(0o600)
