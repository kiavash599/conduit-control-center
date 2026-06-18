# SPDX-License-Identifier: MIT
"""
Integration tests for POST /api/conduit/config/{validate,apply} (M2).

The privileged adapter calls (apply/rollback/verify/helper_is_safe) and the view
reader are monkeypatched, and the audit writer is stubbed, so the apply-pipeline
logic is exercised without systemd, sudo, or a database.

Key invariant under test (production bug fix): HEALTH is the source of truth, not
the helper's restart exit code. A non-zero apply rc with a healthy result still
=> applied; rollback_failed is returned only when the service is unhealthy after
rollback. Plus: _write_config_audit uses the request-scoped DB connection.
"""
from __future__ import annotations

import sqlite3

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.conduit as capi
from backend.conduit.models import ConduitConfigView, ConfigField, ReducedConfigView
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    get_db,
    require_csrf_token,
)


def _view(mcc_c, mcc_e, bw_c, bw_e, *, status="running", reduced=None, **bw_kw):
    return ConduitConfigView(
        service_status=status,
        max_common_clients=ConfigField(mcc_c, mcc_e),
        bandwidth_mbps=ConfigField(bw_c, bw_e, **bw_kw),
        reduced=reduced or ReducedConfigView(),
    )


def _client(monkeypatch, *, view, apply_rc=(0, ""), rollback_rc=(0, ""),
            health=(True, None), helper_safe=True, locked=False, capture=None):
    async def _get_view():
        return view
    async def _apply(_m, _b, **kw):
        if capture is not None:
            capture.update({"mcc": _m, "bw": _b, **kw})
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
    app.dependency_overrides[get_db] = lambda: None   # audit is stubbed; db unused
    if locked:
        # The endpoint returns 409 on lock.locked()==True before entering the
        # context manager, so a stub avoids needing a running event loop here
        # (get_event_loop() auto-creation was removed in Python 3.14).
        class _LockedLock:
            def locked(self):
                return True

        app.state.conduit_apply_lock = _LockedLock()
    return TestClient(app)


# --------------------------- validate ---------------------------
def test_validate_ok(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/validate",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 200
    j = r.json()
    assert j["valid"] is True and j["changed"] is True and j["restart_required"] is True
    assert j["normalized"] == {
        "max_common_clients": 200, "bandwidth_mbps": 80,
        "start_min": -1, "end_min": -1,
        "reduced_max_common_clients": 0, "reduced_bandwidth_mbps": 0,
    }


def test_validate_422(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/validate",
               json={"max_common_clients": 0, "bandwidth_mbps": 40})
    assert r.status_code == 422
    assert r.json()["valid"] is False


# --------------------------- apply ---------------------------
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


def test_apply_rc_nonzero_but_healthy_applies(monkeypatch):
    # Production bug: a transient non-zero restart rc must NOT cause a rollback
    # when Conduit is actually healthy with the requested values.
    c = _client(monkeypatch, view=_view(50, 50, 40, 40),
                apply_rc=(4, "restart reported non-zero"), health=(True, None))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 200 and r.json()["status"] == "applied"


def test_apply_health_fail_rollback_healthy_is_rolled_back(monkeypatch):
    # Health fails, but the service is healthy after rollback -> rolled_back, NOT
    # rollback_failed (even if the rollback command reported non-zero).
    c = _client(monkeypatch, view=_view(50, 50, 40, 40),
                health=(False, "read-back mismatch"), rollback_rc=(4, "restart non-zero"))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 200 and r.json()["status"] == "rolled_back"


def test_apply_rollback_failed_when_service_unhealthy(monkeypatch):
    # Health fails AND the service is still unhealthy after rollback -> 500.
    c = _client(monkeypatch, view=_view(50, 50, 40, 40, status="stopped"),
                health=(False, "metrics unreachable"))
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})
    assert r.status_code == 500 and r.json()["status"] == "rollback_failed"


def _enabled_window():
    return ReducedConfigView(enabled=True, start="02:00", end="06:00",
                             max_common_clients=10, bandwidth_mbps=15)


def test_apply_reduced_enable(monkeypatch):
    cap: dict = {}
    c = _client(monkeypatch, view=_view(50, 50, 40, 40), capture=cap)
    r = c.post("/api/conduit/config/apply", json={
        "max_common_clients": 50, "bandwidth_mbps": 40,
        "reduced": {"enabled": True, "start": "02:00", "end": "06:00",
                    "max_common_clients": 10, "bandwidth_mbps": 15},
    })
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert cap["reduced_start_min"] == 120 and cap["reduced_end_min"] == 360
    assert cap["reduced_max_common"] == 10 and cap["reduced_bandwidth_mbps"] == 15


def test_apply_reduced_preserved_when_omitted(monkeypatch):
    # Full-state (BS0 #2): changing only normal config preserves the window.
    cap: dict = {}
    c = _client(monkeypatch, view=_view(50, 50, 40, 40, reduced=_enabled_window()),
                capture=cap)
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 200, "bandwidth_mbps": 80})  # no "reduced"
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert cap["reduced_start_min"] == 120 and cap["reduced_max_common"] == 10


def test_apply_reduced_disable(monkeypatch):
    cap: dict = {}
    c = _client(monkeypatch, view=_view(50, 50, 40, 40, reduced=_enabled_window()),
                capture=cap)
    r = c.post("/api/conduit/config/apply", json={
        "max_common_clients": 50, "bandwidth_mbps": 40, "reduced": {"enabled": False},
    })
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert cap["reduced_start_min"] == -1 and cap["reduced_max_common"] == 0


def test_apply_reduced_invalid_422(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/apply", json={
        "max_common_clients": 50, "bandwidth_mbps": 40,
        "reduced": {"enabled": True, "start": "99:00", "end": "06:00",
                    "max_common_clients": 10, "bandwidth_mbps": 15},
    })
    assert r.status_code == 422 and r.json()["valid"] is False


def test_config_apply_preserves_max_personal(monkeypatch):
    # Symmetric full-set merge (C6b): a config-only change (bandwidth) must NOT
    # silently disable Personal Mode -- the helper call must carry the CURRENT
    # max_personal value (the drop-in is monolithic).
    view = ConduitConfigView(
        service_status="running",
        max_common_clients=ConfigField(50, 50),
        bandwidth_mbps=ConfigField(40, 40),
        max_personal_clients=ConfigField(25, 25),
    )
    cap = {}
    c = _client(monkeypatch, view=view, capture=cap)
    r = c.post("/api/conduit/config/apply",
               json={"max_common_clients": 50, "bandwidth_mbps": 80})
    assert r.status_code == 200
    assert cap["max_personal_clients"] == 25   # preserved, not clobbered to 0


def test_apply_reduced_max_exceeds_mcc_422(monkeypatch):
    c = _client(monkeypatch, view=_view(50, 50, 40, 40))
    r = c.post("/api/conduit/config/apply", json={
        "max_common_clients": 50, "bandwidth_mbps": 40,
        "reduced": {"enabled": True, "start": "02:00", "end": "06:00",
                    "max_common_clients": 60, "bandwidth_mbps": 15},
    })
    assert r.status_code == 422


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


# --------------------------- _write_config_audit (real DB pattern) ---------------------------
async def test_write_config_audit_inserts():
    async with aiosqlite.connect(":memory:") as db:
        await db.execute(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp TEXT, event_type TEXT, username TEXT, detail TEXT)"
        )
        await db.commit()
        aid = await capi._write_config_audit(
            db, "applied", "admin", old={"x": 1}, requested={"x": 2}, effective={"x": 2})
        assert aid is not None
        cur = await db.execute(
            "SELECT event_type, username FROM audit_log WHERE id = ?", (aid,))
        row = await cur.fetchone()
        assert row[0] == "CONDUIT_CONFIG" and row[1] == "admin"


async def test_write_config_audit_failure_returns_none_without_raising():
    class _BrokenDB:
        async def execute(self, *_a, **_k):
            raise sqlite3.OperationalError("boom")

        async def commit(self):  # pragma: no cover - never reached
            pass

    aid = await capi._write_config_audit(
        _BrokenDB(), "rollback_failed", "admin", old=None, requested={}, effective={})
    assert aid is None   # logged + swallowed; operation status is unaffected
