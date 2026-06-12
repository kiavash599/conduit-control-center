# SPDX-License-Identifier: MIT
"""
backend/traffic/retention.py
----------------------------
Rollups, lifetime checkpoints, lifetime computation, and pruning (P0 Step 5).

These operate on a caller-supplied aiosqlite connection. ``apply_tick`` runs
inside the collector's per-tick ``BEGIN IMMEDIATE`` transaction (so rollups and
the lazy checkpoint are atomic with the snapshot/delta); ``prune`` runs on a
slow cadence in its own transaction.

Invariants (load-bearing — see the tests):

- ``lifetime = latest_checkpoint.total + Σ(counted deltas with day > checkpoint_day)``;
  with no checkpoint, ``lifetime = Σ(all counted deltas)``. Pruning deltas that
  a checkpoint already covers therefore never changes the lifetime.
- The latest snapshot is never pruned (it is the reseed/reset baseline).
- A delta is pruned only when it is older than the delta-retention window AND
  its day is at or before the latest checkpoint day (i.e. already folded into a
  checkpoint). Deltas after the latest checkpoint are always kept.
- An hourly bucket is pruned only when its day already has a daily rollup row.
- Daily rollups and lifetime checkpoints are never pruned.

Like the repository, this module references ``aiosqlite`` only as a type hint
and exposes its SQL as constants, so the exact statements are testable with the
stdlib sqlite3 driver.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
SQL_UPSERT_HOURLY = (
    "INSERT INTO traffic_rollup_hourly (bucket_utc, bytes_up, bytes_down, samples) "
    "VALUES (?, ?, ?, 1) "
    "ON CONFLICT(bucket_utc) DO UPDATE SET "
    "bytes_up = bytes_up + excluded.bytes_up, "
    "bytes_down = bytes_down + excluded.bytes_down, "
    "samples = samples + 1"
)
SQL_UPSERT_DAILY = (
    "INSERT INTO traffic_rollup_daily (bucket_utc, bytes_up, bytes_down, samples) "
    "VALUES (?, ?, ?, 1) "
    "ON CONFLICT(bucket_utc) DO UPDATE SET "
    "bytes_up = bytes_up + excluded.bytes_up, "
    "bytes_down = bytes_down + excluded.bytes_down, "
    "samples = samples + 1"
)
SQL_LATEST_CHECKPOINT = (
    "SELECT day_utc, total_bytes_up, total_bytes_down "
    "FROM lifetime_checkpoint ORDER BY day_utc DESC LIMIT 1"
)
SQL_MAX_CHECKPOINT_DAY = "SELECT MAX(day_utc) FROM lifetime_checkpoint"
SQL_EXISTS_COUNTED_UPTO = (
    "SELECT 1 FROM traffic_delta "
    "WHERE counted = 1 AND substr(ts_utc, 1, 10) <= ? LIMIT 1"
)
SQL_SUM_COUNTED_RANGE = (
    "SELECT COALESCE(SUM(bytes_up_delta), 0), COALESCE(SUM(bytes_down_delta), 0) "
    "FROM traffic_delta "
    "WHERE counted = 1 AND substr(ts_utc, 1, 10) > ? AND substr(ts_utc, 1, 10) <= ?"
)
SQL_SUM_COUNTED_SINCE = (
    "SELECT COALESCE(SUM(bytes_up_delta), 0), COALESCE(SUM(bytes_down_delta), 0) "
    "FROM traffic_delta WHERE counted = 1 AND substr(ts_utc, 1, 10) > ?"
)
SQL_SUM_COUNTED_ALL = (
    "SELECT COALESCE(SUM(bytes_up_delta), 0), COALESCE(SUM(bytes_down_delta), 0) "
    "FROM traffic_delta WHERE counted = 1"
)
SQL_INSERT_CHECKPOINT = (
    "INSERT OR REPLACE INTO lifetime_checkpoint "
    "(day_utc, total_bytes_up, total_bytes_down, created_at_utc) VALUES (?, ?, ?, ?)"
)
SQL_PRUNE_SNAPSHOTS = (
    "DELETE FROM traffic_snapshot WHERE substr(ts_utc, 1, 10) < ? "
    "AND id <> (SELECT MAX(id) FROM traffic_snapshot)"
)
SQL_PRUNE_DELTAS = (
    "DELETE FROM traffic_delta "
    "WHERE substr(ts_utc, 1, 10) < ? AND substr(ts_utc, 1, 10) <= ?"
)
SQL_PRUNE_HOURLY = (
    "DELETE FROM traffic_rollup_hourly WHERE substr(bucket_utc, 1, 10) < ? "
    "AND substr(bucket_utc, 1, 10) IN (SELECT bucket_utc FROM traffic_rollup_daily)"
)


# ---------------------------------------------------------------------------
# Pure date helpers (ISO-8601 UTC text)
# ---------------------------------------------------------------------------
def _day(ts_utc: str) -> str:
    """The UTC date portion ('YYYY-MM-DD') of an ISO timestamp."""
    return ts_utc[:10]


def _shift_day(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def hour_bucket(ts_utc: str) -> str:
    """The UTC hour bucket key, e.g. '2026-06-12T15:00:00Z'."""
    return ts_utc[:13] + ":00:00Z"


def day_bucket(ts_utc: str) -> str:
    """The UTC day bucket key, e.g. '2026-06-12'."""
    return ts_utc[:10]


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
async def upsert_rollups(
    db: "aiosqlite.Connection", *, ts_utc: str, bytes_up: int, bytes_down: int
) -> None:
    """Add a counted delta's bytes to its hourly and daily UTC buckets."""
    await db.execute(SQL_UPSERT_HOURLY, (hour_bucket(ts_utc), bytes_up, bytes_down))
    await db.execute(SQL_UPSERT_DAILY, (day_bucket(ts_utc), bytes_up, bytes_down))


async def write_due_checkpoint(db: "aiosqlite.Connection", *, now_ts: str) -> bool:
    """
    Lazily write the checkpoint for the most recent completed day, if due.

    Writes a single checkpoint keyed by *yesterday* whose total is the cumulative
    counted bytes up to end of yesterday. One checkpoint subsumes all earlier
    uncheckpointed days, so the pruning frontier advances even after downtime.
    Returns True if a checkpoint was written.
    """
    today = _day(now_ts)
    yesterday = _shift_day(today, -1)

    cur = await db.execute(SQL_LATEST_CHECKPOINT)
    row = await cur.fetchone()
    base_day = row["day_utc"] if row else ""
    if row is not None and base_day >= yesterday:
        return False  # already checkpointed yesterday (or later)

    cur = await db.execute(SQL_EXISTS_COUNTED_UPTO, (yesterday,))
    if await cur.fetchone() is None:
        return False  # no completed-day counted data to checkpoint yet

    base_up = row["total_bytes_up"] if row else 0
    base_down = row["total_bytes_down"] if row else 0
    cur = await db.execute(SQL_SUM_COUNTED_RANGE, (base_day, yesterday))
    add_up, add_down = await cur.fetchone()
    await db.execute(
        SQL_INSERT_CHECKPOINT,
        (yesterday, base_up + add_up, base_down + add_down, now_ts),
    )
    return True


async def compute_lifetime(db: "aiosqlite.Connection") -> tuple[int, int]:
    """Return (bytes_up, bytes_down) lifetime totals = checkpoint + Σ(deltas since)."""
    cur = await db.execute(SQL_LATEST_CHECKPOINT)
    row = await cur.fetchone()
    if row is None:
        cur = await db.execute(SQL_SUM_COUNTED_ALL)
        up, down = await cur.fetchone()
        return (up, down)
    cur = await db.execute(SQL_SUM_COUNTED_SINCE, (row["day_utc"],))
    add_up, add_down = await cur.fetchone()
    return (row["total_bytes_up"] + add_up, row["total_bytes_down"] + add_down)


async def apply_tick(
    db: "aiosqlite.Connection",
    *,
    counted: bool,
    ts_utc: str,
    bytes_up_delta: int,
    bytes_down_delta: int,
    now_ts: str,
) -> None:
    """Roll up a counted delta and write the lazy checkpoint (within the tick txn)."""
    if counted:
        await upsert_rollups(
            db, ts_utc=ts_utc, bytes_up=bytes_up_delta, bytes_down=bytes_down_delta
        )
    await write_due_checkpoint(db, now_ts=now_ts)


async def prune(
    db: "aiosqlite.Connection",
    *,
    now_ts: str,
    snapshot_days: int = 7,
    delta_days: int = 90,
    hourly_days: int = 180,
) -> dict[str, int]:
    """
    Prune aged rows while preserving the lifetime invariant and reseed baseline.

    Returns a dict of deleted row counts: {"snapshots", "deltas", "hourly"}.
    """
    today = _day(now_ts)
    snap_cut = _shift_day(today, -snapshot_days)
    delta_cut = _shift_day(today, -delta_days)
    hourly_cut = _shift_day(today, -hourly_days)

    counts: dict[str, int] = {}

    cur = await db.execute(SQL_PRUNE_SNAPSHOTS, (snap_cut,))
    counts["snapshots"] = cur.rowcount

    cur = await db.execute(SQL_MAX_CHECKPOINT_DAY)
    latest_cp_day = (await cur.fetchone())[0]
    if latest_cp_day is not None:
        cur = await db.execute(SQL_PRUNE_DELTAS, (delta_cut, latest_cp_day))
        counts["deltas"] = cur.rowcount
    else:
        counts["deltas"] = 0  # no checkpoint coverage -> keep all deltas

    cur = await db.execute(SQL_PRUNE_HOURLY, (hourly_cut,))
    counts["hourly"] = cur.rowcount

    return counts
