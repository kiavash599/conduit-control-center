# SPDX-License-Identifier: MIT
"""Unit tests for POST /api/backup/create (Epic #4, slice S4A.1).

Exercises the endpoint contract in isolation: auth + CSRF are required; a valid
request streams back an application/octet-stream attachment; create_backup()
failures map to generic 500s with no secret leakage. create_backup itself is
stubbed -- its real collect/pack/encrypt behaviour is covered by the S1-S2C
suites and needs a real CCC dir, which is out of scope for an endpoint test.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.backup as backup_api
from backend.backup.exclusion import KeyExclusionError
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_csrf_token,
)

_VALID_PASSPHRASE = "correct horse battery staple"   # >= 12 chars
_FAKE_BLOB = b"CCCBAK\x01fake-encrypted-bytes"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(backup_api.router, prefix="/api/backup")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    app.dependency_overrides[require_csrf_token] = lambda: None
    return TestClient(app)


# --- success path -----------------------------------------------------------


def test_create_success_returns_octet_stream(client, monkeypatch):
    captured = {}

    def fake_create_backup(passphrase):
        captured["passphrase"] = passphrase
        return _FAKE_BLOB

    monkeypatch.setattr(backup_api, "create_backup", fake_create_backup)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})

    assert r.status_code == 200
    assert r.content == _FAKE_BLOB
    assert captured["passphrase"] == _VALID_PASSPHRASE       # passed through verbatim


# --- response headers -------------------------------------------------------


def test_create_response_headers(client, monkeypatch):
    monkeypatch.setattr(backup_api, "create_backup", lambda passphrase: _FAKE_BLOB)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment; filename=")
    assert "ccc-backup-" in cd and cd.rstrip('"').endswith(".cccbak")
    assert r.headers.get("cache-control") == "no-store"
    # No secret leakage: the passphrase must not appear in any header.
    assert _VALID_PASSPHRASE not in "\n".join(f"{k}: {v}" for k, v in r.headers.items())


# --- unauthenticated --------------------------------------------------------


def test_create_requires_auth(client, monkeypatch):
    monkeypatch.setattr(backup_api, "create_backup", lambda passphrase: _FAKE_BLOB)
    client.app.dependency_overrides.pop(get_current_user, None)   # real dependency -> 401
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 401


# --- csrf failure -----------------------------------------------------------


def test_create_requires_csrf(client, monkeypatch):
    monkeypatch.setattr(backup_api, "create_backup", lambda passphrase: _FAKE_BLOB)
    client.app.dependency_overrides.pop(require_csrf_token, None)  # real dependency -> 403
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 403


# --- validation failure -----------------------------------------------------


def test_create_rejects_short_passphrase(client):
    r = client.post("/api/backup/create", json={"passphrase": "tooshort"})   # < 12
    assert r.status_code == 422


def test_create_rejects_missing_passphrase(client):
    r = client.post("/api/backup/create", json={})
    assert r.status_code == 422


# --- create_backup error mapping -------------------------------------------


def test_create_key_exclusion_maps_to_500_generic(client, monkeypatch):
    def boom(passphrase):
        raise KeyExclusionError("pem")            # marker text, must not leak

    monkeypatch.setattr(backup_api, "create_backup", boom)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "safety" in detail.lower()
    assert _VALID_PASSPHRASE not in detail and "pem" not in detail


def test_create_unexpected_error_maps_to_500_generic(client, monkeypatch):
    def boom(passphrase):
        raise OSError("/etc/conduit-cc/ccc.db missing")   # path/detail must not leak

    monkeypatch.setattr(backup_api, "create_backup", boom)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail == "Backup creation failed."
    assert _VALID_PASSPHRASE not in detail and "ccc.db" not in detail
