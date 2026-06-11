# SPDX-License-Identifier: MIT
"""
Unit tests for backend/auth/sessions.py

Coverage:
  - create_session()     — ID format, DB row, expiry, uniqueness
  - get_session()        — hit / miss / expired
  - touch_session()      — extends expiry / no-op on unknown
  - delete_session()     — removes row / no-op on unknown
  - delete_all_sessions()— removes all / returns correct count
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.auth.sessions import (
    _iso,
    _now,
    create_session,
    delete_all_sessions,
    delete_session,
    get_session,
    touch_session,
)


# ---------------------------------------------------------------------------
# Config patch — session_timeout_minutes must be known for expiry assertions
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_session_config(monkeypatch):
    cfg = SimpleNamespace(session_timeout_minutes=60)
    monkeypatch.setattr("backend.auth.sessions.get_app_config", lambda: cfg)


# ---------------------------------------------------------------------------
# create_session()
# ---------------------------------------------------------------------------


class TestCreateSession:
    async def test_returns_64_char_hex_string(self, db):
        sid = await create_session(db, "admin")
        assert len(sid) == 64
        assert all(c in "0123456789abcdef" for c in sid)

    async def test_row_inserted_in_sessions_table(self, db):
        sid = await create_session(db, "admin")
        cursor = await db.execute(
            "SELECT user_id FROM sessions WHERE id=?", (sid,)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["user_id"] == "admin"

    async def test_expires_at_is_in_the_future(self, db):
        sid = await create_session(db, "admin")
        cursor = await db.execute(
            "SELECT expires_at FROM sessions WHERE id=?", (sid,)
        )
        row = await cursor.fetchone()
        assert row["expires_at"] > _iso(_now())

    async def test_two_sessions_have_unique_ids(self, db):
        sid1 = await create_session(db, "admin")
        sid2 = await create_session(db, "admin")
        assert sid1 != sid2

    async def test_created_at_and_last_active_are_set(self, db):
        sid = await create_session(db, "admin")
        cursor = await db.execute(
            "SELECT created_at, last_active FROM sessions WHERE id=?", (sid,)
        )
        row = await cursor.fetchone()
        assert row["created_at"] is not None
        assert row["last_active"] is not None


# ---------------------------------------------------------------------------
# get_session()
# ---------------------------------------------------------------------------


class TestGetSession:
    async def test_valid_session_returns_row(self, db):
        sid = await create_session(db, "admin")
        row = await get_session(db, sid)
        assert row is not None

    async def test_row_has_correct_user_id(self, db):
        sid = await create_session(db, "admin")
        row = await get_session(db, sid)
        assert row["user_id"] == "admin"

    async def test_unknown_session_returns_none(self, db):
        row = await get_session(db, "a" * 64)
        assert row is None

    async def test_empty_string_returns_none(self, db):
        row = await get_session(db, "")
        assert row is None

    async def test_expired_session_returns_none(self, db):
        """A row whose expires_at is in the past must not be returned."""
        past = _iso(_now() - timedelta(seconds=1))
        await db.execute(
            """
            INSERT INTO sessions (id, user_id, created_at, last_active, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("e" * 64, "admin", past, past, past),
        )
        await db.commit()
        row = await get_session(db, "e" * 64)
        assert row is None


# ---------------------------------------------------------------------------
# touch_session()
# ---------------------------------------------------------------------------


class TestTouchSession:
    async def test_extends_expires_at(self, db):
        sid = await create_session(db, "admin")
        cursor = await db.execute(
            "SELECT expires_at FROM sessions WHERE id=?", (sid,)
        )
        original_expiry = (await cursor.fetchone())["expires_at"]

        # Advance time by patching _now so the second call yields a later value
        from unittest.mock import patch
        later = _now() + timedelta(seconds=2)
        with patch("backend.auth.sessions._now", return_value=later):
            await touch_session(db, sid)

        cursor = await db.execute(
            "SELECT expires_at FROM sessions WHERE id=?", (sid,)
        )
        new_expiry = (await cursor.fetchone())["expires_at"]
        assert new_expiry > original_expiry

    async def test_updates_last_active(self, db):
        sid = await create_session(db, "admin")
        cursor = await db.execute(
            "SELECT last_active FROM sessions WHERE id=?", (sid,)
        )
        original = (await cursor.fetchone())["last_active"]

        from unittest.mock import patch
        later = _now() + timedelta(seconds=2)
        with patch("backend.auth.sessions._now", return_value=later):
            await touch_session(db, sid)

        cursor = await db.execute(
            "SELECT last_active FROM sessions WHERE id=?", (sid,)
        )
        updated = (await cursor.fetchone())["last_active"]
        assert updated >= original

    async def test_no_op_for_unknown_session(self, db):
        """Must not raise for a non-existent session ID."""
        await touch_session(db, "z" * 64)


# ---------------------------------------------------------------------------
# delete_session()
# ---------------------------------------------------------------------------


class TestDeleteSession:
    async def test_removes_session_row(self, db):
        sid = await create_session(db, "admin")
        await delete_session(db, sid)
        row = await get_session(db, sid)
        assert row is None

    async def test_no_op_for_unknown_session(self, db):
        """Must not raise for a non-existent session ID."""
        await delete_session(db, "b" * 64)

    async def test_only_target_session_is_deleted(self, db):
        sid1 = await create_session(db, "admin")
        sid2 = await create_session(db, "admin")
        await delete_session(db, sid1)
        row2 = await get_session(db, sid2)
        assert row2 is not None


# ---------------------------------------------------------------------------
# delete_all_sessions()
# ---------------------------------------------------------------------------


class TestDeleteAllSessions:
    async def test_removes_all_sessions(self, db):
        await create_session(db, "admin")
        await create_session(db, "admin")
        await delete_all_sessions(db)
        cursor = await db.execute("SELECT count(*) FROM sessions")
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_returns_count_of_deleted_rows(self, db):
        await create_session(db, "admin")
        await create_session(db, "admin")
        count = await delete_all_sessions(db)
        assert count == 2

    async def test_returns_zero_when_table_is_empty(self, db):
        count = await delete_all_sessions(db)
        assert count == 0

    async def test_sessions_are_no_longer_retrievable(self, db):
        sid = await create_session(db, "admin")
        await delete_all_sessions(db)
        row = await get_session(db, sid)
        assert row is None


# ---------------------------------------------------------------------------
# purge_expired_sessions() — uses its own DB connection via get_db()
# ---------------------------------------------------------------------------


class TestPurgeExpiredSessions:
    """
    purge_expired_sessions() opens its own connection via get_db().
    We patch backend.auth.sessions.get_db with an async context manager
    that yields a pre-populated in-memory database.
    """

    async def test_removes_expired_sessions_and_returns_count(self):
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import aiosqlite

        from backend.auth.sessions import purge_expired_sessions
        from backend.database import _TABLE_DDL

        @asynccontextmanager
        async def mock_get_db():
            async with aiosqlite.connect(":memory:") as conn:
                conn.row_factory = aiosqlite.Row
                for ddl in _TABLE_DDL:
                    await conn.execute(ddl)
                await conn.commit()
                # Insert one expired and one valid session
                past = "2020-01-01T00:00:00"
                future = "2099-01-01T00:00:00"
                await conn.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                    ("expired-1", "admin", past, past, past),
                )
                await conn.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                    ("valid-1", "admin", past, past, future),
                )
                await conn.commit()
                yield conn

        with patch("backend.auth.sessions.get_db", mock_get_db):
            count = await purge_expired_sessions()

        assert count == 1

    async def test_returns_zero_when_no_expired_sessions(self):
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import aiosqlite

        from backend.auth.sessions import purge_expired_sessions
        from backend.database import _TABLE_DDL

        @asynccontextmanager
        async def mock_get_db():
            async with aiosqlite.connect(":memory:") as conn:
                conn.row_factory = aiosqlite.Row
                for ddl in _TABLE_DDL:
                    await conn.execute(ddl)
                await conn.commit()
                yield conn

        with patch("backend.auth.sessions.get_db", mock_get_db):
            count = await purge_expired_sessions()

        assert count == 0
