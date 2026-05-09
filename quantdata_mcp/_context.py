"""Internal helpers: ``tool_context`` manager and small shared utilities.

Why this module exists
----------------------
Every ``@mcp.tool()`` function in :mod:`quantdata_mcp.server` shared the same
boilerplate: set the page filter, GET the tool, PUT new metadata, PUT new
filter, fetch the data, then PUT to restore "default" metadata + filter, then
restore the page filter -- all wrapped in a try/except. That repeated pattern
caused four problems:

* heavy duplication (30-60 lines of boilerplate per tool),
* extra round trips (every ``update_tool_metadata`` call did a GET and a PUT),
* hardcoded restore values (which clobbered any customization the user had
  saved in their QuantData UI), and
* race conditions between concurrent tool calls (the page filter is
  server-side global state).

``tool_context`` solves all four. It snapshots the original metadata on enter,
applies page + tool mutations via a single PUT, and restores the snapshot via
a second PUT on exit. A module-level :class:`threading.Lock` serializes the
mutations so two concurrent tool calls cannot interleave their page filters.

The lock is :class:`threading.Lock` (not ``asyncio.Lock``) because FastMCP's
stdio runner dispatches sync tool functions on a worker thread.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterator

from quantdata_mcp.client import QuantDataAuthError, QuantDataClient

if TYPE_CHECKING:
    from quantdata_mcp.tools import ToolSpec


# Module-level lock that serializes page filter + tool metadata mutations.
# QuantData's page filter is server-side global state -- if two tool calls
# both set the page (e.g. one to SPX, one to AAPL), the responses will
# interleave and corrupt each other. FastMCP can dispatch tool calls
# concurrently, so we serialize on the client side.
#
# RLock so an outer ``page_filter_context`` can host inner ``tool_context``
# calls (with ``skip_page_filter=True``) on the same thread without
# deadlocking. Other threads still block as expected.
_mutation_lock = threading.RLock()


# Auth-error message shown to the LLM when the QuantData JWT has expired.
# Surface this with re-run instructions instead of a generic "Error fetching X".
AUTH_ERROR_MESSAGE = (
    "Your QuantData auth token has expired. Re-run: "
    "quantdata-mcp setup --auth-token <NEW_TOKEN> --instance-id <SAME_INSTANCE_ID>"
)


# Module-level cache of the "active" page state. Updated whenever the user
# explicitly sets a page (via ``qd_set_page_date``) or implicitly sets one
# (via a ``qd_get_*`` call with explicit ticker/date args). When a tool call
# is made WITHOUT explicit ticker/date args, ``tool_context`` resolves the
# missing pieces from this cache instead of falling back to "today/SPX" ---
# so context sticks across calls.
_active_page: dict[str, str | None] = {
    "session_date": None,
    "ticker": None,
    "expiration_date": None,
}


def get_active_page() -> dict[str, str | None]:
    """Return a snapshot of the active page state. Used by tests / introspection."""
    return dict(_active_page)


def update_active_page(
    *,
    session_date: str | None = None,
    ticker: str | None = None,
    expiration_date: str | None = None,
) -> None:
    """Update the cached active page state. Only non-None args overwrite —
    pass ``None`` to keep the existing cached value for that field.
    """
    if session_date is not None:
        _active_page["session_date"] = session_date
    if ticker is not None:
        _active_page["ticker"] = ticker
    if expiration_date is not None:
        _active_page["expiration_date"] = expiration_date


def clear_active_page() -> None:
    """Reset the cache. Used by tests for isolation."""
    _active_page["session_date"] = None
    _active_page["ticker"] = None
    _active_page["expiration_date"] = None


def _today() -> str:
    """Return today's date in YYYY-MM-DD (Eastern Time, since market data is keyed by ET)."""
    et = timezone(timedelta(hours=-4))  # EDT (summer); close enough for date boundary
    return datetime.now(et).strftime("%Y-%m-%d")


def _eq(value: Any) -> dict[str, Any]:
    """Return a QuantData ``EQUALS`` filter clause around ``value``.

    Many filter dicts have the shape::

        {"filterOperationType": "EQUALS", "value": <something>}

    This helper keeps callers from re-typing that boilerplate.
    """
    return {"filterOperationType": "EQUALS", "value": value}


def _gte(value: Any) -> dict[str, Any]:
    """Return a QuantData ``GREATER_THAN_OR_EQUAL_TO`` filter clause around ``value``.

    Used for "minimum" thresholds (e.g. ``min_premium``, ``min_volume``,
    greek floors). Mirrors the shape of :func:`_eq`.
    """
    return {"filterOperationType": "GREATER_THAN_OR_EQUAL_TO", "value": value}


def _lte(value: Any) -> dict[str, Any]:
    """Return a QuantData ``LESS_THAN_OR_EQUAL_TO`` filter clause around ``value``.

    Used for "maximum" thresholds (e.g. ``max_dte``).
    """
    return {"filterOperationType": "LESS_THAN_OR_EQUAL_TO", "value": value}


def _contains(value: Any) -> dict[str, Any]:
    """Return a QuantData ``CONTAINS`` filter clause around ``value``.

    Used for substring matches (e.g. ticker fragments) where the API
    supports a CONTAINS operator on a text field.
    """
    return {"filterOperationType": "CONTAINS", "value": value}


@dataclass
class ToolContext:
    """Per-call context object yielded by :func:`tool_context`.

    Attributes:
        client: The shared :class:`QuantDataClient` instance.
        tool_spec: Resolved :class:`ToolSpec` for the current tool key.
        tool_dto: Cached tool DTO (one GET per call). Mutated locally and PUT
            on enter and exit; callers usually do not need to touch it.
        ticker: The ticker the page filter was set to.
        date: The session date the page filter was set to.
        expiration_date: The expiration date the page filter was set to (or
            ``None`` to default to ``date``).
    """

    client: QuantDataClient
    tool_spec: "ToolSpec"
    tool_dto: dict[str, Any]
    ticker: str
    date: str
    expiration_date: str | None


@contextmanager
def tool_context(
    tool_name: str,
    *,
    ticker: str | None = None,
    date: str | None = None,
    expiration_date: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
    filter_updates: dict[str, dict[str, Any] | None] | None = None,
    time_minutes: int | None = None,
    needs_tool: bool = True,
    skip_page_filter: bool = False,
    get_client: Any = None,
    get_specs: Any = None,
    get_page_id: Any = None,
) -> Iterator[ToolContext]:
    """Set up + tear down per-tool state with snapshot/restore semantics.

    On enter:
      1. Acquire the module-level mutation lock.
      2. (Unless ``skip_page_filter``) set the page filter (date / ticker /
         expiration).
      3. (If ``needs_tool``) GET the tool DTO once and snapshot its metadata.
      4. Apply ``metadata_updates``, ``filter_updates``, and -- if
         ``time_minutes`` is given -- ``numberOfMinutesIntoMarketOpen`` to
         the in-memory DTO and PUT it back. ONE round trip total, even when
         time scrubbing is in play.

    On exit:
      1. Restore the snapshotted metadata + filter via a single PUT, so any
         user customizations in the QuantData UI are not clobbered. Because
         ``numberOfMinutesIntoMarketOpen`` was added by the apply payload but
         not present in the snapshot, the restore PUT also drops the time
         scrubber back to live mode -- no extra GET/PUT needed.
      2. (Unless ``skip_page_filter``) restore the page filter to today/SPX
         if it was changed away from defaults.
      3. Release the lock.

    Total network cost per call: 1 page-filter PUT + 1 tool GET + 2 tool PUTs
    (vs. the old 1 page-filter PUT + 3 tool GETs + 3 tool PUTs). The cost
    holds even when ``time_minutes`` is set -- folding the time scrubber into
    the existing apply/restore PUT pair avoids the extra GET+PUT that
    ``client.set_tool_time`` / ``client.reset_to_live`` would do.

    Args:
        tool_name: Key in the spec registry (e.g. ``"net_drift"``).
        ticker: Ticker for the page filter. ``None`` (default) inherits the
            cached active page from a prior ``qd_set_page_date`` or tool call;
            falls through to ``"SPX"`` only when the cache is empty.
        date: Session date (``YYYY-MM-DD``). ``None`` (default) inherits the
            cached active page; falls through to today (ET) only when the
            cache is empty.
        expiration_date: Expiration date. ``None`` (default) inherits the
            cached active page; falls through to ``date`` (0DTE) when neither
            cache nor caller specified a value.
        metadata_updates: Top-level metadata fields to set on the tool
            (e.g. ``{"greekModeType": "DELTA"}``). Pass ``None`` to skip.
        filter_updates: Filter dict updates merged into ``metadata.filter``.
            Values that are ``None`` are skipped (so callers can pass a
            single dict literal with conditional entries).
        time_minutes: Minutes-from-midnight for time scrubbing. ``None`` =
            live mode. Folded into the apply PUT as
            ``numberOfMinutesIntoMarketOpen``; restore PUT drops it.
        needs_tool: Set to ``False`` for tools that only need the page filter
            and no tool-level mutations (e.g. ``qd_get_max_pain``,
            ``qd_get_oi_by_strike``). Skips the GET + PUT pair entirely.
        skip_page_filter: Set to ``True`` when an OUTER scope is already
            managing the page filter (e.g. ``qd_get_market_snapshot`` wrapping
            six fetches under a single page filter). Avoids redundant
            page-filter PUTs.
        get_client / get_specs / get_page_id: Injected accessors. The server
            module passes its lazy-loading helpers; tests can pass mocks.
    """
    # Late binding to avoid circular imports with server.py.
    if get_client is None or get_specs is None or get_page_id is None:
        from quantdata_mcp import server as _srv

        if get_client is None:
            get_client = _srv._get_client
        if get_specs is None:
            get_specs = _srv._get_specs
        if get_page_id is None:
            get_page_id = _srv._get_page_id

    # Resolve None args from the cached active page; only fall through to
    # hardcoded defaults when both the caller and the cache are empty.
    session_date = date or _active_page.get("session_date") or _today()
    resolved_ticker: str = ticker or _active_page.get("ticker") or "SPX"
    resolved_expiration = expiration_date or _active_page.get("expiration_date")

    with _mutation_lock:
        client: QuantDataClient = get_client()
        spec = get_specs()[tool_name]

        # 1. Page filter -- skip when an outer scope owns it.
        if not skip_page_filter:
            client.set_page_filter(
                get_page_id(),
                session_date=session_date,
                ticker=resolved_ticker,
                expiration_date=resolved_expiration,
            )
            # Persist this as the active page so subsequent calls inherit.
            update_active_page(
                session_date=session_date,
                ticker=resolved_ticker,
                expiration_date=resolved_expiration,
            )

        snapshot_metadata: dict[str, Any] | None = None
        tool_dto: dict[str, Any] = {}
        # Keep the un-mutated DTO scaffold separately so the apply / restore
        # PUTs each receive a FRESH dict. NOTE: this is NOT needed for
        # production correctness -- ``requests.put(json=dict)`` serializes the
        # body synchronously inside the call, so mutating the source dict
        # afterward has no effect on the already-sent request. The reason we
        # keep separate dicts is purely to make the test suite's
        # ``MagicMock.call_args_list`` assertions readable: that recorder
        # stores REFERENCES to the dicts passed in, so two PUTs sharing one
        # dict would each show the FINAL (post-mutation) state when inspected
        # later. Splitting into a scaffold + per-PUT metadata dict keeps each
        # recorded call payload independent.
        tool_dto_scaffold: dict[str, Any] = {}

        try:
            if needs_tool:
                # 2. Single GET; snapshot metadata for restore.
                fetched = client.get_tool(spec.tool_id)
                if fetched is None:
                    raise RuntimeError(
                        f"Failed to fetch tool {spec.tool_id} ({tool_name}) for snapshot"
                    )
                tool_dto = fetched
                # Snapshot original metadata + filter (shallow copy of each --
                # we never mutate values inside individual filter clauses).
                # The snapshot is OPAQUE: any unknown / future top-level
                # metadata keys round-trip unchanged through the restore PUT.
                original_metadata = dict(tool_dto.get("metadata", {}))
                original_filter = dict(original_metadata.get("filter", {}))
                snapshot_metadata = dict(original_metadata)
                snapshot_metadata["filter"] = dict(original_filter)

                # The DTO scaffold has every top-level key EXCEPT ``metadata``;
                # we'll attach a freshly built metadata dict per PUT.
                tool_dto_scaffold = {k: v for k, v in tool_dto.items() if k != "metadata"}

                # 3. Apply mutations -- a brand-new metadata dict so the apply
                # payload is not aliased to the restore payload.
                new_metadata = dict(original_metadata)
                new_metadata["filter"] = dict(original_filter)
                if metadata_updates:
                    for k, v in metadata_updates.items():
                        new_metadata[k] = v
                if filter_updates:
                    for k, v in filter_updates.items():
                        if v is None:
                            continue
                        new_metadata["filter"][k] = v
                # Time scrubber rides along with the apply PUT. Because the
                # snapshot was taken BEFORE this assignment, the restore PUT
                # naturally drops the key back to live mode.
                if time_minutes is not None:
                    new_metadata["numberOfMinutesIntoMarketOpen"] = time_minutes

                apply_payload = dict(tool_dto_scaffold)
                apply_payload["metadata"] = new_metadata
                apply_payload["lastUpdatedTime"] = int(
                    datetime.now(UTC).timestamp() * 1000
                )
                client._make_request("PUT", "tool", json=apply_payload, timeout=10)

            yield ToolContext(
                client=client,
                tool_spec=spec,
                tool_dto=tool_dto,
                ticker=resolved_ticker,
                date=session_date,
                expiration_date=resolved_expiration,
            )
        finally:
            # 4. Restore snapshotted tool metadata via a single PUT (fresh
            # payload). This also drops ``numberOfMinutesIntoMarketOpen`` if
            # we set it as a transient time scrubber for this call —
            # PERSISTENT scrubbers set via ``client.set_tool_time`` are
            # preserved because the snapshot was taken AFTER they landed.
            if needs_tool and snapshot_metadata is not None:
                try:
                    restore_payload = dict(tool_dto_scaffold)
                    restore_payload["metadata"] = snapshot_metadata
                    restore_payload["lastUpdatedTime"] = int(
                        datetime.now(UTC).timestamp() * 1000
                    )
                    client._make_request(
                        "PUT", "tool", json=restore_payload, timeout=10
                    )
                except Exception:  # pragma: no cover - best effort
                    pass

            # NOTE: previously this block also reset the PAGE filter back to
            # today/SPX. We deliberately don't do that anymore — the page
            # filter is now sticky across calls so the user can keep their
            # context (ticker / date / expiration) across many tool calls
            # and watch the QuantData browser stay in sync. To return to
            # today's session, callers explicitly pass ``date=today,
            # ticker="SPX"`` or call ``qd_set_page_date(...)``.


@contextmanager
def page_filter_context(
    *,
    ticker: str | None = None,
    date: str | None = None,
    expiration_date: str | None = None,
    get_client: Any = None,
    get_page_id: Any = None,
) -> Iterator[None]:
    """Apply the page filter ONCE around a block of inner fetches.

    Use this to wrap a batch of ``tool_context(..., skip_page_filter=True)``
    calls so the page-filter PUT happens exactly once instead of once per
    inner call. ``qd_get_market_snapshot`` uses this to keep its 6 section
    fetches under one shared page filter.

    Acquires the same module-level mutation lock as ``tool_context`` so the
    apply + entire batch is atomic relative to other tool calls.

    None args inherit from the cached active page (same resolution rules as
    :func:`tool_context`). After the block finishes the page filter stays
    where it was set — sticky across calls.
    """
    if get_client is None or get_page_id is None:
        from quantdata_mcp import server as _srv

        if get_client is None:
            get_client = _srv._get_client
        if get_page_id is None:
            get_page_id = _srv._get_page_id

    session_date = date or _active_page.get("session_date") or _today()
    resolved_ticker: str = ticker or _active_page.get("ticker") or "SPX"
    resolved_expiration = expiration_date or _active_page.get("expiration_date")

    with _mutation_lock:
        client: QuantDataClient = get_client()
        client.set_page_filter(
            get_page_id(),
            session_date=session_date,
            ticker=resolved_ticker,
            expiration_date=resolved_expiration,
        )
        update_active_page(
            session_date=session_date,
            ticker=resolved_ticker,
            expiration_date=resolved_expiration,
        )
        yield


def format_error(operation: str, exc: BaseException) -> str:
    """Convert an exception into a user-facing error string.

    Special-cases :class:`QuantDataAuthError` so the LLM gets actionable
    re-setup instructions instead of a vague "Error fetching X" message.
    """
    if isinstance(exc, QuantDataAuthError):
        return AUTH_ERROR_MESSAGE
    return f"Error fetching {operation}: {exc}"
