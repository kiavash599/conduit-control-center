"""
backend/auth/sessions.py
------------------------
SQLite-backed server-side session store.

Public API
----------
    create_session(db, user_id)   -> session_id: str
    get_session(db, session_id)   -> aiosqlite.Row | None
    touch_session(db, session_id) -> None
    delete_session(db, session_id)-> None
    purge_expired_sessions()      -> int  (rows deleted, logged)

Internal
--------
    _purge_loop()   -- private; called only from backend.main lifespan.
                       Not part of the public API.

Session lifecycle
-----------------
    login   -> create_session  -> set cookie
    request -> get_session     -> touch_session (extend sliding window)
    logout  -> delete_session  -> clear cookie
    hourly  -> _purge_loop     -> purge_expired_sessions

Security notes
--------------
- Session IDs: secrets.token_hex(32) -- 64 hex chars, 256-bit entropy.
- Expiry is sliding: each authenticated request calls touch_session()
  which extends expires_at by session_timeout_minutes from now.
- get_session() rejects expired rows on read (expires_at > NOW check).
- purge_expired_sessions() removes stale rows so the table stays small.
- All datetimes are UTC, stored as ISO 8601 TEXT in SQLite.
  Example: "2026-06-02T14:30:00"
- Session IDs are never logged in full; log messages show only the first
  8 characters followed by "..." for traceability without exposure.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

import aiosqlite

from backend.config import get_app_config
from backend.database import get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Format a UTC datetime as an ISO 8601 string for SQLite storage."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _timeout() -> timedelta:
    """Read session_timeout_minutes from config and return as a timedelta."""
    return timedelta(minutes=get_app_config().session_timeout_minutes)


def _mask(session_id: str) -> str:
    """Return first 8 chars + '...' for safe log output."""
    return session_id[:8] + "..."


# ---------------------------------------------------------------------------
# Public session functions
# ---------------------------------------------------------------------------


async def create_session(db: aiosqlite.Connection, user_id: str) -> str:
    """
    Create a new session for user_id and return the session ID.

    The session ID is a 64-character hex string (32 random bytes).
    Expiry is now + session_timeout_minutes (from config.json).

    Parameters
    ----------
    db       : open aiosqlite connection from the request dependency
    user_id  : identifier for the authenticated user (e.g. "admin")

    Returns
    -------
    session_id : str -- the opaque token to set as a cookie
    """
    session_id = secrets.token_hex(32)
    now = _now()
    expires_at = now + _timeout()

    await db.execute(
        """
        INSERT INTO sessions (id, user_id, created_at, last_active, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, user_id, _iso(now), _iso(now), _iso(expires_at)),
    )
    await db.commit()
    logger.debug(
        "Session created: id=%s user=%r expires=%s",
        _mask(session_id),
        user_id,
        _iso(expires_at),
    )
    return session_id


async def get_session(
    db: aiosqlite.Connection,
    session_id: str,
) -> aiosqlite.Row | None:
    """
    Return the session row if the session exists and has not expired.

    Returns None for any unknown, invalid, or expired session ID.
    Never raises on bad input -- callers treat None as "not authenticated".

    Parameters
    ----------
    db         : open aiosqlite connection
    session_id : value from the session cookie

    Returns
    -------
    aiosqlite.Row with keys (id, user_id, created_at, last_active, expires_at)
    or None if not found / expired.
    """
    cursor = await db.execute(
        "SELECT * FROM sessions WHERE id = ? AND expires_at > ?",
        (session_id, _iso(_now())),
    )
    return await cursor.fetchone()


async def touch_session(db: aiosqlite.Connection, session_id: str) -> None:
    """
    Extend the session expiry and update last_active (sliding window).

    Called on every authenticated request so active users are not logged out
    mid-session. Silent no-op if the session does not exist or has expired.

    Parameters
    ----------
    db         : open aiosqlite connection
    session_id : value from the session cookie
    """
    now = _now()
    new_expires = now + _timeout()
    await db.execute(
        """
        UPDATE sessions
           SET last_active = ?,
               expires_at  = ?
         WHERE id = ?
        """,
        (_iso(now), _iso(new_expires), session_id),
    )
    await db.commit()


async def delete_session(db: aiosqlite.Connection, session_id: str) -> None:
    """
    Delete a session (logout).

    Silent no-op if the session ID is not found.

    Parameters
    ----------
    db         : open aiosqlite connection
    session_id : value from the session cookie
    """
    await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await db.commit()
    logger.debug("Session deleted: id=%s", _mask(session_id))


async def delete_all_sessions(db: aiosqlite.Connection) -> int:
    """
    Delete every session row from the store.

    Called by PUT /api/settings/password after a successful password change
    to invalidate all active sessions — including the one currently making
    the request — before writing the new password hash.  This ensures that
    any session obtained with the old password becomes invalid regardless
    of whether the subsequent hash write succeeds.

    Parameters
    ----------
    db : aiosqlite.Connection -- open database connection from get_db

    Returns
    -------
    int -- number of sessions deleted (0 if no active sessions existed)
    """
    cursor = await db.execute("DELETE FROM sessions")
    count  = cursor.rowcount
    await db.commit()
    logger.info(
        "All %d session(s) deleted (password change -- pre-hash-write step)",
        count,
    )
    return count


async def purge_expired_sessions() -> int:
    """
    Delete all expired sessions and return the number of rows removed.

    Opens its own database connection so it can be called from outside a
    request context (startup purge and the hourly background loop).

    Returns
    -------
    count : int -- number of expired sessions deleted (0 if none found)

    Logs
    ----
    INFO  when one or more sessions are removed (includes count)
    DEBUG when nothing needed purging
    """
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM sessions WHERE expires_at <= ?",
            (_iso(_now()),),
        )
        count = cursor.rowcount
        await db.commit()

    if count:
        logger.info(
            "Session purge: removed %d expired session(s)", count
        )
    else:
        logger.debug("Session purge: no expired sessions found")
    return count


# ---------------------------------------------------------------------------
# Private background loop -- used only by backend.main lifespan
# ---------------------------------------------------------------------------


async def _purge_loop() -> None:
    """
    Run purge_expired_sessions() every hour indefinitely.

    This function is PRIVATE to this module. It is imported and managed
    exclusively by backend.main.lifespan. Do not call it from anywhere else.

    Lifecycle contract (enforced by backend.main)
    ---------------------------------------------
    1. Started:   asyncio.create_task(_purge_loop(), name="session-purge")
    2. Running:   sleeps 3600 s, then calls purge_expired_sessions()
    3. Cancelled: task.cancel() injects CancelledError at the sleep point
    4. Awaited:   main.py awaits the task after cancel to confirm clean exit

    Error handling
    --------------
    - asyncio.CancelledError is re-raised immediately so cancellation
      propagates correctly. Swallowing it would break task.cancel().
    - Any other Exception is logged and the loop continues. One transient
      database error should not permanently disable session cleanup on a
      long-running Pi service.

    Logs
    ----
    INFO  on start, on each completed purge cycle, and on clean shutdown
    ERROR on unexpected exceptions (loop continues)
    """
    logger.info(
        "Session purge loop started (interval: 1 hour, "
        "timeout: %d min)",
        get_app_config().session_timeout_minutes,
    )
    while True:
        try:
            await asyncio.sleep(3600)
            count = await purge_expired_sessions()
            logger.info(
                "Hourly session purge complete: %d session(s) removed", count
            )
        except asyncio.CancelledError:
            logger.info(
                "Session purge loop received cancellation -- exiting cleanly"
            )
            raise
        except Exception:
            logger.exception(
                "Session purge loop encountered an error; "
                "will retry next cycle"
            )
