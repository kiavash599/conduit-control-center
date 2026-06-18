# SPDX-License-Identifier: MIT
"""
Unit tests for backend/database.py

Coverage:
  - get_db_path()   — dev path when prod dir absent / prod path when dir exists
  - _TABLE_DDL      — all application tables are represented
  - create_tables() — creates all tables / idempotent (IF NOT EXISTS)
  - get_db()        — yields aiosqlite.Connection with row_factory set
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import aiosqlite
import pytest

from backend.database import (
    _TABLE_DDL,
    _restrict_db_file_permissions,
    create_tables,
    get_db,
    get_db_path,
)


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
    """_TABLE_DDL must define every application table."""

    # Tables that must always be present. Single source of truth: guards against
    # accidental schema loss (a missing/renamed table fails the presence loop)
    # and silent drift (the count must equal this set). Add new tables here.
    EXPECTED_TABLES = {"sessions", "failed_attempts", "audit_log", "app_settings"}

    def _combined(self):
        return " ".join(_TABLE_DDL).lower()

    def test_sessions_table_defined(self):
        assert "sessions" in self._combined()

    def test_failed_attempts_table_defined(self):
        assert "failed_attempts" in self._combined()

    def test_audit_log_table_defined(self):
        assert "audit_log" in self._combined()

    def test_app_settings_table_defined(self):
        assert "app_settings" in self._combined()

    def test_all_statements_use_if_not_exists(self):
        """DDL must be idempotent (safe to run on every startup)."""
        combined = self._combined()
        count = combined.count("if not exists")
        assert count == len(_TABLE_DDL)

    def test_ddl_defines_exactly_expected_tables(self):
        """All four expected application tables present, and no silent drift."""
        combined = self._combined()
        for table in self.EXPECTED_TABLES:
            assert table in combined, f"missing DDL for {table}"
        assert len(_TABLE_DDL) == len(self.EXPECTED_TABLES)


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


# ---------------------------------------------------------------------------
# File permissions (db-perms-600)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
class TestDatabaseFilePermissions:
    @pytest.fixture
    def patched_db_path(self, tmp_path, monkeypatch):
        db_file = tmp_path / "test.db"
        import backend.database as db_mod
        monkeypatch.setattr(db_mod, "_PROD_DB_PATH", tmp_path / "absent" / "ccc.db")
        monkeypatch.setattr(db_mod, "_DEV_DB_PATH", db_file)
        return db_file

    @staticmethod
    def _mode(p: Path) -> int:
        return stat.S_IMODE(os.stat(p).st_mode)

    @staticmethod
    def _sidecar(db_file: Path, suffix: str) -> Path:
        return db_file.with_name(db_file.name + suffix)

    async def test_db_file_is_0600_after_create(self, patched_db_path):
        await create_tables()
        assert self._mode(patched_db_path) == 0o600

    async def test_preexisting_loose_db_is_tightened(self, patched_db_path):
        # Simulate a database created before this change with loose perms.
        patched_db_path.write_bytes(b"")
        os.chmod(patched_db_path, 0o644)
        await create_tables()
        assert self._mode(patched_db_path) == 0o600

    def test_helper_restricts_main_and_existing_sidecars(self, patched_db_path):
        for suffix in ("", "-wal", "-shm"):
            f = patched_db_path if not suffix else self._sidecar(patched_db_path, suffix)
            f.write_bytes(b"")
            os.chmod(f, 0o644)
        _restrict_db_file_permissions(patched_db_path)
        for suffix in ("", "-wal", "-shm"):
            f = patched_db_path if not suffix else self._sidecar(patched_db_path, suffix)
            assert self._mode(f) == 0o600

    def test_helper_safe_when_sidecars_absent(self, patched_db_path):
        patched_db_path.write_bytes(b"")
        os.chmod(patched_db_path, 0o644)
        _restrict_db_file_permissions(patched_db_path)  # must not raise
        assert self._mode(patched_db_path) == 0o600
        assert not self._sidecar(patched_db_path, "-wal").exists()

    @staticmethod
    def _failing_chmod(fail_paths):
        """Return an os.chmod replacement that raises for the given paths."""
        real_chmod = os.chmod

        def fake_chmod(path, mode, *args, **kwargs):
            if str(path) in fail_paths:
                raise OSError("simulated chmod failure")
            return real_chmod(path, mode, *args, **kwargs)

        return fake_chmod

    def test_main_chmod_failure_raises(self, patched_db_path, monkeypatch):
        """A chmod failure on the main DB must propagate (startup fails)."""
        patched_db_path.write_bytes(b"")
        monkeypatch.setattr(os, "chmod", self._failing_chmod({str(patched_db_path)}))
        with pytest.raises(OSError):
            _restrict_db_file_permissions(patched_db_path)

    def test_sidecar_chmod_failure_is_warning_only(self, patched_db_path, monkeypatch):
        """A chmod failure on a sidecar must NOT raise; main DB still tightened."""
        patched_db_path.write_bytes(b"")
        wal = self._sidecar(patched_db_path, "-wal")
        wal.write_bytes(b"")
        monkeypatch.setattr(os, "chmod", self._failing_chmod({str(wal)}))
        _restrict_db_file_permissions(patched_db_path)  # must not raise
        assert self._mode(patched_db_path) == 0o600
