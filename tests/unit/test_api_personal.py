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
from backend.conduit.models import ConduitConfigView, ConfigField, PersonalCompartmentStatus
from backend.database import PERSONAL_COMPARTMENT_NAME_KEY
from backend.dependencies import AuthenticatedUser, get_current_user, require_csrf_token


def _view(*, mpc_cfg=0, mpc_eff=None, mcc=50, bw=40):
    return ConduitConfigView(
        service_status="running",
        max_common_clients=ConfigField(mcc, mcc),
        bandwidth_mbps=ConfigField(bw, bw),
        max_personal_clients=ConfigField(mpc_cfg, mpc_eff),
    )


def _patch_view(monkeypatch, **kw):
    v = _view(**kw)
    async def f():
        return v
    monkeypatch.setattr(personal_api, "get_conduit_config_view", f)


def _patch_apply_chain(monkeypatch, *, health=(True, None), svc_healthy=True, capture=None):
    async def _apply(mcc, bw, **kwargs):
        if capture is not None:
            capture.update({"mcc": mcc, "bw": bw, **kwargs})
        return (0, "")
    async def _verify(_m, _b, **_k):
        return health
    async def _rollback():
        return (0, "")
    async def _svc():
        return svc_healthy
    monkeypatch.setattr(personal_api, "apply_conduit_config", _apply)
    monkeypatch.setattr(personal_api, "verify_conduit_config_health", _verify)
    monkeypatch.setattr(personal_api, "rollback_conduit_config", _rollback)
    monkeypatch.setattr(personal_api, "_service_healthy", _svc)


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
    # Default config view (Personal Mode off) + safe helper, so status/max-clients
    # tests do not hit real subprocesses. Tests override via _patch_view /
    # _patch_apply_chain as needed.
    async def _default_view():
        return _view()
    monkeypatch.setattr(personal_api, "get_conduit_config_view", _default_view)
    monkeypatch.setattr(personal_api, "helper_is_safe", lambda: True)

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
        "compartment_exists": True, "valid": True, "backup_exists": False,
        "display_name": "raspberrypi", "max_personal_clients": 0, "active": False,
    }


def test_status_active_when_compartment_and_limit(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True, backup=False))
    _patch_view(monkeypatch, mpc_eff=5)
    r = client.get("/api/conduit/personal/status").json()
    assert r["max_personal_clients"] == 5 and r["active"] is True


def test_status_inactive_when_no_compartment(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    _patch_view(monkeypatch, mpc_eff=5)        # limit set but no compartment
    r = client.get("/api/conduit/personal/status").json()
    assert r["active"] is False


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


# --- max-clients (C6b) -----------------------------------------------------

def test_max_clients_requires_auth(client):
    client.app.dependency_overrides.pop(get_current_user, None)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 0})
    assert r.status_code == 401


def test_max_clients_requires_csrf(client):
    client.app.dependency_overrides.pop(require_csrf_token, None)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 0})
    assert r.status_code == 403


def test_max_clients_range_rejected(client):
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 1001})
    assert r.status_code == 422


def test_max_clients_409_when_enabling_without_compartment(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=False))
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 409


def test_max_clients_noop_skips_apply(client, monkeypatch):
    # current configured == requested -> no apply, no restart
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    _patch_view(monkeypatch, mpc_cfg=5)
    called = {"apply": False}
    async def _apply(*a, **k):
        called["apply"] = True
        return (0, "")
    monkeypatch.setattr(personal_api, "apply_conduit_config", _apply)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "no-op"
    assert called["apply"] is False


def test_max_clients_full_set_merge_preserves_config(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    _patch_view(monkeypatch, mpc_cfg=0, mcc=77, bw=33)
    cap = {}
    _patch_apply_chain(monkeypatch, capture=cap)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied" and body["active"] is True
    assert body["max_personal_clients"] == 5
    # preserved config, changed only personal
    assert cap["mcc"] == 77 and cap["bw"] == 33 and cap["max_personal_clients"] == 5
    assert "token" not in body and "id" not in body


def test_max_clients_disable(client, monkeypatch):
    _patch_view(monkeypatch, mpc_cfg=5)        # currently active
    _patch_apply_chain(monkeypatch)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 0})
    assert r.status_code == 200
    assert r.json()["status"] == "applied" and r.json()["active"] is False


def test_max_clients_rolled_back_on_unhealthy(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    _patch_view(monkeypatch, mpc_cfg=0)
    _patch_apply_chain(monkeypatch, health=(False, "unhealthy"), svc_healthy=True)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "rolled_back"


def test_max_clients_rollback_failed(client, monkeypatch):
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    _patch_view(monkeypatch, mpc_cfg=0)
    _patch_apply_chain(monkeypatch, health=(False, "unhealthy"), svc_healthy=False)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 503
    assert r.json()["status"] == "rollback_failed"


def test_max_clients_personal_present_matches_applied(client, monkeypatch):
    # Effective conduit_max_personal_clients present and == requested -> applied.
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    _patch_view(monkeypatch, mpc_cfg=0, mpc_eff=5)
    _patch_apply_chain(monkeypatch)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    assert r.json()["max_personal_clients"] == 5


def test_max_clients_personal_mismatch_rolls_back(client, monkeypatch):
    # mcc/bw health passes, but effective personal != requested -> rollback.
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    _patch_view(monkeypatch, mpc_cfg=0, mpc_eff=3)        # effective != requested 5
    _patch_apply_chain(monkeypatch)                       # health True, svc healthy
    rolled = {"called": False}
    async def _rollback():
        rolled["called"] = True
        return (0, "")
    monkeypatch.setattr(personal_api, "rollback_conduit_config", _rollback)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "rolled_back"
    assert rolled["called"] is True


def test_max_clients_personal_absent_applies_via_fallback(client, monkeypatch):
    # Metric absent (eff None) is NOT a failure -> applied using mcc/bw-only.
    _patch_status(monkeypatch, PersonalCompartmentStatus(exists=True, valid=True))
    _patch_view(monkeypatch, mpc_cfg=0, mpc_eff=None)
    _patch_apply_chain(monkeypatch)
    r = client.put("/api/conduit/personal/max-clients", json={"max_personal_clients": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    assert r.json()["max_personal_clients"] == 5         # falls back to requested


def test_module_has_no_restart_or_systemctl_wiring():
    # C6a/C6b must not wire systemctl directly; restart happens ONLY through the
    # M2 apply path (apply_conduit_config), never a direct systemctl call here.
    # (A source substring scan would false-fail on docstrings, so check the
    # module namespace.)
    assert not hasattr(personal_api, "systemctl")
    assert not hasattr(personal_api, "restart")
