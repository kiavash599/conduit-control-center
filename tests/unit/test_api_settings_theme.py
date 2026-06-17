# SPDX-License-Identifier: MIT
"""Unit tests for POST /api/settings/theme (Theme Support, TS2).

Exercises the endpoint contract: valid value -> 200 + a theme cookie with the
right attributes; invalid value -> 422 with no cookie; auth + CSRF required.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.settings as settings_api
from backend.dependencies import AuthenticatedUser, get_current_user, require_csrf_token


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/api/settings")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    app.dependency_overrides[require_csrf_token] = lambda: None
    return TestClient(app)


def test_valid_theme_sets_cookie(client):
    for theme in ("light", "dark", "system"):
        r = client.post("/api/settings/theme", json={"theme": theme})
        assert r.status_code == 200, theme
        assert r.json() == {"theme": theme}
        sc = r.headers.get("set-cookie", "").lower()
        assert f"theme={theme}" in sc
        assert "httponly" in sc
        assert "samesite=strict" in sc
        assert "max-age=31536000" in sc          # 1 year
        assert "path=/" in sc


def test_invalid_theme_422_and_no_cookie(client):
    r = client.post("/api/settings/theme", json={"theme": "neon"})
    assert r.status_code == 422
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_requires_auth(client):
    client.app.dependency_overrides.pop(get_current_user, None)
    r = client.post("/api/settings/theme", json={"theme": "light"})
    assert r.status_code == 401


def test_requires_csrf(client):
    client.app.dependency_overrides.pop(require_csrf_token, None)
    r = client.post("/api/settings/theme", json={"theme": "light"})
    assert r.status_code == 403
