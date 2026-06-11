# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/ddns.py -- Issue #42

Coverage targets:
  - DdnsStatusResponse model
  - _reset_ddns_cache()               -- cache cleared, fresh read on next call
  - _parse_ddns_log()                 -- all edge cases
  - GET /api/ddns/status              -- route behaviour via TestClient

Test cases:
  1. valid "updated" entry
  2. valid "no_change" entry
  3. trailing consecutive_errors count (mixed log)
  4. consecutive_errors when ALL entries are errors
  5. malformed JSON lines skipped; valid lines still parsed
  6. all-malformed log returns unknown (not error)
  7. missing log file -> unknown (200, not 4xx/5xx)
  8. empty log file -> unknown
  9. 401 without session
  10. cache hit: second call within TTL serves stale data
  11. _reset_ddns_cache() allows fresh file read
  12. unknown/missing-file result IS cached for the normal TTL
  13. hostname comes from get_settings().cf_record_name
  14. empty cf_record_name -> hostname: null
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from backend.api.ddns import _reset_ddns_cache, router
from backend.dependencies import AuthenticatedUser, get_current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    result: str,
    ip: str | None = "1.2.3.4",
    ts: str = "2026-06-07T12:00:00Z",
    msg: str = "ok",
    record_name: str = "conduit.example.com",
) -> str:
    """Return one JSON log line as a string, matching the script schema."""
    obj: dict = {
        "timestamp": ts,
        "record_name": record_name,
        "result": result,
        "message": msg,
    }
    # Preserve JSON null for error entries (ip=None)
    if ip is None:
        obj["ip"] = None
    else:
        obj["ip"] = ip
    return json.dumps(obj)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the DDNS module-level cache before and after every test."""
    _reset_ddns_cache()
    yield
    _reset_ddns_cache()


@pytest.fixture(autouse=True)
def patch_ddns_config(monkeypatch):
    """
    Inject deterministic AppConfig and Settings stubs for every test.

    Returns (cfg, settings) so individual tests can override fields.
    """
    cfg = SimpleNamespace(
        ddns_log_path="/nonexistent/ddns.log",
        ddns_status_cache_seconds=30,
    )
    settings_stub = SimpleNamespace(cf_record_name="conduit.example.com")
    monkeypatch.setattr("backend.api.ddns.get_app_config", lambda: cfg)
    monkeypatch.setattr("backend.api.ddns.get_settings", lambda: settings_stub)
    return cfg, settings_stub


@pytest.fixture
def ddns_client():
    """Authenticated TestClient for the DDNS router."""
    app = FastAPI()
    app.include_router(router, prefix="/api/ddns")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_unauthenticated_returns_401(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/ddns")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/ddns/status")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Fresh-install / empty log
# ---------------------------------------------------------------------------


class TestMissingOrEmptyLog:
    def test_missing_log_file_returns_200_unknown(self, ddns_client, patch_ddns_config):
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = "/absolutely/does/not/exist/ddns.log"

        r = ddns_client.get("/api/ddns/status")
        assert r.status_code == 200
        data = r.json()
        assert data["last_result"] == "unknown"
        assert data["current_ip"] is None
        assert data["last_updated"] is None
        assert data["last_message"] is None
        assert data["consecutive_errors"] == 0

    def test_empty_log_file_returns_unknown(self, ddns_client, patch_ddns_config, tmp_path):
        log_file = tmp_path / "ddns.log"
        log_file.write_text("")
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["last_result"] == "unknown"
        assert data["consecutive_errors"] == 0

    def test_whitespace_only_log_returns_unknown(self, ddns_client, patch_ddns_config, tmp_path):
        log_file = tmp_path / "ddns.log"
        log_file.write_text("\n\n   \n")
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["last_result"] == "unknown"


# ---------------------------------------------------------------------------
# Valid log entries
# ---------------------------------------------------------------------------


class TestValidLogEntries:
    def test_updated_entry_fields(self, ddns_client, patch_ddns_config, tmp_path):
        log_file = tmp_path / "ddns.log"
        log_file.write_text(
            _entry("updated", ip="1.2.3.4", ts="2026-06-07T12:00:00Z", msg="A record updated") + "\n"
        )
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["last_result"] == "updated"
        assert data["current_ip"] == "1.2.3.4"
        assert data["last_updated"] == "2026-06-07T12:00:00Z"
        assert data["last_message"] == "A record updated"
        assert data["consecutive_errors"] == 0
        assert data["hostname"] == "conduit.example.com"

    def test_no_change_entry_fields(self, ddns_client, patch_ddns_config, tmp_path):
        log_file = tmp_path / "ddns.log"
        log_file.write_text(
            _entry("no_change", ip="5.6.7.8", msg="IP unchanged") + "\n"
        )
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["last_result"] == "no_change"
        assert data["current_ip"] == "5.6.7.8"
        assert data["consecutive_errors"] == 0


# ---------------------------------------------------------------------------
# consecutive_errors
# ---------------------------------------------------------------------------


class TestConsecutiveErrors:
    def test_trailing_errors_after_non_error(self, ddns_client, patch_ddns_config, tmp_path):
        """consecutive_errors counts trailing errors; non-error entry resets the counter."""
        lines = [
            _entry("updated", ip="1.1.1.1"),
            _entry("error", ip=None, msg="fail1"),
            _entry("error", ip=None, msg="fail2"),
            _entry("error", ip=None, msg="fail3"),
        ]
        log_file = tmp_path / "ddns.log"
        log_file.write_text("\n".join(lines) + "\n")
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["consecutive_errors"] == 3
        assert data["last_result"] == "error"
        assert data["current_ip"] is None

    def test_all_error_entries(self, ddns_client, patch_ddns_config, tmp_path):
        """If every entry is an error, consecutive_errors equals the entry count."""
        lines = [_entry("error", ip=None, msg=f"fail{i}") for i in range(5)]
        log_file = tmp_path / "ddns.log"
        log_file.write_text("\n".join(lines) + "\n")
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["consecutive_errors"] == 5

    def test_errors_then_non_error_resets(self, ddns_client, patch_ddns_config, tmp_path):
        """Errors followed by a non-error entry means consecutive_errors=0."""
        lines = [
            _entry("error", ip=None, msg="fail1"),
            _entry("error", ip=None, msg="fail2"),
            _entry("no_change", ip="9.9.9.9", msg="IP unchanged"),
        ]
        log_file = tmp_path / "ddns.log"
        log_file.write_text("\n".join(lines) + "\n")
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["consecutive_errors"] == 0
        assert data["last_result"] == "no_change"


# ---------------------------------------------------------------------------
# Malformed lines
# ---------------------------------------------------------------------------


class TestMalformedLines:
    def test_malformed_lines_skipped_valid_lines_parsed(self, ddns_client, patch_ddns_config, tmp_path):
        """Valid lines are parsed even when surrounded by malformed ones."""
        log_file = tmp_path / "ddns.log"
        log_file.write_text(
            "not json at all\n"
            + _entry("updated", ip="9.9.9.9") + "\n"
            + "{broken json\n"
        )
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        # The valid entry before the trailing broken line is still the last valid entry
        assert data["last_result"] == "updated"
        assert data["current_ip"] == "9.9.9.9"

    def test_all_malformed_lines_returns_unknown_not_error(self, ddns_client, patch_ddns_config, tmp_path):
        """If every line is malformed, returns unknown (not error)."""
        log_file = tmp_path / "ddns.log"
        log_file.write_text("not json\nalso not json\n{broken\n")
        cfg, _ = patch_ddns_config
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["last_result"] == "unknown"
        assert data["consecutive_errors"] == 0


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


class TestCache:
    def test_cache_hit_serves_stale_data(self, ddns_client, patch_ddns_config, tmp_path):
        """Second call within TTL returns cached data even after log file changes."""
        cfg, _ = patch_ddns_config
        log_file = tmp_path / "ddns.log"
        log_file.write_text(_entry("updated", ip="1.1.1.1") + "\n")
        cfg.ddns_log_path = str(log_file)

        r1 = ddns_client.get("/api/ddns/status")
        assert r1.json()["current_ip"] == "1.1.1.1"

        # Modify the log -- cache must still return old result
        log_file.write_text(_entry("updated", ip="2.2.2.2") + "\n")

        r2 = ddns_client.get("/api/ddns/status")
        assert r2.json()["current_ip"] == "1.1.1.1"

    def test_reset_cache_allows_fresh_file_read(self, ddns_client, patch_ddns_config, tmp_path):
        """After _reset_ddns_cache(), the next call reads the file again."""
        cfg, _ = patch_ddns_config
        log_file = tmp_path / "ddns.log"
        log_file.write_text(_entry("updated", ip="1.1.1.1") + "\n")
        cfg.ddns_log_path = str(log_file)

        ddns_client.get("/api/ddns/status")  # populate cache

        _reset_ddns_cache()
        log_file.write_text(_entry("updated", ip="2.2.2.2") + "\n")

        r = ddns_client.get("/api/ddns/status")
        assert r.json()["current_ip"] == "2.2.2.2"

    def test_unknown_result_is_cached_for_normal_ttl(self, ddns_client, patch_ddns_config, tmp_path):
        """The unknown/missing-file result is cached -- file appearing within TTL is not seen."""
        cfg, _ = patch_ddns_config
        missing = tmp_path / "not_yet_created.log"
        cfg.ddns_log_path = str(missing)

        r1 = ddns_client.get("/api/ddns/status")
        assert r1.json()["last_result"] == "unknown"

        # Create the file -- within TTL, cached result should still be unknown
        missing.write_text(_entry("updated", ip="3.3.3.3") + "\n")

        r2 = ddns_client.get("/api/ddns/status")
        assert r2.json()["last_result"] == "unknown"  # still the cached value


# ---------------------------------------------------------------------------
# Hostname / settings
# ---------------------------------------------------------------------------


class TestHostname:
    def test_hostname_from_settings_cf_record_name(self, ddns_client, patch_ddns_config, tmp_path):
        cfg, settings_stub = patch_ddns_config
        settings_stub.cf_record_name = "myhost.example.net"
        log_file = tmp_path / "ddns.log"
        log_file.write_text(_entry("updated") + "\n")
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["hostname"] == "myhost.example.net"

    def test_empty_cf_record_name_returns_null_hostname(self, ddns_client, patch_ddns_config, tmp_path):
        """Empty cf_record_name (unset .env on fresh install) -> hostname: null."""
        cfg, settings_stub = patch_ddns_config
        settings_stub.cf_record_name = ""
        log_file = tmp_path / "ddns.log"
        log_file.write_text(_entry("updated") + "\n")
        cfg.ddns_log_path = str(log_file)

        data = ddns_client.get("/api/ddns/status").json()
        assert data["hostname"] is None

    def test_hostname_on_missing_log(self, ddns_client, patch_ddns_config):
        """hostname is still returned correctly even when the log file is missing."""
        cfg, settings_stub = patch_ddns_config
        settings_stub.cf_record_name = "conduit.example.com"
        cfg.ddns_log_path = "/does/not/exist.log"

        data = ddns_client.get("/api/ddns/status").json()
        assert data["hostname"] == "conduit.example.com"
        assert data["last_result"] == "unknown"
