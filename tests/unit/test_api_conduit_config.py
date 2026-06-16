# SPDX-License-Identifier: MIT
"""
Tests for GET /api/conduit/config (M1, §6.1): auth, structured shape, drift
states, and the guarantee that the read-only path invokes no control
(start/stop/restart) operation.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.conduit as capi
from backend.conduit.models import ConduitConfigView, ConfigField, ReducedConfigView
from backend.dependencies import AuthenticatedUser, get_current_user


def _view(mcc_c, mcc_e, bw_c, bw_e, *, reduced=None, **bw_kw):
    return ConduitConfigView(
        service_status="running",
        max_common_clients=ConfigField(mcc_c, mcc_e),
        bandwidth_mbps=ConfigField(bw_c, bw_e, **bw_kw),
        reduced=reduced or ReducedConfigView(),
    )


def _client(monkeypatch, view, *, authed=True):
    async def _get_view():
        return view
    monkeypatch.setattr(capi, "get_conduit_config_view", _get_view)
    app = FastAPI()
    app.include_router(capi.router, prefix="/api/conduit")
    if authed:
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


def test_requires_auth(monkeypatch):
    c = _client(monkeypatch, _view(50, 50, 40, 40), authed=False)
    assert c.get("/api/conduit/config").status_code == 401


def test_shape_and_in_sync(monkeypatch):
    c = _client(monkeypatch, _view(50, 50, 40, 40))
    r = c.get("/api/conduit/config")
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"service_status", "drift", "max_common_clients", "bandwidth_mbps", "reduced"}
    assert j["service_status"] == "running"
    assert j["drift"] is False
    assert j["max_common_clients"] == {
        "configured": 50, "effective": 50, "drift": False,
        "unlimited_configured": False, "unlimited_effective": False,
    }
    assert j["reduced"] == {
        "enabled": False, "start": None, "end": None,
        "max_common_clients": None, "bandwidth_mbps": None,
    }


def test_reduced_enabled_serialized(monkeypatch):
    red = ReducedConfigView(enabled=True, start="02:00", end="06:00",
                            max_common_clients=10, bandwidth_mbps=15)
    c = _client(monkeypatch, _view(50, 50, 40, 40, reduced=red))
    j = c.get("/api/conduit/config").json()
    assert j["reduced"] == {
        "enabled": True, "start": "02:00", "end": "06:00",
        "max_common_clients": 10, "bandwidth_mbps": 15,
    }


def test_drift_true(monkeypatch):
    c = _client(monkeypatch, _view(50, 40, 40, 40))
    j = c.get("/api/conduit/config").json()
    assert j["max_common_clients"]["drift"] is True
    assert j["drift"] is True


def test_drift_unknown_when_effective_missing(monkeypatch):
    c = _client(monkeypatch, _view(50, None, 40, 40))
    j = c.get("/api/conduit/config").json()
    assert j["max_common_clients"]["drift"] is None
    assert j["drift"] is None


def test_bandwidth_unlimited_serialized(monkeypatch):
    c = _client(
        monkeypatch,
        _view(50, 50, -1, 0, unlimited_configured=True, unlimited_effective=True),
    )
    bw = c.get("/api/conduit/config").json()["bandwidth_mbps"]
    assert bw["unlimited_configured"] is True and bw["unlimited_effective"] is True
    assert bw["drift"] is False


def test_no_privileged_calls(monkeypatch):
    called = {"hit": False}

    async def _boom(*_a, **_k):
        called["hit"] = True
        return "running"

    monkeypatch.setattr(capi, "start", _boom)
    monkeypatch.setattr(capi, "stop", _boom)
    monkeypatch.setattr(capi, "restart", _boom)
    c = _client(monkeypatch, _view(50, 50, 40, 40))
    c.get("/api/conduit/config")
    assert called["hit"] is False
