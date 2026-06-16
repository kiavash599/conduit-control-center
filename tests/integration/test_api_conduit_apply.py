# SPDX-License-Identifier: MIT
"""
Integration tests for POST /api/conduit/config/{validate,apply} (M2).

The privileged adapter calls (apply/rollback/verify/helper_is_safe) and the view
reader are monkeypatched, and the audit writer is stubbed, so the apply-pipeline
logic (validation, no-op, optimistic concurrency, health gate, rollback, status
codes) is exercised without systemd, sudo, or a database.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.conduit as capi
from backend.conduit.models import ConduitConfigView, ConfigField
from backend.dependencies import AuthenticatedUser, get_current_user, require_csrf_token


def _view(mcc_c, mcc_e, bw_c, bw_e, **bw_kw):
    return ConduitConfigView(
        service_status="running",
        max_common_clients=ConfigField(mcc_c, mcc_e),
        bandwidth_mbps=ConfigField(bw_c, bw_e, **bw_kw),
    )


def _client(monkeypatch, *, view, apply_rc=(0, ""), rollback_rc=(0, ""),
            health=(True, None), helper_safe=True, locked=False):
    async def _get_view():
        return view
    async def _apply(_m, _b):
        return apply_rc
    async def _rollback():
        return rollback_rc
    async def _verify(_m, _b, **_k):
        return health
    async def _audit(*_a, **_k):
        return 1

    monkeypatch.setattr(capi, "get_conduit_config_view", _get_view)
    monkeypatch.setattr(capi, "apply_conduit_config", _apply)
    monkeypatch.setattr(capi, "rollback_conduit_config", _rollback)
    monkeypatch.setattr(capi, "verify_conduit_config_health", _verify)
    monkeypatch.setattr(capi, "helper_is_safe", lambda: helper_safe)
    monkeypatch.setattr(capi, "_write_config_audit", _audit)

    app = FastAPI()
    app.include_router(capi.router, prefix="/api/conduit")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    app.dependency_overrides[require_csrf_token] = lambda: None
    if locked:
        # The endpoint returns 409 on lock.locked()==True before entering the
        # context manager, so a stub avoids needing a running event loop here
        # (get_event_loop() auto-creation was removed in Python 3.14).
        class _LockedLock:
            def locked(self):
                return True

        app.state.conduit_apply_lock = _LockedLock()
    return TestClient(app)


def test_validate_ok(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/validate",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 200
    j = r.json()
    assert j["valid"] is True and j["changed"] is True and j["restart_required"] is True
    assert j["normalized"] == {"max_common_clients": 200, "bandwidth_mbps": 80}


def test_validate_422(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/validate",
               json={"max_common_clients": 0, "bandwidth_mbps": 40})
    assert r.status_code == 422
    assert r.json()["valid"] is False


def test_apply_happy(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 200 and r.json()["status"] == "applied"


def test_apply_noop(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 50, "bandwidth_mbps": 40})
    assert r.status_code == 200 and r.json()["status"] == "applied"


def test_apply_drift_conflict(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80,
                     "expected_effective": {"max_common_clients": 999, "bandwidth_mbps": 40}})
    assert r.status_code == 409 and r.json()["reason"] == "drift"


def test_apply_lock_conflict(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40), locked=True)
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 409 and r.json()["reason"] == "apply_in_progress"


def test_apply_wrapper_failure_rolls_back(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40), apply_rc=(4, "restart failed"))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 200 and r.json()["status"] == "rolled_back"


def test_apply_health_fail_rolls_back(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40),
                health=(False, "max_common_clients read-back mismatch"))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 200 and r.json()["status"] == "rolled_back"


def test_apply_rollback_failure_500(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40),
                health=(False, "metrics unreachable"), rollback_rc=(4, "restart failed"))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 500 and r.json()["status"] == "rollback_failed"


def test_apply_helper_unsafe_503(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40), helper_safe=False)
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 503


def test_apply_requires_auth(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    c.app.dependency_overrides.pop(get_current_user, None)
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 401
