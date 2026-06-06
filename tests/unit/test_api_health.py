# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/health.py

Coverage targets:
  - GET /api/health  — 200 response, correct schema, uptime calculation
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend._version import APP_VERSION
from backend.api.health import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def health_client():
    app = FastAPI()
    app.include_router(router)
    app.state.started_at = time.time()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_200(self, health_client):
        response = health_client.get("/health")
        assert response.status_code == 200

    def test_response_has_status_ok(self, health_client):
        data = health_client.get("/health").json()
        assert data["status"] == "ok"

    def test_response_has_version(self, health_client):
        data = health_client.get("/health").json()
        assert data["version"] == APP_VERSION

    def test_uptime_seconds_is_non_negative(self, health_client):
        data = health_client.get("/health").json()
        assert data["uptime_seconds"] >= 0.0

    def test_uptime_seconds_is_float(self, health_client):
        data = health_client.get("/health").json()
        assert isinstance(data["uptime_seconds"], (int, float))

    def test_no_auth_required(self, health_client):
        """Health endpoint must be accessible without cookies."""
        response = health_client.get("/health")
        assert response.status_code == 200

    def test_response_content_type_is_json(self, health_client):
        response = health_client.get("/health")
        assert "application/json" in response.headers["content-type"]
