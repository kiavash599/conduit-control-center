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
from backend.conduit.models import LiveStatus
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
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_live_status", new_callable=AsyncMock, return_value=None):
            response = status_client.get("/api/status")
        data = response.json()
        assert data["uptime_seconds"] is not None
        assert data["uptime_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# GET /api/status -- live block (Live Operations, Option 1)
# ---------------------------------------------------------------------------


class TestLiveBlock:
    def _core(self):
        return (
            patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="running"),
            patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value="2026-01-01T00:00:00Z"),
            patch("backend.api.status.get_version", new_callable=AsyncMock, return_value="2.0.0"),
        )

    def test_broker_live_and_fields(self, status_client):
        live = LiveStatus(is_live=True, announcing=1, connecting_clients=3, idle_seconds=0, build_rev="8531118")
        s, lc, v = self._core()
        with s, lc, v, patch("backend.api.status.get_live_status", new_callable=AsyncMock, return_value=live):
            d = status_client.get("/api/status").json()
        assert d["live"]["broker_state"] == "live"
        assert d["live"]["connecting_clients"] == 3
        assert d["live"]["idle_seconds"] == 0
        assert d["live"]["build_rev"] == "8531118"

    def test_broker_starting(self, status_client):
        live = LiveStatus(is_live=False, announcing=2, connecting_clients=1)
        s, lc, v = self._core()
        with s, lc, v, patch("backend.api.status.get_live_status", new_callable=AsyncMock, return_value=live):
            assert status_client.get("/api/status").json()["live"]["broker_state"] == "starting"

    def test_broker_disconnected(self, status_client):
        live = LiveStatus(is_live=False, announcing=0)
        s, lc, v = self._core()
        with s, lc, v, patch("backend.api.status.get_live_status", new_callable=AsyncMock, return_value=live):
            assert status_client.get("/api/status").json()["live"]["broker_state"] == "disconnected"

    def test_broker_not_running(self, status_client):
        with patch("backend.api.status.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.status.get_last_changed", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_version", new_callable=AsyncMock, return_value=None), \
             patch("backend.api.status.get_live_status", new_callable=AsyncMock, return_value=None):
            d = status_client.get("/api/status").json()
        assert d["live"]["broker_state"] == "not_running"
        assert d["live"]["connecting_clients"] is None

    def test_metrics_none_degrades_to_unknown_but_200(self, status_client):
        # running but metrics unreachable -> live None -> 'unknown'; core fields intact.
        s, lc, v = self._core()
        with s, lc, v, patch("backend.api.status.get_live_status", new_callable=AsyncMock, return_value=None):
            r = status_client.get("/api/status")
        assert r.status_code == 200
        d = r.json()
        assert d["node_status"] == "running"
        assert d["conduit_version"] == "2.0.0"
        assert d["uptime_seconds"] is not None
        assert d["live"]["broker_state"] == "unknown"
        assert d["live"]["connecting_clients"] is None and d["live"]["build_rev"] is None

    def test_metrics_exception_never_changes_status_or_core_fields(self, status_client):
        # get_live_status RAISES -> caught by return_exceptions -> still 200,
        # node_status/version/uptime intact, broker_state degrades to 'unknown'.
        s, lc, v = self._core()
        with s, lc, v, patch("backend.api.status.get_live_status", new_callable=AsyncMock, side_effect=Exception("boom")):
            r = status_client.get("/api/status")
        assert r.status_code == 200
        d = r.json()
        assert d["node_status"] == "running"
        assert d["conduit_version"] == "2.0.0"
        assert d["uptime_seconds"] is not None
        assert d["live"]["broker_state"] == "unknown"
