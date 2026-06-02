"""
backend/auth/lockout.py
-----------------------
Account lockout: brute-force protection for the login endpoint.

Public API
----------
    check_lockout(db, username)          -> None   (raises AccountLocked if locked)
    record_failed_attempt(db, username)  -> None   (increments counter;
                                            locks if threshold reached)
    record_successful_login(db, username)-> None   (resets counter; writes audit)
    clear_lockout(db, username)          -> bool   (used by ccc-unlock;
                                            returns True if found)

Exception
---------
    AccountLocked(locked_until)  -- domain exception; caller converts to HTTP 429

Audit events written to audit_log table
---------------------------------------
    LOGIN_LOCKED   -- account just crossed the failure threshold
    LOGIN_SUCCESS  -- successful login (for correlation with any prior lockouts)
    UNLOCK_CLI     -- ccc-unlock cleared the lockout record

Security design
---------------
- No FastAPI imports. This module is framework-agnostic so it can be used
  by the ccc-unlock CLI and tested without an HTTP context.
- record_failed_attempt() is called ONLY after the username has been
  confirmed as "admin" (exact match). Wrong-username attempts are rejected
  before reaching this module and do not write to failed_attempts.
  This prevents an attacker from locking out the admin account by submitting
  incorrect username variants (e.g. "Admin", "ADMIN").
- Thresholds and lockout duration are read from AppConfig (config.json),
  defaulting to max_failed_login_attempts=5 and lockout_duration_minutes=15.
- Audit log write failures are swallowed after an ERROR log. A failed audit
  write must never abort a security operation (lockout recording or unlock).

DoS risk (accepted)
-------------------
An attacker who knows the username "admin" can lock the account by sending
five wrong passwords every 16 minutes. Recovery requires SSH access and
running ccc-unlock. Issue #34 (Nginx rate limiting) is the primary mitigation
and MUST be deployed on any internet-facing installation before this is used.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from backend.config import get_app_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AccountLocked(Exception):
    """
    Raised by check_lockout() when the account is currently locked.

    Carries locked_until (UTC, timezone-aware) so the route handler can
    compute the Retry-After header value without re-querying the database.

    The route handler should convert this to HTTP 429 with:
        Retry-After: <ceil((locked_until - now).total_seconds())>
    """

    def __init__(self, locked_until: datetime) -> None:
        super().__init__(
            f"Account locked until {locked_until.isoformat()}"
        )
        self.locked_until: datetime = locked_until


# ---------------------------------------------------------------------------
# Datetime helpers (UTC; consistent with sessions.py convention)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """
    Format a UTC datetime as ISO 8601 for SQLite TEXT storage.

    Strips timezone info before storing (naive UTC), matching the convention
    used throughout this project (sessions.py).  String comparison of ISO
    8601 datetimes in the same format is lexicographically correct.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _iso_now() -> str:
    """Current UTC time as an ISO 8601 string."""
    return _iso(_now())


# ---------------------------------------------------------------------------
# Audit log helper (private)
# ---------------------------------------------------------------------------


async def _write_audit(
    db: aiosqlite.Connection,
    event_type: str,
    username: str,
    detail: str,
) -> None:
    """
    Append one row to audit_log.

    Parameters
    ----------
    db         : open aiosqlite connection
    event_type : one of LOGIN_LOCKED, LOGIN_SUCCESS, UNLOCK_CLI
    username   : the affected username (always "admin" in v0.1)
    detail     : static server-side description -- NEVER user-submitted data

    Failures are logged at ERROR and silently suppressed so that a database
    hiccup never aborts the security operation that called this function.
    """
    try:
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, event_type, username, detail)
            VALUES (?, ?, ?, ?)
            """,
            (_iso_now(), event_type, username, detail),
        )
        await db.commit()
    except Exception:
        logger.error(
            "Failed to write audit log "
            "(event=%r, username=%r) -- continuing",
            event_type,
            username,
        )


# ---------------------------------------------------------------------------
# Public lockout functions
# ---------------------------------------------------------------------------


async def check_lockout(
    db: aiosqlite.Connection,
    username: str,
) -> None:
    """
    Raise AccountLocked if the account is currently locked.

    Silent no-op if no lockout record exists or the lockout has expired.
    Expired lockout rows are left in place; they are cleared on the next
    successful login or by ccc-unlock.

    Parameters
    ----------
    db       : open aiosqlite connection (from request dependency)
    username : must be the canonical "admin" (callers ensure this)

    Raises
    ------
    AccountLocked  -- account is locked; .locked_until gives the expiry
    """
    cursor = await db.execute(
        "SELECT locked_until FROM failed_attempts WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()

    if row is None or row["locked_until"] is None:
        return  # no record or no active lockout

    # String comparison is safe: both sides are "YYYY-MM-DDTHH:MM:SS" UTC
    if _iso_now() < row["locked_until"]:
        locked_until = datetime.fromisoformat(
            row["locked_until"]
        ).replace(tzinfo=timezone.utc)
        raise AccountLocked(locked_until=locked_until)

    # Lockout timestamp is in the past -- expired, do not raise


async def record_failed_attempt(
    db: aiosqlite.Connection,
    username: str,
) -> None:
    """
    Increment the failed-attempt counter for username.

    If the counter reaches max_failed_login_attempts (from config.json),
    set locked_until = now + lockout_duration_minutes and write a
    LOGIN_LOCKED audit entry.

    This function must only be called AFTER confirming username == "admin".
    It is the caller's responsibility to ensure this precondition.

    Parameters
    ----------
    db       : open aiosqlite connection (from request dependency)
    username : must be the canonical "admin"
    """
    cfg = get_app_config()
    max_attempts = cfg.max_failed_login_attempts
    lockout_minutes = cfg.lockout_duration_minutes

    # Read current count (0 if no row yet)
    cursor = await db.execute(
        "SELECT count FROM failed_attempts WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()
    new_count = (row["count"] if row else 0) + 1

    locked_until_str: str | None = None
    if new_count >= max_attempts:
        locked_until_str = _iso(_now() + timedelta(minutes=lockout_minutes))

    # Upsert: insert or update on conflict
    await db.execute(
        """
        INSERT INTO failed_attempts (username, count, locked_until)
        VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            count        = excluded.count,
            locked_until = excluded.locked_until
        """,
        (username, new_count, locked_until_str),
    )
    await db.commit()

    logger.info(
        "Failed login attempt %d/%d for user %r",
        new_count,
        max_attempts,
        username,
    )

    if locked_until_str:
        logger.warning(
            "Account %r locked after %d failed attempts "
            "(until %s)",
            username,
            new_count,
            locked_until_str,
        )
        await _write_audit(
            db,
            "LOGIN_LOCKED",
            username,
            f"Locked after {new_count} failed attempts, "
            f"until {locked_until_str}",
        )


async def record_successful_login(
    db: aiosqlite.Connection,
    username: str,
) -> None:
    """
    Clear the failed-attempt record and write a LOGIN_SUCCESS audit entry.

    Called after password verification succeeds so that the counter is reset
    and the audit trail records the successful authentication.

    Parameters
    ----------
    db       : open aiosqlite connection (from request dependency)
    username : must be the canonical "admin"
    """
    await db.execute(
        "DELETE FROM failed_attempts WHERE username = ?",
        (username,),
    )
    await db.commit()
    await _write_audit(db, "LOGIN_SUCCESS", username, "Login successful")


async def clear_lockout(
    db: aiosqlite.Connection,
    username: str,
) -> bool:
    """
    Clear the lockout record for username (used by ccc-unlock).

    Parameters
    ----------
    db       : open aiosqlite connection (caller manages this)
    username : the username to unlock (validated as "admin" by the CLI)

    Returns
    -------
    True  if a record was found and deleted
    False if no record existed (already clear or never locked)

    Writes an UNLOCK_CLI audit entry when a record is found and cleared.
    The audit detail includes the OS username so the unlock is traceable.
    """
    import os  # stdlib; deferred so normal server path has no overhead

    cursor = await db.execute(
        "SELECT count, locked_until FROM failed_attempts WHERE username = ?",
        (username,),
    )
    row = await cursor.fetchone()

    if row is None:
        return False

    os_user = os.getenv("USER") or os.getenv("LOGNAME") or "unknown"
    locked_until = row["locked_until"] or "not set"

    await db.execute(
        "DELETE FROM failed_attempts WHERE username = ?",
        (username,),
    )
    await db.commit()

    await _write_audit(
        db,
        "UNLOCK_CLI",
        username,
        f"Cleared by ccc-unlock (os_user={os_user!r}, "
        f"was locked_until={locked_until!r}, "
        f"attempt_count={row['count']})",
    )
    logger.info(
        "Lockout cleared for %r by os_user=%r "
        "(was locked_until=%r, count=%d)",
        username,
        os_user,
        locked_until,
        row["count"],
    )
    return True
