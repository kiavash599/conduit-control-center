# SPDX-License-Identifier: MIT
"""
backend/traffic/collector.py
----------------------------
The in-process traffic persistence collector (P0 Step 3b).

A single asyncio task that, once per interval, reads Conduit's counters, runs
the pure accounting state machine, and persists the result through the
repository. It enforces single-writer behaviour with an OS advisory lock
(flock), uses a fresh per-tick connection whose implicit transaction is
committed atomically, and isolates failures into a separate health-update
transaction.

This module is intentionally lean: it imports only ``backend.conduit.errors``
(dependency-free), the pure ``accounting`` core, and the ``repository`` SQL
layer. The production defaults (``read_counters`` and ``get_db``) and the
Unix-only ``fcntl`` module are imported lazily, so the collector can be
imported and unit-tested without aiosqlite, pydantic, or a Unix host, with all
collaborators injected.

Wiring into the application lifespan, and the ``traffic_collector_enabled``
feature flag, are deliberately **not** here — that is Step 3c. Nothing imports
or starts this collector yet.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from backend.conduit.errors import ConduitUnreachableError, MetricsContractError
from backend.traffic import repository as repo
from backend.traffic import retention
from backend.traffic.accounting import Epoch, Snapshot, decide
from backend.traffic.models import CounterReading

logger = logging.getLogger(__name__)

# Health status values (mirror the collector_health CHECK constraint).
STATUS_RUNNING = "running"
STATUS_DEFERRED = "deferred_clock_unsynced"
STATUS_ERROR = "error"
STATUS_LOCK_DENIED = "lock_denied"

_SYNC_MARKER = "/run/systemd/timesync/synchronized"


def _default_clock() -> str:
    """Current time as ISO-8601 UTC text (display/bucketing; never ordering)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_clock_sync_check() -> bool:
    """True when systemd-timesyncd reports the clock is NTP-synchronised."""
    return Path(_SYNC_MARKER).exists()


class TrafficCollector:
    """
    Single-writer traffic collector. Construct, then ``await run()`` as a task;
    call ``request_stop()`` and await the task to shut it down.
    """

    def __init__(
        self,
        *,
        metrics_reader: Optional[Callable[[], Awaitable[CounterReading]]] = None,
        db_factory: Optional[Callable[[], object]] = None,
        clock: Optional[Callable[[], str]] = None,
        clock_sync_check: Optional[Callable[[], bool]] = None,
        lock_path: Optional[str] = None,
        interval_seconds: float = 60.0,
        gap_threshold_seconds: float = 90.0,
        lock_retries: int = 5,
        lock_retry_delay_seconds: float = 1.0,
        shutdown_budget_seconds: float = 5.0,
        snapshot_retention_days: int = 7,
        delta_retention_days: int = 90,
        hourly_retention_days: int = 180,
        holder_id: Optional[str] = None,
    ) -> None:
        self._metrics_reader = metrics_reader
        self._db_factory = db_factory
        self._clock = clock or _default_clock
        self._clock_sync_check = clock_sync_check or _default_clock_sync_check
        self._lock_path = str(lock_path) if lock_path else None
        self._interval = interval_seconds
        self._gap = gap_threshold_seconds
        self._lock_retries = max(1, lock_retries)
        self._lock_retry_delay = lock_retry_delay_seconds
        self._shutdown_budget = shutdown_budget_seconds
        self._snapshot_days = snapshot_retention_days
        self._delta_days = delta_retention_days
        self._hourly_days = hourly_retention_days
        self._holder_id = holder_id or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self._stop = asyncio.Event()
        self._lock_fd: Optional[int] = None
        self._prev: Optional[Snapshot] = None
        self._epoch: Optional[Epoch] = None
        self._first_tick = True
        self._last_prune_day: Optional[str] = None

    # -- lazy default resolution (keeps module import light) -----------------
    def _reader(self) -> Callable[[], Awaitable[CounterReading]]:
        if self._metrics_reader is None:
            from backend.conduit.adapter import read_counters
            self._metrics_reader = read_counters
        return self._metrics_reader

    def _dbf(self) -> Callable[[], object]:
        if self._db_factory is None:
            from backend.database import get_db
            self._db_factory = get_db
        return self._db_factory

    def _lockpath(self) -> str:
        if self._lock_path is None:
            from backend.database import get_db_path
            self._lock_path = str(get_db_path().with_name("collector.lock"))
        return self._lock_path

    # -- public control ------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    @property
    def holder_id(self) -> str:
        return self._holder_id

    async def run(self) -> None:
        """Acquire the lock, reseed, then loop until stopped; always release."""
        if not await self._acquire_lock():
            return  # lock_denied status already recorded; app keeps serving
        try:
            await self._reseed()
            while not self._stop.is_set():
                await self._tick()
                await self._wait_interval()
            # Graceful stop: best-effort, bounded final snapshot.
            await self._final_snapshot()
        finally:
            self._release_lock()

    # -- lock ----------------------------------------------------------------
    async def _acquire_lock(self) -> bool:
        import fcntl  # lazy: Unix-only

        path = self._lockpath()
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        for attempt in range(self._lock_retries):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_fd = fd
                logger.info(
                    "traffic collector: lock acquired (%s, holder=%s)",
                    path, self._holder_id,
                )
                return True
            except OSError:
                if attempt < self._lock_retries - 1:
                    await asyncio.sleep(self._lock_retry_delay)
        os.close(fd)
        logger.warning(
            "traffic collector: lock held by another process at %s; not starting",
            path,
        )
        await self._set_status_safe(
            STATUS_LOCK_DENIED, last_error="collector lock held by another process"
        )
        return False

    def _release_lock(self) -> None:
        if self._lock_fd is None:
            return
        import fcntl
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    # -- pragmas / reseed ----------------------------------------------------
    async def _apply_pragmas(self, db) -> None:
        # NOTE: do NOT set db.isolation_level here. aiosqlite's isolation_level
        # property setter mutates the underlying sqlite3 connection from the
        # event-loop thread rather than aiosqlite's worker thread, which raises
        # "SQLite objects created in a thread can only be used in that same
        # thread". We instead rely on aiosqlite's implicit (deferred)
        # transaction plus commit() for atomicity, and on the flock for
        # single-writer enforcement. PRAGMAs below are safe -- execute() is
        # dispatched onto the worker thread. They run before any DML, so
        # foreign_keys=ON takes effect (it cannot change inside a transaction).
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")

    async def _reseed(self) -> None:
        async with self._dbf()() as db:
            await self._apply_pragmas(db)
            self._prev = await repo.load_latest_snapshot(db)
            self._epoch = await repo.load_active_epoch(db)
        self._first_tick = True

    # -- the tick ------------------------------------------------------------
    async def _tick(self) -> None:
        synced = self._clock_sync_check()
        now = self._clock()

        reader = self._reader()
        try:
            reading = await reader()
        except ConduitUnreachableError:
            await self._record_failure_safe("metrics endpoint unreachable")
            return
        except MetricsContractError as exc:
            await self._record_failure_safe(f"metrics contract error: {exc}")
            return

        if not synced:
            # Option C: persist nothing while the clock is unsynced.
            await self._set_status_safe(STATUS_DEFERRED)
            return

        decision = decide(
            reading, self._prev, self._epoch, now, self._first_tick,
            gap_threshold_seconds=self._gap,
        )
        try:
            snapshot_id, epoch_id = await self._persist(decision, now)
        except Exception as exc:  # noqa: BLE001 - any DB error is isolated below
            logger.warning("traffic collector: tick persist failed (%s)", type(exc).__name__)
            await self._record_failure_safe(f"db error: {type(exc).__name__}")
            return  # in-memory prev/epoch unchanged -> next tick retries safely

        # Advance in-memory state ONLY after a successful commit.
        snap = decision.snapshot
        self._prev = Snapshot(
            id=snapshot_id, seq=snap.seq, epoch_id=epoch_id,
            uptime_seconds=snap.uptime_seconds,
            bytes_up=snap.bytes_up, bytes_down=snap.bytes_down,
        )
        if decision.new_epoch is not None:
            build_rev = decision.new_epoch.build_rev
        else:
            build_rev = self._epoch.build_rev if self._epoch else None
        self._epoch = Epoch(id=epoch_id, build_rev=build_rev)
        self._first_tick = False

        # Prune on a slow (daily) cadence, in its own transaction.
        await self._maybe_prune(now)

    async def _maybe_prune(self, now: str) -> None:
        """Run retention pruning once per UTC day (best-effort, isolated)."""
        day = now[:10]
        if day == self._last_prune_day:
            return
        try:
            async with self._dbf()() as db:
                await self._apply_pragmas(db)
                await retention.prune(
                    db, now_ts=now,
                    snapshot_days=self._snapshot_days,
                    delta_days=self._delta_days,
                    hourly_days=self._hourly_days,
                )
                await db.commit()
            self._last_prune_day = day
        except Exception:  # noqa: BLE001 - pruning must never crash the loop
            logger.warning("traffic collector: prune failed", exc_info=False)

    async def _persist(self, decision, now: str) -> tuple[int, int]:
        """Success path: one implicit transaction committed atomically. Raises on DB failure."""
        current_epoch_id = self._epoch.id if self._epoch else None
        async with self._dbf()() as db:
            await self._apply_pragmas(db)
            # Implicit (deferred) transaction: aiosqlite opens it on the first
            # write below and commit() makes the whole tick atomic. On any
            # exception the connection is closed without commit -> rollback.
            snapshot_id, epoch_id = await repo.persist_tick(
                db, decision,
                current_epoch_id=current_epoch_id,
                holder_id=self._holder_id, now_ts=now,
            )
            # Rollups + lazy lifetime checkpoint, atomic with the snapshot/delta.
            await retention.apply_tick(
                db,
                counted=bool(decision.delta.counted),
                ts_utc=decision.delta.ts_utc,
                bytes_up_delta=decision.delta.bytes_up_delta,
                bytes_down_delta=decision.delta.bytes_down_delta,
                now_ts=now,
            )
            await db.commit()
        return snapshot_id, epoch_id

    # -- health writes (always on a fresh, separate connection) --------------
    async def _record_failure_safe(self, message: str) -> None:
        try:
            now = self._clock()
            async with self._dbf()() as db:
                await self._apply_pragmas(db)
                await repo.record_failure(
                    db, last_error=message, now_ts=now,
                    holder_id=self._holder_id, status=STATUS_ERROR,
                )
                await db.commit()
        except Exception:  # noqa: BLE001 - health write must never crash the loop
            logger.error("traffic collector: could not record health failure", exc_info=False)

    async def _set_status_safe(self, status: str, last_error: Optional[str] = None) -> None:
        try:
            now = self._clock()
            async with self._dbf()() as db:
                await self._apply_pragmas(db)
                await repo.set_status(
                    db, status=status, now_ts=now,
                    holder_id=self._holder_id, last_error=last_error,
                )
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.error("traffic collector: could not set health status", exc_info=False)

    # -- shutdown helpers ----------------------------------------------------
    async def _final_snapshot(self) -> None:
        """One best-effort, time-bounded extra tick to tighten the tail."""
        try:
            await asyncio.wait_for(self._tick(), timeout=self._shutdown_budget)
        except Exception:  # noqa: BLE001 - never raise out of shutdown
            logger.debug("traffic collector: final snapshot skipped/failed")

    async def _wait_interval(self) -> None:
        """Sleep up to one interval, returning early if stop was requested."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
        except asyncio.TimeoutError:
            pass
