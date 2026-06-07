"""
backend/api/auth.py
-------------------
Authentication route handlers.

Routes
------
    POST /api/auth/login   -- verify credentials, create session, set cookies
    POST /api/auth/logout  -- delete session, clear cookies (lenient)

Security notes
--------------
- All failure paths return HTTP 401 with the same generic message
  "Invalid credentials". The client cannot determine which field was wrong.
- A locked account returns HTTP 429 with a Retry-After header (seconds until
  unlock). The response body is "Too many failed login attempts" -- no hint
  about the lock duration is given beyond the standard Retry-After header.
- A missing ADMIN_PASSWORD_HASH returns HTTP 503 "Service temporarily
  unavailable" -- no configuration details are exposed to the client.
- Session cookie is set with HttpOnly, Secure (configurable), SameSite=strict.
- CSRF token cookie is set alongside the session cookie on login (Issue #33).
  It is non-HttpOnly so the frontend JavaScript can read it and send it as
  the X-CSRF-Token header on state-changing requests.
- Logout is lenient: both cookies are always cleared and HTTP 200 is always
  returned, even if the session was already expired or not found.
  This prevents a race-condition lockout when the session expires between
  page load and the user clicking "Log out".
  CSRF validation is intentionally omitted from logout: the worst outcome of
  a CSRF logout is a forced logout (DoS), not a data breach. SameSite=strict
  already mitigates this for modern browsers, and omitting CSRF on logout
  avoids the upgrade edge-case where existing sessions have no csrf_token
  cookie yet.
"""

from __future__ import annotations

import logging
import math
import secrets
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from backend.auth.login import (
    AccountLocked,
    AuthConfigError,
    InvalidCredentials,
    authenticate_user,
)
from backend.auth.sessions import create_session, delete_session
from backend.auth.cookies import (
    COOKIE_NAME,
    set_session_cookie,
    clear_session_cookie,
    set_csrf_cookie,
    clear_csrf_cookie,
)
from backend.config import get_settings
from backend.dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """
    Body accepted by POST /api/auth/login.

    Both fields are required. Constraints guard against oversized payloads
    without leaking whether the credentials are "close" to correct.
    """

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

# Cookie helpers are in backend/auth/cookies.py -- imported above.
# Private copies removed (Issue #31). CSRF helpers added (Issue #33).


def _retry_after_seconds(locked_until: datetime) -> int:
    """
    Compute the Retry-After value in whole seconds.

    Returns the ceiling of (locked_until - now) in seconds, clamped to a
    minimum of 1 so the header is never zero or negative (which would imply
    the client can retry immediately, potentially bypassing the lockout).

    Parameters
    ----------
    locked_until : timezone-aware UTC datetime from AccountLocked.locked_until

    Returns
    -------
    int -- seconds to wait before retrying, always >= 1
    """
    delta = (locked_until - datetime.now(timezone.utc)).total_seconds()
    return max(1, math.ceil(delta))


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    summary="Log in with username and password",
    responses={
        200: {"description": "Login successful; session and CSRF cookies set"},
        401: {"description": "Invalid credentials"},
        422: {"description": "Request body validation failed"},
        429: {"description": "Account locked; Retry-After header gives wait seconds"},
        503: {"description": "Server not configured for login"},
    },
)
async def login(
    body: LoginRequest,
    response: Response,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """
    Authenticate the user and create a session.

    On success, sets a session cookie (HttpOnly) and a CSRF token cookie
    (non-HttpOnly, readable by JavaScript) and returns {"status": "ok"}.
    On failure, returns 401 with a generic message (no field hint).
    If the account is locked, returns 429 with a Retry-After header.

    The cookies are set on the injected Response object; FastAPI merges
    their headers into the final response alongside the returned dict body.

    CSRF token (Issue #33)
    ----------------------
    A 256-bit random token is generated per session and set as the
    non-HttpOnly ``csrf_token`` cookie.  The frontend (api.js getCsrfToken)
    reads this value and sends it as the ``X-CSRF-Token`` header on every
    state-changing API call.  The backend validates this with the
    require_csrf_token dependency (see backend/dependencies.py).
    """
    try:
        await authenticate_user(db, body.username, body.password)
    except AuthConfigError:
        # Server-side misconfiguration: hash not set.
        # Log detail server-side; return generic 503 to client.
        logger.error(
            "Login rejected due to server misconfiguration "
            "(ADMIN_PASSWORD_HASH not set)"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )
    except AccountLocked as exc:
        # Account locked after too many failed attempts.
        # Retry-After tells the client how long to wait (RFC 9110 s10.2.4).
        # The exact wait time is intentionally exposed here: it is already
        # derivable from the lockout_duration_minutes in config.json, and
        # concealing it would only reduce usability for legitimate operators.
        retry_after = _retry_after_seconds(exc.locked_until)
        logger.warning(
            "Login rejected: account locked (retry_after=%ds)", retry_after
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    admin_username = get_settings().admin_username
    session_id = await create_session(db, admin_username)
    csrf_token = secrets.token_hex(32)

    set_session_cookie(response, session_id)
    set_csrf_cookie(response, csrf_token)

    logger.info("Session created for user %r", admin_username)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    summary="Invalidate current session and clear cookies",
    responses={
        200: {"description": "Logged out; session and CSRF cookies cleared"},
    },
)
async def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """
    Delete the server-side session and clear both cookies.

    Lenient behaviour: always returns 200 and always clears both cookies,
    even if the session was already expired or the cookie was absent.
    This prevents a race-condition lockout when the session expires between
    the dashboard page loading and the user clicking "Log out".

    CSRF validation is intentionally omitted here (Issue #33 design decision):
    the worst outcome of a CSRF logout attack is a forced logout (DoS), not
    a data breach. SameSite=strict already mitigates this for modern browsers.
    Omitting it also avoids the post-upgrade edge case where a pre-#33 session
    exists without a csrf_token cookie.
    """
    if session_id is not None:
        await delete_session(db, session_id)
        logger.info("Session deleted on logout")
    else:
        logger.debug("Logout called with no session cookie present")

    clear_session_cookie(response)
    clear_csrf_cookie(response)
    return {"status": "ok"}
