# SPDX-License-Identifier: MIT
"""
Integration tests — CSRF double-submit cookie protection.

Verifies that the real FastAPI app correctly enforces the CSRF gate on all
state-changing endpoints.  Tests the four required combinations:

  1. Missing CSRF cookie (but valid session + header present) → 403
  2. Missing X-CSRF-Token header (but valid session + cookie present) → 403
  3. Header/cookie value mismatch → 403
  4. Matching header + cookie → passes CSRF gate (downstream response)

Also verifies that login and logout intentionally skip CSRF:
  - POST /api/auth/login has no session yet → no CSRF requirement
  - POST /api/auth/logout: CSRF intentionally omitted (documented in auth.py)

The canonical endpoint for CSRF matrix testing is PUT /api/settings/password
because it:
  - Requires both get_current_user and require_csrf_token
  - Returns 400 (wrong password) when CSRF passes but credentials are wrong
    — this distinguishes "CSRF passed" from "CSRF blocked (403)"

All tests use the logged_in fixture so the session cookie is present.
The ADMIN_PASSWORD_HASH env var (set in integration_client) ensures the
password-change endpoint can verify the current password field.
"""
from __future__ import annotations

import pytest

from tests.integration.conftest import KNOWN_PASSWORD, NEW_PASSWORD

# Endpoint used for CSRF matrix testing
_CHANGE_PW_URL = "/api/settings/password"

# A valid body for the password-change endpoint (correct shape and length)
_VALID_BODY = {
    "current_password": KNOWN_PASSWORD,
    "new_password": NEW_PASSWORD,
    "confirm_password": NEW_PASSWORD,
}

# A body with the wrong current password — passes model validation but
# returns 400 after the CSRF gate is cleared.
_WRONG_CURRENT_BODY = {
    "current_password": "deliberately_wrong_current",
    "new_password": NEW_PASSWORD,
    "confirm_password": NEW_PASSWORD,
}


# ---------------------------------------------------------------------------
# CSRF matrix on PUT /api/settings/password
# ---------------------------------------------------------------------------


class TestCsrfMatrix:
    def test_missing_csrf_header_returns_403(self, logged_in, tmp_path, monkeypatch):
        """
        Valid session cookie + csrf_token cookie present, but NO X-CSRF-Token header.
        require_csrf_token should return 403.
        """
        client, _csrf = logged_in
        monkeypatch.setattr("backend.api.settings.get_env_file_path", lambda: tmp_path / ".env")
        response = client.put(
            _CHANGE_PW_URL,
            json=_WRONG_CURRENT_BODY,
            # Deliberately omit X-CSRF-Token header
        )
        assert response.status_code == 403

    def test_missing_csrf_cookie_returns_403(self, logged_in, tmp_path, monkeypatch):
        """
        Valid session + X-CSRF-Token header present, but csrf_token cookie removed.
        require_csrf_token checks csrf_cookie parameter (from cookie) — must be 403.
        """
        client, csrf = logged_in
        monkeypatch.setattr("backend.api.settings.get_env_file_path", lambda: tmp_path / ".env")
        # Remove csrf_token from the httpx cookie jar for this test
        del client.cookies["csrf_token"]
        response = client.put(
            _CHANGE_PW_URL,
            json=_WRONG_CURRENT_BODY,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 403

    def test_csrf_header_cookie_mismatch_returns_403(self, logged_in, tmp_path, monkeypatch):
        """
        Valid session + both cookies present, but header value ≠ cookie value.
        hmac.compare_digest fails → 403.
        """
        client, _csrf = logged_in
        monkeypatch.setattr("backend.api.settings.get_env_file_path", lambda: tmp_path / ".env")
        response = client.put(
            _CHANGE_PW_URL,
            json=_WRONG_CURRENT_BODY,
            headers={"X-CSRF-Token": "wrong-token-value-that-does-not-match"},
        )
        assert response.status_code == 403

    def test_valid_csrf_passes_gate(self, logged_in, tmp_path, monkeypatch):
        """
        Valid session + matching X-CSRF-Token header + csrf_token cookie.
        CSRF gate is cleared → endpoint proceeds → 400 (wrong current password).
        A 400 (not 403) proves the request passed the CSRF check.
        """
        client, csrf = logged_in
        monkeypatch.setattr("backend.api.settings.get_env_file_path", lambda: tmp_path / ".env")
        response = client.put(
            _CHANGE_PW_URL,
            json=_WRONG_CURRENT_BODY,
            headers={"X-CSRF-Token": csrf},
        )
        # 400 = wrong current password — CSRF check passed, auth check passed,
        # password verification failed (expected)
        assert response.status_code == 400

    def test_both_csrf_missing_returns_403(self, logged_in, tmp_path, monkeypatch):
        """No X-CSRF-Token header AND csrf_token cookie deleted → 403."""
        client, csrf = logged_in
        monkeypatch.setattr("backend.api.settings.get_env_file_path", lambda: tmp_path / ".env")
        del client.cookies["csrf_token"]
        response = client.put(
            _CHANGE_PW_URL,
            json=_WRONG_CURRENT_BODY,
            # No header, no cookie
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Verify intentional CSRF skips on login and logout
# ---------------------------------------------------------------------------


class TestCsrfSkips:
    def test_login_requires_no_csrf(self, integration_client):
        """
        POST /api/auth/login intentionally skips CSRF (no session yet to read
        a CSRF cookie from).  Sending no CSRF header must succeed (200 or 401,
        not 403).
        """
        response = integration_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
            # No X-CSRF-Token header
        )
        assert response.status_code == 200

    def test_logout_requires_no_csrf(self, logged_in):
        """
        POST /api/auth/logout intentionally skips CSRF (documented in auth.py:
        worst outcome of CSRF-forced logout is a DoS, not a data breach;
        SameSite=strict mitigates on modern browsers).
        Sending no CSRF header must not return 403.
        """
        client, _ = logged_in
        # Delete CSRF cookie to make it clear the request has no CSRF credentials
        del client.cookies["csrf_token"]
        response = client.post(
            "/api/auth/logout",
            # No X-CSRF-Token header
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# CSRF protection on conduit control endpoints
# ---------------------------------------------------------------------------


class TestCsrfConduitEndpoints:
    def test_start_without_csrf_returns_403(self, logged_in):
        """POST /api/conduit/start without CSRF → 403 before any adapter call."""
        client, _ = logged_in
        response = client.post("/api/conduit/start")
        assert response.status_code == 403

    def test_stop_without_csrf_returns_403(self, logged_in):
        client, _ = logged_in
        response = client.post("/api/conduit/stop")
        assert response.status_code == 403

    def test_restart_without_csrf_returns_403(self, logged_in):
        client, _ = logged_in
        response = client.post("/api/conduit/restart")
        assert response.status_code == 403
