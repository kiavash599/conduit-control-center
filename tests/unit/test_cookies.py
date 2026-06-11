# SPDX-License-Identifier: MIT
"""
Unit tests for backend/auth/cookies.py

Coverage targets:
  - COOKIE_NAME / CSRF_COOKIE_NAME constants
  - set_session_cookie()    — calls response.set_cookie with correct attributes
  - clear_session_cookie()  — calls response.delete_cookie
  - set_csrf_cookie()       — non-HttpOnly, correct max_age
  - clear_csrf_cookie()     — calls response.delete_cookie
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.auth.cookies import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    clear_csrf_cookie,
    clear_session_cookie,
    set_csrf_cookie,
    set_session_cookie,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_config_and_settings(monkeypatch):
    cfg = SimpleNamespace(session_timeout_minutes=60)
    settings = SimpleNamespace(secure_cookies=True)
    monkeypatch.setattr("backend.auth.cookies.get_app_config", lambda: cfg)
    monkeypatch.setattr("backend.auth.cookies.get_settings", lambda: settings)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestCookieNameConstants:
    def test_cookie_name_is_session_id(self):
        assert COOKIE_NAME == "session_id"

    def test_csrf_cookie_name_is_csrf_token(self):
        assert CSRF_COOKIE_NAME == "csrf_token"


# ---------------------------------------------------------------------------
# set_session_cookie()
# ---------------------------------------------------------------------------


class TestSetSessionCookie:
    def test_calls_set_cookie_on_response(self):
        mock_response = MagicMock()
        set_session_cookie(mock_response, "test-session-id")
        mock_response.set_cookie.assert_called_once()

    def test_session_id_value_passed(self):
        mock_response = MagicMock()
        set_session_cookie(mock_response, "my-session")
        kwargs = mock_response.set_cookie.call_args.kwargs
        assert kwargs["value"] == "my-session"

    def test_httponly_is_true(self):
        mock_response = MagicMock()
        set_session_cookie(mock_response, "s")
        kwargs = mock_response.set_cookie.call_args.kwargs
        assert kwargs["httponly"] is True

    def test_max_age_is_3600(self):
        mock_response = MagicMock()
        set_session_cookie(mock_response, "s")
        kwargs = mock_response.set_cookie.call_args.kwargs
        assert kwargs["max_age"] == 60 * 60  # 60 min * 60 sec

    def test_samesite_is_strict(self):
        mock_response = MagicMock()
        set_session_cookie(mock_response, "s")
        kwargs = mock_response.set_cookie.call_args.kwargs
        assert kwargs["samesite"] == "strict"


# ---------------------------------------------------------------------------
# clear_session_cookie()
# ---------------------------------------------------------------------------


class TestClearSessionCookie:
    def test_calls_delete_cookie(self):
        mock_response = MagicMock()
        clear_session_cookie(mock_response)
        mock_response.delete_cookie.assert_called_once()

    def test_correct_cookie_name(self):
        mock_response = MagicMock()
        clear_session_cookie(mock_response)
        kwargs = mock_response.delete_cookie.call_args.kwargs
        assert kwargs["key"] == COOKIE_NAME


# ---------------------------------------------------------------------------
# set_csrf_cookie()
# ---------------------------------------------------------------------------


class TestSetCsrfCookie:
    def test_calls_set_cookie_on_response(self):
        mock_response = MagicMock()
        set_csrf_cookie(mock_response, "csrf-token-value")
        mock_response.set_cookie.assert_called_once()

    def test_httponly_is_false(self):
        """CSRF cookie must be readable by JavaScript."""
        mock_response = MagicMock()
        set_csrf_cookie(mock_response, "token")
        kwargs = mock_response.set_cookie.call_args.kwargs
        assert kwargs["httponly"] is False

    def test_csrf_token_value_passed(self):
        mock_response = MagicMock()
        set_csrf_cookie(mock_response, "my-csrf-token")
        kwargs = mock_response.set_cookie.call_args.kwargs
        assert kwargs["value"] == "my-csrf-token"

    def test_correct_cookie_name(self):
        mock_response = MagicMock()
        set_csrf_cookie(mock_response, "token")
        kwargs = mock_response.set_cookie.call_args.kwargs
        assert kwargs["key"] == CSRF_COOKIE_NAME


# ---------------------------------------------------------------------------
# clear_csrf_cookie()
# ---------------------------------------------------------------------------


class TestClearCsrfCookie:
    def test_calls_delete_cookie(self):
        mock_response = MagicMock()
        clear_csrf_cookie(mock_response)
        mock_response.delete_cookie.assert_called_once()

    def test_correct_cookie_name(self):
        mock_response = MagicMock()
        clear_csrf_cookie(mock_response)
        kwargs = mock_response.delete_cookie.call_args.kwargs
        assert kwargs["key"] == CSRF_COOKIE_NAME
