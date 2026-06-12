# SPDX-License-Identifier: MIT
"""
Unit tests for backend/traffic/retention.py (P0 Step 5).

Test matrix I (rollups / checkpoint / lifetime) and H (pruning invariants):
  - hourly + daily rollups accumulate into UTC buckets; samples increment
  - compute_lifetime: Σ counted with no checkpoint; checkpoint + Σ-since with one
  - write_due_checkpoint: writes yesterday's cumulative; idempotent; data-gated
  - LIFETIME INVARIANT: compute_lifetime is unchanged after checkpoint + prune
  - prune: latest snapshot never removed; aged snapshots removed (delta link
    SET NULL); deltas removed only when aged AND covered by a checkpoint;
    deltas after the checkpoint are never removed; hourly removed only when the
    day has a daily rollup; daily + checkpoints never removed
"""
from __future__ import annotations

import aiosqlite
import pytest

from backend.traffic.retention import (
    compute_lifetime,
    prune,
    upsert_rollups,
    write_due_checkpoint,
)
from backend.traffic.schema import apply_traffic_schema

NOW = "2026-06-12T12:00:00Z"        # today=2026-06-12, yesterday=2026-06-11
OLD = "2026-01-01T08:00:00Z"        # > 90 days before NOW
YESTERDAY = "2026-06-11T20:00:00Z"


@pytest.fixture
async def tdb():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await apply_traffic_schema(conn)
        await conn.execute(
            "INSERT INTO traffic_epoch (id, started_at_utc, first_uptime_seconds, reason) "
            "VALUES (1, ?, 1.0, 'startup')", (OLD,)
        )
        await conn.commit()
        yield conn


_seq = iter(range(1, 10_000))


async def _delta(db, ts, up, down, counted=1):
    src = "normal" if counted else "initial_baseline"
    await db.execute(
        "INSERT INTO traffic_delta "
        "(ts_utc, seq, epoch_id, interval_seconds, bytes_up_delta, bytes_down_delta, "
        " source, anomaly_flag, counted) VALUES (?, ?, 1, 60, ?, ?, ?, 'none', ?)",
        (ts, next(_seq), up, down, src, counted),
    )


async def _snapshot(db, ts, seq):
    await db.execute(
        "INSERT INTO traffic_snapshot "
        "(ts_utc, seq, epoch_id, uptime_seconds, bytes_up, bytes_down) "
        "VALUES (?, ?, 1, 1.0, 1, 1)", (ts, seq),
    )


async def _one(db, sql, args=()):
    cur = await db.execute(sql, args)
    return await cur.fetchone()


# ---------------------------------------------------------------------------
# Rollups
# ---------------------------------------------------------------------------


class TestRollups:
    async def test_hourly_and_daily_accumulate(self, tdb):
        await upsert_rollups(tdb, ts_utc="2026-06-12T15:30:00Z", bytes_up=100, bytes_down=200)
        await upsert_rollups(tdb, ts_utc="2026-06-12T15:45:00Z", bytes_up=10, bytes_down=20)
        await tdb.commit()
        h = await _one(tdb, "SELECT * FROM traffic_rollup_hourly WHERE bucket_utc='2026-06-12T15:00:00Z'")
        assert h["bytes_up"] == 110 and h["bytes_down"] == 220 and h["samples"] == 2
        d = await _one(tdb, "SELECT * FROM traffic_rollup_daily WHERE bucket_utc='2026-06-12'")
        assert d["bytes_up"] == 110 and d["bytes_down"] == 220 and d["samples"] == 2

    async def test_separate_hours(self, tdb):
        await upsert_rollups(tdb, ts_utc="2026-06-12T15:10:00Z", bytes_up=5, bytes_down=5)
        await upsert_rollups(tdb, ts_utc="2026-06-12T16:10:00Z", bytes_up=7, bytes_down=7)
        await tdb.commit()
        cur = await tdb.execute("SELECT COUNT(*) FROM traffic_rollup_hourly")
        assert (await cur.fetchone())[0] == 2


# ---------------------------------------------------------------------------
# Lifetime + checkpoint
# ---------------------------------------------------------------------------


class TestLifetimeAndCheckpoint:
    async def test_lifetime_no_checkpoint_sums_counted_only(self, tdb):
        await _delta(tdb, NOW, 100, 200, counted=1)
        await _delta(tdb, NOW, 10, 20, counted=1)
        await _delta(tdb, NOW, 999, 999, counted=0)  # excluded
        await tdb.commit()
        assert await compute_lifetime(tdb) == (110, 220)

    async def test_checkpoint_writes_yesterday_cumulative(self, tdb):
        await _delta(tdb, OLD, 1000, 2000, counted=1)        # day far in the past
        await _delta(tdb, YESTERDAY, 50, 60, counted=1)
        await _delta(tdb, NOW, 7, 8, counted=1)              # today (after checkpoint)
        await tdb.commit()

        wrote = await write_due_checkpoint(tdb, now_ts=NOW)
        await tdb.commit()
        assert wrote is True
        cp = await _one(tdb, "SELECT * FROM lifetime_checkpoint WHERE day_utc='2026-06-11'")
        assert cp["total_bytes_up"] == 1050 and cp["total_bytes_down"] == 2060

        # lifetime = checkpoint(up to yesterday) + today's counted deltas
        assert await compute_lifetime(tdb) == (1057, 2068)

        # idempotent: a second call does nothing
        assert await write_due_checkpoint(tdb, now_ts=NOW) is False

    async def test_checkpoint_data_gated(self, tdb):
        # no deltas at all -> nothing to checkpoint
        assert await write_due_checkpoint(tdb, now_ts=NOW) is False


# ---------------------------------------------------------------------------
# Lifetime invariant under pruning (matrix H golden)
# ---------------------------------------------------------------------------


class TestLifetimeInvariantUnderPruning:
    async def test_lifetime_unchanged_after_checkpoint_and_prune(self, tdb):
        await _delta(tdb, OLD, 1000, 2000, counted=1)         # aged + will be covered
        await _delta(tdb, "2026-02-01T00:00:00Z", 500, 600, counted=1)  # aged + covered
        await _delta(tdb, YESTERDAY, 50, 60, counted=1)
        await _delta(tdb, NOW, 7, 8, counted=1)
        await tdb.commit()

        before = await compute_lifetime(tdb)
        assert before == (1557, 2668)

        await write_due_checkpoint(tdb, now_ts=NOW)   # checkpoints up to yesterday
        await tdb.commit()
        counts = await prune(tdb, now_ts=NOW, delta_days=90)
        await tdb.commit()

        # the two aged deltas (covered by the checkpoint) were pruned
        assert counts["deltas"] == 2
        # lifetime is exactly preserved
        assert await compute_lifetime(tdb) == before

    async def test_deltas_after_checkpoint_never_pruned(self, tdb):
        # a counted delta dated today must survive pruning even with delta_days=0
        await _delta(tdb, OLD, 1, 1, counted=1)
        await _delta(tdb, NOW, 42, 43, counted=1)
        await tdb.commit()
        await write_due_checkpoint(tdb, now_ts=NOW)
        await tdb.commit()
        await prune(tdb, now_ts=NOW, delta_days=0)
        await tdb.commit()
        # today's delta (after the checkpoint day) is retained
        row = await _one(tdb, "SELECT COUNT(*) c FROM traffic_delta WHERE substr(ts_utc,1,10)='2026-06-12'")
        assert row["c"] == 1


# ---------------------------------------------------------------------------
# Pruning: snapshots / hourly guards
# ---------------------------------------------------------------------------


class TestPruning:
    async def test_latest_snapshot_never_pruned(self, tdb):
        await _snapshot(tdb, OLD, 1)            # aged
        await _snapshot(tdb, "2026-05-01T00:00:00Z", 2)  # aged, but newest id -> kept
        await tdb.commit()
        await prune(tdb, now_ts=NOW, snapshot_days=7)
        await tdb.commit()
        cur = await tdb.execute("SELECT seq FROM traffic_snapshot ORDER BY seq")
        rows = [r[0] for r in await cur.fetchall()]
        assert rows == [2]  # the aged-but-latest snapshot survives; the older one is gone

    async def test_pruned_snapshot_nulls_delta_link(self, tdb):
        await _snapshot(tdb, OLD, 1)
        await _snapshot(tdb, NOW, 2)  # latest
        await tdb.execute(
            "INSERT INTO traffic_delta "
            "(ts_utc, seq, epoch_id, snapshot_id, prev_snapshot_id, interval_seconds, "
            " bytes_up_delta, bytes_down_delta, source, anomaly_flag, counted) "
            "VALUES (?, 5, 1, 1, NULL, 60, 1, 1, 'normal', 'none', 1)", (OLD,),
        )
        await tdb.commit()
        await prune(tdb, now_ts=NOW, snapshot_days=7, delta_days=99999)
        await tdb.commit()
        # snapshot id=1 was aged-and-not-latest -> deleted; the delta survives, link nulled
        row = await _one(tdb, "SELECT snapshot_id FROM traffic_delta WHERE seq=5")
        assert row is not None and row["snapshot_id"] is None

    async def test_hourly_pruned_only_with_daily(self, tdb):
        # buckets aged > 180 days before NOW (2026-06-12)
        # aged hourly bucket WITHOUT a daily row -> not pruned
        await tdb.execute(
            "INSERT INTO traffic_rollup_hourly (bucket_utc, bytes_up, bytes_down, samples) "
            "VALUES ('2025-06-01T05:00:00Z', 1, 1, 1)"
        )
        # aged hourly WITH a daily row -> pruned
        await tdb.execute(
            "INSERT INTO traffic_rollup_hourly (bucket_utc, bytes_up, bytes_down, samples) "
            "VALUES ('2025-06-02T05:00:00Z', 1, 1, 1)"
        )
        await tdb.execute(
            "INSERT INTO traffic_rollup_daily (bucket_utc, bytes_up, bytes_down, samples) "
            "VALUES ('2025-06-02', 1, 1, 1)"
        )
        await tdb.commit()
        await prune(tdb, now_ts=NOW, hourly_days=180)
        await tdb.commit()
        cur = await tdb.execute("SELECT bucket_utc FROM traffic_rollup_hourly ORDER BY bucket_utc")
        remaining = [r[0] for r in await cur.fetchall()]
        assert remaining == ["2025-06-01T05:00:00Z"]   # only the one without a daily survives
        # daily is never pruned
        cur = await tdb.execute("SELECT COUNT(*) FROM traffic_rollup_daily")
        assert (await cur.fetchone())[0] == 1

    async def test_no_checkpoint_keeps_all_deltas(self, tdb):
        await _delta(tdb, OLD, 1, 1, counted=1)
        await tdb.commit()
        counts = await prune(tdb, now_ts=NOW, delta_days=0)  # no checkpoint exists
        await tdb.commit()
        assert counts["deltas"] == 0
        cur = await tdb.execute("SELECT COUNT(*) FROM traffic_delta")
        assert (await cur.fetchone())[0] == 1
