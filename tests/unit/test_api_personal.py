# SPDX-License-Identifier: MIT
"""Unit tests for the Personal Mode API (C6a): status / create / token.

Mocks the C5 adapter (personal_status/create/show_token) and the C1 settings
store. Verifies auth + CSRF, the 409 create guard, token return + no-store, the
404 token case, and that the token is never logged or persisted.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.personal as personal_api
from backend.conduit.models import PersonalCompartmentStatus
from backend.database import PERSONAL_COMPARTMENT_NAME_KEY
from backend.dependencies import AuthenticatedUser, get_current_user, require_csrf_token


@pytest.fixture
def client(monkeypatch):
    # In-memory settings store standing in for C1 app_settings.
    store: dict[str, str] = {}

    async def fake_get(key, default=None):
        return store.get(key, default)

    async def fake_set(key, value):
        store[key] = value

    monkeypatch.setattr(personal_api, "get_setting", fake_get)
    monkeypatch.setattr(personal_api, "set_setting", fake_set)

    app = FastAPI()
    app.include_router(personal_api.router, prefix="/api/conduit")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    app.dependency_overrides[require_csrf_token] = lambda: None

    c = TestClient(app)
    c.store = store
    return c


def _patch_status(monkeypatch, st):
    async def f():
        return st
    monkeypatch.setattr(personal_api, "personal_status", f)


def _patch_create(monkeypatch, token=None, exc=None):
    async def f(name):
        if exc:
            raise exc
        return token
    monkeypatch.setattr(personal_api, "personal_create", f)


def _patch_show(monkeypatch, token=None, exc=None):
    async def f(name):
        if exc:
            raise exc
        return token
    monkeypatch.setattr(personal_api, "personal_show_token", f)


# --- status ----------------------------------------------------------------

def test_status_authenticated_structure(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True, backup=False))
    client.store[PERSONAL_COMPARTMENT_NAME_KEY] = "raspberrypi"
    r = client.get("/api/conduit/personal/status")
    assert r.status_code == 200
    assert r.json() == {
        "compartment_exists": True, "valid": True,
        "backup_exists": False, "display_name": "raspberrypi",
    }


def test_status_requires_auth(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus())
    client.app.dependency_overrides.pop(get_current_user, None)
    assert client.get("/api/conduit/personal/status").status_code == 401


def test_status_never_returns_token_field(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True, backup=True))
    body = client.get("/api/conduit/personal/status").json()
    assert "token" not in body and "id" not in body


# --- create ----------------------------------------------------------------

def test_create_returns_token_and_no_store(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    _patch_create(monkeypatch, token="TOK")
    r = client.post("/api/conduit/personal/compartment", json={"display_name": "myhost"})
    assert r.status_code == 200
    assert r.json() == {"token": "TOK"}
    assert r.headers["cache-control"] == "no-store"


def test_create_stores_display_name_only(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    _patch_create(monkeypatch, token="SECRET")
    client.post("/api/conduit/personal/compartment", json={"display_name": "myhost"})
    assert client.store[PERSONAL_COMPARTMENT_NAME_KEY] == "myhost"
    assert "SECRET" not in client.store.values()          # token never persisted


def test_create_409_when_exists(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    r = client.post("/api/conduit/personal/compartment", json={"display_name": "x"})
    assert r.status_code == 409


def test_create_requires_auth(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    client.app.dependency_overrides.pop(get_current_user, None)
    r = client.post("/api/conduit/personal/compartment", json={"display_name": "x"})
    assert r.status_code == 401


def test_create_requires_csrf(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    client.app.dependency_overrides.pop(require_csrf_token, None)
    r = client.post("/api/conduit/personal/compartment", json={"display_name": "x"})
    assert r.status_code == 403


def test_create_rejects_blank_name(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    r = client.post("/api/conduit/personal/compartment", json={"display_name": "   "})
    assert r.status_code == 422


# --- token -----------------------------------------------------------------

def test_token_returns_with_no_store(client, monkeypatch):
    client.store[PERSONAL_COMPARTMENT_NAME_KEY] = "host"
    _patch_show(monkeypatch, token="TOK")
    r = client.get("/api/conduit/personal/token")
    assert r.status_code == 200
    assert r.json() == {"token": "TOK"}
    assert r.headers["cache-control"] == "no-store"


def test_token_404_when_no_name(client):
    assert client.get("/api/conduit/personal/token").status_code == 404


def test_token_requires_auth(client):
    client.app.dependency_overrides.pop(get_current_user, None)
    assert client.get("/api/conduit/personal/token").status_code == 401


# --- security: no token leakage; no restart in the module ------------------

def test_token_not_logged_on_create(client, monkeypatch, caplog):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    _patch_create(monkeypatch, token="SECRET_TOKEN_123")
    with caplog.at_level(logging.DEBUG):
        client.post("/api/conduit/personal/compartment", json={"display_name": "x"})
    assert "SECRET_TOKEN_123" not in caplog.text


def test_module_has_no_restart_or_systemctl_wiring():
    # C6a must not wire any restart / config-apply path. A source substring scan
    # would false-fail on the docstrings (which state "no restart"/"no
    # systemctl"), so check the module namespace for imported/used names.
    for forbidden in ("restart", "systemctl", "apply_conduit_config",
                      "rollback_conduit_config"):
        assert not hasattr(personal_api, forbidden), forbidden
