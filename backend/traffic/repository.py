# SPDX-License-Identifier: MIT
"""
backend/traffic/repository.py
-----------------------------
All SQL for the traffic_* tables (P0 Step 3a).

This is the only layer that touches the traffic tables. It exposes small async
functions that operate on a caller-supplied aiosqlite connection; the collector
(Step 3b) owns the connection lifecycle and the transaction boundaries
(``BEGIN IMMEDIATE`` for the success path, a separate connection for the
failure-health write).

To keep this module importable without the aiosqlite runtime dependency (and to
let the exact SQL be exercised against the real schema with the stdlib sqlite3
driver), the SQL statements are exposed as module constants and ``aiosqlite`` is
referenced only as a type hint.

Responsibilities (Step 3a — no rollups/checkpoints; those are Step 5):
  - load_latest_snapshot()  -> the reseed/comparison baseline (`prev`)
  - load_active_epoch()     -> the current epoch (ended_at_utc IS NULL)
  - persist_tick()          -> epoch?/snapshot/delta/health in the caller's txn
  - record_failure()        -> separate-connection health failure write
  - set_status()            -> health status without touching failures/last_ok
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.traffic.accounting import Epoch, Snapshot, TickDecision

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

# ---------------------------------------------------------------------------
# SQL (exposed as constants so the exact statements are testable)
# ---------------------------------------------------------------------------
SQL_LATEST_SNAPSHOT = (
    "SELECT id, seq, epoch_id, uptime_seconds, bytes_up, bytes_down "
    "FROM traffic_snapshot ORDER BY seq DESC, id DESC LIMIT 1"
)

SQL_ACTIVE_EPOCH = (
    "SELECT id, conduit_build_rev FROM traffic_epoch "
    "WHERE ended_at_utc IS NULL ORDER BY id DESC LIMIT 1"
)

SQL_CLOSE_EPOCH = "UPDATE traffic_epoch SET ended_at_utc = ? WHERE id = ?"

SQL_INSERT_EPOCH = (
    "INSERT INTO traffic_epoch "
    "(started_at_utc, first_uptime_seconds, conduit_build_rev, reason) "
    "VALUES (?, ?, ?, ?)"
)

SQL_INSERT_SNAPSHOT = (
    "INSERT INTO traffic_snapshot "
    "(ts_utc, seq, epoch_id, uptime_seconds, bytes_up, bytes_down) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

SQL_INSERT_DELTA = (
    "INSERT INTO traffic_delta "
    "(ts_utc, seq, epoch_id, snapshot_id, prev_snapshot_id, interval_seconds, "
    " bytes_up_delta, bytes_down_delta, source, anomaly_flag, counted) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

# Success path: mark healthy, clear the failure counter, set last_ok.
SQL_HEALTH_RUNNING_OK = (
    "UPDATE collector_health SET "
    "status = 'running', last_ok_ts_utc = ?, consecutive_failures = 0, "
    "holder_id = ?, updated_at_utc = ? WHERE id = 1"
)

# Failure path: bump the counter, set status/last_error; leave last_ok intact.
SQL_HEALTH_FAILURE = (
    "UPDATE collector_health SET "
    "status = ?, last_error = ?, last_error_ts_utc = ?, "
    "consecutive_failures = consecutive_failures + 1, "
    "holder_id = COALESCE(?, holder_id), updated_at_utc = ? WHERE id = 1"
)

# Non-failure status change (disabled / deferred_clock_unsynced / lock_denied):
# does not touch consecutive_failures or last_ok.
SQL_HEALTH_SET_STATUS = (
    "UPDATE collector_health SET "
    "status = ?, holder_id = COALESCE(?, holder_id), "
    "last_error = COALESCE(?, last_error), updated_at_utc = ? WHERE id = 1"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
async def load_latest_snapshot(db: "aiosqlite.Connection") -> Snapshot | None:
    """Return the most recent snapshot (reseed baseline), or None on an empty DB."""
    cur = await db.execute(SQL_LATEST_SNAPSHOT)
    row = await cur.fetchone()
    if row is None:
        return None
    return Snapshot(
        id=row["id"],
        seq=row["seq"],
        epoch_id=row["epoch_id"],
        uptime_seconds=row["uptime_seconds"],
        bytes_up=row["bytes_up"],
        bytes_down=row["bytes_down"],
    )


async def load_active_epoch(db: "aiosqlite.Connection") -> Epoch | None:
    """Return the active epoch (ended_at_utc IS NULL), or None if there is none."""
    cur = await db.execute(SQL_ACTIVE_EPOCH)
    row = await cur.fetchone()
    if row is None:
        return None
    return Epoch(id=row["id"], build_rev=row["conduit_build_rev"])


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
async def persist_tick(
    db: "aiosqlite.Connection",
    decision: TickDecision,
    *,
    current_epoch_id: int | None,
    holder_id: str,
    now_ts: str,
) -> tuple[int, int]:
    """
    Persist one tick (epoch?/snapshot/delta/health) on the caller's transaction.

    The caller wraps this in a single ``BEGIN IMMEDIATE`` ... ``COMMIT`` so the
    whole tick is atomic. Returns ``(snapshot_id, epoch_id)``.

    ``current_epoch_id`` is the id of the active epoch before this tick (used to
    close it on a reset and to attach same-epoch rows); it may be None only when
    a new epoch is being created on an empty DB (bootstrap).
    """
    # Resolve the epoch: create a new one (closing the previous on a reset) or
    # reuse the active epoch.
    if decision.new_epoch is not None:
        if decision.close_prev_epoch and current_epoch_id is not None:
            await db.execute(SQL_CLOSE_EPOCH, (now_ts, current_epoch_id))
        cur = await db.execute(
            SQL_INSERT_EPOCH,
            (
                decision.new_epoch.started_at_utc,
                decision.new_epoch.first_uptime_seconds,
                decision.new_epoch.build_rev,
                decision.new_epoch.reason,
            ),
        )
        epoch_id = cur.lastrowid
    else:
        if current_epoch_id is None:
            raise ValueError("persist_tick: no epoch to attach a same-epoch tick to")
        epoch_id = current_epoch_id

    snap = decision.snapshot
    cur = await db.execute(
        SQL_INSERT_SNAPSHOT,
        (snap.ts_utc, snap.seq, epoch_id, snap.uptime_seconds, snap.bytes_up, snap.bytes_down),
    )
    snapshot_id = cur.lastrowid

    d = decision.delta
    await db.execute(
        SQL_INSERT_DELTA,
        (
            d.ts_utc, d.seq, epoch_id, snapshot_id, d.prev_snapshot_id,
            d.interval_seconds, d.bytes_up_delta, d.bytes_down_delta,
            d.source, d.anomaly_flag, d.counted,
        ),
    )

    await db.execute(SQL_HEALTH_RUNNING_OK, (now_ts, holder_id, now_ts))
    return snapshot_id, epoch_id


async def record_failure(
    db: "aiosqlite.Connection",
    *,
    last_error: str,
    now_ts: str,
    holder_id: str | None,
    status: str = "error",
) -> None:
    """Increment the failure counter and record the error (separate connection)."""
    await db.execute(SQL_HEALTH_FAILURE, (status, last_error, now_ts, holder_id, now_ts))


async def set_status(
    db: "aiosqlite.Connection",
    *,
    status: str,
    now_ts: str,
    holder_id: str | None = None,
    last_error: str | None = None,
) -> None:
    """Set the health status (disabled / deferred / lock_denied) without
    touching consecutive_failures or last_ok."""
    await db.execute(SQL_HEALTH_SET_STATUS, (status, holder_id, last_error, now_ts))
