"""Tests for timezone-aware date helpers and market-session filtering.

These tests cover the DST-correctness of `_today()` and the market-open
cutoff used by `_fmt_drift()`. They patch `datetime.now()` so we can
deterministically simulate a UTC instant and verify the resulting Eastern
Time conversion is correct in both EDT and EST.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from quantdata_mcp import server


class _FrozenDatetime(datetime):
    """A datetime subclass whose `now()` returns a fixed instant.

    The `_now_utc` class attribute (set per test) is a timezone-aware UTC
    datetime. `now(tz)` returns that instant converted to `tz` (or UTC if
    `tz` is None). All other datetime behavior (constructors, .date(),
    .timestamp(), fromtimestamp, arithmetic) is inherited unchanged.
    """

    _now_utc: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        assert tz is not None, "test helper requires explicit tz"
        return cls._now_utc.astimezone(tz)


def _patch_clock(monkeypatch, when_utc: datetime) -> None:
    """Patch the `datetime` symbol used inside server.py to a frozen clock."""
    _FrozenDatetime._now_utc = when_utc.astimezone(timezone.utc)
    monkeypatch.setattr(server, "datetime", _FrozenDatetime)


# ---------------------------------------------------------------------------
# _today()
# ---------------------------------------------------------------------------


def test_today_during_edt(monkeypatch):
    """In July (EDT, UTC-4), 2026-07-15 03:00 UTC is 2026-07-14 23:00 ET."""
    _patch_clock(monkeypatch, datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc))
    assert server._today() == "2026-07-14"


def test_today_during_est(monkeypatch):
    """In January (EST, UTC-5), 2026-01-15 02:00 UTC is 2026-01-14 21:00 ET.

    With the buggy hardcoded EDT offset (UTC-4), this would compute 22:00
    on the 14th — same date by luck, but the offset is wrong. The clearer
    failure is at 03:30 UTC: EDT-4 -> 23:30 on the 14th, EST-5 -> 22:30 on
    the 14th, so we use a UTC time that crosses midnight differently under
    each offset.
    """
    # 2026-01-15 04:30 UTC:
    #   EST (-5) -> 2026-01-14 23:30 (correct)
    #   EDT (-4) -> 2026-01-15 00:30 (buggy: would say "2026-01-15")
    _patch_clock(monkeypatch, datetime(2026, 1, 15, 4, 30, tzinfo=timezone.utc))
    assert server._today() == "2026-01-14"


# ---------------------------------------------------------------------------
# _fmt_drift() market-open cutoff
# ---------------------------------------------------------------------------


def _ms(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def test_fmt_drift_market_open_cutoff_in_winter(monkeypatch):
    """During EST, market open is 9:30 ET = 14:30 UTC (not 13:30 UTC).

    The buggy code computed market open as a hardcoded 13:30 UTC, which is
    correct in EDT but an hour too early in EST. So a 9:00 ET pre-market
    entry (= 14:00 UTC in winter) would be wrongly included.

    Build a mock response with one pre-market entry (09:00 ET = 14:00 UTC)
    and one regular-session entry (10:00 ET = 15:00 UTC). With the fix,
    the pre-market entry is filtered out.
    """
    # Pretend "now" is mid-morning on a winter trading day so `_today()`
    # picks the correct ET date.
    et = ZoneInfo("America/New_York")
    now_et = datetime(2026, 1, 15, 11, 0, tzinfo=et)  # 11 AM ET
    _patch_clock(monkeypatch, now_et)

    pre_market_et = datetime(2026, 1, 15, 9, 0, tzinfo=et)   # 14:00 UTC (pre-market)
    regular_et = datetime(2026, 1, 15, 10, 0, tzinfo=et)     # 15:00 UTC (regular session)

    pre_market_ts = _ms(pre_market_et)
    regular_ts = _ms(regular_et)

    # netDrift entries: [ts, callPremium, _, _, putPremium, _, _, spotPrice]
    data = {
        "response": {
            "netDrift": [
                [pre_market_ts, 100_00, 0, 0, 50_00, 0, 0, 5000_00],
                [regular_ts, 200_00, 0, 0, 75_00, 0, 0, 5010_00],
            ]
        }
    }

    out = server._fmt_drift(data, last_n=10)

    # The label says "of N session" entries — only the regular-session one
    # should count.
    assert "of 1 session" in out, out

    # Pre-market timestamp must not appear in any rendered row.
    assert "09:00:00" not in out, out
    # Regular-session row should appear.
    assert "10:00:00" in out, out


def test_fmt_drift_falls_back_when_all_entries_are_pre_market(monkeypatch):
    """If every entry is before market open, the formatter falls back to
    showing all of them rather than returning empty output.
    """
    et = ZoneInfo("America/New_York")
    now_et = datetime(2026, 1, 15, 11, 0, tzinfo=et)
    _patch_clock(monkeypatch, now_et)

    pre_market_ts = _ms(datetime(2026, 1, 15, 9, 0, tzinfo=et))
    data = {
        "response": {
            "netDrift": [
                [pre_market_ts, 10_00, 0, 0, 5_00, 0, 0, 5000_00],
            ]
        }
    }

    out = server._fmt_drift(data, last_n=10)
    assert "of 1 session" in out, out
    assert "09:00:00" in out, out
