# SPDX-License-Identifier: MIT
"""
Unit tests for backend/traffic/schema.py (P0 Step 1 — schema + bootstrap).

Test matrix category B (schema / bootstrap):
  - all DDL statements are idempotent (IF NOT EXISTS)
  - apply_traffic_schema() creates all eight tables + indexes
  - applying twice is a no-op (idempotent)
  - collector_health singleton seeded with status='disabled', failures=0
  - schema_version stamped = SCHEMA_VERSION
  - ON DELETE SET NULL: deleting a referenced snapshot nulls the delta link
    while the delta row (and its byte values) survives
  - CHECK constraints reject bad enum values
  - create_tables() lands the traffic schema additively alongside sessions/audit
  - recording_since == MIN(traffic_epoch.started_at_utc) is derivable
"""
from __future__ import annotations

import aiosqlite
import pytest

from backend.traffic.schema import (
    SCHEMA_VERSION,
    TRAFFIC_DDL,
    apply_traffic_schema,
)

_TRAFFIC_TABLES = {
    "schema_version",
    "traffic_epoch",
    "traffic_snapshot",
    "traffic_delta",
    "traffic_rollup_hourly",
    "traffic_rollup_daily",
    "lifetime_checkpoint",
    "collector_health",
}

_TRAFFIC_INDEXES = {
    "idx_traffic_snapshot_seq",
    "idx_traffic_snapshot_epoch",
    "idx_traffic_delta_ts",
    "idx_traffic_delta_epoch",
}


@pytest.fixture
async def tdb():
    """In-memory aiosqlite connection with the traffic schema applied."""
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await apply_traffic_schema(conn)
        await conn.commit()
        yield conn


async def _names(conn, kind: str) -> set[str]:
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
    )
    return {row["name"] for row in await cur.fetchall()}


# ---------------------------------------------------------------------------
# DDL shape
# ---------------------------------------------------------------------------


class TestDdlShape:
    def test_all_statements_idempotent(self):
        """Every DDL statement must be IF NOT EXISTS (safe on every startup)."""
        for stmt in TRAFFIC_DDL:
            assert "if not exists" in stmt.lower(), stmt

    def test_schema_version_constant(self):
        assert SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# Table / index creation + idempotency
# ---------------------------------------------------------------------------


class TestApplySchema:
    async def test_creates_all_tables(self, tdb):
        assert _TRAFFIC_TABLES <= await _names(tdb, "table")

    async def test_creates_all_indexes(self, tdb):
        assert _TRAFFIC_INDEXES <= await _names(tdb, "index")

    async def test_idempotent_second_apply(self, tdb):
        # Applying again must not raise and must not duplicate rows.
        await apply_traffic_schema(tdb)
        await tdb.commit()
        cur = await tdb.execute("SELECT COUNT(*) AS c FROM collector_health")
        assert (await cur.fetchone())["c"] == 1
        cur = await tdb.execute("SELECT COUNT(*) AS c FROM schema_version")
        assert (await cur.fetchone())["c"] == 1


# ---------------------------------------------------------------------------
# Bootstrap rows
# ---------------------------------------------------------------------------


class TestBootstrapRows:
    async def test_health_singleton_seeded_disabled(self, tdb):
        cur = await tdb.execute(
            "SELECT id, status, consecutive_failures FROM collector_health"
        )
        row = await cur.fetchone()
        assert row["id"] == 1
        assert row["status"] == "disabled"
        assert row["consecutive_failures"] == 0

    async def test_schema_version_stamped(self, tdb):
        cur = await tdb.execute("SELECT version FROM schema_version WHERE id = 1")
        assert (await cur.fetchone())["version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# ON DELETE SET NULL on snapshot references
# ---------------------------------------------------------------------------


class TestSnapshotReferenceSetNull:
    async def _seed_epoch_snapshot_delta(self, conn):
        await conn.execute(
            "INSERT INTO traffic_epoch "
            "(id, started_at_utc, first_uptime_seconds, reason) "
            "VALUES (1, '2026-06-12T00:00:00Z', 1.0, 'startup')"
        )
        await conn.execute(
            "INSERT INTO traffic_snapshot "
            "(id, ts_utc, seq, epoch_id, uptime_seconds, bytes_up, bytes_down) "
            "VALUES (1, '2026-06-12T00:01:00Z', 1, 1, 60.0, 100, 200)"
        )
        await conn.execute(
            "INSERT INTO traffic_delta "
            "(id, ts_utc, seq, epoch_id, snapshot_id, prev_snapshot_id, "
            " interval_seconds, bytes_up_delta, bytes_down_delta, source, "
            " anomaly_flag, counted) "
            "VALUES (1, '2026-06-12T00:01:00Z', 1, 1, 1, NULL, 60.0, "
            " 100, 200, 'normal', 'none', 1)"
        )
        await conn.commit()

    async def test_deleting_snapshot_nulls_link_and_keeps_delta(self, tdb):
        await self._seed_epoch_snapshot_delta(tdb)
        await tdb.execute("DELETE FROM traffic_snapshot WHERE id = 1")
        await tdb.commit()
        cur = await tdb.execute(
            "SELECT snapshot_id, bytes_up_delta, bytes_down_delta, counted "
            "FROM traffic_delta WHERE id = 1"
        )
        row = await cur.fetchone()
        assert row is not None, "delta row must survive snapshot deletion"
        assert row["snapshot_id"] is None, "snapshot_id must be SET NULL"
        assert row["bytes_up_delta"] == 100
        assert row["bytes_down_delta"] == 200
        assert row["counted"] == 1


# ---------------------------------------------------------------------------
# CHECK constraints
# ---------------------------------------------------------------------------


class TestCheckConstraints:
    async def test_rejects_bad_epoch_reason(self, tdb):
        with pytest.raises(aiosqlite.IntegrityError):
            await tdb.execute(
                "INSERT INTO traffic_epoch "
                "(started_at_utc, first_uptime_seconds, reason) "
                "VALUES ('2026-06-12T00:00:00Z', 1.0, 'bogus')"
            )
            await tdb.commit()

    async def test_rejects_bad_delta_source(self, tdb):
        await tdb.execute(
            "INSERT INTO traffic_epoch (id, started_at_utc, first_uptime_seconds, reason)"
            " VALUES (1, '2026-06-12T00:00:00Z', 1.0, 'startup')"
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await tdb.execute(
                "INSERT INTO traffic_delta "
                "(ts_utc, seq, epoch_id, interval_seconds, bytes_up_delta, "
                " bytes_down_delta, source) "
                "VALUES ('2026-06-12T00:01:00Z', 1, 1, 60.0, 1, 1, 'bogus')"
            )
            await tdb.commit()

    async def test_rejects_bad_health_status(self, tdb):
        with pytest.raises(aiosqlite.IntegrityError):
            await tdb.execute(
                "UPDATE collector_health SET status = 'bogus' WHERE id = 1"
            )
            await tdb.commit()

    async def test_rejects_bad_counted_value(self, tdb):
        await tdb.execute(
            "INSERT INTO traffic_epoch (id, started_at_utc, first_uptime_seconds, reason)"
            " VALUES (1, '2026-06-12T00:00:00Z', 1.0, 'startup')"
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await tdb.execute(
                "INSERT INTO traffic_delta "
                "(ts_utc, seq, epoch_id, interval_seconds, bytes_up_delta, "
                " bytes_down_delta, source, counted) "
                "VALUES ('2026-06-12T00:01:00Z', 1, 1, 60.0, 1, 1, 'normal', 2)"
            )
            await tdb.commit()


# ---------------------------------------------------------------------------
# recording_since derivation
# ---------------------------------------------------------------------------


class TestRecordingSinceDerivation:
    async def test_min_epoch_start_is_recording_since(self, tdb):
        for eid, start in ((1, "2026-06-12T08:00:00Z"), (2, "2026-06-13T09:30:00Z")):
            reason = "startup" if eid == 1 else "reset"
            await tdb.execute(
                "INSERT INTO traffic_epoch "
                "(id, started_at_utc, first_uptime_seconds, reason) "
                "VALUES (?, ?, 1.0, ?)",
                (eid, start, reason),
            )
        await tdb.commit()
        cur = await tdb.execute("SELECT MIN(started_at_utc) AS rec FROM traffic_epoch")
        assert (await cur.fetchone())["rec"] == "2026-06-12T08:00:00Z"


# ---------------------------------------------------------------------------
# create_tables() integration — additive alongside existing tables
# ---------------------------------------------------------------------------


class TestCreateTablesIntegration:
    @pytest.fixture
    def patched_db_path(self, tmp_path, monkeypatch):
        db_file = tmp_path / "test.db"
        import backend.database as db_mod
        monkeypatch.setattr(db_mod, "_PROD_DB_PATH", tmp_path / "absent" / "ccc.db")
        monkeypatch.setattr(db_mod, "_DEV_DB_PATH", db_file)
        return db_file

    async def test_traffic_and_existing_tables_coexist(self, patched_db_path):
        from backend.database import create_tables

        await create_tables()
        async with aiosqlite.connect(patched_db_path) as conn:
            conn.row_factory = aiosqlite.Row
            tables = await _names(conn, "table")
        # existing application tables
        assert {"sessions", "failed_attempts", "audit_log"} <= tables
        # traffic schema landed additively
        assert _TRAFFIC_TABLES <= tables
