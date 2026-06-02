"""
backend/api/auth.py
-------------------
Authentication route handlers.

Routes
------
    POST /api/auth/login   -- verify credentials, create session, set cookie
    POST /api/auth/logout  -- delete session, clear cookie (lenient)

Security notes
--------------
- All failure paths return HTTP 401 with the same generic message
  "Invalid credentials". The client cannot determine which field was wrong.
- A missing ADMIN_PASSWORD_HASH returns HTTP 503 "Service temporarily
  unavailable" -- no configuration details are exposed to the client.
- Session cookie is set with HttpOnly, Secure (configurable), SameSite=strict.
- Logout is lenient: the session cookie is always cleared and HTTP 200 is
  always returned, even if the session was already expired or not found.
  This prevents a race-condition lockout when the session expires between
  page load and the user clicking "Log out".

TODOs
-----
- Issue #15: call check_lockout() inside authenticate_user() before bcrypt.
  No change required in this file; the call site is in backend/auth/login.py.
- Issue #16: implement session validation middleware / get_current_user.
  Protected routes currently return 501 (stub). This file is not affected.
- Issue #33: add CSRF token generation on login and validation on all
  state-changing endpoints. When implemented, login should set a second
  non-HttpOnly "csrf_token" cookie alongside the session cookie.
"""

from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from backend.auth.login import AuthConfigError, InvalidCredentials, authenticate_user
from backend.auth.sessions import create_session, delete_session
from backend.config import get_app_config, get_settings
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

_COOKIE_NAME = "session_id"
_COOKIE_PATH = "/"
_COOKIE_SAMESITE = "strict"


def _set_session_cookie(response: Response, session_id: str) -> None:
    """
    Attach the session cookie to an outgoing response.

    Attributes
    ----------
    HttpOnly  -- not readable by JavaScript (mitigates XSS cookie theft)
    Secure    -- HTTPS only in production; overridable via SECURE_COOKIES=false
    SameSite  -- strict (blocks cross-site request forgery at the browser level)
    Max-Age   -- matches the server-side session_timeout_minutes
    Path      -- / (cookie sent on all paths)
    """
    max_age = get_app_config().session_timeout_minutes * 60
    response.set_cookie(
        key=_COOKIE_NAME,
        value=session_id,
        max_age=max_age,
        path=_COOKIE_PATH,
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite=_COOKIE_SAMESITE,
    )


def _clear_session_cookie(response: Response) -> None:
    """
    Expire the session cookie on the client by setting Max-Age=0.

    Must use the same path, httponly, secure, and samesite attributes as
    _set_session_cookie() so browsers recognise it as the same cookie.
    """
    response.delete_cookie(
        key=_COOKIE_NAME,
        path=_COOKIE_PATH,
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite=_COOKIE_SAMESITE,
    )


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    summary="Log in with username and password",
    responses={
        200: {"description": "Login successful; session cookie set"},
        401: {"description": "Invalid credentials"},
        422: {"description": "Request body validation failed"},
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

    On success, sets a session cookie and returns {"status": "ok"}.
    On failure, returns 401 with a generic message (no field hint).

    The cookie is set on the injected Response object; FastAPI merges its
    headers into the final response alongside the returned dict body.

    TODO (Issue #33): after creating the session, also set a non-HttpOnly
    CSRF token cookie so the frontend can read and send it as X-CSRF-Token.
    """
    try:
        await authenticate_user(body.username, body.password)
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
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    session_id = await create_session(db, "admin")
    _set_session_cookie(response, session_id)

    logger.info("Session created for user 'admin'")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    summary="Invalidate current session and clear cookie",
    responses={
        200: {"description": "Logged out; session cookie cleared"},
    },
)
async def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """
    Delete the server-side session and clear the cookie.

    Lenient behaviour: always returns 200 and always clears the cookie,
    even if the session was already expired or the cookie was absent.
    This prevents a race-condition lockout when the session expires between
    the dashboard page loading and the user clicking "Log out".

    TODO (Issue #16): once get_current_user is implemented, decide whether
    logout should optionally require a valid session. Current consensus is
    to keep it lenient (see design notes in the module docstring).

    TODO (Issue #33): when CSRF is added, also clear the csrf_token cookie
    here to keep the two cookies in sync.
    """
    if session_id is not None:
        await delete_session(db, session_id)
        logger.info("Session deleted on logout")
    else:
        logger.debug("Logout called with no session cookie present")

    _clear_session_cookie(response)
    return {"status": "ok"}
