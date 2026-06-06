# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/ddns.py

The DDNS endpoint is a stub returning HTTP 501 until Issue #42.
Coverage: router import + 501 route body.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ddns import router
from backend.dependencies import AuthenticatedUser, get_current_user


@pytest.fixture
def ddns_client():
    app = FastAPI()
    app.include_router(router, prefix="/api/ddns")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


class TestDdnsStatusRoute:
    def test_returns_501_not_implemented(self, ddns_client):
        response = ddns_client.get("/api/ddns/status")
        assert response.status_code == 501

    def test_response_body_has_detail(self, ddns_client):
        data = ddns_client.get("/api/ddns/status").json()
        assert "detail" in data

    def test_unauthenticated_returns_401(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/ddns")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/ddns/status")
        assert response.status_code == 401
