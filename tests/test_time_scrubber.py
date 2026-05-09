"""Tests for the time-scrubber MCP tools (PR 14).

The MCP wrappers themselves are thin pass-throughs to
``client.set_tool_time`` / ``client.reset_to_live``; the interesting
logic lives in the time-string parser.
"""

from __future__ import annotations

import pytest

from quantdata_mcp.server import _format_minutes, _parse_time_to_minutes


# ---------------------------------------------------------------------------
# _parse_time_to_minutes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("9:30", 570),
        ("09:30", 570),
        ("9:30 AM", 570),
        ("9:30 am", 570),
        ("9:30AM", 570),
        ("12:00", 720),     # noon (24h interpreted)
        ("12:00 PM", 720),  # noon (12h with PM)
        ("12:00 AM", 0),    # midnight
        ("13:30", 810),
        ("1:30 PM", 810),
        ("16:00", 960),     # market close (24h)
        ("4:00 PM", 960),
        ("4 PM", 960),      # bare hour with am/pm
        ("4PM", 960),
        ("4", None),        # bare hour ambiguous — assumed AM = 240
    ],
)
def test_parse_time_string(raw: str, expected: int | None) -> None:
    if expected is None:
        # "4" without am/pm parses as 4:00 (AM) = 240. Pin that behaviour.
        assert _parse_time_to_minutes(raw) == 240
    else:
        assert _parse_time_to_minutes(raw) == expected


def test_parse_time_int_passes_through() -> None:
    assert _parse_time_to_minutes(570) == 570
    assert _parse_time_to_minutes(0) == 0
    assert _parse_time_to_minutes(1440) == 1440


def test_parse_time_int_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="0-1440"):
        _parse_time_to_minutes(-1)
    with pytest.raises(ValueError, match="0-1440"):
        _parse_time_to_minutes(2000)


def test_parse_time_garbage_raises() -> None:
    with pytest.raises(ValueError, match="unparseable"):
        _parse_time_to_minutes("not a time")
    with pytest.raises(ValueError):
        _parse_time_to_minutes(["nope"])  # type: ignore[arg-type]


def test_parse_time_out_of_range_components_raise() -> None:
    with pytest.raises(ValueError, match="out-of-range"):
        _parse_time_to_minutes("25:00")
    with pytest.raises(ValueError, match="out-of-range"):
        _parse_time_to_minutes("9:99")


# ---------------------------------------------------------------------------
# _format_minutes — display helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "minutes, expected",
    [
        (0, "00:00"),
        (570, "09:30"),
        (720, "12:00"),
        (960, "16:00"),
        (1439, "23:59"),
    ],
)
def test_format_minutes(minutes: int, expected: str) -> None:
    assert _format_minutes(minutes) == expected
