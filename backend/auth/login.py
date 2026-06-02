"""
backend/auth/login.py
---------------------
Password verification and user authentication for the single admin account.

Public API
----------
    verify_password(plain_password, stored_hash) -> bool
    authenticate_user(username, password)        -> None  (raises on failure)

Exceptions
----------
    AuthConfigError    -- ADMIN_PASSWORD_HASH not set in .env; caller returns 503
    InvalidCredentials -- username or password wrong;  caller returns 401

Security notes
--------------
- Passwords are verified with bcrypt.checkpw() (cost factor set at hash time,
  minimum 12 recommended; enforced by install.sh).
- verify_password() catches all bcrypt exceptions and treats them as a mismatch,
  so a malformed stored hash never results in an open door.
- authenticate_user() raises InvalidCredentials for BOTH wrong username AND
  wrong password. The client always receives the same generic message.
- Server-side log messages do distinguish the failure reason (username vs
  password vs misconfiguration) to aid debugging on the Pi.
- ADMIN_PASSWORD_HASH is read from Settings on every call. It is never cached
  in module state, so a hash rotation takes effect without a server restart
  (the lru_cache on get_settings() means a process restart is still needed to
  pick up .env changes, but the hash is not double-cached here).
- bcrypt.checkpw() is only called when the username matches "admin". Since
  "admin" is the only valid username and is not secret, the timing difference
  on a wrong username does not leak information useful to an attacker.
  Issue #15 (account lockout) provides the primary brute-force defence.

TODOs
-----
- Issue #15: add check_lockout(db, username) before verify_password().
  The call site is marked below with a TODO comment.
  Signature change required: add db: aiosqlite.Connection parameter.
"""

from __future__ import annotations

import logging

import bcrypt

from backend.config import get_settings

logger = logging.getLogger(__name__)

# The only valid username. Single-user application; not a secret.
_ADMIN_USERNAME = "admin"


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


async def authenticate_user(username: str, password: str) -> None:
    """
    Authenticate username/password against the configured admin account.

    This function is async so that Issue #15 can add an awaited lockout
    check without changing the call signature in the route handler.

    Parameters
    ----------
    username : submitted username  (from LoginRequest body)
    password : submitted password  (from LoginRequest body)

    Returns
    -------
    None on success (single-user app; no user object to return).

    Raises
    ------
    AuthConfigError
        ADMIN_PASSWORD_HASH is not set in .env / Settings.
        Route handler should return HTTP 503.

    InvalidCredentials
        Username or password is wrong.
        Route handler should return HTTP 401.
        Both wrong-username and wrong-password raise this exception so
        the client can never distinguish which field was incorrect.

    TODO (Issue #15)
    ----------------
    Add account lockout check before the password verification step.
    Requires adding db: aiosqlite.Connection to this signature.
    Insert the following call at the marked location below:

        from backend.auth.lockout import check_lockout
        await check_lockout(db, username)
        # check_lockout raises HTTPException(429) if the account is locked.
    """
    stored_hash = get_settings().admin_password_hash

    # ------------------------------------------------------------------
    # Guard: server misconfiguration -- hash not set.
    # Fail closed: an unconfigured server must not allow login.
    # Log the real reason; raise AuthConfigError for the route handler
    # to translate into a generic 503 response.
    # ------------------------------------------------------------------
    if not stored_hash:
        logger.error(
            "Login attempt rejected: ADMIN_PASSWORD_HASH is not set. "
            "Run install.sh or set ADMIN_PASSWORD_HASH in .env."
        )
        raise AuthConfigError("ADMIN_PASSWORD_HASH not configured")

    # ------------------------------------------------------------------
    # Username check.
    # Must come before the bcrypt call (bcrypt is CPU-bound).
    # Wrong username raises the same InvalidCredentials as wrong password
    # -- the client never learns which field was wrong.
    # ------------------------------------------------------------------
    if username != _ADMIN_USERNAME:
        logger.info(
            "Login rejected: unknown username %r", username
        )
        raise InvalidCredentials()

    # ------------------------------------------------------------------
    # TODO (Issue #15): insert check_lockout(db, username) here.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Password verification.
    # ------------------------------------------------------------------
    if not verify_password(password, stored_hash):
        logger.info(
            "Login rejected: wrong password for user %r", username
        )
        raise InvalidCredentials()

    logger.info("Login successful for user %r", username)
