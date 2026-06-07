# SPDX-License-Identifier: MIT
"""
Shared fixtures for integration tests.

Integration tests run the full FastAPI application (backend.main.app)
using Starlette's TestClient.  The Conduit adapter (systemctl subprocess
calls) is mocked per test so no real Conduit installation is required.

Isolation contract
------------------
Each test receives:
  - A fresh temporary SQLite database (tmp_path scope).
  - A known admin password hash injected via environment variable.
  - get_settings() and get_app_config() lru_cache cleared before and
    after every test so env-var patches take effect immediately.
  - SECURE_COOKIES=false so httpx's cookie jar accepts cookies over
    the http://testserver transport (TestClient base URL is non-HTTPS).

Lifespan
--------
TestClient.__enter__ triggers app lifespan startup:
  - create_tables() uses the patched DB path.
  - purge_expired_sessions() runs against the (empty) temp DB.
  - _purge_loop() background task starts (sleeping for 3600 s).

TestClient.__exit__ triggers app lifespan shutdown:
  - _purge_loop task is cancelled and awaited cleanly.
  - No task leakage between tests.

Implementation: Issue #37.
"""
from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import backend.database as db_mod
from backend.config import get_app_config, get_settings
from backend.main import app

# ---------------------------------------------------------------------------
# Known test credentials  (bcrypt cost=4 — fast, not prod-safe)
# ---------------------------------------------------------------------------

KNOWN_PASSWORD = "integration_test_password_secure"
KNOWN_HASH = bcrypt.hashpw(KNOWN_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
NEW_PASSWORD = "new_password_for_test_12345"


# ---------------------------------------------------------------------------
# Autouse: clear lru_cache before and after EVERY integration test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_config_caches():
    """
    Clear get_settings() and get_app_config() lru_cache before and after
    each test.  This ensures that env-var monkeypatches applied by the
    integration_client fixture take effect immediately, and that no stale
    settings leak between tests.
    """
    get_settings.cache_clear()
    get_app_config.cache_clear()
    yield
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.fixture(autouse=True)
def reset_traffic_metrics_cache():
    """
    Reset the module-level traffic metrics cache in backend.api.metrics before
    each test.  The cache is a module-level variable (_traffic_cache,
    _traffic_cache_ts) that persists across TestClient instances; without this
    reset a test that populates the cache with null bytes would cause the next
    test (which mocks non-null bytes) to return stale cached null values.
    """
    import backend.api.metrics as metrics_mod
    metrics_mod._traffic_cache = None
    metrics_mod._traffic_cache_ts = 0.0
    yield
    metrics_mod._traffic_cache = None
    metrics_mod._traffic_cache_ts = 0.0


# ---------------------------------------------------------------------------
# Primary fixture: full-stack TestClient with isolated temp DB
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_client(tmp_path, monkeypatch):
    """
    Full-stack TestClient backed by a temp SQLite database and a known
    admin password.

    Patches applied (Challenge Check constraints):
      1. backend.database._DEV_DB_PATH  → tmp_path/test.db
         backend.database._PROD_DB_PATH → tmp_path/nonexistent/ccc.db
         (parent does not exist → get_db_path() always returns dev path)
         Effect: ALL DB access during lifespan and request handling uses
         the isolated temp file.  Covers: create_tables(), get_db(),
         purge_expired_sessions(), per-request session/lockout writes.

      2. ADMIN_PASSWORD_HASH env var → KNOWN_HASH
         SECURE_COOKIES env var      → false
         Effect: login works with KNOWN_PASSWORD; cookies are set without
         the Secure flag so httpx's cookie jar includes them on
         http://testserver requests.

      3. get_settings() and get_app_config() caches cleared before the
         TestClient enters, so the env var patches take effect.
    """
    db_file = tmp_path / "test.db"

    # Constraint 1 — redirect all DB access to temp file
    monkeypatch.setattr(db_mod, "_DEV_DB_PATH", db_file)
    monkeypatch.setattr(db_mod, "_PROD_DB_PATH", tmp_path / "nonexistent" / "ccc.db")

    # Constraint 2 — known password + HTTP-safe cookies
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", KNOWN_HASH)
    monkeypatch.setenv("SECURE_COOKIES", "false")

    # Constraint 3 — fresh settings before TestClient starts
    get_settings.cache_clear()
    get_app_config.cache_clear()

    with TestClient(app) as client:
        yield client

    # Post-test cleanup
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# Convenience fixture: pre-authenticated client
# ---------------------------------------------------------------------------


@pytest.fixture
def logged_in(integration_client):
    """
    Return (client, csrf_token) with a valid admin session already established.

    The client's httpx cookie jar contains both session_id (HttpOnly) and
    csrf_token (readable) after login.  Subsequent requests made with this
    client automatically include the session_id cookie.  State-changing
    requests must also include the X-CSRF-Token header set to csrf_token.
    """
    resp = integration_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": KNOWN_PASSWORD},
    )
    assert resp.status_code == 200, f"Fixture login failed ({resp.status_code}): {resp.text}"
    csrf_token = integration_client.cookies.get("csrf_token", "")
    assert csrf_token, "No csrf_token cookie after login — check SECURE_COOKIES=false patch"
    return integration_client, csrf_token
