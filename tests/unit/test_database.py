# SPDX-License-Identifier: MIT
"""
Unit tests for backend/database.py

Coverage:
  - get_db_path()   — dev path when prod dir absent / prod path when dir exists
  - _TABLE_DDL      — all three tables are represented
  - create_tables() — creates all tables / idempotent (IF NOT EXISTS)
  - get_db()        — yields aiosqlite.Connection with row_factory set
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from backend.database import _TABLE_DDL, create_tables, get_db, get_db_path


# ---------------------------------------------------------------------------
# get_db_path()
# ---------------------------------------------------------------------------


class TestGetDbPath:
    def test_returns_path_object(self):
        assert isinstance(get_db_path(), Path)

    def test_dev_path_when_prod_directory_absent(self, tmp_path, monkeypatch):
        """When /etc/conduit-cc/ does not exist, the dev path must be returned."""
        import backend.database as db_mod
        monkeypatch.setattr(db_mod, "_PROD_DB_PATH", tmp_path / "absent" / "ccc.db")
        monkeypatch.setattr(db_mod, "_DEV_DB_PATH", tmp_path / "dev.db")
        assert get_db_path() == tmp_path / "dev.db"

    def test_prod_path_when_prod_directory_exists(self, tmp_path, monkeypatch):
        """When /etc/conduit-cc/ exists, the prod path must be returned."""
        prod_dir = tmp_path / "conduit-cc"
        prod_dir.mkdir()
        import backend.database as db_mod
        monkeypatch.setattr(db_mod, "_PROD_DB_PATH", prod_dir / "ccc.db")
        monkeypatch.setattr(db_mod, "_DEV_DB_PATH", tmp_path / "dev.db")
        assert get_db_path() == prod_dir / "ccc.db"


# ---------------------------------------------------------------------------
# _TABLE_DDL — schema presence checks
# ---------------------------------------------------------------------------


class TestTableDdl:
    """_TABLE_DDL must define all three application tables."""

    def _combined(self):
        return " ".join(_TABLE_DDL).lower()

    def test_sessions_table_defined(self):
        assert "sessions" in self._combined()

    def test_failed_attempts_table_defined(self):
        assert "failed_attempts" in self._combined()

    def test_audit_log_table_defined(self):
        assert "audit_log" in self._combined()

    def test_all_statements_use_if_not_exists(self):
        """DDL must be idempotent (safe to run on every startup)."""
        combined = self._combined()
        count = combined.count("if not exists")
        assert count == len(_TABLE_DDL)

    def test_three_ddl_statements(self):
        assert len(_TABLE_DDL) == 3


# ---------------------------------------------------------------------------
# create_tables()
# ---------------------------------------------------------------------------


class TestCreateTables:
    @pytest.fixture
    def patched_db_path(self, tmp_path, monkeypatch):
        """Point both path constants at tmp_path so no real files are touched."""
        db_file = tmp_path / "test.db"
        import backend.database as db_mod
        monkeypatch.setattr(db_mod, "_PROD_DB_PATH", tmp_path / "absent" / "ccc.db")
        monkeypatch.setattr(db_mod, "_DEV_DB_PATH", db_file)
        return db_file

    async def test_creates_sessions_table(self, patched_db_path):
        await create_tables()
        async with aiosqlite.connect(patched_db_path) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_failed_attempts_table(self, patched_db_path):
        await create_tables()
        async with aiosqlite.connect(patched_db_path) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='failed_attempts'"
            )
            assert await cursor.fetchone() is not None

    async def test_creates_audit_log_table(self, patched_db_path):
        await create_tables()
        async with aiosqlite.connect(patched_db_path) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
            )
            assert await cursor.fetchone() is not None

    async def test_idempotent_second_call_does_not_raise(self, patched_db_path):
        await create_tables()
        await create_tables()  # must not raise (IF NOT EXISTS)


# ---------------------------------------------------------------------------
# get_db()
# ---------------------------------------------------------------------------


class TestGetDb:
    @pytest.fixture
    async def ready_db_path(self, tmp_path, monkeypatch):
        """Create tables first so get_db() has a valid schema to connect to."""
        db_file = tmp_path / "test.db"
        import backend.database as db_mod
        monkeypatch.setattr(db_mod, "_PROD_DB_PATH", tmp_path / "absent" / "ccc.db")
        monkeypatch.setattr(db_mod, "_DEV_DB_PATH", db_file)
        await create_tables()
        return db_file

    async def test_yields_aiosqlite_connection(self, ready_db_path):
        async with get_db() as conn:
            assert isinstance(conn, aiosqlite.Connection)

    async def test_row_factory_is_aiosqlite_row(self, ready_db_path):
        async with get_db() as conn:
            assert conn.row_factory is aiosqlite.Row

    async def test_can_execute_query(self, ready_db_path):
        async with get_db() as conn:
            cursor = await conn.execute("SELECT count(*) FROM sessions")
            row = await cursor.fetchone()
            assert row[0] == 0
