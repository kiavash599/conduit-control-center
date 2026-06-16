# SPDX-License-Identifier: MIT
"""
Integration tests for GET /api/conduit/regions (RA-1).

adapter.get_regions is monkeypatched so the endpoint contract (auth, aggregate-
only shape, order passthrough, empty case) is exercised without metrics access.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.conduit as capi
from backend.conduit.models import RegionStat
from backend.dependencies import AuthenticatedUser, get_current_user


def _client(monkeypatch, rows, *, authed=True):
    async def _get_regions(*, scope="common", limit=10):
        return rows
    monkeypatch.setattr(capi, "get_regions", _get_regions)
    app = FastAPI()
    app.include_router(capi.router, prefix="/api/conduit")
    if authed:
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


def test_requires_auth(monkeypatch):
    c = _client(monkeypatch, [], authed=False)
    assert c.get("/api/conduit/regions").status_code == 401


def test_shape_and_order_passthrough(monkeypatch):
    rows = [RegionStat("SA", 295300000, 1), RegionStat("AE", 182200000, 1)]
    c = _client(monkeypatch, rows)
    r = c.get("/api/conduit/regions")
    assert r.status_code == 200
    j = r.json()
    assert list(j) == ["regions"]
    assert j["regions"] == [
        {"region": "SA", "traffic_bytes": 295300000, "clients": 1},
        {"region": "AE", "traffic_bytes": 182200000, "clients": 1},
    ]


def test_aggregate_only_fields(monkeypatch):
    c = _client(monkeypatch, [RegionStat("SA", 1, 1)])
    row = c.get("/api/conduit/regions").json()["regions"][0]
    assert set(row) == {"region", "traffic_bytes", "clients"}  # no ip/session/scope/per-client


def test_empty_when_no_regions(monkeypatch):
    c = _client(monkeypatch, [])
    assert c.get("/api/conduit/regions").json() == {"regions": []}
