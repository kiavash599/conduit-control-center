# SPDX-License-Identifier: MIT
"""
Unit tests for backend/traffic/repository.py (P0 Step 3a — SQL layer).

Driven through accounting.decide() so each test also exercises the
accounting -> repository handoff:
  - load_latest_snapshot / load_active_epoch (empty + populated)
  - persist_tick bootstrap (new epoch, no prev)
  - persist_tick same-epoch normal tick (reuses current epoch)
  - persist_tick reset (closes prev epoch, opens a new one)
  - health: success sets status='running' + clears failures + sets last_ok
  - record_failure increments failures and preserves last_ok
  - set_status changes status without touching failures / last_ok
"""
from __future__ import annotations

import aiosqlite
import pytest

from backend.traffic import repository as repo
from backend.traffic.accounting import decide
from backend.traffic.models import CounterReading
from backend.traffic.schema import apply_traffic_schema

NOW1 = "2026-06-12T15:00:00Z"
NOW2 = "2026-06-12T15:01:00Z"
NOW3 = "2026-06-12T15:02:00Z"


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await apply_traffic_schema(conn)
        await conn.commit()
        yield conn


def _reading(up, down, uptime, build="rev1"):
    return CounterReading(bytes_up=up, bytes_down=down, uptime_seconds=uptime, build_rev=build)


async def _persist(db, decision, current_epoch_id):
    ids = await repo.persist_tick(
        db, decision,
        current_epoch_id=current_epoch_id, holder_id="holder-1",
        now_ts=decision.snapshot.ts_utc,
    )
    await db.commit()
    return ids


async def _health(db):
    cur = await db.execute("SELECT * FROM collector_health WHERE id = 1")
    return await cur.fetchone()


# ---------------------------------------------------------------------------
# Reads on an empty DB
# ---------------------------------------------------------------------------


class TestEmpty:
    async def test_no_snapshot(self, db):
        assert await repo.load_latest_snapshot(db) is None

    async def test_no_active_epoch(self, db):
        assert await repo.load_active_epoch(db) is None


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


class TestBootstrap:
    async def test_persists_epoch_snapshot_delta_and_health(self, db):
        decision = decide(_reading(500, 700, 42.0), None, None, NOW1, False)
        snap_id, epoch_id = await _persist(db, decision, None)
        assert snap_id == 1 and epoch_id == 1

        prev = await repo.load_latest_snapshot(db)
        assert prev is not None
        assert prev.bytes_up == 500 and prev.bytes_down == 700 and prev.seq == 1
        assert prev.epoch_id == epoch_id

        epoch = await repo.load_active_epoch(db)
        assert epoch is not None and epoch.id == epoch_id and epoch.build_rev == "rev1"

        cur = await db.execute("SELECT source, counted FROM traffic_delta WHERE id = 1")
        row = await cur.fetchone()
        assert row["source"] == "initial_baseline" and row["counted"] == 0

        h = await _health(db)
        assert h["status"] == "running"
        assert h["consecutive_failures"] == 0
        assert h["last_ok_ts_utc"] == NOW1
        assert h["holder_id"] == "holder-1"


# ---------------------------------------------------------------------------
# Same-epoch normal tick
# ---------------------------------------------------------------------------


class TestNormalTick:
    async def test_reuses_current_epoch(self, db):
        await _persist(db, decide(_reading(500, 700, 42.0), None, None, NOW1, False), None)
        prev = await repo.load_latest_snapshot(db)
        epoch = await repo.load_active_epoch(db)

        decision = decide(_reading(900, 1100, 102.0), prev, epoch, NOW2, False)
        snap_id, epoch_id = await _persist(db, decision, epoch.id)
        assert epoch_id == epoch.id          # same epoch reused
        assert snap_id == 2

        cur = await db.execute(
            "SELECT source, counted, bytes_up_delta, bytes_down_delta, prev_snapshot_id "
            "FROM traffic_delta WHERE seq = 2"
        )
        row = await cur.fetchone()
        assert row["source"] == "normal" and row["counted"] == 1
        assert row["bytes_up_delta"] == 400 and row["bytes_down_delta"] == 400
        assert row["prev_snapshot_id"] == prev.id

        # exactly one epoch exists
        cur = await db.execute("SELECT COUNT(*) AS c FROM traffic_epoch")
        assert (await cur.fetchone())["c"] == 1


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    async def test_closes_prev_epoch_and_opens_new(self, db):
        await _persist(db, decide(_reading(500, 700, 102.0), None, None, NOW1, False), None)
        prev = await repo.load_latest_snapshot(db)
        epoch = await repo.load_active_epoch(db)

        # uptime drops 102 -> 3 => reset
        decision = decide(_reading(10, 20, 3.0), prev, epoch, NOW3, False)
        assert decision.new_epoch is not None and decision.close_prev_epoch is True
        _snap, new_epoch_id = await _persist(db, decision, epoch.id)
        assert new_epoch_id != epoch.id

        # old epoch closed, new epoch active
        cur = await db.execute(
            "SELECT ended_at_utc FROM traffic_epoch WHERE id = ?", (epoch.id,)
        )
        assert (await cur.fetchone())["ended_at_utc"] == NOW3
        active = await repo.load_active_epoch(db)
        assert active.id == new_epoch_id

        cur = await db.execute(
            "SELECT source, anomaly_flag, counted FROM traffic_delta WHERE seq = 2"
        )
        row = await cur.fetchone()
        assert row["source"] == "epoch_baseline"
        assert row["anomaly_flag"] == "reset"
        assert row["counted"] == 1


# ---------------------------------------------------------------------------
# Health: failure / status transitions
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_record_failure_increments_and_preserves_last_ok(self, db):
        await _persist(db, decide(_reading(1, 2, 5.0), None, None, NOW1, False), None)
        before = await _health(db)
        assert before["last_ok_ts_utc"] == NOW1

        await repo.record_failure(
            db, last_error="boom", now_ts=NOW2, holder_id="holder-1"
        )
        await db.commit()
        after = await _health(db)
        assert after["status"] == "error"
        assert after["consecutive_failures"] == 1
        assert after["last_error"] == "boom"
        assert after["last_ok_ts_utc"] == NOW1  # preserved

        # second failure increments again
        await repo.record_failure(db, last_error="boom2", now_ts=NOW3, holder_id="holder-1")
        await db.commit()
        assert (await _health(db))["consecutive_failures"] == 2

    async def test_set_status_does_not_touch_failures_or_last_ok(self, db):
        await _persist(db, decide(_reading(1, 2, 5.0), None, None, NOW1, False), None)
        await repo.record_failure(db, last_error="x", now_ts=NOW2, holder_id="h")
        await db.commit()
        failures_before = (await _health(db))["consecutive_failures"]

        await repo.set_status(db, status="deferred_clock_unsynced", now_ts=NOW3)
        await db.commit()
        h = await _health(db)
        assert h["status"] == "deferred_clock_unsynced"
        assert h["consecutive_failures"] == failures_before  # unchanged
        assert h["last_ok_ts_utc"] == NOW1  # unchanged

    async def test_success_after_failure_resets_counter(self, db):
        await _persist(db, decide(_reading(1, 2, 5.0), None, None, NOW1, False), None)
        await repo.record_failure(db, last_error="x", now_ts=NOW2, holder_id="h")
        await db.commit()
        assert (await _health(db))["consecutive_failures"] == 1

        prev = await repo.load_latest_snapshot(db)
        epoch = await repo.load_active_epoch(db)
        await _persist(db, decide(_reading(3, 4, 65.0), prev, epoch, NOW3, False), epoch.id)
        h = await _health(db)
        assert h["status"] == "running"
        assert h["consecutive_failures"] == 0
        assert h["last_ok_ts_utc"] == NOW3
