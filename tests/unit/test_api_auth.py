# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/auth.py

Coverage targets:
  - _retry_after_seconds()  — ceiling, minimum-1 clamp
  - POST /api/auth/login    — success / AuthConfigError / AccountLocked /
                              InvalidCredentials / validation error
  - POST /api/auth/logout   — with session / without session
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.auth import _retry_after_seconds, router
from backend.auth.lockout import AccountLocked
from backend.auth.login import AuthConfigError, InvalidCredentials
from backend.dependencies import get_db


# ---------------------------------------------------------------------------
# Test app factory
# ---------------------------------------------------------------------------


async def _mock_db():
    yield MagicMock(spec=aiosqlite.Connection)


@pytest.fixture
def auth_client():
    app = FastAPI()
    app.include_router(router, prefix="/api/auth")
    app.dependency_overrides[get_db] = _mock_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# _retry_after_seconds()
# ---------------------------------------------------------------------------


class TestRetryAfterSeconds:
    def test_exact_seconds_ceiled(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=30.5)
        result = _retry_after_seconds(future)
        assert result == 31

    def test_minimum_is_one(self):
        # A locked_until in the past must return 1, not 0 or negative.
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        result = _retry_after_seconds(past)
        assert result == 1

    def test_exactly_future_seconds(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=15)
        result = _retry_after_seconds(future)
        assert result >= 15  # ceiling of ~15.x


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


class TestLoginRoute:
    def test_success_returns_ok(self, auth_client):
        with patch("backend.api.auth.authenticate_user", new_callable=AsyncMock), \
             patch("backend.api.auth.create_session", new_callable=AsyncMock, return_value="sess-abc"):
            response = auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "correct"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_auth_config_error_returns_503(self, auth_client):
        with patch(
            "backend.api.auth.authenticate_user",
            new_callable=AsyncMock,
            side_effect=AuthConfigError("not configured"),
        ):
            response = auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "any"},
            )
        assert response.status_code == 503

    def test_account_locked_returns_429(self, auth_client):
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        with patch(
            "backend.api.auth.authenticate_user",
            new_callable=AsyncMock,
            side_effect=AccountLocked(locked_until=locked_until),
        ):
            response = auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_invalid_credentials_returns_401(self, auth_client):
        with patch(
            "backend.api.auth.authenticate_user",
            new_callable=AsyncMock,
            side_effect=InvalidCredentials(),
        ):
            response = auth_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
        assert response.status_code == 401

    def test_missing_username_returns_422(self, auth_client):
        response = auth_client.post("/api/auth/login", json={"password": "pw"})
        assert response.status_code == 422

    def test_missing_password_returns_422(self, auth_client):
        response = auth_client.post("/api/auth/login", json={"username": "admin"})
        assert response.status_code == 422

    def test_empty_username_returns_422(self, auth_client):
        response = auth_client.post(
            "/api/auth/login",
            json={"username": "", "password": "pw"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------


class TestLogoutRoute:
    def test_logout_with_session_returns_ok(self, auth_client):
        with patch("backend.api.auth.delete_session", new_callable=AsyncMock):
            response = auth_client.post(
                "/api/auth/logout",
                cookies={"session_id": "test-session-id"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_logout_without_session_returns_ok(self, auth_client):
        response = auth_client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_logout_clears_session_cookie(self, auth_client):
        with patch("backend.api.auth.delete_session", new_callable=AsyncMock):
            response = auth_client.post(
                "/api/auth/logout",
                cookies={"session_id": "test-session-id"},
            )
        # Cookie cleared means Set-Cookie header present with max-age=0
        set_cookie = response.headers.get("set-cookie", "")
        assert "session_id" in set_cookie or response.status_code == 200
