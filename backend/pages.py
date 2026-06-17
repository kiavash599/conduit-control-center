"""
backend/pages.py
----------------
HTML page routes served by Jinja2 templates.

These routes are intentionally separate from backend/api/, which contains
only JSON API endpoints.  HTML page routes belong here; API routes belong
in backend/api/.

Routes (Issue #25)
------------------
  GET  /login  -- render the login page
  POST /login  -- handle plain HTML form submission (progressive enhancement)

Routes (Issue #26)
------------------
  GET  /dashboard  -- dashboard shell; requires authentication via require_auth_html
              Template variables: version, username, session_timeout (minutes)

POST /login authentication path
---------------------------------
Calls authenticate_user() from backend/auth/login.py -- the same function
used by POST /api/auth/login.  No authentication logic is duplicated here;
this handler is a transport-layer adapter that reads form-encoded data and
converts errors into template context rather than JSON responses.

On success: HTTP 303 See Other redirect to the validated `next` parameter
            or /dashboard if next is absent or unsafe.
On failure: re-render login.html with an error message.  Username is
            preserved in the template context; the password value is never
            round-tripped through the server.

next redirect safety
---------------------
_is_safe_next() applies five rules that mirror the client-side validation
in login.html and the backend validation from Issue #16.  Both sides must
use the same rules to prevent inconsistent open-redirect behaviour.

Cookie settings
---------------
Cookie helpers are imported from backend/auth/cookies.py
(Issue #31 closed this long-standing TODO).
Both session and CSRF cookies are set on successful login (Issue #33).
"""

from __future__ import annotations

import logging
import math
import secrets
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from backend._version import APP_VERSION
from backend.auth.login import (
    AccountLocked,
    AuthConfigError,
    InvalidCredentials,
    authenticate_user,
)
from backend.auth.cookies import read_theme, set_session_cookie, set_csrf_cookie
from backend.auth.sessions import create_session
from backend.config import get_app_config, get_settings
from backend.dependencies import AuthenticatedUser, get_db, require_auth_html

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"], include_in_schema=False)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------
# set_session_cookie() and set_csrf_cookie() are imported from
# backend.auth.cookies.  (Closes the TODO that previously appeared here
# and in api/auth.py for session cookie; CSRF cookie added by Issue #33.)


# ---------------------------------------------------------------------------
# next redirect safety
# ---------------------------------------------------------------------------

def _is_safe_next(url: str | None) -> bool:
    """
    Return True if url is a safe relative path for post-login redirect.

    Five-rule validation -- must match isSafeNext() in login.html and the
    backend validation applied by Issue #16:

      1. Must be a non-empty string that starts with /
      2. Must not start with //   (protocol-relative open redirect)
      3. Must not contain ://    (URI scheme, e.g. javascript:, https:)
      4. Must not contain @      (user@host open redirect)
      5. Must not contain \\     (backslash -- Windows browser open redirect)
    """
    if not url or not isinstance(url, str):
        return False
    if not url.startswith('/'):
        return False
    if url.startswith('//'):
        return False
    if '://' in url:
        return False
    if '@' in url:
        return False
    if '\\' in url:
        return False
    return True


def _safe_next(url: str | None, default: str = "/dashboard") -> str:
    """Return url if it passes _is_safe_next(), otherwise default."""
    return url if _is_safe_next(url) else default


# ---------------------------------------------------------------------------
# Template helper
# ---------------------------------------------------------------------------

def _templates(request: Request):
    """
    Return the Jinja2Templates instance from app state.

    Raises HTTP 500 if the templates directory was not found on startup
    (main.py sets app.state.templates to None in that case).
    """
    t = request.app.state.templates
    if t is None:
        logger.error(
            "Jinja2Templates not configured -- "
            "frontend/templates/ directory missing"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: templates not available.",
        )
    return t


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse, summary="Login page")
async def login_page(
    request: Request,
    next: str | None = Query(default=None),
) -> HTMLResponse:
    """
    Render the login page.

    The ``next`` query parameter is read here and embedded in the form
    action so it is preserved through both the JS-enhanced path and the
    plain HTML form (POST /login) path.
    """
    redirect_to = _safe_next(next)
    # Only include next in the template when it differs from the default,
    # to keep the form action URL clean for the common case.
    next_for_tpl = redirect_to if redirect_to != "/dashboard" else ""

    return _templates(request).TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next":     next_for_tpl,
            "username": "",
            "error":    "",
            "theme":    read_theme(request),
        },
    )


# ---------------------------------------------------------------------------
# POST /login  (progressive enhancement -- plain HTML form submission)
# ---------------------------------------------------------------------------

@router.post("/login", response_class=HTMLResponse, response_model=None, summary="Login form handler")
async def login_form(
    request: Request,
    username: str = Form(default="", max_length=64),
    password: str = Form(default="", max_length=1024),
    next: str | None = Query(default=None),
    db: aiosqlite.Connection = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """
    Handle a plain HTML form POST to /login.

    This is the progressive-enhancement fallback path.  When JavaScript is
    available, the form submit is intercepted and POSTed to
    /api/auth/login instead; this handler is never reached in that case.

    Authentication calls authenticate_user() from backend/auth/login.py --
    the same function and the same lockout/bcrypt/audit logic as the JSON
    API path.  Nothing is duplicated.

    Input constraints (Issue #35)
    ------------------------------
    username and password carry max_length to match the limits on LoginRequest
    used by POST /api/auth/login (username: 64, password: 1024).  This prevents
    bcrypt from receiving an oversized input via the form path and guards against
    large request-body allocations.

    On success : HTTP 303 redirect to the validated next param or /dashboard.
                 Both session and CSRF cookies are set so subsequent
                 apiFetch calls can include the X-CSRF-Token header.
    On failure : re-render login.html with an error message.
                 Username is preserved; password is never included.
    """
    tmpl = _templates(request)
    redirect_to = _safe_next(next)
    next_for_tpl = redirect_to if redirect_to != "/dashboard" else ""

    def render_error(msg: str) -> HTMLResponse:
        """Re-render the login form with an inline error.  Password never included."""
        return tmpl.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next":     next_for_tpl,
                "username": username,   # preserved
                "error":    msg,
                "theme":    read_theme(request),
                # password is intentionally absent -- never round-trips
            },
            status_code=200,
        )

    if not username.strip() or not password:
        return render_error("Username and password are required.")

    try:
        await authenticate_user(db, username, password)

    except AuthConfigError:
        logger.error(
            "POST /login: server misconfiguration (ADMIN_PASSWORD_HASH not set)"
        )
        return render_error(
            "Server configuration error. Contact your administrator."
        )

    except AccountLocked as exc:
        delta_s = (exc.locked_until - datetime.now(timezone.utc)).total_seconds()
        seconds = max(1, math.ceil(delta_s))
        if seconds < 120:
            msg = f"Account locked. Try again in {seconds} seconds."
        else:
            minutes = math.ceil(seconds / 60)
            msg = f"Account locked. Try again in {minutes} minutes."
        return render_error(msg)

    except InvalidCredentials:
        return render_error("Invalid credentials. Please try again.")

    # Authenticated -- create session, set cookies, redirect.
    # Both the session cookie and the CSRF token cookie must be set here
    # so that subsequent API calls (apiFetch) can read the csrf_token cookie
    # and include it as the X-CSRF-Token header (Issue #33).
    admin_username = get_settings().admin_username
    session_id     = await create_session(db, admin_username)
    csrf_token     = secrets.token_hex(32)

    response = RedirectResponse(url=redirect_to, status_code=303)
    set_session_cookie(response, session_id)
    set_csrf_cookie(response, csrf_token)
    logger.info("Session created for user %r via HTML form login", admin_username)
    return response


# ---------------------------------------------------------------------------
# GET /dashboard  (Issue #26)
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_class=HTMLResponse, summary="Dashboard shell")
async def dashboard(
    request: Request,
    user: AuthenticatedUser = Depends(require_auth_html),
) -> HTMLResponse:
    """
    Render the dashboard shell.

    Requires authentication.  require_auth_html raises AuthRedirect (converted
    to HTTP 302 by main.py) when the session is missing or expired, sending
    the browser to /login?next=/dashboard.

    Passes APP_VERSION and the authenticated username to the template so the
    sidebar can display them without an additional API call.
    """
    return _templates(request).TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "version":         APP_VERSION,
            "username":        user.user_id,
            # session_timeout_minutes from config.json -- displayed read-only
            # on the Settings page (Issue #31). Edit deferred to v1.1.
            "session_timeout": get_app_config().session_timeout_minutes,
            "theme":           read_theme(request),
        },
    )
