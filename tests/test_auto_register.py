"""Tests for `_auto_register_missing_tools` — the upgrade-time auto-heal
that creates QuantData tool instances for any TOOL_DEFINITIONS missing
from the user's on-disk config.

Closes the gap where adding a new tool to a release would force every
existing user to re-run `quantdata-mcp setup`. The helper is invoked
inside `_load()` on first server start.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from quantdata_mcp.config import Config
from quantdata_mcp.server import _auto_register_missing_tools
from quantdata_mcp.tools import TOOL_DEFINITIONS


def _create_tool_response(tool_id: str) -> dict[str, Any]:
    """Mirror the real shape of POST /api/tool — see client.create_tool."""
    return {"response": {"toolDTO": {"id": tool_id}}}


@pytest.fixture
def fresh_config(tmp_path, monkeypatch) -> Config:
    """A config with NO registered tools — every TOOL_DEFINITIONS entry is missing."""
    monkeypatch.setenv("QUANTDATA_MCP_CONFIG_DIR", str(tmp_path))
    # Force config module to pick up the env var by reimporting on next access
    import importlib

    import quantdata_mcp.config as cfg_mod
    importlib.reload(cfg_mod)
    return Config(
        auth_token="t",
        instance_id="i",
        page_id="page-abc",
        tools={},
    )


@pytest.fixture
def half_full_config(tmp_path, monkeypatch) -> Config:
    """A config with a few existing tool IDs — simulates an upgrade where some
    tools were registered by a prior `setup` run."""
    monkeypatch.setenv("QUANTDATA_MCP_CONFIG_DIR", str(tmp_path))
    import importlib

    import quantdata_mcp.config as cfg_mod
    importlib.reload(cfg_mod)
    # Pick three names that exist in TOOL_DEFINITIONS as "already registered"
    pre_existing = list(TOOL_DEFINITIONS)[:3]
    return Config(
        auth_token="t",
        instance_id="i",
        page_id="page-abc",
        tools={name: f"prior-{name}" for name in pre_existing},
    )


def test_creates_all_tools_when_none_registered(fresh_config: Config) -> None:
    client = MagicMock()
    # Each create_tool call returns a fresh tool ID
    client.create_tool.side_effect = [
        _create_tool_response(f"new-{name}") for name in TOOL_DEFINITIONS
    ]
    client.update_page_layout.return_value = True

    created = _auto_register_missing_tools(client, fresh_config)

    assert created is True
    assert client.create_tool.call_count == len(TOOL_DEFINITIONS)
    # Every definition is now registered
    for name in TOOL_DEFINITIONS:
        assert fresh_config.tools[name] == f"new-{name}"
    # Page layout refresh was attempted with the full set
    client.update_page_layout.assert_called_once()


def test_creates_only_missing_tools(half_full_config: Config) -> None:
    pre_existing = set(half_full_config.tools.keys())
    missing = [n for n in TOOL_DEFINITIONS if n not in pre_existing]

    client = MagicMock()
    client.create_tool.side_effect = [
        _create_tool_response(f"new-{name}") for name in missing
    ]
    client.update_page_layout.return_value = True

    created = _auto_register_missing_tools(client, half_full_config)

    assert created is True
    # Only the missing ones got created
    assert client.create_tool.call_count == len(missing)
    # Pre-existing IDs are untouched
    for name in pre_existing:
        assert half_full_config.tools[name] == f"prior-{name}"
    # New ones got registered
    for name in missing:
        assert half_full_config.tools[name] == f"new-{name}"


def test_no_op_when_all_tools_registered(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUANTDATA_MCP_CONFIG_DIR", str(tmp_path))
    import importlib

    import quantdata_mcp.config as cfg_mod
    importlib.reload(cfg_mod)

    full = Config(
        auth_token="t",
        instance_id="i",
        page_id="page-abc",
        tools={name: f"existing-{name}" for name in TOOL_DEFINITIONS},
    )

    client = MagicMock()

    created = _auto_register_missing_tools(client, full)

    assert created is False
    client.create_tool.assert_not_called()
    client.update_page_layout.assert_not_called()


def test_skips_when_no_page_id(tmp_path, monkeypatch) -> None:
    """Fresh installs without a page_id can't auto-register — that path goes
    through the existing `quantdata-mcp setup` flow."""
    monkeypatch.setenv("QUANTDATA_MCP_CONFIG_DIR", str(tmp_path))
    import importlib

    import quantdata_mcp.config as cfg_mod
    importlib.reload(cfg_mod)

    no_page = Config(auth_token="t", instance_id="i", page_id="", tools={})
    client = MagicMock()

    created = _auto_register_missing_tools(client, no_page)

    assert created is False
    client.create_tool.assert_not_called()


def test_partial_failure_persists_progress(half_full_config: Config) -> None:
    """If create_tool returns None partway through, the successfully-created
    tools should still land in the config — no orphan IDs in QuantData."""
    pre_existing = set(half_full_config.tools.keys())
    missing = [n for n in TOOL_DEFINITIONS if n not in pre_existing]

    # Succeed for the first half of missing, fail for the rest
    half = len(missing) // 2
    side_effects: list[Any] = [
        _create_tool_response(f"new-{name}") for name in missing[:half]
    ]
    side_effects.extend([None] * (len(missing) - half))

    client = MagicMock()
    client.create_tool.side_effect = side_effects
    client.update_page_layout.return_value = True

    created = _auto_register_missing_tools(client, half_full_config)

    assert created is True  # at least one succeeded
    # Successful creations landed
    for name in missing[:half]:
        assert half_full_config.tools[name] == f"new-{name}"
    # Failed creations are NOT in the config (no orphan placeholder IDs)
    for name in missing[half:]:
        assert name not in half_full_config.tools


def test_persists_to_disk_after_each_create(half_full_config: Config) -> None:
    """save_config should be invoked after every successful create so the
    on-disk state stays consistent if the process is killed mid-loop."""
    pre_existing = set(half_full_config.tools.keys())
    missing = [n for n in TOOL_DEFINITIONS if n not in pre_existing]

    client = MagicMock()
    client.create_tool.side_effect = [
        _create_tool_response(f"new-{name}") for name in missing
    ]
    client.update_page_layout.return_value = True

    # Re-import save_config bound to the env-overridden CONFIG_DIR
    from unittest.mock import patch

    with patch("quantdata_mcp.server.save_config") as mock_save:
        _auto_register_missing_tools(client, half_full_config)

    # One save per successful create — every successful POST is durable.
    assert mock_save.call_count == len(missing)


def test_layout_failure_is_nonfatal(fresh_config: Config) -> None:
    """A failed page-layout refresh should not undo successful tool creation."""
    client = MagicMock()
    client.create_tool.side_effect = [
        _create_tool_response(f"new-{name}") for name in TOOL_DEFINITIONS
    ]
    client.update_page_layout.side_effect = RuntimeError("layout API down")

    # Should not raise — layout refresh is best-effort
    created = _auto_register_missing_tools(client, fresh_config)

    assert created is True
    assert len(fresh_config.tools) == len(TOOL_DEFINITIONS)
