"""
backend/auth/cookies.py
-----------------------
Shared session cookie helpers for Conduit Control Center.

This module is the single source of truth for session cookie attributes.
All code that sets or clears the session cookie imports from here to prevent
attribute drift across call sites.

Consumers
---------
  backend/api/auth.py      -- POST /api/auth/login (set), POST /api/auth/logout (clear)
  backend/pages.py         -- POST /login HTML form handler (set)
  backend/api/settings.py  -- PUT /api/settings/password (clear)

Previously auth.py and pages.py each had private copies of these helpers
with a documented TODO to extract them here. That TODO is closed by
Issue #31.

Public API
----------
  COOKIE_NAME              -- cookie name string constant ("session_id")
  set_session_cookie(response, session_id)
  clear_session_cookie(response)
"""

from __future__ import annotations

from fastapi import Response

from backend.config import get_app_config, get_settings

# ---------------------------------------------------------------------------
# Cookie attribute constants
# ---------------------------------------------------------------------------

COOKIE_NAME     = "session_id"
_COOKIE_PATH    = "/"
_COOKIE_SAMESITE = "strict"


# ---------------------------------------------------------------------------
# Cookie helpers
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
