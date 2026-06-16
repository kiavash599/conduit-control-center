# SPDX-License-Identifier: MIT
"""
Tests for GET /api/advisor (A1.3c step C2): auth, response shape, Cache-Control,
aggregate-only, degraded inputs (Conduit down / DB error), and the warm-up gate
end-to-end through the endpoint.

The gather helpers and get_db are monkeypatched for determinism; app.state
buffer/lock are lazily created by the endpoint's _ensure_state. C3 wires the
router into main.py and the lifespan -- not exercised here.
"""
from __future__ import annotations

import sqlite3
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.advisor as adv
from backend.advisor.models import BytesPair, ConduitState, SystemSnapshot, TrafficSnapshot
from backend.dependencies import AuthenticatedUser, get_current_user
from backend.traffic.models import NodeRuntime

UTC = timezone.utc


@asynccontextmanager
async def _fake_db():
    yield None


@asynccontextmanager
async def _boom_db():
    raise sqlite3.OperationalError("db gone")
    yield  # pragma: no cover - unreachable; required to make this an async generator


def _client(
    monkeypatch,
    *,
    authed=True,
    system=None,
    node=None,
    conduit=None,
    traffic=None,
    db_raises=False,
    seed_samples=None,
    now=None,
):
    monkeypatch.setattr(adv, "_gather_system", lambda: system)

    async def _node():
        return node

    async def _conduit():
        return conduit if conduit is not None else ConduitState(None, None)

    async def _traffic(db, *, now_ts, cfg):
        return traffic

    monkeypatch.setattr(adv, "_gather_node", _node)
    monkeypatch.setattr(adv, "_gather_conduit", _conduit)
    monkeypatch.setattr(adv, "_gather_traffic", _traffic)
    monkeypatch.setattr(adv, "get_db", (lambda: _boom_db()) if db_raises else (lambda: _fake_db()))
    if now is not None:
        monkeypatch.setattr(adv, "_now", lambda: now)

    app = FastAPI()
    app.include_router(adv.router, prefix="/api")
    if seed_samples is not None:
        app.state.advisor_samples = seed_samples
    if authed:
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


def test_requires_auth(monkeypatch):
    c = _client(monkeypatch, authed=False, system=SystemSnapshot(30, 50, 60),
                conduit=ConduitState(True, 10 * 86400))
    assert c.get("/api/advisor").status_code == 401


def test_response_shape_and_no_store(monkeypatch):
    c = _client(
        monkeypatch,
        system=SystemSnapshot(30, 50, 60),
        node=NodeRuntime(10, 0, 50),
        conduit=ConduitState(True, 10 * 86400),
        traffic=TrafficSnapshot(lifetime=BytesPair(100, 200), recording_since="2026-06-01T00:00:00Z"),
    )
    r = c.get("/api/advisor")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    j = r.json()
    assert set(j) == {"summary", "items", "generated_at"}
    assert {"status", "headline", "is_live", "connected_clients",
            "lifetime_up", "lifetime_down", "recording_since"} <= set(j["summary"])
    assert isinstance(j["items"], list)
    for it in j["items"]:
        assert it["severity"] in {"warning", "strong_suggestion", "suggestion", "info"}
        assert it["domain"] in {"capacity", "reduced_mode", "health"}
    assert j["summary"]["status"] == "live"


def test_aggregate_only_no_region_or_scope(monkeypatch):
    c = _client(monkeypatch, system=SystemSnapshot(30, 50, 60), node=NodeRuntime(10, 0, 50),
                conduit=ConduitState(True, 10 * 86400), traffic=TrafficSnapshot(lifetime=BytesPair(1, 2)))
    body = c.get("/api/advisor").text.lower()
    assert "region" not in body and "scope" not in body


def test_conduit_offline_summary(monkeypatch):
    c = _client(monkeypatch, system=SystemSnapshot(30, 50, 60), node=None,
                conduit=ConduitState(None, None), traffic=None)
    r = c.get("/api/advisor")
    assert r.status_code == 200
    assert r.json()["summary"]["status"] == "offline"


def test_broker_disconnected_warning(monkeypatch):
    c = _client(monkeypatch, system=SystemSnapshot(30, 50, 60), node=NodeRuntime(0, 0, 50),
                conduit=ConduitState(False, 10 * 86400), traffic=TrafficSnapshot(lifetime=BytesPair(1, 2)))
    j = c.get("/api/advisor").json()
    assert any(it["domain"] == "health" and it["severity"] == "warning" for it in j["items"])


def test_db_error_degrades_to_200(monkeypatch):
    c = _client(monkeypatch, system=SystemSnapshot(30, 50, 60), node=NodeRuntime(10, 0, 50),
                conduit=ConduitState(True, 10 * 86400), db_raises=True)
    assert c.get("/api/advisor").status_code == 200  # degrade, not 5xx


def test_growth_warmup_emits_growth(monkeypatch):
    base = datetime(2026, 6, 14, 11, 0, 0, tzinfo=UTC)
    samples = deque(
        (base + timedelta(seconds=70 * i), SystemSnapshot(22, 50, 55)) for i in range(11)
    )  # >=10 samples, span 700 s, all headroom
    now = base + timedelta(seconds=750)
    c = _client(
        monkeypatch,
        system=SystemSnapshot(22, 50, 55),
        node=NodeRuntime(42, 0, 50),                  # demand 42/50 = 84%
        conduit=ConduitState(True, 10 * 86400),
        traffic=TrafficSnapshot(lifetime=BytesPair(1, 2)),
        seed_samples=samples,
        now=now,
    )
    j = c.get("/api/advisor").json()
    assert any(it["domain"] == "capacity" and it["severity"] == "strong_suggestion"
               for it in j["items"])


def test_growth_suppressed_on_cold_buffer(monkeypatch):
    # No seeded samples -> only the one appended sample -> warm-up (G1) fails -> no growth item.
    c = _client(monkeypatch, system=SystemSnapshot(22, 50, 55), node=NodeRuntime(42, 0, 50),
                conduit=ConduitState(True, 10 * 86400), traffic=TrafficSnapshot(lifetime=BytesPair(1, 2)))
    j = c.get("/api/advisor").json()
    assert not any(
        it["domain"] == "capacity" and it["severity"] in {"strong_suggestion", "suggestion"}
        for it in j["items"]
    )
