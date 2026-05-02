"""Tests for the snapshot/restore behaviour and round-trip count of ``tool_context``."""

from __future__ import annotations

from typing import Any

from quantdata_mcp._context import _eq, tool_context


def test_snapshots_and_restores_original_metadata(
    mock_client, tool_dto, context_kwargs
) -> None:
    """``tool_context`` must restore the EXACT original metadata + filter on exit.

    The fixture's ``tool_dto`` carries non-default values (DELTA, FIVE_MINUTE,
    ITM moneyness, ...). After the context block applies its own mutations and
    exits, the second PUT must contain those exact original values -- not any
    hardcoded "GAMMA"/"ONE_MINUTE" defaults.
    """
    original_metadata = {k: v for k, v in tool_dto["metadata"].items() if k != "filter"}
    original_filter = dict(tool_dto["metadata"]["filter"])

    with tool_context(
        "net_drift",
        ticker="AAPL",
        date="2025-04-15",
        metadata_updates={"aggregationPeriodType": "ONE_MINUTE", "dataModeType": "PREMIUM"},
        filter_updates={"strikePriceInCents": _eq([170_00])},
        **context_kwargs,
    ):
        pass

    # Two PUTs to /tool: apply, then restore.
    put_calls = [
        call for call in mock_client._make_request.call_args_list
        if call.args[0] == "PUT" and call.args[1] == "tool"
    ]
    assert len(put_calls) == 2, f"Expected 2 PUT /tool calls, got {len(put_calls)}"

    # The APPLY PUT carries the merged mutations.
    apply_payload = put_calls[0].kwargs["json"]
    assert apply_payload["metadata"]["aggregationPeriodType"] == "ONE_MINUTE"
    assert apply_payload["metadata"]["dataModeType"] == "PREMIUM"
    assert apply_payload["metadata"]["filter"]["strikePriceInCents"]["value"] == [17000]
    # And it preserves the user's pre-existing filter entries.
    assert apply_payload["metadata"]["filter"]["moneynessMoneyType"]["value"] == [
        "IN_THE_MONEY"
    ]

    # The RESTORE PUT must match the original snapshot exactly.
    restore_payload = put_calls[1].kwargs["json"]
    for k, expected in original_metadata.items():
        assert restore_payload["metadata"][k] == expected, (
            f"Metadata key {k} not restored: got {restore_payload['metadata'][k]!r}, "
            f"expected {expected!r}"
        )
    assert restore_payload["metadata"]["filter"] == original_filter


def test_minimum_round_trips_per_call(mock_client, context_kwargs) -> None:
    """Cost budget: 1 GET + 2 PUTs to /tool, plus 1 PUT to /page-filter.

    The old code did up to 3 GETs and 3 PUTs to /tool per call. The refactor
    locks the budget to 1 GET + 2 PUTs (one to apply, one to restore).
    """
    with tool_context(
        "net_drift",
        ticker="SPX",
        metadata_updates={"aggregationPeriodType": "FIVE_MINUTE"},
        filter_updates={"strikePriceInCents": _eq([5600_00])},
        **context_kwargs,
    ):
        pass

    assert mock_client.get_tool.call_count == 1, "Expected exactly one GET /tool"

    put_tool = [
        call for call in mock_client._make_request.call_args_list
        if call.args[0] == "PUT" and call.args[1] == "tool"
    ]
    assert len(put_tool) == 2, f"Expected exactly 2 PUT /tool, got {len(put_tool)}"

    # Page filter PUT happens via client.set_page_filter, not _make_request.
    assert mock_client.set_page_filter.call_count == 1


def test_needs_tool_false_skips_tool_round_trips(mock_client, context_kwargs) -> None:
    """Tools like ``max_pain`` only need the page filter -- skip GET+PUT pair."""
    with tool_context(
        "max_pain",
        ticker="SPX",
        needs_tool=False,
        **context_kwargs,
    ):
        pass

    assert mock_client.get_tool.call_count == 0
    put_tool = [
        call for call in mock_client._make_request.call_args_list
        if call.args[0] == "PUT" and call.args[1] == "tool"
    ]
    assert put_tool == []


def test_filter_updates_with_none_values_are_skipped(mock_client, context_kwargs) -> None:
    """``filter_updates`` accepts ``None`` values for conditional dict entries."""
    with tool_context(
        "net_drift",
        filter_updates={
            "strikePriceInCents": None,            # caller said "no filter"
            "moneynessMoneyType": _eq(["AT_THE_MONEY"]),
        },
        **context_kwargs,
    ):
        pass

    apply_put = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ][0]
    new_filter = apply_put.kwargs["json"]["metadata"]["filter"]
    assert new_filter["moneynessMoneyType"]["value"] == ["AT_THE_MONEY"]
    # None entries did NOT clobber the original filter: the original ITM
    # moneyness key was overwritten by the test's update, but strikes
    # were never added.
    assert "strikePriceInCents" not in new_filter or new_filter.get(
        "strikePriceInCents"
    ) is not None


def test_eq_helper_shape() -> None:
    assert _eq(["AA", "BB"]) == {
        "filterOperationType": "EQUALS",
        "value": ["AA", "BB"],
    }
    assert _eq(170_00) == {"filterOperationType": "EQUALS", "value": 17000}


def test_page_filter_restored_when_changed(mock_client, context_kwargs) -> None:
    """Changing ticker/date away from defaults triggers a restore on exit."""
    with tool_context(
        "net_drift",
        ticker="AAPL",          # not the default SPX
        date="2024-12-20",      # not today
        **context_kwargs,
    ):
        pass

    # Two set_page_filter calls: one to apply AAPL, one to restore SPX/today.
    assert mock_client.set_page_filter.call_count == 2
    restore_call = mock_client.set_page_filter.call_args_list[-1]
    assert restore_call.kwargs["ticker"] == "SPX"


def test_page_filter_not_re_set_when_already_default(mock_client, context_kwargs) -> None:
    """If we set ticker=SPX and date=today, no restore PUT is issued."""
    from quantdata_mcp._context import _today

    with tool_context(
        "net_drift",
        ticker="SPX",
        date=_today(),
        **context_kwargs,
    ):
        pass

    # Only one page-filter call (the initial apply); no restore needed.
    assert mock_client.set_page_filter.call_count == 1


def test_time_minutes_folded_into_apply_put(mock_client, context_kwargs) -> None:
    """``time_minutes`` rides along with the apply PUT; restore PUT drops it.

    Previously this used ``client.set_tool_time`` / ``client.reset_to_live``,
    which each did their own GET+PUT pair (3 GETs + 4 PUTs total). Folding
    the time scrubber into the existing apply/restore PUT pair collapses the
    cost back to the documented 1 GET + 2 PUTs budget.
    """
    with tool_context(
        "exposure_by_strike",
        ticker="SPX",
        time_minutes=600,
        **context_kwargs,
    ):
        pass

    # The dedicated time-scrubber endpoints should NOT be called -- folded
    # into the apply / restore PUTs instead.
    assert mock_client.set_tool_time.call_count == 0
    assert mock_client.reset_to_live.call_count == 0

    # Cost budget holds: 1 GET + 2 PUTs even with time_minutes set.
    assert mock_client.get_tool.call_count == 1
    put_calls = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert len(put_calls) == 2

    # Apply PUT carries the time scrubber.
    apply_payload = put_calls[0].kwargs["json"]
    assert apply_payload["metadata"]["numberOfMinutesIntoMarketOpen"] == 600
    # Restore PUT drops it (snapshot was taken before the assignment).
    restore_payload = put_calls[1].kwargs["json"]
    assert "numberOfMinutesIntoMarketOpen" not in restore_payload["metadata"]


def test_metadata_updates_do_not_leak_into_snapshot(
    mock_client, tool_dto, context_kwargs
) -> None:
    """Mutations applied inside the context must not contaminate the snapshot."""
    with tool_context(
        "net_drift",
        metadata_updates={"greekModeType": "VANNA", "isNet": True},
        **context_kwargs,
    ):
        pass

    put_calls = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    restore = put_calls[-1].kwargs["json"]
    # The original DTO had DELTA / isNet=False -- must come back unchanged.
    assert restore["metadata"]["greekModeType"] == "DELTA"
    assert restore["metadata"]["isNet"] is False


def test_snapshot_preserves_unknown_future_metadata_keys(
    mock_client, make_tool_dto, context_kwargs
) -> None:
    """Forward-compat: unknown / future top-level metadata keys round-trip.

    The snapshot/restore cycle is shape-agnostic -- if QuantData adds a new
    metadata key tomorrow, it should pass through unchanged when our DTO
    doesn't know about it. This locks in the design intent.
    """
    # Inject some hypothetical future keys alongside an existing one.
    dto = make_tool_dto(
        metadata_overrides={
            "futureKey1": "x",
            "futureKey2": 42,
            "futureNestedKey": {"nested": True, "list": [1, 2, 3]},
            "greekModeType": "GAMMA",  # known key, mutated below
        }
    )
    mock_client.get_tool.return_value = dto

    with tool_context(
        "net_drift",
        metadata_updates={"greekModeType": "DELTA"},  # mutate the known key
        **context_kwargs,
    ):
        pass

    put_calls = [
        c for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]
    assert len(put_calls) == 2
    apply_payload = put_calls[0].kwargs["json"]
    restore_payload = put_calls[1].kwargs["json"]

    # Apply PUT carries the mutated known key AND the unknown future keys.
    assert apply_payload["metadata"]["greekModeType"] == "DELTA"
    assert apply_payload["metadata"]["futureKey1"] == "x"
    assert apply_payload["metadata"]["futureKey2"] == 42
    assert apply_payload["metadata"]["futureNestedKey"] == {
        "nested": True,
        "list": [1, 2, 3],
    }

    # Restore PUT brings back the ORIGINAL value for the known key AND
    # preserves the unknown future keys verbatim.
    assert restore_payload["metadata"]["greekModeType"] == "GAMMA"
    assert restore_payload["metadata"]["futureKey1"] == "x"
    assert restore_payload["metadata"]["futureKey2"] == 42
    assert restore_payload["metadata"]["futureNestedKey"] == {
        "nested": True,
        "list": [1, 2, 3],
    }
