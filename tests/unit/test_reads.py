# SPDX-License-Identifier: MIT
"""
Unit tests for backend/traffic/reads.py (Traffic Read API read-layer).

Covers:
  - get_summary: empty DB (not recording) and populated (lifetime, windows,
    status/last_ok), with window boundaries (rollups outside the window excluded)
  - get_series: 24h hourly dense/zero-filled grid; 7d/30d daily grids;
    bucket ordering; invalid range raises
All reads are exercised against in-memory aiosqlite with seeded P0 tables.
"""
from __future__ import annotations

import aiosqlite
import pytest

from backend.traffic import reads
from backend.traffic.schema import apply_traffic_schema

# Fixed reference time: hour floor = 2026-06-13T12:00:00Z; today = 2026-06-13.
NOW = "2026-06-13T12:30:00Z"


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await apply_traffic_schema(conn)
        await conn.commit()
        yield conn


async def _epoch(db, started, eid=1):
    await db.execute(
        "INSERT INTO traffic_epoch (id, started_at_utc, first_uptime_seconds, reason) "
        "VALUES (?, ?, 1.0, 'startup')", (eid, started),
    )


async def _delta(db, ts, up, down, seq, counted=1, eid=1):
    src = "normal" if counted else "initial_baseline"
    await db.execute(
        "INSERT INTO traffic_delta "
        "(ts_utc, seq, epoch_id, interval_seconds, bytes_up_delta, bytes_down_delta, "
        " source, anomaly_flag, counted) VALUES (?, ?, ?, 60, ?, ?, ?, 'none', ?)",
        (ts, seq, eid, up, down, src, counted),
    )


async def _hourly(db, bucket, up, down):
    await db.execute(
        "INSERT INTO traffic_rollup_hourly (bucket_utc, bytes_up, bytes_down, samples) "
        "VALUES (?, ?, ?, 1)", (bucket, up, down),
    )


async def _daily(db, bucket, up, down):
    await db.execute(
        "INSERT INTO traffic_rollup_daily (bucket_utc, bytes_up, bytes_down, samples) "
        "VALUES (?, ?, ?, 1)", (bucket, up, down),
    )


async def _set_health(db, status, last_ok):
    await db.execute(
        "UPDATE collector_health SET status = ?, last_ok_ts_utc = ? WHERE id = 1",
        (status, last_ok),
    )


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


class TestSummary:
    async def test_empty_db_not_recording(self, db):
        s = await reads.get_summary(db, now_ts=NOW)
        assert s["recording_since"] is None
        assert s["lifetime"] is None
        assert s["status"] == "disabled"          # bootstrap-seeded
        assert s["last_ok_ts_utc"] is None
        assert s["windows"]["last_24h"] == {"bytes_up": 0, "bytes_down": 0}
        assert s["windows"]["last_7d"] == {"bytes_up": 0, "bytes_down": 0}

    async def test_populated(self, db):
        await _epoch(db, "2026-06-12T20:00:00Z")
        # counted deltas -> lifetime (no checkpoint -> Σ counted = 130/240)
        await _delta(db, "2026-06-13T10:00:00Z", 100, 200, seq=1)
        await _delta(db, "2026-06-13T11:00:00Z", 30, 40, seq=2)
        # hourly rollups: one inside the 24h window, one before the cutoff
        await _hourly(db, "2026-06-13T10:00:00Z", 50, 60)      # in window
        await _hourly(db, "2026-06-12T08:00:00Z", 999, 999)    # < cutoff 2026-06-12T13:00 -> excluded
        # daily rollups: two inside 7d, one outside
        await _daily(db, "2026-06-13", 100, 110)               # in 7d
        await _daily(db, "2026-06-10", 20, 25)                 # in 7d
        await _daily(db, "2026-05-01", 999, 999)               # outside 7d
        await _set_health(db, "running", "2026-06-13T12:29:00Z")
        await db.commit()

        s = await reads.get_summary(db, now_ts=NOW)
        assert s["recording_since"] == "2026-06-12T20:00:00Z"
        assert s["lifetime"] == {"bytes_up": 130, "bytes_down": 240}
        assert s["status"] == "running"
        assert s["last_ok_ts_utc"] == "2026-06-13T12:29:00Z"
        assert s["windows"]["last_24h"] == {"bytes_up": 50, "bytes_down": 60}
        assert s["windows"]["last_7d"] == {"bytes_up": 120, "bytes_down": 135}

    async def test_recording_with_zero_lifetime(self, db):
        # epoch exists but no counted deltas -> lifetime present but zero
        await _epoch(db, "2026-06-13T00:00:00Z")
        await db.commit()
        s = await reads.get_summary(db, now_ts=NOW)
        assert s["recording_since"] == "2026-06-13T00:00:00Z"
        assert s["lifetime"] == {"bytes_up": 0, "bytes_down": 0}

    async def test_no_internal_fields_exposed(self, db):
        s = await reads.get_summary(db, now_ts=NOW)
        flat = str(s)
        assert "holder_id" not in s and "holder_id" not in flat
        assert "last_error" not in s


# ---------------------------------------------------------------------------
# get_series
# ---------------------------------------------------------------------------


class TestSeries:
    async def test_24h_dense_zero_filled(self, db):
        await _hourly(db, "2026-06-13T10:00:00Z", 5, 6)
        await _hourly(db, "2026-06-13T12:00:00Z", 7, 8)    # current hour
        await db.commit()
        s = await reads.get_series(db, range_key="24h", now_ts=NOW)
        assert s["range"] == "24h" and s["granularity"] == "hour"
        assert len(s["buckets"]) == 24
        assert s["buckets"][0]["bucket_utc"] == "2026-06-12T13:00:00Z"    # oldest
        assert s["buckets"][-1]["bucket_utc"] == "2026-06-13T12:00:00Z"   # current hour
        m = {b["bucket_utc"]: (b["bytes_up"], b["bytes_down"]) for b in s["buckets"]}
        assert m["2026-06-13T10:00:00Z"] == (5, 6)
        assert m["2026-06-13T12:00:00Z"] == (7, 8)
        assert m["2026-06-13T09:00:00Z"] == (0, 0)         # zero-filled gap

    async def test_7d_daily_dense(self, db):
        await _daily(db, "2026-06-13", 1, 1)
        await _daily(db, "2026-06-11", 2, 2)
        await db.commit()
        s = await reads.get_series(db, range_key="7d", now_ts=NOW)
        assert s["granularity"] == "day" and len(s["buckets"]) == 7
        assert s["buckets"][0]["bucket_utc"] == "2026-06-07"
        assert s["buckets"][-1]["bucket_utc"] == "2026-06-13"
        m = {b["bucket_utc"]: (b["bytes_up"], b["bytes_down"]) for b in s["buckets"]}
        assert m["2026-06-13"] == (1, 1)
        assert m["2026-06-11"] == (2, 2)
        assert m["2026-06-12"] == (0, 0)

    async def test_30d_length(self, db):
        s = await reads.get_series(db, range_key="30d", now_ts=NOW)
        assert s["granularity"] == "day" and len(s["buckets"]) == 30
        assert s["buckets"][-1]["bucket_utc"] == "2026-06-13"

    async def test_invalid_range_raises(self, db):
        with pytest.raises(ValueError):
            await reads.get_series(db, range_key="bogus", now_ts=NOW)
