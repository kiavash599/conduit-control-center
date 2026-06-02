"""
backend/dependencies.py
-----------------------
Reusable FastAPI dependencies shared across route modules.

Dependencies
------------
get_db              -- yields an aiosqlite connection for the duration of a request
get_current_user    -- validates session cookie; returns AuthenticatedUser or raises 401
                       also calls touch_session to extend the sliding session window
require_auth_html   -- like get_current_user but raises AuthRedirect instead of 401;
                       main.py converts AuthRedirect to a 302 redirect to /login?next=<path>

Exceptions
----------
AuthRedirect        -- raised by require_auth_html when the session is missing or expired;
                       carries the redirect URL (e.g. "/login?next=/dashboard");
                       converted to RedirectResponse(302) by the exception handler in main.py

Data types
----------
AuthenticatedUser   -- frozen dataclass returned by both auth dependencies;
                       user_id: str always equals settings.admin_username in v0.1

Usage
-----
    from backend.dependencies import get_current_user, require_auth_html, AuthenticatedUser
    from fastapi import Depends

    # API route (returns JSON 401 if unauthenticated)
    @router.get("/api/something")
    async def api_route(
        user: AuthenticatedUser = Depends(get_current_user),
        db: aiosqlite.Connection = Depends(get_db),
    ):
        ...

    # HTML route (redirects to /login?next=<path> if unauthenticated)
    @router.get("/dashboard")
    async def dashboard(
        user: AuthenticatedUser = Depends(require_auth_html),
        request: Request = ...,
    ):
        ...

Security notes
--------------
- get_current_user calls touch_session on every valid request so the sliding
  session window (session_timeout_minutes in config.json) is correctly extended.
  Without this call, sessions would expire at their original creation time
  regardless of user activity.
- require_auth_html uses _is_safe_next() to validate the ?next= parameter
  before embedding it in the redirect URL. This prevents open-redirect attacks.
- The safe-next check rejects anything that does not start with "/", starts with
  "//" or backslash (which some browsers normalise to "//"), or contains "://"
  or "@" (common open-redirect indicators). If the path fails the check, the
  redirect falls back to "/".

TODOs
-----
- Issue #33: when CSRF is implemented, require_auth_html should also validate
  the CSRF token for state-changing HTML form submissions. Read-only GET routes
  are not affected.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import AsyncGenerator
from urllib.parse import quote

import aiosqlite
from fastapi import Cookie, Depends, HTTPException, Request, status

from backend.auth.sessions import get_session, touch_session
from backend.database import get_db as _get_db_ctx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COOKIE_NAME = "session_id"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AuthenticatedUser:
    """
    Represents an authenticated user returned by the auth dependencies.

    In v0.1 this is always the single admin account; user_id equals
    settings.admin_username. Future issues may add roles or additional fields.

    frozen=True makes this immutable and hashable so it is safe to cache
    or use as a dictionary key if needed.
    """

    user_id: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthRedirect(Exception):
    """
    Raised by require_auth_html when no valid session is present.

    Carries the redirect URL to send the browser to (e.g. "/login?next=/dashboard").
    Converted to RedirectResponse(302) by the exception handler registered in
    backend/main.py.

    This is a domain-level exception, not an HTTPException, so it bypasses
    FastAPI's default JSON error handler and can be converted to a proper
    browser redirect by the dedicated handler.
    """

    def __init__(self, redirect_url: str) -> None:
        super().__init__(redirect_url)
        self.redirect_url = redirect_url


# ---------------------------------------------------------------------------
# Database dependency
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    FastAPI dependency that opens a database connection for the request
    and closes it automatically when the request is complete.

    Usage::

        @router.get("/example")
        async def handler(db: aiosqlite.Connection = Depends(get_db)):
            ...
    """
    async with _get_db_ctx() as db:
        yield db


# ---------------------------------------------------------------------------
# Safe-next helper (private)
# ---------------------------------------------------------------------------


def _is_safe_next(value: str) -> bool:
    """
    Return True if value is a safe relative path for post-login redirect.

    A safe value:
    - is non-empty
    - starts with "/"  (relative, not protocol-relative or absolute)
    - does NOT start with "//" (protocol-relative URL, e.g. //evil.com)
    - does NOT start with "\\" (backslash -- some browsers normalise to //)
    - does NOT contain "://" (absolute URL scheme)
    - does NOT contain "@" (userinfo in URLs, e.g. //user@evil.com)

    Callers fall back to "/" when this returns False.
    """
    if not value:
        return False
    if not value.startswith("/"):
        return False
    if value.startswith("//") or value.startswith("\\"):
        return False
    if "://" in value or "@" in value:
        return False
    return True


# ---------------------------------------------------------------------------
# Authentication dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    session_id: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    db: aiosqlite.Connection = Depends(get_db),
) -> AuthenticatedUser:
    """
    FastAPI dependency for protected API routes.

    Validates the session cookie, extends the sliding session window via
    touch_session(), and returns the authenticated user.

    Parameters
    ----------
    session_id : str | None
        Value of the "session_id" cookie from the request. None if absent.
    db : aiosqlite.Connection
        Database connection from get_db.

    Returns
    -------
    AuthenticatedUser
        Frozen dataclass with user_id set to the authenticated username.

    Raises
    ------
    HTTPException(401)
        No cookie present, or the session ID is expired or unknown.
        The response body is generic JSON to avoid leaking session details.

    Notes
    -----
    touch_session() is called on every successful validation so that active
    users are never logged out mid-session. The sliding window is defined by
    session_timeout_minutes in config.json.
    """
    _UNAUTH = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )

    if session_id is None:
        raise _UNAUTH

    session = await get_session(db, session_id)
    if session is None:
        logger.debug("Session lookup returned None for id prefix %s...", session_id[:8])
        raise _UNAUTH

    await touch_session(db, session_id)

    user_id: str = session["user_id"]
    logger.debug("Authenticated request for user %r", user_id)
    return AuthenticatedUser(user_id=user_id)


async def require_auth_html(
    request: Request,
    session_id: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    db: aiosqlite.Connection = Depends(get_db),
) -> AuthenticatedUser:
    """
    FastAPI dependency for protected HTML routes.

    Behaves identically to get_current_user on success. On failure, instead
    of raising HTTP 401 JSON, raises AuthRedirect which main.py converts to
    a browser redirect to /login?next=<current_path>.

    The ?next= parameter is validated with _is_safe_next() before being
    embedded in the redirect URL. If the path is not safe (e.g. contains
    "://"), the redirect falls back to "/login" with no next parameter.

    Parameters
    ----------
    request : Request
        Current FastAPI request; used to read the current URL path for ?next=.
    session_id : str | None
        Value of the "session_id" cookie. None if absent.
    db : aiosqlite.Connection
        Database connection from get_db.

    Returns
    -------
    AuthenticatedUser
        Same as get_current_user on success.

    Raises
    ------
    AuthRedirect
        Session is missing or invalid. redirect_url is set to
        "/login?next=<url-encoded-path>" or "/login" if the path is unsafe.

    Notes
    -----
    The post-login redirect from /login back to the original page is a
    frontend JavaScript responsibility: the login page JS reads the ?next=
    query parameter from its own URL and performs window.location.href after
    a successful POST /api/auth/login response.
    This function only sets the ?next= parameter on the way TO /login.

    The /dashboard route does not yet exist (Issue #19/20). This dependency is
    ready for it; the redirect behaviour can be verified once that route is
    implemented.
    """
    if session_id is not None:
        session = await get_session(db, session_id)
        if session is not None:
            await touch_session(db, session_id)
            user_id: str = session["user_id"]
            logger.debug(
                "Authenticated HTML request for user %r path=%r",
                user_id,
                request.url.path,
            )
            return AuthenticatedUser(user_id=user_id)

    # Not authenticated -- build redirect URL
    path = request.url.path
    if _is_safe_next(path):
        redirect_url = "/login?next=" + quote(path, safe="/")
    else:
        redirect_url = "/login"

    logger.debug(
        "Unauthenticated HTML request on %r -- redirecting to %r",
        path,
        redirect_url,
    )
    raise AuthRedirect(redirect_url)
