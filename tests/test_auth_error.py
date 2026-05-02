"""Tests for the QuantDataAuthError UX."""

from __future__ import annotations

from unittest.mock import patch

from quantdata_mcp import server
from quantdata_mcp._context import AUTH_ERROR_MESSAGE, format_error, tool_context
from quantdata_mcp.client import QuantDataAuthError


def test_format_error_special_cases_auth_error() -> None:
    msg = format_error("net drift", QuantDataAuthError("token expired"))
    assert msg == AUTH_ERROR_MESSAGE
    assert "quantdata-mcp setup" in msg
    assert "<NEW_TOKEN>" in msg


def test_format_error_passthrough_for_other_exceptions() -> None:
    msg = format_error("net drift", RuntimeError("kaboom"))
    assert msg == "Error fetching net drift: kaboom"


def test_auth_error_inside_context_propagates(mock_client, context_kwargs) -> None:
    """Auth errors raised by the underlying client bubble out of ``tool_context``."""
    mock_client.get_tool.side_effect = QuantDataAuthError("401")

    raised = False
    try:
        with tool_context("net_drift", **context_kwargs):
            pass
    except QuantDataAuthError:
        raised = True
    assert raised, "QuantDataAuthError should propagate out of tool_context"


def test_qd_get_net_drift_returns_auth_message_on_401(mock_client, mock_specs) -> None:
    """The tool wrapper catches QuantDataAuthError and returns the friendly string."""
    mock_client.get_tool.side_effect = QuantDataAuthError("token expired")

    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        result = server.qd_get_net_drift(ticker="SPX")

    assert result == AUTH_ERROR_MESSAGE


def test_qd_get_max_pain_returns_auth_message_on_401(mock_client, mock_specs) -> None:
    """needs_tool=False tools also surface the auth message."""
    # max_pain doesn't call get_tool; the auth error has to come from the fetch.
    mock_client.fetch_max_pain.side_effect = QuantDataAuthError("token expired")

    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        result = server.qd_get_max_pain(ticker="SPX")

    assert result == AUTH_ERROR_MESSAGE


def test_generic_exception_returns_error_fetching(mock_client, mock_specs) -> None:
    """Non-auth errors fall back to the original "Error fetching X: ..." string."""
    mock_client.fetch_net_drift.side_effect = RuntimeError("boom")

    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        result = server.qd_get_net_drift(ticker="SPX")

    assert result.startswith("Error fetching net drift:")
    assert "boom" in result
