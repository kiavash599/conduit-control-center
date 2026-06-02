"""
backend/auth/login.py
---------------------
Password verification and user authentication for the single admin account.

Public API
----------
    verify_password(plain_password, stored_hash) -> bool
    authenticate_user(db, username, password)    -> None  (raises on failure)

Exceptions
----------
    AuthConfigError    -- ADMIN_PASSWORD_HASH not set in .env; caller returns 503
    InvalidCredentials -- username or password wrong;  caller returns 401
    AccountLocked      -- account is locked (re-exported from lockout.py);
                          caller returns 429 with Retry-After header

Authentication flow (in authenticate_user)
------------------------------------------
1. Verify ADMIN_PASSWORD_HASH is configured          -> AuthConfigError if not
2. Check username == settings.admin_username (exact) -> InvalidCredentials if not
   (no DB write: wrong-username attempts do not touch the lockout table)
3. check_lockout(db, username)                       -> AccountLocked if locked
4. Verify password with bcrypt                       -> record_failed_attempt
                                                        + InvalidCredentials
5. record_successful_login(db, username)             -> returns None

Security notes
--------------
- Wrong-username and wrong-password both raise InvalidCredentials.
  The client always receives the same generic message.
- record_failed_attempt() is only reached after the username is confirmed
  as exactly settings.admin_username. Wrong-casing attempts (e.g. "Admin")
  fail at step 2 and write nothing to the database. This prevents the DoS
  variant where an attacker locks out the admin by submitting incorrect casing.
- ADMIN_PASSWORD_HASH and ADMIN_USERNAME are read from Settings on each call.
  Settings is an lru_cache singleton so this is effectively free.
- bcrypt.checkpw() is CPU-bound but acceptable on a single-user Pi. A future
  improvement could run it in an executor; deferred until profiling shows need.
- See lockout.py for the DoS risk acceptance note regarding Issue #34.
"""

from __future__ import annotations

import logging

import aiosqlite
import bcrypt

from backend.auth.lockout import (
    AccountLocked,  # re-exported for callers  # noqa: F401
    check_lockout,
    record_failed_attempt,
    record_successful_login,
)
from backend.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthConfigError(Exception):
    """
    Raised when the server is not configured for login.

    Cause: ADMIN_PASSWORD_HASH is empty or not set in .env.
    The route handler should return HTTP 503 (Service Unavailable).
    This exception must never produce a meaningful message to the client --
    only a generic server error response.
    """


class InvalidCredentials(Exception):
    """
    Raised when the submitted username or password does not match.

    The route handler should return HTTP 401 with a generic message.
    Callers must never indicate which field was wrong.
    """


# ---------------------------------------------------------------------------
# Password verification
# ---------------------------------------------------------------------------


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """
    Return True if plain_password matches stored_hash using bcrypt.

    Parameters
    ----------
    plain_password : submitted plaintext password (from request body)
    stored_hash    : bcrypt hash string from .env ADMIN_PASSWORD_HASH

    Returns
    -------
    True if the password matches, False otherwise.

    Notes
    -----
    Both arguments are UTF-8 encoded before passing to bcrypt.
    Any exception from bcrypt (e.g. malformed hash) is caught and treated
    as a mismatch -- never as a configuration error visible to the caller.
    The calling logger.warning makes the server-side cause visible in logs.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            stored_hash.encode("utf-8"),
        )
    except Exception:
        logger.warning(
            "bcrypt.checkpw raised an exception -- "
            "ADMIN_PASSWORD_HASH may be malformed in .env"
        )
        return False


# ---------------------------------------------------------------------------
# User authentication
# ---------------------------------------------------------------------------


async def authenticate_user(
    db: aiosqlite.Connection,
    username: str,
    password: str,
) -> None:
    """
    Authenticate username/password against the configured admin account.

    Performs all lockout checks and recording in a single call so that
    every call site automatically gets the complete, correct behaviour.

    Parameters
    ----------
    db       : open aiosqlite connection (from route get_db dependency)
    username : submitted username (from LoginRequest body)
    password : submitted password (from LoginRequest body)

    Returns
    -------
    None on success.

    Raises
    ------
    AuthConfigError
        ADMIN_PASSWORD_HASH is not configured.
        Route handler returns HTTP 503.

    AccountLocked
        Account is currently locked (.locked_until carries the expiry).
        Route handler returns HTTP 429 with Retry-After header.

    InvalidCredentials
        Username or password is wrong.
        Route handler returns HTTP 401.
        Both wrong-username and wrong-password raise this exception so
        the client can never distinguish which field was incorrect.
    """
    stored_hash = get_settings().admin_password_hash

    # ------------------------------------------------------------------
    # Guard: server misconfiguration -- hash not set.
    # Fail closed: an unconfigured server must not allow login.
    # ------------------------------------------------------------------
    if not stored_hash:
        logger.error(
            "Login attempt rejected: ADMIN_PASSWORD_HASH is not set. "
            "Run install.sh or set ADMIN_PASSWORD_HASH in .env."
        )
        raise AuthConfigError("ADMIN_PASSWORD_HASH not configured")

    # ------------------------------------------------------------------
    # Username check.
    # Wrong username: fast path, no DB write, no lockout involvement.
    # The client sees the same generic error as a wrong password.
    # ------------------------------------------------------------------
    if username != get_settings().admin_username:
        logger.info("Login rejected: unknown username %r", username)
        raise InvalidCredentials()

    # ------------------------------------------------------------------
    # Lockout check.
    # Only reached after username is confirmed as exactly admin_username.
    # Raises AccountLocked if the account is currently locked.
    # ------------------------------------------------------------------
    await check_lockout(db, username)

    # ------------------------------------------------------------------
    # Password verification.
    # On failure: record attempt (may trigger lockout), raise generic error.
    # ------------------------------------------------------------------
    if not verify_password(password, stored_hash):
        logger.info("Login rejected: wrong password for user %r", username)
        await record_failed_attempt(db, username)
        raise InvalidCredentials()

    # ------------------------------------------------------------------
    # Success: reset failed-attempt counter, write audit entry.
    # ------------------------------------------------------------------
    await record_successful_login(db, username)
    logger.info("Login successful for user %r", username)
