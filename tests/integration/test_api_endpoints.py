# SPDX-License-Identifier: MIT
"""
Integration tests — authenticated API endpoints.

Tests the full request-response cycle for every protected route, verifying
that the real FastAPI app (backend.main.app) correctly handles:
  - GET /api/status          (adapter mocked at backend.api.status)
  - GET /api/metrics/system  (psutil runs natively; no mock needed)
  - GET /api/metrics/traffic (adapter mocked at backend.api.metrics)
  - GET /api/logs            (journalctl mocked at backend.api.logs)
  - POST /api/conduit/start  (adapter mocked at backend.api.conduit)
  - POST /api/conduit/stop
  - POST /api/conduit/restart
  - GET /api/ddns/status     (501 stub)
  - PUT /api/settings/password:
      happy path + sessions-deleted verification + new-password works
      wrong current password → 400
      new ≠ confirm → 422
      .env write fails → 500

Constraint: adapters are patched at the importing-module namespace
(backend.api.*), not at backend.conduit.adapter.
Constraint: backend.api.settings.get_env_file_path is patched to
tmp_path/.env so the real .env is never touched.
"""
from __future__ import annotations

import bcrypt
from unittest.mock import AsyncMock, patch

import pytest

from tests.integration.conftest import KNOWN_PASSWORD, NEW_PASSWORD

_CSRF_HEADERS = {}  # filled by logged_in fixture; set per-test via helper


def _auth_put(client, url, csrf, **kwargs):
    """Helper: PUT with CSRF header."""
    return client.put(url, headers={"X-CSRF-Token": csrf}, **kwargs)


def _auth_post(client, url, csrf, **kwargs):
    """Helper: POST with CSRF header."""
    return client.post(url, headers={"X-CSRF-Token": csrf}, **kwargs)


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------


class TestStatusEndpoint:
    def test_returns_200(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value="2026-01-01T00:00:00Z"), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value="1.2.3"):
            response = client.get("/api/status")
        assert response.status_code == 200

    def test_response_contains_node_status(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None):
            data = client.get("/api/status").json()
        assert data["node_status"] == "stopped"

    def test_uptime_calculated_when_running(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value="2026-01-01T00:00:00Z"), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None):
            data = client.get("/api/status").json()
        assert data["uptime_seconds"] is not None
        assert data["uptime_seconds"] >= 0.0

    def test_adapter_error_returns_503(self, logged_in):
        from backend.conduit.adapter import ConduitAdapterError
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock,
                   side_effect=ConduitAdapterError("unit not found")):
            response = client.get("/api/status")
        assert response.status_code == 503

    def test_secondary_failures_degrade_to_null(self, logged_in):
        """get_last_changed and get_version failures → null fields, not 503."""
        from backend.conduit.adapter import ConduitAdapterError
        client, _ = logged_in
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock,
                   side_effect=ConduitAdapterError("show failed")), \
             patch("backend.api.status.get_version", new_callable=AsyncMock,
                   side_effect=Exception("version unavailable")):
            data = client.get("/api/status").json()
        assert data["node_status"] == "running"
        assert data["last_changed"] is None
        assert data["conduit_version"] is None


# ---------------------------------------------------------------------------
# GET /api/metrics/system
# ---------------------------------------------------------------------------


class TestSystemMetricsEndpoint:
    def test_returns_200(self, logged_in):
        client, _ = logged_in
        response = client.get("/api/metrics/system")
        assert response.status_code == 200

    def test_response_has_cpu_ram_disk(self, logged_in):
        client, _ = logged_in
        data = client.get("/api/metrics/system").json()
        assert "cpu" in data
        assert "ram" in data
        assert "disk" in data

    def test_cpu_usage_percent_is_number(self, logged_in):
        client, _ = logged_in
        data = client.get("/api/metrics/system").json()
        assert isinstance(data["cpu"]["usage_percent"], (int, float))


# ---------------------------------------------------------------------------
# GET /api/metrics/traffic
# ---------------------------------------------------------------------------


class TestTrafficMetricsEndpoint:
    def test_returns_200_when_metrics_unavailable(self, logged_in):
        """Conduit metrics endpoint unreachable → null bytes, still 200."""
        client, _ = logged_in
        with patch("backend.api.metrics.get_traffic_metrics", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.metrics.get_last_changed", new_callable=AsyncMock, return_value=None):
            response = client.get("/api/metrics/traffic")
        assert response.status_code == 200

    def test_null_bytes_when_conduit_not_running(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.metrics.get_traffic_metrics", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.metrics.get_last_changed", new_callable=AsyncMock, return_value=None):
            data = client.get("/api/metrics/traffic").json()
        assert data["bytes_sent"] is None
        assert data["bytes_received"] is None

    def test_byte_values_populated_when_metrics_available(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.metrics.get_traffic_metrics", new_callable=AsyncMock,
                   return_value={"bytes_uploaded": 1024, "bytes_downloaded": 2048}), \
             patch("backend.api.metrics.get_last_changed", new_callable=AsyncMock,
                   return_value="2026-01-01T00:00:00Z"):
            data = client.get("/api/metrics/traffic").json()
        assert data["bytes_sent"] == 1024
        assert data["bytes_received"] == 2048

    def test_response_has_timestamp(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.metrics.get_traffic_metrics", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.metrics.get_last_changed", new_callable=AsyncMock, return_value=None):
            data = client.get("/api/metrics/traffic").json()
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# GET /api/logs
# ---------------------------------------------------------------------------


class TestLogsEndpoint:
    _SAMPLE = (
        "2026-06-01T14:30:00+0000 host conduit[1]: INFO: Conduit started\n"
        "2026-06-01T14:30:01+0000 host conduit[1]: ERROR: something failed\n"
    )

    def test_returns_200(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.logs._run_journalctl",
                   new=AsyncMock(return_value=(0, self._SAMPLE, ""))):
            response = client.get("/api/logs")
        assert response.status_code == 200

    def test_returns_list_of_log_lines(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.logs._run_journalctl",
                   new=AsyncMock(return_value=(0, self._SAMPLE, ""))):
            data = client.get("/api/logs").json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_pairing_link_redacted(self, logged_in):
        """Lines containing psi:// must be replaced with [REDACTED]."""
        client, _ = logged_in
        sensitive = "2026-06-01T00:00:00+0000 host conduit[1]: psi://secret-link\n"
        with patch("backend.api.logs._run_journalctl",
                   new=AsyncMock(return_value=(0, sensitive, ""))):
            response = client.get("/api/logs")
        assert response.status_code == 200
        assert "psi://" not in response.text
        assert "REDACTED" in response.text

    def test_journalctl_not_found_returns_503(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.logs._run_journalctl",
                   new=AsyncMock(side_effect=FileNotFoundError)):
            response = client.get("/api/logs")
        assert response.status_code == 503

    def test_journalctl_error_exit_returns_503(self, logged_in):
        client, _ = logged_in
        with patch("backend.api.logs._run_journalctl",
                   new=AsyncMock(return_value=(1, "", "journalctl error"))):
            response = client.get("/api/logs")
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Conduit control endpoints
# ---------------------------------------------------------------------------


class TestConduitControl:
    def test_start_when_stopped_returns_200(self, logged_in):
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.conduit.start", new_callable=AsyncMock,
                   return_value={"success": True, "status": "running", "message": "started"}):
            response = _auth_post(client, "/api/conduit/start", csrf)
        assert response.status_code == 200

    def test_start_response_has_action_field(self, logged_in):
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.conduit.start", new_callable=AsyncMock,
                   return_value={"success": True, "status": "running", "message": "ok"}):
            data = _auth_post(client, "/api/conduit/start", csrf).json()
        assert data["action"] == "start"

    def test_start_when_already_running_returns_409(self, logged_in):
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="running"):
            response = _auth_post(client, "/api/conduit/start", csrf)
        assert response.status_code == 409

    def test_stop_when_running_returns_200(self, logged_in):
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.conduit.stop", new_callable=AsyncMock,
                   return_value={"success": True, "status": "stopped", "message": "stopped"}):
            response = _auth_post(client, "/api/conduit/stop", csrf)
        assert response.status_code == 200

    def test_stop_when_already_stopped_returns_409(self, logged_in):
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"):
            response = _auth_post(client, "/api/conduit/stop", csrf)
        assert response.status_code == 409

    def test_restart_when_running_returns_200(self, logged_in):
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.conduit.restart", new_callable=AsyncMock,
                   return_value={"success": True, "status": "running", "message": "restarted"}):
            response = _auth_post(client, "/api/conduit/restart", csrf)
        assert response.status_code == 200

    def test_restart_action_field_in_response(self, logged_in):
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.conduit.restart", new_callable=AsyncMock,
                   return_value={"success": True, "status": "running", "message": "ok"}):
            data = _auth_post(client, "/api/conduit/restart", csrf).json()
        assert data["action"] == "restart"

    def test_adapter_permission_error_returns_503(self, logged_in):
        from backend.conduit.adapter import ConduitPermissionError
        client, csrf = logged_in
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.conduit.start", new_callable=AsyncMock,
                   side_effect=ConduitPermissionError("sudoers missing")):
            response = _auth_post(client, "/api/conduit/start", csrf)
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/ddns/status
# ---------------------------------------------------------------------------


class TestDdnsEndpoint:
    """Integration tests for GET /api/ddns/status (Issue #42).

    The test server has no config.json, so AppConfig uses its default
    ddns_log_path (/var/log/conduit-cc/ddns.log), which does not exist in
    CI.  This is the correct fresh-install scenario: the endpoint returns
    HTTP 200 with last_result="unknown" rather than an error.
    """

    def test_returns_200(self, logged_in):
        client, _ = logged_in
        response = client.get("/api/ddns/status")
        assert response.status_code == 200

    def test_response_schema_fields_present(self, logged_in):
        client, _ = logged_in
        data = client.get("/api/ddns/status").json()
        for field in ("hostname", "current_ip", "last_updated", "last_result",
                      "last_message", "consecutive_errors"):
            assert field in data, f"Missing field: {field}"

    def test_fresh_install_returns_unknown(self, logged_in):
        """No log file on the test server -> last_result must be 'unknown'."""
        client, _ = logged_in
        data = client.get("/api/ddns/status").json()
        assert data["last_result"] == "unknown"
        assert data["current_ip"] is None
        assert data["consecutive_errors"] == 0

    def test_requires_authentication(self, integration_client):
        response = integration_client.get("/api/ddns/status")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/settings/password
# ---------------------------------------------------------------------------


def _extract_hash_from_env(env_content: str) -> str | None:
    """
    Extract the ADMIN_PASSWORD_HASH value from .env file content.

    The production code single-quotes the bcrypt hash when writing it
    (e.g. ADMIN_PASSWORD_HASH='$2b$12$...') so that bash `source .env`
    under `set -euo pipefail` does not treat $2 as an unbound positional
    parameter.  pydantic-settings strips surrounding quotes automatically,
    but test code reading the raw file must do so explicitly.
    """
    for line in env_content.splitlines():
        if line.startswith("ADMIN_PASSWORD_HASH="):
            return line.split("=", 1)[1].strip().strip("'")
    return None


class TestPasswordChange:
    """
    Challenge Check constraints:
      - backend.api.settings.get_env_file_path patched to tmp_path/.env
      - Real .env is never touched
      - Happy path, wrong current password (400), mismatch (422), write error (500)
    """

    def _change_pw(self, client, csrf, tmp_path, monkeypatch, *,
                   current=KNOWN_PASSWORD, new=NEW_PASSWORD, confirm=NEW_PASSWORD):
        monkeypatch.setattr(
            "backend.api.settings.get_env_file_path",
            lambda: tmp_path / ".env",
        )
        return _auth_put(
            client,
            "/api/settings/password",
            csrf,
            json={
                "current_password": current,
                "new_password": new,
                "confirm_password": confirm,
            },
        )

    def test_happy_path_returns_200(self, logged_in, tmp_path, monkeypatch):
        client, csrf = logged_in
        response = self._change_pw(client, csrf, tmp_path, monkeypatch)
        assert response.status_code == 200

    def test_happy_path_body_has_status_ok(self, logged_in, tmp_path, monkeypatch):
        client, csrf = logged_in
        data = self._change_pw(client, csrf, tmp_path, monkeypatch).json()
        assert data.get("status") == "ok"

    def test_new_hash_written_to_temp_env_file(self, logged_in, tmp_path, monkeypatch):
        """Verify the temp .env file was written with a valid bcrypt hash for NEW_PASSWORD."""
        client, csrf = logged_in
        self._change_pw(client, csrf, tmp_path, monkeypatch)
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "ADMIN_PASSWORD_HASH=" in env_content
        new_hash = _extract_hash_from_env(env_content)
        assert new_hash, "ADMIN_PASSWORD_HASH not found in temp .env"
        assert bcrypt.checkpw(NEW_PASSWORD.encode(), new_hash.encode()), \
            "Stored hash does not match NEW_PASSWORD"

    def test_sessions_deleted_after_password_change(self, logged_in, tmp_path, monkeypatch):
        """
        Step 2 of the change_password flow deletes all sessions.
        Verify: a GET request using the old session after the change returns 401.
        """
        client, csrf = logged_in
        self._change_pw(client, csrf, tmp_path, monkeypatch)
        # Old session cookie is still in the jar but the DB row was deleted
        response = client.get("/api/status")
        assert response.status_code == 401

    def test_new_password_works_after_change(self, logged_in, tmp_path, monkeypatch):
        """
        After a successful password change, logging in with NEW_PASSWORD succeeds.
        Verification strategy: read the new hash from the temp .env file,
        inject it via ADMIN_PASSWORD_HASH env var, clear the settings cache,
        then POST /api/auth/login with NEW_PASSWORD.
        """
        client, csrf = logged_in
        self._change_pw(client, csrf, tmp_path, monkeypatch)

        # Read new hash from temp file (single quotes are stripped by helper)
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        new_hash = _extract_hash_from_env(env_content)
        assert new_hash, "No new hash found in temp .env"

        # Inject new hash via env var + clear cache so get_settings() picks it up
        from backend.config import get_settings
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", new_hash)
        get_settings.cache_clear()

        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": NEW_PASSWORD},
        )
        assert response.status_code == 200

    def test_old_password_rejected_after_change(self, logged_in, tmp_path, monkeypatch):
        """After password change, old password must return 401."""
        client, csrf = logged_in
        self._change_pw(client, csrf, tmp_path, monkeypatch)

        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        new_hash = _extract_hash_from_env(env_content)
        assert new_hash

        from backend.config import get_settings
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", new_hash)
        get_settings.cache_clear()

        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": KNOWN_PASSWORD},
        )
        assert response.status_code == 401

    def test_wrong_current_password_returns_400(self, logged_in, tmp_path, monkeypatch):
        client, csrf = logged_in
        response = self._change_pw(
            client, csrf, tmp_path, monkeypatch,
            current="definitely_wrong_password",
        )
        assert response.status_code == 400

    def test_new_confirm_mismatch_returns_422(self, logged_in, tmp_path, monkeypatch):
        client, csrf = logged_in
        response = self._change_pw(
            client, csrf, tmp_path, monkeypatch,
            new=NEW_PASSWORD,
            confirm="does_not_match_new_password",
        )
        assert response.status_code == 422

    def test_new_password_too_short_returns_422(self, logged_in, tmp_path, monkeypatch):
        client, csrf = logged_in
        response = self._change_pw(
            client, csrf, tmp_path, monkeypatch,
            new="short",
            confirm="short",
        )
        assert response.status_code == 422

    def test_env_write_failure_returns_500(self, logged_in, tmp_path, monkeypatch):
        """
        If _write_password_hash raises OSError, sessions are already deleted
        (step 2) and the endpoint returns 500 with a recovery message.
        """
        client, csrf = logged_in
        monkeypatch.setattr(
            "backend.api.settings.get_env_file_path",
            lambda: tmp_path / ".env",
        )
        with patch("backend.api.settings._write_password_hash",
                   side_effect=OSError("disk full")):
            response = _auth_put(
                client,
                "/api/settings/password",
                csrf,
                json={
                    "current_password": KNOWN_PASSWORD,
                    "new_password": NEW_PASSWORD,
                    "confirm_password": NEW_PASSWORD,
                },
            )
        assert response.status_code == 500
