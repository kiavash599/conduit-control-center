# SPDX-License-Identifier: MIT
"""
Unit tests for the LOCAL-DAY bucket grid in backend/traffic/reads.py (v0.3.22).

Storage stays UTC. For the 7d/30d ranges the dense grid may be built in the
operator's display timezone, re-aggregated at read time from the hourly rollup
(the documented source of truth) rather than the UTC-day convenience cache,
because a UTC calendar day is NOT a local calendar day.

Covered here:
  - local-day -> UTC instant ranges, including DST 23 h / 25 h days
  - sub-hour offsets (Asia/Yangon +06:30, Asia/Kathmandu +05:45)
  - whole-hour sums from the hourly rollup
  - boundary hours resolved EXACTLY from traffic_delta
  - deltas aged out -> hour attributed to the day it starts in, flagged
    `approximate` rather than silently split
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.traffic import reads
from backend.traffic.schema import TRAFFIC_DDL

YANGON = ZoneInfo("Asia/Yangon")          # +06:30 (sub-hour)
STOCKHOLM = ZoneInfo("Europe/Stockholm")  # +01:00 / +02:00 (DST)
KATHMANDU = ZoneInfo("Asia/Kathmandu")    # +05:45 (quarter-hour)


# --------------------------------------------------------------------------- #
# Pure grid helpers
# --------------------------------------------------------------------------- #
class TestLocalDayRanges:
    def test_whole_hour_zone_is_24h(self):
        s, e = reads._local_day_utc_range("2026-07-25", STOCKHOLM)
        assert (e - s).total_seconds() == 24 * 3600
        assert s == datetime(2026, 7, 24, 22, 0, tzinfo=timezone.utc)

    def test_sub_hour_zone_boundaries_are_offset(self):
        """Asia/Yangon (+06:30): the local day starts at 17:30 UTC the day before."""
        s, e = reads._local_day_utc_range("2026-07-25", YANGON)
        assert s == datetime(2026, 7, 24, 17, 30, tzinfo=timezone.utc)
        assert e == datetime(2026, 7, 25, 17, 30, tzinfo=timezone.utc)

    def test_quarter_hour_zone(self):
        s, _ = reads._local_day_utc_range("2026-07-25", KATHMANDU)
        assert s == datetime(2026, 7, 24, 18, 15, tzinfo=timezone.utc)

    def test_dst_spring_forward_day_is_23h(self):
        s, e = reads._local_day_utc_range("2026-03-29", STOCKHOLM)
        assert (e - s).total_seconds() == 23 * 3600

    def test_dst_fall_back_day_is_25h(self):
        s, e = reads._local_day_utc_range("2026-10-25", STOCKHOLM)
        assert (e - s).total_seconds() == 25 * 3600

    def test_day_keys_are_local_and_ordered(self):
        # 02:00 UTC is already the same calendar day 08:30 local in Yangon.
        keys = reads._local_day_keys("2026-07-25T02:00:00Z", 3, YANGON)
        assert keys == ["2026-07-23", "2026-07-24", "2026-07-25"]

    def test_day_keys_roll_over_before_utc_midnight(self):
        """22:00 UTC on the 24th is already the 25th in Yangon (+06:30)."""
        keys = reads._local_day_keys("2026-07-24T22:00:00Z", 1, YANGON)
        assert keys == ["2026-07-25"]


# --------------------------------------------------------------------------- #
# Aggregation against a real SQLite database
# --------------------------------------------------------------------------- #
@pytest.fixture
def db(tmp_path):
    """A synchronous sqlite3 connection wrapped to look like aiosqlite enough
    for these read helpers (execute/fetchall/fetchone are awaited)."""
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    for ddl in TRAFFIC_DDL:
        conn.execute(ddl)
    conn.commit()

    class _Async:
        def __init__(self, c):
            self._c = c

        async def execute(self, sql, params=()):
            cur = self._c.execute(sql, params)

            class _Cur:
                async def fetchall(_s):
                    return cur.fetchall()

                async def fetchone(_s):
                    return cur.fetchone()

            return _Cur()

    yield _Async(conn), conn
    conn.close()


def _add_hour(conn, bucket_utc, up, down):
    conn.execute(
        "INSERT OR REPLACE INTO traffic_rollup_hourly (bucket_utc, bytes_up, bytes_down, samples)"
        " VALUES (?, ?, ?, 1)",
        (bucket_utc, up, down),
    )
    conn.commit()


def _add_delta(conn, ts_utc, up, down, epoch=1):
    conn.execute(
        "INSERT OR IGNORE INTO traffic_epoch (id, started_at_utc, first_uptime_seconds, reason)"
        " VALUES (?, '2026-01-01T00:00:00Z', 1.0, 'startup')",
        (epoch,),
    )
    conn.execute(
        "INSERT INTO traffic_delta (ts_utc, seq, epoch_id, interval_seconds,"
        " bytes_up_delta, bytes_down_delta, source, anomaly_flag, counted)"
        " VALUES (?, (SELECT COALESCE(MAX(seq),0)+1 FROM traffic_delta), ?, 60, ?, ?, 'normal', 'none', 1)",
        (ts_utc, epoch, up, down),
    )
    conn.commit()


class TestLocalDayAggregation:
    async def test_whole_hour_zone_sums_hourly_rollups(self, db):
        adb, conn = db
        # Local 2026-07-25 in Stockholm == 2026-07-24T22:00Z .. 2026-07-25T22:00Z
        _add_hour(conn, "2026-07-24T22:00:00Z", 10, 100)
        _add_hour(conn, "2026-07-25T21:00:00Z", 5, 50)
        _add_hour(conn, "2026-07-25T22:00:00Z", 999, 999)  # next local day
        s, e = reads._local_day_utc_range("2026-07-25", STOCKHOLM)
        up, down, approx = await reads._local_day_totals(adb, s, e, None)
        assert (up, down) == (15, 150)
        assert approx is False   # whole-hour zone -> no bisected boundary

    async def test_sub_hour_boundary_resolved_from_deltas(self, db):
        adb, conn = db
        # Yangon local 2026-07-25 == 07-24T17:30Z .. 07-25T17:30Z.
        # Whole hours 18:00Z..17:00Z contribute via the rollup;
        # the 17:00-18:00Z hour is split at :30 on both ends.
        _add_hour(conn, "2026-07-24T18:00:00Z", 100, 1000)
        _add_delta(conn, "2026-07-24T17:45:00Z", 7, 70)    # inside the day
        _add_delta(conn, "2026-07-24T17:15:00Z", 999, 999)  # before the day
        _add_delta(conn, "2026-07-25T17:15:00Z", 3, 30)    # inside (trailing)
        _add_delta(conn, "2026-07-25T17:45:00Z", 888, 888)  # after the day
        s, e = reads._local_day_utc_range("2026-07-25", YANGON)
        delta_floor = datetime(2026, 7, 1, tzinfo=timezone.utc)  # deltas cover it
        up, down, approx = await reads._local_day_totals(adb, s, e, delta_floor)
        assert (up, down) == (110, 1100)   # 100 rollup + 7 + 3
        assert approx is False             # exactly resolved

    async def test_sub_hour_boundary_marked_approximate_when_deltas_aged_out(self, db):
        adb, conn = db
        _add_hour(conn, "2026-07-24T18:00:00Z", 100, 1000)
        s, e = reads._local_day_utc_range("2026-07-25", YANGON)
        # Deltas only start well AFTER this window -> cannot split the boundary.
        delta_floor = datetime(2026, 9, 1, tzinfo=timezone.utc)
        up, down, approx = await reads._local_day_totals(adb, s, e, delta_floor)
        assert approx is True
        assert (up, down) == (100, 1000)

    async def test_series_uses_local_day_keys_and_reports_timezone(self, db):
        adb, conn = db
        _add_hour(conn, "2026-07-24T18:00:00Z", 100, 1000)
        out = await reads.get_series(
            adb, range_key="7d", now_ts="2026-07-25T02:00:00Z", tz=YANGON
        )
        assert out["granularity"] == "day"
        keys = [b["bucket_utc"] for b in out["buckets"]]
        assert keys == sorted(keys) and len(keys) == 7
        assert keys[-1] == "2026-07-25"          # local today
        total = sum(b["bytes_up"] for b in out["buckets"])
        assert total == 100

    async def test_utc_tz_preserves_original_daily_behaviour(self, db):
        """tz=None (or UTC) must keep reading the UTC-day cache unchanged."""
        adb, conn = db
        conn.execute(
            "INSERT INTO traffic_rollup_daily (bucket_utc, bytes_up, bytes_down, samples)"
            " VALUES ('2026-07-25', 42, 420, 1)"
        )
        conn.commit()
        out = await reads.get_series(adb, range_key="7d", now_ts="2026-07-25T02:00:00Z")
        found = {b["bucket_utc"]: b["bytes_up"] for b in out["buckets"]}
        assert found["2026-07-25"] == 42
