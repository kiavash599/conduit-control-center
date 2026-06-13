# SPDX-License-Identifier: MIT
"""
Unit/integration tests for backend/api/traffic.py (Traffic Read API router).

Covers:
  - auth required (401 without a session) on both endpoints
  - GET /api/traffic/summary: empty (not recording) and populated shapes
  - aggregate-only: no holder_id / last_error in responses
  - GET /api/traffic/series: default 24h, explicit 7d/30d, dense grids
  - invalid range -> 422

The endpoints open the DB via get_db(); tests point the DB path at a temp file
seeded synchronously, override get_current_user for the authed cases, and pin
_now_utc so bucket grids are deterministic.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.database as dbmod
from backend.api.traffic import router
from backend.dependencies import AuthenticatedUser, get_current_user
from backend.traffic.schema import (
    SCHEMA_VERSION,
    SEED_HEALTH_SQL,
    STAMP_VERSION_SQL,
    TRAFFIC_DDL,
)

NOW = "2026-06-13T12:30:00Z"   # hour floor 12:00; today 2026-06-13


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    f = tmp_path / "ccc.db"
    monkeypatch.setattr(dbmod, "_PROD_DB_PATH", tmp_path / "absent" / "ccc.db")
    monkeypatch.setattr(dbmod, "_DEV_DB_PATH", f)
    return f


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr("backend.api.traffic._now_utc", lambda: NOW)


def _seed(path, *, populated):
    c = sqlite3.connect(path)
    try:
        c.execute("PRAGMA foreign_keys=ON")
        for ddl in TRAFFIC_DDL:
            c.execute(ddl)
        c.execute(SEED_HEALTH_SQL, ("2026-01-01T00:00:00Z",))
        c.execute(STAMP_VERSION_SQL, (SCHEMA_VERSION, "2026-01-01T00:00:00Z"))
        if populated:
            c.execute(
                "INSERT INTO traffic_epoch (id, started_at_utc, first_uptime_seconds, reason) "
                "VALUES (1, '2026-06-12T20:00:00Z', 1.0, 'startup')"
            )
            for ts, seq, up, down in (
                ("2026-06-13T10:00:00Z", 1, 100, 200),
                ("2026-06-13T11:00:00Z", 2, 30, 40),
            ):
                c.execute(
                    "INSERT INTO traffic_delta (ts_utc, seq, epoch_id, interval_seconds, "
                    "bytes_up_delta, bytes_down_delta, source, anomaly_flag, counted) "
                    "VALUES (?, ?, 1, 60, ?, ?, 'normal', 'none', 1)",
                    (ts, seq, up, down),
                )
            c.execute(
                "INSERT INTO traffic_rollup_hourly (bucket_utc, bytes_up, bytes_down, samples) "
                "VALUES ('2026-06-13T10:00:00Z', 50, 60, 1)"
            )
            c.execute(
                "INSERT INTO traffic_rollup_daily (bucket_utc, bytes_up, bytes_down, samples) "
                "VALUES ('2026-06-13', 100, 110, 1)"
            )
            c.execute(
                "UPDATE collector_health SET status='running', "
                "last_ok_ts_utc='2026-06-13T12:29:00Z' WHERE id=1"
            )
        c.commit()
    finally:
        c.close()


def _client(*, authed):
    app = FastAPI()
    app.include_router(router, prefix="/api/traffic")
    if authed:
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_summary_requires_auth(self, db_path):
        _seed(db_path, populated=False)
        r = _client(authed=False).get("/api/traffic/summary")
        assert r.status_code == 401

    def test_series_requires_auth(self, db_path):
        _seed(db_path, populated=False)
        r = _client(authed=False).get("/api/traffic/series")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_empty_not_recording(self, db_path):
        _seed(db_path, populated=False)
        r = _client(authed=True).get("/api/traffic/summary")
        assert r.status_code == 200
        j = r.json()
        assert j["recording_since"] is None
        assert j["lifetime"] is None
        assert j["status"] == "disabled"
        assert j["windows"]["last_24h"] == {"bytes_up": 0, "bytes_down": 0}
        assert j["windows"]["last_7d"] == {"bytes_up": 0, "bytes_down": 0}

    def test_populated(self, db_path):
        _seed(db_path, populated=True)
        r = _client(authed=True).get("/api/traffic/summary")
        assert r.status_code == 200
        j = r.json()
        assert j["recording_since"] == "2026-06-12T20:00:00Z"
        assert j["lifetime"] == {"bytes_up": 130, "bytes_down": 240}
        assert j["status"] == "running"
        assert j["last_ok_ts_utc"] == "2026-06-13T12:29:00Z"
        assert j["windows"]["last_24h"] == {"bytes_up": 50, "bytes_down": 60}
        assert j["windows"]["last_7d"] == {"bytes_up": 100, "bytes_down": 110}

    def test_no_internal_fields(self, db_path):
        _seed(db_path, populated=True)
        j = _client(authed=True).get("/api/traffic/summary").json()
        flat = str(j)
        assert "holder_id" not in flat
        assert "last_error" not in flat
        assert "consecutive_failures" not in flat


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


class TestSeries:
    def test_default_range_is_24h(self, db_path):
        _seed(db_path, populated=True)
        r = _client(authed=True).get("/api/traffic/series")
        assert r.status_code == 200
        j = r.json()
        assert j["range"] == "24h" and j["granularity"] == "hour"
        assert len(j["buckets"]) == 24
        assert j["buckets"][0]["bucket_utc"] == "2026-06-12T13:00:00Z"
        assert j["buckets"][-1]["bucket_utc"] == "2026-06-13T12:00:00Z"
        m = {b["bucket_utc"]: (b["bytes_up"], b["bytes_down"]) for b in j["buckets"]}
        assert m["2026-06-13T10:00:00Z"] == (50, 60)
        assert m["2026-06-13T09:00:00Z"] == (0, 0)   # zero-filled gap

    def test_explicit_7d(self, db_path):
        _seed(db_path, populated=True)
        j = _client(authed=True).get("/api/traffic/series?range=7d").json()
        assert j["range"] == "7d" and j["granularity"] == "day"
        assert len(j["buckets"]) == 7
        assert j["buckets"][-1]["bucket_utc"] == "2026-06-13"
        m = {b["bucket_utc"]: (b["bytes_up"], b["bytes_down"]) for b in j["buckets"]}
        assert m["2026-06-13"] == (100, 110)
        assert m["2026-06-12"] == (0, 0)

    def test_explicit_30d(self, db_path):
        _seed(db_path, populated=False)
        j = _client(authed=True).get("/api/traffic/series?range=30d").json()
        assert j["granularity"] == "day" and len(j["buckets"]) == 30

    def test_invalid_range_422(self, db_path):
        _seed(db_path, populated=False)
        r = _client(authed=True).get("/api/traffic/series?range=bogus")
        assert r.status_code == 422
