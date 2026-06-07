# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/conduit.py

Coverage targets:
  - ActionResponse / PairRequest models
  - POST /api/conduit/start   — 409 pre-condition / 503 permission error /
                                 503 adapter error / 200 success
  - POST /api/conduit/stop    — 409 / 200
  - POST /api/conduit/restart — 409 / 200
  - POST /api/conduit/pair    — validation / success / adapter error

Note: _control_action is not yet implemented in adapter.py.
      start/stop/restart are mocked at the api/conduit module level so
      tests exercise the route handler logic without calling the adapter.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.conduit import router
from backend.conduit.adapter import ConduitAdapterError, ConduitPermissionError
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    get_db,
    require_csrf_token,
)


# ---------------------------------------------------------------------------
# Test app factory
# ---------------------------------------------------------------------------


async def _mock_db():
    yield MagicMock(spec=aiosqlite.Connection)


async def _mock_csrf():
    return None


@pytest.fixture
def conduit_client():
    app = FastAPI()
    app.include_router(router, prefix="/api/conduit")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    app.dependency_overrides[require_csrf_token] = _mock_csrf
    app.dependency_overrides[get_db] = _mock_db
    return TestClient(app)


_CSRF = {"X-CSRF-Token": "test-token"}

# ---------------------------------------------------------------------------
# POST /api/conduit/start
# ---------------------------------------------------------------------------


class TestStartRoute:
    def test_already_running_returns_409(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="running"):
            response = conduit_client.post("/api/conduit/start", headers=_CSRF)
        assert response.status_code == 409

    def test_permission_error_returns_503(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.conduit.start", new_callable=AsyncMock, side_effect=ConduitPermissionError("denied")):
            response = conduit_client.post("/api/conduit/start", headers=_CSRF)
        assert response.status_code == 503

    def test_adapter_error_returns_503(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch("backend.api.conduit.start", new_callable=AsyncMock, side_effect=ConduitAdapterError("not found")):
            response = conduit_client.post("/api/conduit/start", headers=_CSRF)
        assert response.status_code == 503

    def test_success_returns_200(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"), \
             patch(
                 "backend.api.conduit.start",
                 new_callable=AsyncMock,
                 return_value={"success": True, "status": "running", "message": "started"},
             ):
            response = conduit_client.post("/api/conduit/start", headers=_CSRF)
        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "start"

    def test_starting_state_returns_409(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="starting"):
            response = conduit_client.post("/api/conduit/start", headers=_CSRF)
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/conduit/stop
# ---------------------------------------------------------------------------


class TestStopRoute:
    def test_already_stopped_returns_409(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopped"):
            response = conduit_client.post("/api/conduit/stop", headers=_CSRF)
        assert response.status_code == 409

    def test_success_returns_200(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="running"), \
             patch(
                 "backend.api.conduit.stop",
                 new_callable=AsyncMock,
                 return_value={"success": True, "status": "stopped", "message": "stopped"},
             ):
            response = conduit_client.post("/api/conduit/stop", headers=_CSRF)
        assert response.status_code == 200
        assert response.json()["action"] == "stop"


# ---------------------------------------------------------------------------
# POST /api/conduit/restart
# ---------------------------------------------------------------------------


class TestRestartRoute:
    def test_success_from_running_returns_200(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="running"), \
             patch(
                 "backend.api.conduit.restart",
                 new_callable=AsyncMock,
                 return_value={"success": True, "status": "running", "message": "restarted"},
             ):
            response = conduit_client.post("/api/conduit/restart", headers=_CSRF)
        assert response.status_code == 200
        assert response.json()["action"] == "restart"

    def test_stopping_state_returns_409(self, conduit_client):
        with patch("backend.api.conduit.get_status", new_callable=AsyncMock, return_value="stopping"):
            response = conduit_client.post("/api/conduit/restart", headers=_CSRF)
        assert response.status_code == 409
