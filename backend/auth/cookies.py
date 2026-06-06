"""
backend/auth/cookies.py
-----------------------
Shared session and CSRF cookie helpers for Conduit Control Center.

This module is the single source of truth for session and CSRF cookie
attributes.  All code that sets or clears either cookie imports from here
to prevent attribute drift across call sites.

Consumers
---------
  backend/api/auth.py      -- POST /api/auth/login (set both),
                              POST /api/auth/logout (clear both)
  backend/pages.py         -- POST /login HTML form handler (set both)
  backend/api/settings.py  -- PUT /api/settings/password (clear both)

Previously auth.py and pages.py each had private copies of the session
helpers with a documented TODO to extract them here. That TODO was closed
by Issue #31.  CSRF cookie helpers added by Issue #33.

Public API
----------
  COOKIE_NAME              -- session cookie name ("session_id")
  CSRF_COOKIE_NAME         -- CSRF cookie name ("csrf_token")
  set_session_cookie(response, session_id)
  clear_session_cookie(response)
  set_csrf_cookie(response, csrf_token)
  clear_csrf_cookie(response)
"""

from __future__ import annotations

from fastapi import Response

from backend.config import get_app_config, get_settings

# ---------------------------------------------------------------------------
# Cookie attribute constants
# ---------------------------------------------------------------------------

COOKIE_NAME      = "session_id"
CSRF_COOKIE_NAME = "csrf_token"
_COOKIE_PATH     = "/"
_COOKIE_SAMESITE = "strict"


# ---------------------------------------------------------------------------
# Session cookie helpers
# ---------------------------------------------------------------------------


def set_session_cookie(response: Response, session_id: str) -> None:
    """
    Attach the session cookie to an outgoing response.

    Attributes
    ----------
    HttpOnly  -- not readable by JavaScript (mitigates XSS cookie theft)
    Secure    -- HTTPS only; overridable via SECURE_COOKIES=false in .env
                 for local HTTP development
    SameSite  -- strict (blocks cross-site request forgery at browser level)
    Max-Age   -- seconds matching session_timeout_minutes from config.json
    Path      -- / (cookie sent on all paths under the domain)
    """
    max_age = get_app_config().session_timeout_minutes * 60
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=max_age,
        path=_COOKIE_PATH,
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite=_COOKIE_SAMESITE,
    )


def clear_session_cookie(response: Response) -> None:
    """
    Expire the session cookie on the client by setting Max-Age=0.

    All attributes (path, httponly, secure, samesite) must match
    set_session_cookie() exactly so browsers recognise this as the
    same cookie and remove it rather than creating a new one.
    """
    response.delete_cookie(
        key=COOKIE_NAME,
        path=_COOKIE_PATH,
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite=_COOKIE_SAMESITE,
    )


# ---------------------------------------------------------------------------
# CSRF cookie helpers  (Issue #33)
# ---------------------------------------------------------------------------


def set_csrf_cookie(response: Response, csrf_token: str) -> None:
    """
    Attach the CSRF token cookie to an outgoing response.

    The CSRF cookie is intentionally NOT HttpOnly so that the frontend
    JavaScript (api.js getCsrfToken()) can read it and send its value as
    the X-CSRF-Token request header.  The backend then validates that the
    header value matches the cookie value (double-submit cookie pattern).

    Attributes
    ----------
    HttpOnly  -- False (JavaScript must be able to read this cookie)
    Secure    -- same as session cookie (HTTPS only in production)
    SameSite  -- strict (consistent with session cookie)
    Max-Age   -- matches session_timeout_minutes so both cookies expire together
    Path      -- / (cookie sent on all paths under the domain)

    Security model
    --------------
    An attacker on a different origin cannot read this cookie value (blocked
    by the browser same-origin policy) and therefore cannot forge the
    X-CSRF-Token header.  The double-submit pattern is secure as long as
    HTTPS is enforced (the Secure flag ensures this in production).
    """
    max_age = get_app_config().session_timeout_minutes * 60
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        path=_COOKIE_PATH,
        httponly=False,
        secure=get_settings().secure_cookies,
        samesite=_COOKIE_SAMESITE,
    )


def clear_csrf_cookie(response: Response) -> None:
    """
    Expire the CSRF cookie on the client by setting Max-Age=0.

    All attributes (path, httponly, secure, samesite) must match
    set_csrf_cookie() exactly so browsers recognise this as the
    same cookie and remove it rather than creating a new one.
    """
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path=_COOKIE_PATH,
        httponly=False,
        secure=get_settings().secure_cookies,
        samesite=_COOKIE_SAMESITE,
    )
