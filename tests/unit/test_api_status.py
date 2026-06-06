# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/status.py

Coverage targets:
  - _compute_uptime()    — running with timestamp / not running / None timestamp /
                           bad timestamp / future timestamp
  - GET /api/status      — success / ConduitPermissionError / ConduitAdapterError /
                           secondary calls fail gracefully
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.status import _compute_uptime, router
from backend.conduit.adapter import ConduitAdapterError, ConduitPermissionError
from backend.dependencies import AuthenticatedUser, get_current_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def status_client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


# ---------------------------------------------------------------------------
# _compute_uptime()
# ---------------------------------------------------------------------------


class TestComputeUptime:
    def test_running_with_valid_timestamp_returns_float(self):
        result = _compute_uptime("2026-01-01T00:00:00Z", "running")
        assert isinstance(result, float)
        assert result >= 0.0

    def test_stopped_returns_none(self):
        result = _compute_uptime("2026-01-01T00:00:00Z", "stopped")
        assert result is None

    def test_error_state_returns_none(self):
        result = _compute_uptime("2026-01-01T00:00:00Z", "error")
        assert result is None

    def test_none_last_changed_returns_none(self):
        result = _compute_uptime(None, "running")
        assert result is None

    def test_bad_timestamp_returns_none(self):
        result = _compute_uptime("not-a-date", "running")
        assert result is None

    def test_result_clamped_to_zero_minimum(self):
        # A timestamp in the far future would give a negative delta; must clamp to 0.
        result = _compute_uptime("2099-01-01T00:00:00Z", "running")
        assert result == 0.0


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------


class TestGetConduitStatusRoute:
    def test_success_returns_200(self, status_client):
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value="2026-01-01T00:00:00Z"), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value="1.2.3"):
            response = status_client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["node_status"] == "running"
        assert data["conduit_version"] == "1.2.3"

    def test_stopped_status_returned(self, status_client):
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None):
            response = status_client.get("/api/status")
        assert response.status_code == 200
        assert response.json()["node_status"] == "stopped"

    def test_permission_error_returns_503(self, status_client):
        with patch(
            "backend.api.status.get_status",
            new_callable=AsyncMock,
            side_effect=ConduitPermissionError("no sudoers"),
        ):
            response = status_client.get("/api/status")
        assert response.status_code == 503

    def test_adapter_error_returns_503(self, status_client):
        with patch(
            "backend.api.status.get_status",
            new_callable=AsyncMock,
            side_effect=ConduitAdapterError("unit not found"),
        ):
            response = status_client.get("/api/status")
        assert response.status_code == 503

    def test_secondary_failures_return_null_fields(self, status_client):
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, side_effect=ConduitAdapterError("err")), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, side_effect=Exception("fail")):
            response = status_client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["last_changed"] is None
        assert data["conduit_version"] is None

    def test_uptime_calculated_when_running(self, status_client):
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value="2026-01-01T00:00:00Z"), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None):
            response = status_client.get("/api/status")
        data = response.json()
        assert data["uptime_seconds"] is not None
        assert data["uptime_seconds"] >= 0.0
