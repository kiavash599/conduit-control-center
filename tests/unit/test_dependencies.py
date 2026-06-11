# SPDX-License-Identifier: MIT
"""
Unit tests for backend/dependencies.py

Coverage targets:
  - AuthenticatedUser dataclass
  - AuthRedirect exception
  - _is_safe_next()         — all validation paths
  - get_current_user()      — no cookie / bad session / valid session
  - require_auth_html()     — valid session / no session + safe path / unsafe path
  - require_csrf_token()    — no header / no cookie / mismatch / match
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.dependencies import (
    AuthRedirect,
    AuthenticatedUser,
    _is_safe_next,
    get_current_user,
    require_auth_html,
    require_csrf_token,
)


# ---------------------------------------------------------------------------
# AuthenticatedUser
# ---------------------------------------------------------------------------


class TestAuthenticatedUser:
    def test_stores_user_id(self):
        user = AuthenticatedUser(user_id="admin")
        assert user.user_id == "admin"

    def test_frozen_raises_on_mutation(self):
        user = AuthenticatedUser(user_id="admin")
        with pytest.raises(Exception):
            user.user_id = "other"  # type: ignore[misc]

    def test_equality(self):
        assert AuthenticatedUser(user_id="admin") == AuthenticatedUser(user_id="admin")


# ---------------------------------------------------------------------------
# AuthRedirect
# ---------------------------------------------------------------------------


class TestAuthRedirect:
    def test_carries_redirect_url(self):
        exc = AuthRedirect("/login?next=/dashboard")
        assert exc.redirect_url == "/login?next=/dashboard"

    def test_is_exception(self):
        assert isinstance(AuthRedirect("/login"), Exception)


# ---------------------------------------------------------------------------
# _is_safe_next()
# ---------------------------------------------------------------------------


class TestIsSafeNext:
    def test_empty_string_is_unsafe(self):
        assert _is_safe_next("") is False

    def test_absolute_url_is_unsafe(self):
        assert _is_safe_next("https://evil.com") is False

    def test_protocol_relative_is_unsafe(self):
        assert _is_safe_next("//evil.com") is False

    def test_backslash_start_is_unsafe(self):
        assert _is_safe_next("\\evil.com") is False

    def test_contains_scheme_is_unsafe(self):
        assert _is_safe_next("/path?url=http://evil.com") is False

    def test_contains_at_sign_is_unsafe(self):
        assert _is_safe_next("/path@evil.com") is False

    def test_simple_path_is_safe(self):
        assert _is_safe_next("/dashboard") is True

    def test_nested_path_is_safe(self):
        assert _is_safe_next("/api/conduit/status") is True

    def test_root_path_is_safe(self):
        assert _is_safe_next("/") is True

    def test_path_with_query_string_is_safe(self):
        assert _is_safe_next("/search?q=conduit") is True


# ---------------------------------------------------------------------------
# Helpers for calling dependency functions directly
# ---------------------------------------------------------------------------


def _mock_request(path: str = "/dashboard", headers: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.method = "GET"
    req.url.path = path
    _headers = headers or {}
    req.headers.get = lambda key, default=None: _headers.get(key, default)
    return req


# ---------------------------------------------------------------------------
# get_current_user()
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    async def test_no_cookie_raises_401(self, db):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(session_id=None, db=db)
        assert exc_info.value.status_code == 401

    async def test_unknown_session_raises_401(self, db):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(session_id="a" * 64, db=db)
        assert exc_info.value.status_code == 401

    async def test_valid_session_returns_user(self, db):
        from backend.auth.sessions import create_session
        # get_app_config is used inside backend.auth.sessions, not backend.dependencies
        with patch(
            "backend.auth.sessions.get_app_config",
            return_value=SimpleNamespace(session_timeout_minutes=60),
        ):
            sid = await create_session(db, "admin")
            user = await get_current_user(session_id=sid, db=db)
        assert user.user_id == "admin"

    async def test_valid_session_touches_session(self, db):
        from backend.auth.sessions import create_session
        with patch(
            "backend.auth.sessions.get_app_config",
            return_value=SimpleNamespace(session_timeout_minutes=60),
        ):
            sid = await create_session(db, "admin")
            with patch("backend.dependencies.touch_session", new_callable=AsyncMock) as mock_touch:
                await get_current_user(session_id=sid, db=db)
        mock_touch.assert_called_once_with(db, sid)


# ---------------------------------------------------------------------------
# require_auth_html()
# ---------------------------------------------------------------------------


class TestRequireAuthHtml:
    async def test_valid_session_returns_user(self, db):
        from backend.auth.sessions import create_session
        with patch(
            "backend.auth.sessions.get_app_config",
            return_value=SimpleNamespace(session_timeout_minutes=60),
        ):
            sid = await create_session(db, "admin")
            req = _mock_request("/dashboard")
            user = await require_auth_html(request=req, session_id=sid, db=db)
        assert user.user_id == "admin"

    async def test_no_session_safe_path_redirects_with_next(self, db):
        req = _mock_request("/dashboard")
        with pytest.raises(AuthRedirect) as exc_info:
            await require_auth_html(request=req, session_id=None, db=db)
        assert "/dashboard" in exc_info.value.redirect_url

    async def test_no_session_unsafe_path_redirects_to_login(self, db):
        req = _mock_request("//evil.com")
        with pytest.raises(AuthRedirect) as exc_info:
            await require_auth_html(request=req, session_id=None, db=db)
        assert exc_info.value.redirect_url == "/login"

    async def test_unknown_session_raises_auth_redirect(self, db):
        req = _mock_request("/dashboard")
        with pytest.raises(AuthRedirect):
            await require_auth_html(request=req, session_id="z" * 64, db=db)


# ---------------------------------------------------------------------------
# require_csrf_token()
# ---------------------------------------------------------------------------


class TestRequireCsrfToken:
    async def test_missing_header_raises_403(self):
        req = _mock_request(headers={})
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf_token(request=req, csrf_cookie="mytoken")
        assert exc_info.value.status_code == 403

    async def test_missing_cookie_raises_403(self):
        req = _mock_request(headers={"X-CSRF-Token": "mytoken"})
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf_token(request=req, csrf_cookie=None)
        assert exc_info.value.status_code == 403

    async def test_mismatch_raises_403(self):
        req = _mock_request(headers={"X-CSRF-Token": "token-A"})
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf_token(request=req, csrf_cookie="token-B")
        assert exc_info.value.status_code == 403

    async def test_matching_tokens_returns_none(self):
        req = _mock_request(headers={"X-CSRF-Token": "valid-token"})
        result = await require_csrf_token(request=req, csrf_cookie="valid-token")
        assert result is None

    async def test_both_missing_raises_403(self):
        req = _mock_request(headers={})
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf_token(request=req, csrf_cookie=None)
        assert exc_info.value.status_code == 403
