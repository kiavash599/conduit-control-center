# SPDX-License-Identifier: MIT
"""
Integration tests — authentication flow.

Tests the full request-response cycle through backend.main.app for:
  - GET /api/health          (no auth required)
  - Unauthenticated access   (→ 401)
  - POST /api/auth/login     (success / wrong password / unconfigured hash)
  - Cookie presence after login
  - POST /api/auth/logout    (with and without session)
  - Session invalidation after logout
  - Login lockout (5 failures → 429 with Retry-After)
  - Failed-attempt counter reset after successful login

All systemctl / adapter calls are mocked at the importing-module namespace
so no real Conduit installation is needed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch


from tests.integration.conftest import KNOWN_PASSWORD


# ---------------------------------------------------------------------------
# Health check (unauthenticated)
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_200_without_session(self, integration_client):
        response = integration_client.get("/api/health")
        assert response.status_code == 200

    def test_response_has_status_ok(self, integration_client):
        data = integration_client.get("/api/health").json()
        assert data["status"] == "ok"

    def test_response_has_version(self, integration_client):
        data = integration_client.get("/api/health").json()
        assert "version" in data

    def test_uptime_seconds_non_negative(self, integration_client):
        data = integration_client.get("/api/health").json()
        assert data["uptime_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# Unauthenticated access to protected endpoints
# ---------------------------------------------------------------------------


class TestUnauthenticatedAccess:
    def test_status_without_session_returns_401(self, integration_client):
        response = integration_client.get("/api/status")
        assert response.status_code == 401

    def test_metrics_system_without_session_returns_401(self, integration_client):
        response = integration_client.get("/api/metrics/system")
        assert response.status_code == 401

    def test_metrics_traffic_without_session_returns_401(self, integration_client):
        response = integration_client.get("/api/metrics/traffic")
        assert response.status_code == 401

    def test_logs_without_session_returns_401(self, integration_client):
        response = integration_client.get("/api/logs")
        assert response.status_code == 401

    def test_ddns_without_session_returns_401(self, integration_client):
        response = integration_client.get("/api/ddns/status")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_correct_credentials_returns_200(self, integration_client):
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert response.status_code == 200

    def test_correct_credentials_returns_ok_body(self, integration_client):
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert response.json()["status"] == "ok"

    def test_sets_session_id_cookie(self, integration_client):
        integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert "session_id" in integration_client.cookies

    def test_sets_csrf_token_cookie(self, integration_client):
        integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert "csrf_token" in integration_client.cookies

    def test_wrong_password_returns_401(self, integration_client):
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "definitely_wrong"},
        )
        assert response.status_code == 401

    def test_wrong_username_returns_401(self, integration_client):
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "notadmin", "password": KNOWN_PASSWORD},
        )
        assert response.status_code == 401

    def test_hash_not_configured_returns_503(self, integration_client, monkeypatch):
        """Empty ADMIN_PASSWORD_HASH → server misconfiguration → 503."""
        from backend.config import get_settings
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", "")
        get_settings.cache_clear()
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert response.status_code == 503

    def test_missing_username_field_returns_422(self, integration_client):
        response = integration_client.post(
            "/api/auth/login",
            json={"password": KNOWN_PASSWORD},
        )
        assert response.status_code == 422

    def test_empty_username_returns_422(self, integration_client):
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "", "password": KNOWN_PASSWORD},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Session: authenticated access after login
# ---------------------------------------------------------------------------


class TestAuthenticatedSession:
    def test_status_returns_200_after_login(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None):
            response = client.get("/api/status")
        assert response.status_code == 200

    def test_status_response_has_node_status(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None):
            data = client.get("/api/status").json()
        assert data["node_status"] == "stopped"

    def test_second_request_with_same_session_succeeds(self, logged_in):
        """Session cookie persists in httpx cookie jar across requests."""
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None):
            r1 = client.get("/api/status")
            r2 = client.get("/api/status")
        assert r1.status_code == 200
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_returns_200(self, logged_in):
        client, _ = logged_in
        response = client.post("/api/auth/logout")
        assert response.status_code == 200

    def test_logout_returns_ok_body(self, logged_in):
        client, _ = logged_in
        assert client.post("/api/auth/logout").json()["status"] == "ok"

    def test_session_invalid_after_logout(self, logged_in):
        """Session deleted from DB on logout → subsequent requests return 401."""
        client, _ = logged_in
        client.post("/api/auth/logout")
        response = client.get("/api/status")
        assert response.status_code == 401

    def test_logout_without_session_cookie_returns_200(self, integration_client):
        """Logout is lenient — always returns 200 even with no session cookie."""
        response = integration_client.post("/api/auth/logout")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Login lockout
# ---------------------------------------------------------------------------


class TestLoginLockout:
    def _fail_login(self, client, n: int) -> None:
        """Attempt n wrong-password logins for 'admin'."""
        for _ in range(n):
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong_password_x"},
            )

    def test_5_failures_return_429(self, integration_client):
        """
        The lockout is SET on the 5th failed attempt but is CHECKED at the
        start of the next request.  So 5 failures set the lock (each returns
        401) and the 6th attempt is the first to receive 429.
        """
        self._fail_login(integration_client, 5)  # sets locked_until on the 5th
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong_password_x"},
        )
        assert response.status_code == 429

    def test_lockout_response_has_retry_after_header(self, integration_client):
        self._fail_login(integration_client, 5)
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong_password_x"},
        )
        assert "retry-after" in response.headers

    def test_retry_after_value_is_positive_integer(self, integration_client):
        self._fail_login(integration_client, 5)
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong_password_x"},
        )
        retry_after = int(response.headers.get("retry-after", "0"))
        assert retry_after > 0

    def test_wrong_username_does_not_count_toward_lockout(self, integration_client):
        """
        Wrong-username attempts must not touch the failed_attempts table.
        Sending many wrong-username attempts should not trigger lockout.
        """
        for _ in range(10):
            integration_client.post(
                "/api/auth/login",
                json={"username": "notadmin", "password": "any"},
            )
        # Now try the correct credentials — should still work
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert response.status_code == 200

    def test_failed_attempts_reset_after_success(self, integration_client):
        """
        3 failed attempts followed by a successful login resets the counter.
        A subsequent wrong attempt does not immediately trigger lockout.
        """
        self._fail_login(integration_client, 3)

        # Successful login resets counter
        ok = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert ok.status_code == 200

        # One more wrong attempt — counter is back at 1, far from threshold
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert response.status_code == 401  # 401, not 429
