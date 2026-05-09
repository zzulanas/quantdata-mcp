"""Tests for v0.5.0 user-managed pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from quantdata_mcp import pages, server
from quantdata_mcp.config import Config


# ---------------------------------------------------------------------------
# pages.py — pure helpers
# ---------------------------------------------------------------------------

def test_page_url_template() -> None:
    assert (
        pages.page_url("abc-123")
        == "https://v3.quantdata.us/page/abc-123"
    )


@pytest.mark.parametrize(
    "name, valid",
    [
        ("tsla", True),
        ("tsla_earnings", True),
        ("page_2", True),
        ("a", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("", False),
        ("Tsla", False),  # uppercase
        ("tsla earnings", False),  # space
        ("tsla-earnings", False),  # hyphen
        ("tsla.earnings", False),  # dot
    ],
)
def test_is_valid_page_name(name: str, valid: bool) -> None:
    assert pages.is_valid_page_name(name) is valid


def test_make_page_record_has_documented_keys() -> None:
    rec = pages.make_page_record(
        name="tsla",
        label="TSLA Workspace",
        page_id="page-uuid",
        ticker="TSLA",
        session_date="2026-05-08",
        expiration_date="2026-05-15",
    )
    assert rec["name"] == "tsla"
    assert rec["label"] == "TSLA Workspace"
    assert rec["page_id"] == "page-uuid"
    assert rec["url"] == "https://v3.quantdata.us/page/page-uuid"
    assert rec["filter"] == {
        "ticker": "TSLA",
        "session_date": "2026-05-08",
        "expiration_date": "2026-05-15",
    }
    assert rec["tools"] == []
    assert "created_at" in rec


def test_find_page_and_index() -> None:
    cfg = Config(
        auth_token="t",
        instance_id="i",
        pages=[
            pages.make_page_record(name="a", label="A", page_id="aa"),
            pages.make_page_record(name="b", label="B", page_id="bb"),
        ],
    )
    assert pages.find_page(cfg, "a")["page_id"] == "aa"
    assert pages.find_page(cfg, "b")["page_id"] == "bb"
    assert pages.find_page(cfg, "missing") is None
    assert pages.page_index(cfg, "a") == 0
    assert pages.page_index(cfg, "missing") == -1


def test_runnable_tools_registry_covers_workspace_essentials() -> None:
    """The qd_run_page batch view should include the most-used tool types
    so a workspace fetch is meaningful out of the box."""
    runnable = pages.runnable_canonical_names()
    for essential in (
        "exposure_by_strike", "net_drift", "net_flow",
        "max_pain", "iv_rank", "contract_statistics",
    ):
        assert essential in runnable, f"{essential} should be runnable on a page"


def test_fetcher_for_returns_triple_or_none() -> None:
    triple = pages.fetcher_for("net_drift")
    assert triple is not None
    method, formatter, fmt_kwargs = triple
    assert method == "fetch_net_drift"
    assert formatter == "_fmt_drift"
    assert fmt_kwargs == {}

    # Tools that need extra kwargs (e.g. greek_type) encode them in the dict
    triple = pages.fetcher_for("exposure_by_strike")
    assert triple is not None
    _, _, fmt_kwargs = triple
    assert fmt_kwargs.get("greek_type") == "GAMMA"
    assert fmt_kwargs.get("_pass_ticker") is True

    assert pages.fetcher_for("order_flow") is None  # too filter-rich for batch
    assert pages.fetcher_for("not_a_real_tool") is None


# ---------------------------------------------------------------------------
# Config persistence — pages round-trip through save_config / load_config
# ---------------------------------------------------------------------------

def test_pages_round_trip_through_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUANTDATA_MCP_CONFIG_DIR", str(tmp_path))
    import importlib

    import quantdata_mcp.config as cfg_mod
    importlib.reload(cfg_mod)
    from quantdata_mcp.config import Config, load_config, save_config  # noqa: I001

    page = pages.make_page_record(
        name="tsla",
        label="TSLA Workspace",
        page_id="page-uuid",
        ticker="TSLA",
        session_date="2026-05-08",
    )
    page["tools"].append(
        pages.make_tool_record("net_drift", "tool-uuid", "Net Drift")
    )
    config = Config(
        auth_token="t",
        instance_id="i",
        page_id="canonical-page",
        tools={"net_drift": "canonical-net-drift-id"},
        pages=[page],
    )
    save_config(config)

    loaded = load_config()
    assert len(loaded.pages) == 1
    assert loaded.pages[0]["name"] == "tsla"
    assert loaded.pages[0]["tools"][0]["canonical_name"] == "net_drift"


# ---------------------------------------------------------------------------
# MCP wrappers — wiring tests with mocked client
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Provide a clean config dir + reset server-module globals."""
    monkeypatch.setenv("QUANTDATA_MCP_CONFIG_DIR", str(tmp_path))
    # Reload config module so it picks up the new dir
    import importlib

    import quantdata_mcp.config as cfg_mod
    importlib.reload(cfg_mod)
    # Reload server to re-bind to the reloaded config module symbols
    import quantdata_mcp.server as srv_mod
    importlib.reload(srv_mod)

    from quantdata_mcp.config import Config, save_config

    starter = Config(
        auth_token="t",
        instance_id="i",
        page_id="canonical-page",
        tools={
            # Just enough for a couple of TOOL_DEFINITIONS lookups.
            "net_drift": "canonical-net-drift-id",
        },
    )
    save_config(starter)

    # Wire mock client into the freshly-reloaded server module.
    client = MagicMock()
    client.create_page.return_value = {"id": "new-page-uuid"}
    client.set_page_filter.return_value = True
    client.create_tool.return_value = {
        "response": {"toolDTO": {"id": "new-tool-uuid"}}
    }
    client.update_page_layout.return_value = True
    client.delete_page.return_value = True
    client.delete_tool.return_value = True
    client.fetch_net_drift.return_value = {"response": {"netDrift": []}}
    client.fetch_max_pain.return_value = {"response": {"strikePriceInCentsWithMaxPain": 0, "stockPriceInCents": 0}}
    client._make_request.return_value = MagicMock(json=lambda: {"ok": True})

    srv_mod._client = client
    srv_mod._config = starter
    from quantdata_mcp.tools import build_tool_specs
    srv_mod._specs = build_tool_specs(starter.tools)

    return srv_mod, client, starter


def test_create_page_persists_and_returns_url(mock_env) -> None:
    srv_mod, client, _starter = mock_env
    out = srv_mod.qd_create_page(
        name="tsla", label="TSLA Workspace",
        ticker="TSLA", date="2026-05-08", expiration_date="2026-05-15",
    )
    assert "Created page 'tsla'" in out
    assert "https://v3.quantdata.us/page/new-page-uuid" in out
    # Initial filter was set (because ticker/date were supplied)
    client.set_page_filter.assert_called_once()
    # Persisted to config
    assert len(srv_mod._config.pages) == 1
    assert srv_mod._config.pages[0]["name"] == "tsla"


def test_create_page_rejects_invalid_name(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    out = srv_mod.qd_create_page(name="TSLA Earnings")  # uppercase + space
    assert "Invalid page name" in out
    assert len(srv_mod._config.pages) == 0


def test_create_page_rejects_duplicate(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA")
    out = srv_mod.qd_create_page(name="tsla", label="TSLA")
    assert "already exists" in out
    assert len(srv_mod._config.pages) == 1  # not re-added


def test_list_pages_empty(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    out = srv_mod.qd_list_pages()
    assert "No user-managed pages" in out


def test_list_pages_renders_record(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA Workspace", ticker="TSLA")
    out = srv_mod.qd_list_pages()
    assert "tsla" in out
    assert "TSLA Workspace" in out
    assert "https://v3.quantdata.us/page/" in out
    assert "no tools" in out.lower() or "(none" in out


def test_add_tool_to_page_appends_record(mock_env) -> None:
    srv_mod, client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA Workspace")
    out = srv_mod.qd_add_tool_to_page(page_name="tsla", tool_canonical_name="net_drift")
    assert "Added 'net_drift'" in out
    assert "1 tool" in out
    page = srv_mod._config.pages[0]
    assert page["tools"][0]["canonical_name"] == "net_drift"
    assert page["tools"][0]["tool_id"] == "new-tool-uuid"
    # create_tool was called with the page's own page_id (NOT the canonical one)
    client.create_tool.assert_called_with(
        page_id="new-page-uuid",
        tool_type="OPTIONS_NET_DRIFT_CHART",
    )


def test_add_tool_rejects_unknown_tool(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA")
    out = srv_mod.qd_add_tool_to_page("tsla", "not_a_real_tool")
    assert "Unknown tool type" in out
    assert srv_mod._config.pages[0]["tools"] == []


def test_add_tool_rejects_missing_page(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    out = srv_mod.qd_add_tool_to_page("does_not_exist", "net_drift")
    assert "not found" in out


def test_run_page_batches_tool_fetches(mock_env) -> None:
    srv_mod, client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA", ticker="TSLA", date="2026-05-08")
    srv_mod.qd_add_tool_to_page("tsla", "net_drift")

    out = srv_mod.qd_run_page("tsla")
    # Header includes label and URL
    assert "TSLA" in out
    assert "https://v3.quantdata.us/page/" in out
    # Section header for the tool
    assert "## net_drift" in out
    # The fetch was called with the page-tool's tool_id (NOT canonical)
    client.fetch_net_drift.assert_called_with("new-tool-uuid")


def test_run_page_skips_unsupported_tools(mock_env) -> None:
    """Tools without an entry in RUNNABLE_TOOLS (e.g. order_flow,
    heat_map) are noted but don't fail the batch."""
    srv_mod, _client, _ = mock_env
    srv_mod.qd_create_page(name="mix", label="Mix")
    # Manually add a non-runnable tool record (skipping the wrapper for speed)
    srv_mod._config.pages[0]["tools"] = [
        {"canonical_name": "net_drift", "tool_id": "td-id", "label": "Net Drift"},
        {"canonical_name": "order_flow", "tool_id": "of-id", "label": "Order Flow"},
        {"canonical_name": "heat_map", "tool_id": "hm-id", "label": "Heat Map"},
    ]
    out = srv_mod.qd_run_page("mix")
    assert "## net_drift" in out
    assert "skipped:" in out
    assert "order_flow" in out  # listed under "skipped"
    assert "heat_map" in out


def test_run_page_handles_missing(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    out = srv_mod.qd_run_page("does_not_exist")
    assert "not found" in out


def test_run_page_handles_empty_page(mock_env) -> None:
    srv_mod, _client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA")
    out = srv_mod.qd_run_page("tsla")
    assert "no tools yet" in out.lower()


def test_delete_page_without_delete_tools_keeps_tool_instances(mock_env) -> None:
    srv_mod, client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA")
    srv_mod.qd_add_tool_to_page("tsla", "net_drift")

    out = srv_mod.qd_delete_page("tsla", delete_tools=False)
    assert "Deleted page 'tsla'" in out
    assert "deleted" not in out.split("Deleted page")[1].lower() or "tool" not in out.split("Deleted page")[1].lower()
    client.delete_page.assert_called_with("new-page-uuid")
    client.delete_tool.assert_not_called()
    # Removed from config
    assert srv_mod._config.pages == []


def test_delete_page_with_delete_tools_cleans_up(mock_env) -> None:
    srv_mod, client, _ = mock_env
    srv_mod.qd_create_page(name="tsla", label="TSLA")
    srv_mod.qd_add_tool_to_page("tsla", "net_drift")
    srv_mod.qd_add_tool_to_page("tsla", "net_drift")

    out = srv_mod.qd_delete_page("tsla", delete_tools=True)
    assert "Deleted page 'tsla'" in out
    assert "deleted 2 tool" in out
    assert client.delete_tool.call_count == 2
    assert srv_mod._config.pages == []
