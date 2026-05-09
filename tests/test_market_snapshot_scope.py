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


def test_market_snapshot_makes_only_one_page_filter_put(
    mock_client, mock_specs
) -> None:
    """``qd_get_market_snapshot`` should call ``set_page_filter`` exactly once.

    One apply on entry covering all six section fetches under a single
    ``page_filter_context``. PR 15 dropped the auto-restore-on-exit so the
    page filter is sticky and the count is now 1, not 2.
    """
    from quantdata_mcp._context import clear_active_page

    clear_active_page()
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_market_snapshot(
            ticker="AAPL",
            date="2025-01-15",
            expiration_date="2025-01-17",
        )

    assert mock_client.set_page_filter.call_count == 1, (
        f"Expected 1 page-filter PUT (apply on entry, no restore on exit) for "
        f"the whole snapshot, got {mock_client.set_page_filter.call_count}. "
        f"The six inner tool_context blocks must share a single "
        f"page_filter_context, and PR 15 removed the exit-restore."
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


def test_page_filter_context_applies_once_and_sticks(
    mock_client, context_kwargs
) -> None:
    """``page_filter_context`` applies on enter and the filter persists after
    the block exits — the page-filter is sticky as of PR 15.
    """
    from quantdata_mcp._context import clear_active_page

    clear_active_page()
    pf_kwargs = {
        "get_client": context_kwargs["get_client"],
        "get_page_id": context_kwargs["get_page_id"],
    }

    with page_filter_context(ticker="AAPL", date="2025-01-15", **pf_kwargs):
        pass

    assert mock_client.set_page_filter.call_count == 1
    apply_call = mock_client.set_page_filter.call_args_list[0]
    assert apply_call.kwargs["ticker"] == "AAPL"
    assert apply_call.kwargs["session_date"] == "2025-01-15"


def test_page_filter_context_with_inner_tool_contexts(
    mock_client, context_kwargs
) -> None:
    """An outer ``page_filter_context`` plus N inner ``tool_context`` calls
    with ``skip_page_filter=True`` produces exactly 1 page-filter PUT total
    (one apply on entry; no restore on exit per PR 15's sticky semantics).
    """
    from quantdata_mcp._context import clear_active_page

    clear_active_page()
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

    # Exactly 1 page-filter PUT — the sticky-page-filter contract.
    assert mock_client.set_page_filter.call_count == 1

    # Each inner tool_context still does its 1 GET + 2 PUTs.
    assert mock_client.get_tool.call_count == 3
    put_tool_calls = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert len(put_tool_calls) == 6  # 3 applies + 3 restores (tool-level only)
