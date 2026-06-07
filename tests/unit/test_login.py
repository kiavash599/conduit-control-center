# SPDX-License-Identifier: MIT
"""
Unit tests for backend/auth/login.py

Coverage:
  - verify_password()   — correct / wrong / malformed input
  - hash_password()     — output format, round-trip, uniqueness
  - authenticate_user() — success path, all failure paths, DoS guard
"""
from __future__ import annotations

from types import SimpleNamespace

import bcrypt
import pytest

from backend.auth.login import (
    AuthConfigError,
    InvalidCredentials,
    authenticate_user,
    hash_password,
    verify_password,
)
from backend.auth.lockout import AccountLocked

# Precomputed hash with low cost-factor so the test suite stays fast.
_KNOWN_PASSWORD = "correct_horse_battery_staple"
_KNOWN_HASH = bcrypt.hashpw(
    _KNOWN_PASSWORD.encode(), bcrypt.gensalt(rounds=4)
).decode()


# ---------------------------------------------------------------------------
# verify_password()
# ---------------------------------------------------------------------------


class TestVerifyPassword:
    def test_correct_password_returns_true(self):
        assert verify_password(_KNOWN_PASSWORD, _KNOWN_HASH) is True

    def test_wrong_password_returns_false(self):
        assert verify_password("definitely_wrong", _KNOWN_HASH) is False

    def test_empty_password_returns_false(self):
        assert verify_password("", _KNOWN_HASH) is False

    def test_malformed_hash_returns_false_not_exception(self):
        # A corrupted hash must not raise — it must return False.
        assert verify_password(_KNOWN_PASSWORD, "not-a-valid-hash") is False

    def test_empty_hash_returns_false_not_exception(self):
        assert verify_password(_KNOWN_PASSWORD, "") is False

    def test_unicode_password_accepted(self):
        password = "pässwörd_日本語"
        h = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode()
        assert verify_password(password, h) is True


# ---------------------------------------------------------------------------
# hash_password()
# ---------------------------------------------------------------------------


class TestHashPassword:
    def test_produces_bcrypt_hash(self):
        h = hash_password("my_password")
        assert h.startswith("$2b$")

    def test_hash_round_trips_with_bcrypt(self):
        plain = "round_trip_test"
        h = hash_password(plain)
        assert bcrypt.checkpw(plain.encode(), h.encode())

    def test_verify_password_accepts_output(self):
        plain = "verify_accepts_me"
        h = hash_password(plain)
        assert verify_password(plain, h) is True

    def test_two_calls_produce_different_hashes(self):
        """bcrypt uses a random salt — identical plaintext yields different hashes."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2

    def test_returns_str_not_bytes(self):
        h = hash_password("test")
        assert isinstance(h, str)


# ---------------------------------------------------------------------------
# authenticate_user()
# ---------------------------------------------------------------------------


class TestAuthenticateUser:
    """
    authenticate_user() is async and requires a DB connection for lockout
    operations.  All tests use the shared in-memory 'db' fixture from
    tests/unit/conftest.py.
    """

    @pytest.fixture(autouse=True)
    def patch_dependencies(self, monkeypatch):
        """
        Patch get_settings (in login.py) and get_app_config (in lockout.py)
        to return known, deterministic values.  This isolates authenticate_user
        from the file system so tests pass without a .env or config.json.
        """
        settings = SimpleNamespace(
            admin_password_hash=_KNOWN_HASH,
            admin_username="admin",
        )
        cfg = SimpleNamespace(
            max_failed_login_attempts=5,
            lockout_duration_minutes=15,
        )
        monkeypatch.setattr("backend.auth.login.get_settings", lambda: settings)
        monkeypatch.setattr("backend.auth.lockout.get_app_config", lambda: cfg)

    # -- Success path --------------------------------------------------------

    async def test_correct_credentials_returns_none(self, db):
        result = await authenticate_user(db, "admin", _KNOWN_PASSWORD)
        assert result is None

    async def test_success_clears_failed_attempts(self, db):
        # Pre-seed a partial failure count.
        await db.execute(
            "INSERT INTO failed_attempts (username, count) VALUES (?, ?)",
            ("admin", 2),
        )
        await db.commit()
        await authenticate_user(db, "admin", _KNOWN_PASSWORD)
        cursor = await db.execute(
            "SELECT count(*) FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_success_writes_login_success_audit(self, db):
        await authenticate_user(db, "admin", _KNOWN_PASSWORD)
        cursor = await db.execute(
            "SELECT event_type FROM audit_log WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row["event_type"] == "LOGIN_SUCCESS"

    # -- Wrong username -------------------------------------------------------

    async def test_wrong_username_raises_invalid_credentials(self, db):
        with pytest.raises(InvalidCredentials):
            await authenticate_user(db, "hacker", _KNOWN_PASSWORD)

    async def test_wrong_username_does_not_write_to_failed_attempts(self, db):
        """
        DoS guard: wrong-username attempts must never touch the lockout table.
        This prevents an attacker locking the admin account via username variants.
        """
        with pytest.raises(InvalidCredentials):
            await authenticate_user(db, "Admin", _KNOWN_PASSWORD)  # wrong case
        cursor = await db.execute("SELECT count(*) FROM failed_attempts")
        row = await cursor.fetchone()
        assert row[0] == 0

    # -- Wrong password -------------------------------------------------------

    async def test_wrong_password_raises_invalid_credentials(self, db):
        with pytest.raises(InvalidCredentials):
            await authenticate_user(db, "admin", "wrong_password")

    async def test_wrong_password_increments_failed_attempts(self, db):
        with pytest.raises(InvalidCredentials):
            await authenticate_user(db, "admin", "bad1")
        with pytest.raises(InvalidCredentials):
            await authenticate_user(db, "admin", "bad2")
        cursor = await db.execute(
            "SELECT count FROM failed_attempts WHERE username='admin'"
        )
        row = await cursor.fetchone()
        assert row["count"] == 2

    # -- Server misconfiguration ----------------------------------------------

    async def test_missing_password_hash_raises_auth_config_error(self, db, monkeypatch):
        settings = SimpleNamespace(admin_password_hash="", admin_username="admin")
        monkeypatch.setattr("backend.auth.login.get_settings", lambda: settings)
        with pytest.raises(AuthConfigError):
            await authenticate_user(db, "admin", _KNOWN_PASSWORD)

    # -- Lockout integration --------------------------------------------------

    async def test_locked_account_raises_account_locked(self, db):
        from datetime import datetime, timedelta, timezone
        from backend.auth.lockout import _iso

        future = _iso(datetime.now(timezone.utc) + timedelta(minutes=10))
        await db.execute(
            "INSERT INTO failed_attempts (username, count, locked_until) VALUES (?, ?, ?)",
            ("admin", 5, future),
        )
        await db.commit()
        with pytest.raises(AccountLocked):
            await authenticate_user(db, "admin", _KNOWN_PASSWORD)
