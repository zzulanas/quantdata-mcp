"""Tests for ``page_filter_context`` + ``skip_page_filter`` scope sharing.

Before the staff-review fix, ``qd_get_market_snapshot`` ran six inner
``tool_context`` blocks each with its own page-filter apply + restore. That
meant 12 page-filter PUTs per snapshot for a non-default ticker -- way over
the budget of 2.

After the fix, all six fetches share one outer ``page_filter_context`` and
each inner ``tool_context`` passes ``skip_page_filter=True``. Total page
filter PUTs: 2.
"""

from __future__ import annotations

from unittest.mock import patch

from quantdata_mcp import server
from quantdata_mcp._context import page_filter_context, tool_context


def test_market_snapshot_makes_only_two_page_filter_puts(
    mock_client, mock_specs
) -> None:
    """``qd_get_market_snapshot`` should call ``set_page_filter`` exactly twice.

    Once on enter (apply the requested AAPL filter), once on exit (restore
    SPX/today). NOT 12 (= 6 sections * 2 inner applies+restores) -- that was
    the regression the staff review flagged.
    """
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_market_snapshot(
            ticker="AAPL",                    # non-default -> triggers restore
            date="2025-01-15",
            expiration_date="2025-01-17",
        )

    assert mock_client.set_page_filter.call_count == 2, (
        f"Expected 2 page-filter PUTs (apply + restore) for the whole "
        f"snapshot, got {mock_client.set_page_filter.call_count}. The six "
        f"inner tool_context blocks must share a single page_filter_context."
    )


def test_skip_page_filter_does_not_call_set_page_filter(
    mock_client, context_kwargs
) -> None:
    """``tool_context(skip_page_filter=True)`` skips the page filter entirely."""
    with tool_context(
        "max_pain",
        ticker="AAPL",
        date="2025-01-15",
        needs_tool=False,
        skip_page_filter=True,
        **context_kwargs,
    ):
        pass

    assert mock_client.set_page_filter.call_count == 0


def test_page_filter_context_applies_and_restores(
    mock_client, context_kwargs
) -> None:
    """``page_filter_context`` applies on enter, restores on exit (when changed)."""
    # Drop the keys page_filter_context doesn't take.
    pf_kwargs = {
        "get_client": context_kwargs["get_client"],
        "get_page_id": context_kwargs["get_page_id"],
    }

    with page_filter_context(ticker="AAPL", date="2025-01-15", **pf_kwargs):
        pass

    assert mock_client.set_page_filter.call_count == 2
    apply_call = mock_client.set_page_filter.call_args_list[0]
    restore_call = mock_client.set_page_filter.call_args_list[1]
    assert apply_call.kwargs["ticker"] == "AAPL"
    assert restore_call.kwargs["ticker"] == "SPX"


def test_page_filter_context_with_inner_tool_contexts(
    mock_client, context_kwargs
) -> None:
    """An outer ``page_filter_context`` plus N inner ``tool_context`` calls
    with ``skip_page_filter=True`` produces exactly 2 page-filter PUTs total.
    """
    pf_kwargs = {
        "get_client": context_kwargs["get_client"],
        "get_page_id": context_kwargs["get_page_id"],
    }

    with page_filter_context(ticker="AAPL", date="2025-01-15", **pf_kwargs):
        for _ in range(3):
            with tool_context(
                "net_drift",
                ticker="AAPL",
                date="2025-01-15",
                metadata_updates={"aggregationPeriodType": "FIVE_MINUTE"},
                skip_page_filter=True,
                **context_kwargs,
            ):
                pass

    # Exactly 2 page-filter PUTs even though 3 inner tool_contexts ran.
    assert mock_client.set_page_filter.call_count == 2

    # Each inner tool_context still does its 1 GET + 2 PUTs.
    assert mock_client.get_tool.call_count == 3
    put_tool_calls = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert len(put_tool_calls) == 6  # 3 applies + 3 restores
