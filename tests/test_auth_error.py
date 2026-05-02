"""Tests for the QuantDataAuthError UX."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from quantdata_mcp import server
from quantdata_mcp._context import AUTH_ERROR_MESSAGE, format_error, tool_context
from quantdata_mcp.client import (
    QuantDataAuthError,
    QuantDataClient,
    QuantDataRateLimitError,
)


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


# ---------------------------------------------------------------------------
# client.set_page_filter must re-raise auth / rate-limit errors so the
# friendly UX message reaches the LLM. Other errors still get logged + return
# False (preserves backwards-compatible boolean return shape for happy-path
# callers).
# ---------------------------------------------------------------------------


def _make_real_client() -> QuantDataClient:
    """Return a real client with the network call patched out at __init__ time."""
    return QuantDataClient(auth_token="dummy", instance_id="dummy", max_retries=1)


def test_set_page_filter_reraises_auth_error() -> None:
    client = _make_real_client()
    with patch.object(
        client, "_make_request", side_effect=QuantDataAuthError("401")
    ):
        with pytest.raises(QuantDataAuthError):
            client.set_page_filter("page-abc", "2025-01-15", "SPX")


def test_set_page_filter_reraises_rate_limit_error() -> None:
    client = _make_real_client()
    with patch.object(
        client, "_make_request", side_effect=QuantDataRateLimitError("429")
    ):
        with pytest.raises(QuantDataRateLimitError):
            client.set_page_filter("page-abc", "2025-01-15", "SPX")


def test_set_page_filter_swallows_generic_errors() -> None:
    """Other errors (network, etc.) still log + return False -- unchanged behavior."""
    client = _make_real_client()
    with patch.object(client, "_make_request", side_effect=RuntimeError("boom")):
        result = client.set_page_filter("page-abc", "2025-01-15", "SPX")
    assert result is False


def test_qd_set_page_date_surfaces_auth_message_on_401() -> None:
    """The KEY regression: qd_set_page_date is the only tool whose ONLY API
    call is set_page_filter. Before fix #1, set_page_filter swallowed 401s
    and the user saw "Failed to set page filter." Now they see the friendly
    re-run-setup message.
    """
    client = MagicMock()
    client.set_page_filter.side_effect = QuantDataAuthError("token expired")

    with patch.object(server, "_get_client", lambda: client), patch.object(
        server, "_get_page_id", lambda: "page-abc"
    ):
        result = server.qd_set_page_date(date="2025-01-15", ticker="SPX")

    assert result == AUTH_ERROR_MESSAGE
