# SPDX-License-Identifier: MIT
"""
Unit tests for backend/traffic/collector.py (P0 Step 3b).

Test matrix categories E (flock single-writer), F (DB failure / rollback
isolation), G (shutdown / final snapshot), plus normal-flow and clock-unsynced
behaviour. The collector runs against real aiosqlite (a temp-file DB so the
per-tick connections share state), a fake metrics reader, injected clock /
clock-sync, and a real flock lock file.

The collector's lock uses POSIX flock, so the module is skipped on Windows.
"""
from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace

import aiosqlite
import pytest

from backend.conduit.errors import ConduitUnreachableError, MetricsContractError
from backend.traffic import repository as repo
from backend.traffic.collector import (
    STATUS_DEFERRED,
    STATUS_ERROR,
    STATUS_LOCK_DENIED,
    STATUS_RUNNING,
    TrafficCollector,
)
from backend.traffic.models import CounterReading
from backend.traffic.schema import apply_traffic_schema

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="collector flock requires POSIX"
)

FIXED_TS = "2026-06-12T15:00:00Z"


class _Factory:
    """db_factory mirroring get_db: yields an aiosqlite connection with Row."""

    def __init__(self, path: str):
        self.path = path

    def __call__(self):
        return _CM(self.path)


class _CM:
    def __init__(self, path: str):
        self.path = path

    async def __aenter__(self):
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        return self.db

    async def __aexit__(self, *exc):
        await self.db.close()
        return False


class _Reader:
    def __init__(self, readings, exc=None):
        self.readings = readings
        self.i = 0
        self.exc = exc

    async def __call__(self):
        if self.exc is not None:
            raise self.exc
        r = self.readings[min(self.i, len(self.readings) - 1)]
        self.i += 1
        return r


def _r(up, down, uptime, build="rev1"):
    return CounterReading(bytes_up=up, bytes_down=down, uptime_seconds=uptime, build_rev=build)


@pytest.fixture
async def env(tmp_path):
    db_path = tmp_path / "ccc.db"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await apply_traffic_schema(db)
        await db.commit()
    return SimpleNamespace(
        db_path=str(db_path),
        lock_path=str(tmp_path / "collector.lock"),
        factory=_Factory(str(db_path)),
    )


def _collector(env, reader, *, synced=True, factory=None, **kw):
    return TrafficCollector(
        metrics_reader=reader,
        db_factory=factory or env.factory,
        clock=lambda: FIXED_TS,
        clock_sync_check=lambda: synced,
        lock_path=env.lock_path,
        interval_seconds=0.02,
        lock_retry_delay_seconds=0.01,
        **kw,
    )


async def _count(path, table):
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
        return (await cur.fetchone())[0]


async def _health(path):
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM collector_health WHERE id = 1")
        return await cur.fetchone()


# ---------------------------------------------------------------------------
# Normal flow
# ---------------------------------------------------------------------------


class TestNormalFlow:
    async def test_bootstrap_then_normal(self, env):
        col = _collector(env, _Reader([_r(500, 700, 42.0), _r(900, 1100, 102.0)]))
        assert await col._acquire_lock()
        await col._reseed()
        await col._tick()
        await col._tick()
        col._release_lock()

        assert await _count(env.db_path, "traffic_snapshot") == 2
        async with aiosqlite.connect(env.db_path) as db:
            cur = await db.execute("SELECT source FROM traffic_delta ORDER BY seq")
            sources = [row[0] for row in await cur.fetchall()]
        assert sources == ["initial_baseline", "normal"]
        h = await _health(env.db_path)
        assert h["status"] == STATUS_RUNNING and h["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Clock unsynced (Option C)
# ---------------------------------------------------------------------------


class TestClockUnsynced:
    async def test_persists_nothing_and_defers(self, env):
        col = _collector(env, _Reader([_r(1, 2, 5.0)]), synced=False)
        assert await col._acquire_lock()
        await col._reseed()
        await col._tick()
        col._release_lock()

        assert await _count(env.db_path, "traffic_snapshot") == 0
        assert await _count(env.db_path, "traffic_epoch") == 0
        h = await _health(env.db_path)
        assert h["status"] == STATUS_DEFERRED
        assert h["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# Scrape / contract failures
# ---------------------------------------------------------------------------


class TestScrapeFailures:
    async def test_unreachable_records_error_no_snapshot(self, env):
        col = _collector(env, _Reader([], exc=ConduitUnreachableError("down")))
        assert await col._acquire_lock()
        await col._reseed()
        await col._tick()
        col._release_lock()
        assert await _count(env.db_path, "traffic_snapshot") == 0
        h = await _health(env.db_path)
        assert h["status"] == STATUS_ERROR and h["consecutive_failures"] == 1

    async def test_contract_error_records_error(self, env):
        col = _collector(env, _Reader([], exc=MetricsContractError("bad")))
        assert await col._acquire_lock()
        await col._reseed()
        await col._tick()
        col._release_lock()
        assert await _count(env.db_path, "traffic_snapshot") == 0
        assert (await _health(env.db_path))["status"] == STATUS_ERROR


# ---------------------------------------------------------------------------
# DB failure / rollback isolation (matrix F)
# ---------------------------------------------------------------------------


class TestRollbackIsolation:
    async def test_failed_persist_rolls_back_and_isolates_health(self, env, monkeypatch):
        col = _collector(env, _Reader([_r(500, 700, 42.0), _r(900, 1100, 102.0)]))
        assert await col._acquire_lock()
        await col._reseed()
        await col._tick()  # bootstrap OK
        assert await _count(env.db_path, "traffic_snapshot") == 1

        # Patch persist_tick to insert a row and then fail, proving the whole
        # transaction is rolled back (the bogus snapshot must not survive).
        real = repo.persist_tick

        async def boom(db, decision, **kw):
            await db.execute(
                "INSERT INTO traffic_snapshot "
                "(ts_utc, seq, epoch_id, uptime_seconds, bytes_up, bytes_down) "
                "VALUES ('x', 999, 1, 1.0, 1, 1)"
            )
            raise sqlite3.OperationalError("boom")

        monkeypatch.setattr(repo, "persist_tick", boom)
        await col._tick()  # fails -> rollback + separate health write
        monkeypatch.setattr(repo, "persist_tick", real)
        col._release_lock()

        # the bogus insert was rolled back; only the bootstrap snapshot remains
        assert await _count(env.db_path, "traffic_snapshot") == 1
        async with aiosqlite.connect(env.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM traffic_snapshot WHERE seq = 999")
            assert (await cur.fetchone())[0] == 0
        h = await _health(env.db_path)
        assert h["status"] == STATUS_ERROR
        assert h["consecutive_failures"] == 1
        assert h["last_ok_ts_utc"] == FIXED_TS  # preserved from the bootstrap tick

    async def test_recovery_after_failure_resets_counter(self, env, monkeypatch):
        col = _collector(env, _Reader([_r(500, 700, 42.0), _r(900, 1100, 102.0), _r(1000, 1200, 162.0)]))
        assert await col._acquire_lock()
        await col._reseed()
        await col._tick()  # bootstrap

        real = repo.persist_tick

        async def boom(db, decision, **kw):
            raise sqlite3.OperationalError("boom")

        monkeypatch.setattr(repo, "persist_tick", boom)
        await col._tick()  # failure
        monkeypatch.setattr(repo, "persist_tick", real)
        assert (await _health(env.db_path))["consecutive_failures"] == 1

        await col._tick()  # recovery
        col._release_lock()
        h = await _health(env.db_path)
        assert h["status"] == STATUS_RUNNING and h["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# flock single-writer (matrix E)
# ---------------------------------------------------------------------------


class TestFlock:
    async def test_two_instances_single_writer(self, env):
        a = _collector(env, _Reader([_r(1, 2, 5.0)]))
        b = _collector(env, _Reader([_r(1, 2, 5.0)]), lock_retries=2)
        assert await a._acquire_lock() is True
        assert await b._acquire_lock() is False
        assert (await _health(env.db_path))["status"] == STATUS_LOCK_DENIED
        a._release_lock()
        assert await b._acquire_lock() is True
        b._release_lock()

    async def test_denied_collector_run_returns_without_crashing(self, env):
        holder = _collector(env, _Reader([_r(1, 2, 5.0)]))
        assert await holder._acquire_lock()
        try:
            b = _collector(env, _Reader([_r(1, 2, 5.0)]), lock_retries=1)
            await b.run()  # must return cleanly, not raise
            assert await _count(env.db_path, "traffic_snapshot") == 0
        finally:
            holder._release_lock()


# ---------------------------------------------------------------------------
# Shutdown / final snapshot / lock release (matrix G)
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_stop_runs_final_snapshot_and_releases_lock(self, env):
        col = _collector(env, _Reader([_r(10, 20, 7.0)]))
        col.request_stop()           # loop skipped; one bounded final-snapshot tick
        await col.run()
        assert await _count(env.db_path, "traffic_snapshot") == 1
        assert col._lock_fd is None  # lock released in finally
        assert (await _health(env.db_path))["status"] == STATUS_RUNNING

    async def test_lock_released_even_on_reseed_error(self, env, monkeypatch):
        col = _collector(env, _Reader([_r(10, 20, 7.0)]))

        async def boom():
            raise RuntimeError("reseed failed")

        monkeypatch.setattr(col, "_reseed", boom)
        with pytest.raises(RuntimeError):
            await col.run()
        assert col._lock_fd is None  # finally released the lock
