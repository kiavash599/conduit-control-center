# SPDX-License-Identifier: MIT
"""
Unit tests for backend/auth/lockout.py

Coverage:
  - check_lockout()            — no record / null locked_until / active / expired
  - record_failed_attempt()    — first insert / increment / threshold / audit
  - record_successful_login()  — clears row / writes audit / no-op if absent
  - clear_lockout()            — returns True/False / deletes row / writes audit
  - AccountLocked exception    — carries locked_until with tzinfo
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.auth.lockout import (
    AccountLocked,
    _iso,
    _now,
    check_lockout,
    clear_lockout,
    record_failed_attempt,
    record_successful_login,
)


# ---------------------------------------------------------------------------
# Shared config fixture (threshold = 3, lockout = 15 min)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_config(monkeypatch):
    cfg = SimpleNamespace(max_failed_login_attempts=3, lockout_duration_minutes=15)
    monkeypatch.setattr("backend.auth.lockout.get_app_config", lambda: cfg)
    return cfg


# ---------------------------------------------------------------------------
# check_lockout()
# ---------------------------------------------------------------------------


class TestCheckLockout:
    async def test_no_record_does_not_raise(self, db):
        await check_lockout(db, "admin")  # must not raise

    async def test_record_with_null_locked_until_does_not_raise(self, db):
        await db.execute(
            "INSERT INTO failed_attempts (username, count, locked_until) VALUES (?, ?, ?)",
            ("admin", 2, None),
        )
        await db.commit()
        await check_lockout(db, "admin")  # must not raise

    async def test_active_lockout_raises_account_locked(self, db):
        future = _iso(_now() + timedelta(minutes=5))
        await db.execute(
            "INSERT INTO failed_attempts (username, count, locked_until) VALUES (?, ?, ?)",
            ("admin", 3, future),
        )
        await db.commit()
        with pytest.raises(AccountLocked):
            await check_lockout(db, "admin")

    async def test_account_locked_carries_timezone_aware_datetime(self, db):
        future = _iso(_now() + timedelta(minutes=5))
        await db.execute(
            "INSERT INTO failed_attempts (username, count, locked_until) VALUES (?, ?, ?)",
            ("admin", 3, future),
        )
        await db.commit()
        with pytest.raises(AccountLocked) as exc_info:
            await check_lockout(db, "admin")
        assert exc_info.value.locked_until.tzinfo is not None

    async def test_expired_lockout_does_not_raise(self, db):
        past = _iso(_now() - timedelta(seconds=1))
        await db.execute(
            "INSERT INTO failed_attempts (username, count, locked_until) VALUES (?, ?, ?)",
            ("admin", 3, past),
        )
        await db.commit()
        await check_lockout(db, "admin")  # must not raise — lockout has elapsed

    async def test_lockout_at_exact_boundary_does_not_raise(self, db):
        """
        Boundary: if locked_until equals _iso_now() exactly, the string
        comparison 'now < locked_until' is False, so no exception is raised.
        """
        now_str = _iso(_now())
        await db.execute(
            "INSERT INTO failed_attempts (username, count, locked_until) VALUES (?, ?, ?)",
            ("admin", 3, now_str),
        )
        await db.commit()
        # Should not raise (locked_until is not in the future)
        await check_lockout(db, "admin")


# ---------------------------------------------------------------------------
# record_failed_attempt()
# ---------------------------------------------------------------------------


class TestRecordFailedAttempt:
    async def test_first_failure_inserts_row(self, db):
        await record_failed_attempt(db, "admin")
        cursor = await db.execute(
            "SELECT count, locked_until FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["count"] == 1

    async def test_first_failure_no_lockout(self, db):
        await record_failed_attempt(db, "admin")
        cursor = await db.execute(
            "SELECT locked_until FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row["locked_until"] is None

    async def test_second_failure_increments(self, db):
        await record_failed_attempt(db, "admin")
        await record_failed_attempt(db, "admin")
        cursor = await db.execute(
            "SELECT count FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row["count"] == 2

    async def test_threshold_sets_locked_until(self, db):
        """Reaching max_failed_login_attempts (3) must set locked_until."""
        for _ in range(3):
            await record_failed_attempt(db, "admin")
        cursor = await db.execute(
            "SELECT locked_until FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row["locked_until"] is not None

    async def test_threshold_locked_until_is_in_future(self, db):
        for _ in range(3):
            await record_failed_attempt(db, "admin")
        cursor = await db.execute(
            "SELECT locked_until FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        # locked_until must be after now
        assert row["locked_until"] > _iso(_now())

    async def test_threshold_writes_login_locked_audit(self, db):
        for _ in range(3):
            await record_failed_attempt(db, "admin")
        cursor = await db.execute(
            "SELECT event_type FROM audit_log WHERE username='admin'"
        )
        rows = await cursor.fetchall()
        event_types = [r["event_type"] for r in rows]
        assert "LOGIN_LOCKED" in event_types

    async def test_below_threshold_no_audit_entry(self, db):
        await record_failed_attempt(db, "admin")  # count = 1, threshold = 3
        cursor = await db.execute("SELECT count(*) FROM audit_log")
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_beyond_threshold_count_keeps_incrementing(self, db):
        """Counter must continue incrementing after lockout (for audit trail)."""
        for _ in range(5):
            await record_failed_attempt(db, "admin")
        cursor = await db.execute(
            "SELECT count FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row["count"] == 5


# ---------------------------------------------------------------------------
# record_successful_login()
# ---------------------------------------------------------------------------


class TestRecordSuccessfulLogin:
    async def test_deletes_failed_attempts_row(self, db):
        await db.execute(
            "INSERT INTO failed_attempts (username, count) VALUES (?, ?)",
            ("admin", 2),
        )
        await db.commit()
        await record_successful_login(db, "admin")
        cursor = await db.execute(
            "SELECT count(*) FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_writes_login_success_audit(self, db):
        await record_successful_login(db, "admin")
        cursor = await db.execute(
            "SELECT event_type FROM audit_log WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["event_type"] == "LOGIN_SUCCESS"

    async def test_no_op_when_no_record_exists(self, db):
        """Must not raise even if no failed_attempts row is present."""
        await record_successful_login(db, "admin")

    async def test_audit_written_even_when_no_failed_attempts_row(self, db):
        await record_successful_login(db, "admin")
        cursor = await db.execute("SELECT count(*) FROM audit_log")
        row = await cursor.fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# clear_lockout()
# ---------------------------------------------------------------------------


class TestClearLockout:
    async def test_returns_false_when_no_record(self, db):
        result = await clear_lockout(db, "admin")
        assert result is False

    async def test_returns_true_when_record_found(self, db):
        await db.execute(
            "INSERT INTO failed_attempts (username, count, locked_until) VALUES (?, ?, ?)",
            ("admin", 3, "2099-01-01T00:00:00"),
        )
        await db.commit()
        result = await clear_lockout(db, "admin")
        assert result is True

    async def test_deletes_failed_attempts_row(self, db):
        await db.execute(
            "INSERT INTO failed_attempts (username, count) VALUES (?, ?)",
            ("admin", 1),
        )
        await db.commit()
        await clear_lockout(db, "admin")
        cursor = await db.execute(
            "SELECT count(*) FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_writes_unlock_cli_audit_entry(self, db):
        await db.execute(
            "INSERT INTO failed_attempts (username, count) VALUES (?, ?)",
            ("admin", 1),
        )
        await db.commit()
        await clear_lockout(db, "admin")
        cursor = await db.execute(
            "SELECT event_type FROM audit_log WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["event_type"] == "UNLOCK_CLI"

    async def test_unlock_cli_audit_detail_contains_os_user(self, db):
        await db.execute(
            "INSERT INTO failed_attempts (username, count) VALUES (?, ?)",
            ("admin", 1),
        )
        await db.commit()
        await clear_lockout(db, "admin")
        cursor = await db.execute(
            "SELECT detail FROM audit_log WHERE event_type='UNLOCK_CLI'"
        )
        row = await cursor.fetchone()
        assert "os_user=" in row["detail"]


# ---------------------------------------------------------------------------
# AccountLocked exception
# ---------------------------------------------------------------------------


class TestAccountLockedException:
    def test_carries_locked_until(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        exc = AccountLocked(locked_until=future)
        assert exc.locked_until == future

    def test_str_representation_includes_timestamp(self):
        future = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        exc = AccountLocked(locked_until=future)
        assert "2099" in str(exc)
