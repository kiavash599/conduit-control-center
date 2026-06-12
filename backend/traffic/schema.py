# SPDX-License-Identifier: MIT
"""
backend/traffic/schema.py
-------------------------
P0 Traffic Persistence Collector — schema definition and bootstrap.

Step 1 of the P0 build: this module defines the eight traffic_* tables, their
indexes, and a lightweight ``schema_version`` stamp, then applies them from
``backend.database.create_tables()``. The tables are landed **dormant** — no
collector runs and no production behaviour changes until the
``traffic_collector_enabled`` feature flag is enabled in a later step.

Design references (approved P0 design summary):

- Deltas are the canonical ledger; ``counted`` is a persisted column because the
  counting decision is a function of *both* ``source`` and ``anomaly_flag``
  (``counted = 0`` iff ``source = 'initial_baseline'`` OR
  ``anomaly_flag = 'negative_clamped'``), and markers such as
  ``build_change_no_reset`` remain ``counted = 1``.
- ``recording_since`` is **derived** as ``MIN(traffic_epoch.started_at_utc)`` —
  there is deliberately no column for it.
- ``traffic_delta`` snapshot references use ``ON DELETE SET NULL`` so snapshots
  may be pruned (7 day retention) without breaking the ledger; delta byte values
  are self-contained.
- ``traffic_epoch.ended_at_utc`` is the reset-detection / closure time written
  retroactively when the successor epoch is created; ``NULL`` means the active
  epoch (never the real Conduit stop time).
- Timestamps are stored as ISO-8601 UTC text (``...Z``); byte counters are
  INTEGER; uptime / interval are REAL.

The schema is additive and idempotent: every statement uses
``IF NOT EXISTS`` so ``create_tables()`` is safe to call on every startup and on
an existing ``ccc.db`` that already holds the ``sessions`` / ``audit_log``
tables.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime aiosqlite import
    import aiosqlite

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
# v1 == the initial traffic schema. Future *structural* migrations (rare; the
# design is additive-only) would add ordered steps and bump this constant.
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Table + index DDL (all IF NOT EXISTS -> idempotent, additive)
# ---------------------------------------------------------------------------
TRAFFIC_DDL: list[str] = [
    # -- schema_version: single-row version stamp for the traffic schema -------
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        id             INTEGER PRIMARY KEY CHECK (id = 1),
        version        INTEGER NOT NULL,
        applied_at_utc TEXT    NOT NULL
    )
    """,
    # -- traffic_epoch: one continuous Conduit run (counters monotonic from 0) -
    """
    CREATE TABLE IF NOT EXISTS traffic_epoch (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at_utc       TEXT    NOT NULL,
        first_uptime_seconds REAL    NOT NULL,
        conduit_build_rev    TEXT,
        reason               TEXT    NOT NULL
            CHECK (reason IN ('startup', 'reset', 'build_change_with_reset')),
        ended_at_utc         TEXT
    )
    """,
    # -- traffic_snapshot: absolute counter reading; latest row = reseed baseline
    """
    CREATE TABLE IF NOT EXISTS traffic_snapshot (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc         TEXT    NOT NULL,
        seq            INTEGER NOT NULL,
        epoch_id       INTEGER NOT NULL REFERENCES traffic_epoch(id),
        uptime_seconds REAL    NOT NULL,
        bytes_up       INTEGER NOT NULL,
        bytes_down     INTEGER NOT NULL
    )
    """,
    # -- traffic_delta: the canonical ledger ----------------------------------
    """
    CREATE TABLE IF NOT EXISTS traffic_delta (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc           TEXT    NOT NULL,
        seq              INTEGER NOT NULL,
        epoch_id         INTEGER NOT NULL REFERENCES traffic_epoch(id),
        snapshot_id      INTEGER REFERENCES traffic_snapshot(id) ON DELETE SET NULL,
        prev_snapshot_id INTEGER REFERENCES traffic_snapshot(id) ON DELETE SET NULL,
        interval_seconds REAL    NOT NULL,
        bytes_up_delta   INTEGER NOT NULL,
        bytes_down_delta INTEGER NOT NULL,
        source           TEXT    NOT NULL
            CHECK (source IN ('normal', 'epoch_baseline', 'initial_baseline',
                              'gap_spanning', 'recovered')),
        anomaly_flag     TEXT    NOT NULL DEFAULT 'none'
            CHECK (anomaly_flag IN ('none', 'negative_clamped', 'reset',
                                    'parse_gap', 'build_change_no_reset')),
        counted          INTEGER NOT NULL DEFAULT 1 CHECK (counted IN (0, 1))
    )
    """,
    # -- traffic_rollup_hourly: source of truth for the time series (UTC) ------
    """
    CREATE TABLE IF NOT EXISTS traffic_rollup_hourly (
        bucket_utc TEXT    PRIMARY KEY,
        bytes_up   INTEGER NOT NULL DEFAULT 0,
        bytes_down INTEGER NOT NULL DEFAULT 0,
        samples    INTEGER NOT NULL DEFAULT 0
    )
    """,
    # -- traffic_rollup_daily: convenience cache; authoritative once hourly aged
    """
    CREATE TABLE IF NOT EXISTS traffic_rollup_daily (
        bucket_utc TEXT    PRIMARY KEY,
        bytes_up   INTEGER NOT NULL DEFAULT 0,
        bytes_down INTEGER NOT NULL DEFAULT 0,
        samples    INTEGER NOT NULL DEFAULT 0
    )
    """,
    # -- lifetime_checkpoint: daily cumulative markers (indefinite retention) --
    """
    CREATE TABLE IF NOT EXISTS lifetime_checkpoint (
        day_utc         TEXT    PRIMARY KEY,
        total_bytes_up  INTEGER NOT NULL,
        total_bytes_down INTEGER NOT NULL,
        created_at_utc  TEXT    NOT NULL
    )
    """,
    # -- collector_health: single-row operational state -----------------------
    """
    CREATE TABLE IF NOT EXISTS collector_health (
        id                   INTEGER PRIMARY KEY CHECK (id = 1),
        status               TEXT    NOT NULL DEFAULT 'disabled'
            CHECK (status IN ('disabled', 'running', 'deferred_clock_unsynced',
                              'error', 'lock_denied')),
        last_ok_ts_utc       TEXT,
        last_error           TEXT,
        last_error_ts_utc    TEXT,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        holder_id            TEXT,
        updated_at_utc       TEXT
    )
    """,
    # -- indexes (access-pattern justified; lean to limit SD write amplification)
    "CREATE INDEX IF NOT EXISTS idx_traffic_snapshot_seq   ON traffic_snapshot(seq)",
    "CREATE INDEX IF NOT EXISTS idx_traffic_snapshot_epoch ON traffic_snapshot(epoch_id)",
    "CREATE INDEX IF NOT EXISTS idx_traffic_delta_ts       ON traffic_delta(ts_utc)",
    "CREATE INDEX IF NOT EXISTS idx_traffic_delta_epoch    ON traffic_delta(epoch_id)",
]


# ---------------------------------------------------------------------------
# Bootstrap SQL (run once after the DDL; exposed as constants so the exact
# statements are unit-testable, including with the stdlib sqlite3 driver)
# ---------------------------------------------------------------------------

# Seed the single collector_health row in the dormant 'disabled' state. The
# collector flips this to 'running' / 'deferred_clock_unsynced' / 'error' /
# 'lock_denied' once enabled. INSERT OR IGNORE keeps it idempotent.
SEED_HEALTH_SQL = (
    "INSERT OR IGNORE INTO collector_health "
    "(id, status, consecutive_failures, updated_at_utc) "
    "VALUES (1, 'disabled', 0, ?)"
)

# Forward-only version stamp: insert on first run; on later runs only advance
# the recorded version (never downgrade). UPSERT requires SQLite >= 3.24.
STAMP_VERSION_SQL = (
    "INSERT INTO schema_version (id, version, applied_at_utc) VALUES (1, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET "
    "version = excluded.version, applied_at_utc = excluded.applied_at_utc "
    "WHERE schema_version.version < excluded.version"
)


def _utcnow_iso() -> str:
    """Return the current time as ISO-8601 UTC text (``YYYY-MM-DDTHH:MM:SSZ``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def apply_traffic_schema(db: "aiosqlite.Connection") -> None:
    """
    Create the traffic schema and apply its bootstrap rows on an open connection.

    Runs all ``TRAFFIC_DDL`` statements (idempotent), seeds the ``collector_health``
    singleton in the dormant ``'disabled'`` state, and stamps ``schema_version``.
    Does **not** commit — the caller (``create_tables()``) commits once so the
    whole startup schema step is a single transaction. Caller must have
    ``PRAGMA foreign_keys=ON`` set (``create_tables`` does).
    """
    for ddl in TRAFFIC_DDL:
        await db.execute(ddl)
    now = _utcnow_iso()
    await db.execute(SEED_HEALTH_SQL, (now,))
    await db.execute(STAMP_VERSION_SQL, (SCHEMA_VERSION, now))
