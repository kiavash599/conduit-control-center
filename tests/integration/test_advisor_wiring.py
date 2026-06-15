# SPDX-License-Identifier: MIT
"""
Integration test for A1.3c C3: the advisor router + lifespan wiring.

Runs the real application lifespan via `with TestClient(app)` (a bare TestClient
would skip startup). Verifies app.state init, route registration, auth, and the
authenticated response (200, Cache-Control: no-store, contract shape). The DB
path is redirected to a temp file so the test never touches a real database.

Conduit metrics are unreachable in CI, so the advisor degrades gracefully
(offline summary) -- which is exactly the wiring path we want to confirm.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import backend.database as dbmod
from backend.dependencies import AuthenticatedUser, get_current_user


def test_advisor_wired_and_state_initialized(monkeypatch, tmp_path):
    monkeypatch.setattr(dbmod, "_PROD_DB_PATH", tmp_path / "absent" / "ccc.db")
    monkeypatch.setattr(dbmod, "_DEV_DB_PATH", tmp_path / "ccc.db")

    from backend.main import app

    with TestClient(app) as c:
        # 1) lifespan initialised the advisor in-memory state
        assert hasattr(app.state, "advisor_state")
        assert hasattr(app.state, "advisor_samples")
        assert hasattr(app.state, "advisor_lock")

        # 2) route registered at exactly /api/advisor
        assert any(getattr(r, "path", None) == "/api/advisor" for r in app.routes)

        # 3) unauthenticated -> 401
        assert c.get("/api/advisor").status_code == 401

        # 4) authenticated -> 200 + Cache-Control: no-store + expected shape
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
        try:
            r = c.get("/api/advisor")
            assert r.status_code == 200
            assert r.headers["cache-control"] == "no-store"
            j = r.json()
            assert set(j) == {"summary", "items", "generated_at"}
            assert "status" in j["summary"]
            assert isinstance(j["items"], list)
        finally:
            app.dependency_overrides.clear()
