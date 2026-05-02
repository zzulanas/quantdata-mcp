"""Tests proving the module-level lock serializes concurrent ``tool_context`` calls."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from quantdata_mcp._context import tool_context
from quantdata_mcp.tools import ToolSpec, ToolType


def _make_specs() -> dict[str, ToolSpec]:
    return {
        "net_drift": ToolSpec(
            tool_id="tool-net-drift",
            tool_type=ToolType.NET_DRIFT,
            endpoint="options/net-drift",
            label="Net Drift",
        ),
    }


def test_two_threads_do_not_interleave_page_filter() -> None:
    """Spawn two threads that both run ``tool_context`` with different tickers.

    Without the lock, their ``set_page_filter`` calls could interleave like
    [SPX, AAPL, SPX-restore, AAPL-restore]. The lock should serialize them
    so we instead see [SPX, SPX-restore, AAPL, AAPL-restore] (or the
    flipped order, but never an interleaved one).
    """
    events: list[tuple[str, str]] = []
    events_lock = threading.Lock()

    def _record_set_page_filter(*args, **kwargs):
        ticker = kwargs.get("ticker")
        with events_lock:
            events.append(("set", ticker))
        # Hold the lock for a beat to make interleaving more likely if the
        # mutex were missing.
        time.sleep(0.05)
        return True

    def _record_get_tool(_tool_id):
        with events_lock:
            events.append(("get", "tool"))
        return {
            "id": "tool-net-drift",
            "metadata": {"filter": {}, "greekModeType": "GAMMA"},
        }

    def _record_make_request(method, path, **kwargs):
        if method == "PUT" and path == "tool":
            payload = kwargs.get("json", {})
            with events_lock:
                events.append(("put-tool", payload.get("id")))
            time.sleep(0.05)
        m = MagicMock()
        m.json = lambda: {"ok": True}
        return m

    def _worker(ticker: str) -> None:
        client = MagicMock()
        client.set_page_filter.side_effect = _record_set_page_filter
        client.get_tool.side_effect = _record_get_tool
        client._make_request.side_effect = _record_make_request

        with tool_context(
            "net_drift",
            ticker=ticker,
            date="2025-04-15",
            metadata_updates={"aggregationPeriodType": "ONE_MINUTE"},
            get_client=lambda: client,
            get_specs=_make_specs,
            get_page_id=lambda: "page-abc",
        ):
            time.sleep(0.05)

    threads = [
        threading.Thread(target=_worker, args=("SPX",)),
        threading.Thread(target=_worker, args=("AAPL",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Find the indices of the first 'set' for each ticker and the 'set'
    # that restores it. With the lock, all events for ticker A complete
    # before ANY event for ticker B starts.
    set_indices = {
        "SPX": [i for i, (kind, t) in enumerate(events) if kind == "set" and t == "SPX"],
        "AAPL": [i for i, (kind, t) in enumerate(events) if kind == "set" and t == "AAPL"],
    }

    # Each ticker should have at least one 'set' (the apply); restores
    # back to SPX/today are NOT recorded as "AAPL"/"SPX" specifically --
    # they show up as ("set", "SPX") for the AAPL worker's restore.
    assert len(set_indices["SPX"]) >= 1
    assert len(set_indices["AAPL"]) >= 1

    # Pick whichever worker started first; assert that all of its events
    # complete before the other worker emits its first event.
    first_kind, first_ticker = events[0]
    assert first_kind == "set"
    other_ticker = "AAPL" if first_ticker == "SPX" else "SPX"

    # Find the index of the first event from the other worker.
    other_first = next(
        i for i, (kind, t) in enumerate(events)
        if kind == "set" and t == other_ticker
    )
    # Events strictly before that index must all belong to the first worker
    # (which means there's an unbroken block of events for worker 1 before
    # worker 2 makes any move). The first worker's block has at least 3
    # events: set(ticker), put-tool(apply), put-tool(restore). It may or
    # may not contain a final ("set", "SPX") restore depending on whether
    # `first_ticker` was already SPX.
    assert other_first >= 3, (
        f"Workers interleaved: events before second worker started = {events[:other_first]}"
    )
