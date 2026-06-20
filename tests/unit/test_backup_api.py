# SPDX-License-Identifier: MIT
"""Unit tests for the backup API.

S4A.1  POST /api/backup/create  -- auth + CSRF; streams an octet-stream
       attachment; create_backup() errors map to generic 500s (no leakage).
S4B-1a POST /api/backup/inspect -- auth + CSRF; multipart upload + passphrase;
       returns a manifest preview only (no restore, no writes); open_backup()
       errors map to generic 4xx; a size guard returns 413.

create_backup/open_backup are stubbed for contract tests; one create->inspect
round trip exercises the real crypto path (covered end-to-end by the S1-S2C
suites). No real CCC dir is required.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.backup as backup_api
from backend._version import APP_VERSION
from backend.backup.crypto import BackupCryptoError
from backend.backup.exclusion import KeyExclusionError
from backend.backup.manifest import BackupArchiveError
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_csrf_token,
)

_VALID_PASSPHRASE = "correct horse battery staple"   # >= 12 chars
_FAKE_BLOB = b"CCCBAK\x01fake-encrypted-bytes"


# --- inspect helpers --------------------------------------------------------


class _FakeOpened:
    """Stand-in for OpenedBackup: the inspect endpoint only reads .manifest."""

    def __init__(self, manifest):
        self.manifest = manifest


def _manifest(app_version="0.2.0"):
    return {
        "format": "ccc-backup",
        "manifest_version": 1,
        "app_version": app_version,
        "created_utc": "2026-01-02T03:04:05Z",
        "kind": "ccc-state",
        "items": [
            {"name": "ccc.db", "sha256": "a" * 64, "size": 4096},
            {"name": "env.subset", "sha256": "b" * 64, "size": 80},
            {"name": "config.json", "sha256": "c" * 64, "size": 32},
        ],
        "excluded": ["conduit_private_key", "SESSION_SECRET", "CF_API_TOKEN"],
    }


def _post_inspect(client, blob=_FAKE_BLOB, passphrase=_VALID_PASSPHRASE):
    return client.post(
        "/api/backup/inspect",
        files={"file": ("backup.cccbak", blob, "application/octet-stream")},
        data={"passphrase": passphrase},
    )


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(backup_api.router, prefix="/api/backup")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    app.dependency_overrides[require_csrf_token] = lambda: None
    return TestClient(app)


# --- success path -----------------------------------------------------------


def test_create_success_returns_octet_stream(client, monkeypatch):
    captured = {}

    def fake_create_backup(passphrase):
        captured["passphrase"] = passphrase
        return _FAKE_BLOB

    monkeypatch.setattr(backup_api, "create_backup", fake_create_backup)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})

    assert r.status_code == 200
    assert r.content == _FAKE_BLOB
    assert captured["passphrase"] == _VALID_PASSPHRASE       # passed through verbatim


# --- response headers -------------------------------------------------------


def test_create_response_headers(client, monkeypatch):
    monkeypatch.setattr(backup_api, "create_backup", lambda passphrase: _FAKE_BLOB)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment; filename=")
    assert "ccc-backup-" in cd and cd.rstrip('"').endswith(".cccbak")
    assert r.headers.get("cache-control") == "no-store"
    # No secret leakage: the passphrase must not appear in any header.
    assert _VALID_PASSPHRASE not in "\n".join(f"{k}: {v}" for k, v in r.headers.items())


# --- unauthenticated --------------------------------------------------------


def test_create_requires_auth(client, monkeypatch):
    monkeypatch.setattr(backup_api, "create_backup", lambda passphrase: _FAKE_BLOB)
    client.app.dependency_overrides.pop(get_current_user, None)   # real dependency -> 401
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 401


# --- csrf failure -----------------------------------------------------------


def test_create_requires_csrf(client, monkeypatch):
    monkeypatch.setattr(backup_api, "create_backup", lambda passphrase: _FAKE_BLOB)
    client.app.dependency_overrides.pop(require_csrf_token, None)  # real dependency -> 403
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 403


# --- validation failure -----------------------------------------------------


def test_create_rejects_short_passphrase(client):
    r = client.post("/api/backup/create", json={"passphrase": "tooshort"})   # < 12
    assert r.status_code == 422


def test_create_rejects_missing_passphrase(client):
    r = client.post("/api/backup/create", json={})
    assert r.status_code == 422


# --- create_backup error mapping -------------------------------------------


def test_create_key_exclusion_maps_to_500_generic(client, monkeypatch):
    def boom(passphrase):
        raise KeyExclusionError("pem")            # marker text, must not leak

    monkeypatch.setattr(backup_api, "create_backup", boom)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "safety" in detail.lower()
    assert _VALID_PASSPHRASE not in detail and "pem" not in detail


def test_create_unexpected_error_maps_to_500_generic(client, monkeypatch):
    def boom(passphrase):
        raise OSError("/etc/conduit-cc/ccc.db missing")   # path/detail must not leak

    monkeypatch.setattr(backup_api, "create_backup", boom)
    r = client.post("/api/backup/create", json={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail == "Backup creation failed."
    assert _VALID_PASSPHRASE not in detail and "ccc.db" not in detail


# ===========================================================================
# POST /api/backup/inspect  (S4B-1a)
# ===========================================================================


# --- success path: manifest preview only ------------------------------------


def test_inspect_success_returns_manifest_preview(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", lambda blob, pw: _FakeOpened(_manifest()))
    r = _post_inspect(client)
    assert r.status_code == 200
    body = r.json()
    assert body["app_version"] == "0.2.0"
    assert body["created_utc"] == "2026-01-02T03:04:05Z"
    assert body["manifest_version"] == 1
    assert body["kind"] == "ccc-state"
    assert body["items"] == [
        {"name": "ccc.db", "size": 4096},
        {"name": "env.subset", "size": 80},
        {"name": "config.json", "size": 32},
    ]
    assert body["excluded"] == ["conduit_private_key", "SESSION_SECRET", "CF_API_TOKEN"]
    assert body["compatibility"]["current_app_version"] == APP_VERSION


def test_inspect_returns_no_sha256_or_file_contents(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", lambda blob, pw: _FakeOpened(_manifest()))
    r = _post_inspect(client)
    assert r.status_code == 200
    raw = r.text                                   # whole serialized response
    assert "sha256" not in raw                     # no per-item digests exposed
    assert "data" not in raw                        # no item bytes
    for item in r.json()["items"]:
        assert set(item.keys()) == {"name", "size"}


# --- compatibility verdict --------------------------------------------------


def test_inspect_older_app_version_is_compatible(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", lambda b, p: _FakeOpened(_manifest("0.0.1")))
    body = _post_inspect(client).json()
    assert body["compatibility"]["compatible"] is True
    assert "older" in body["compatibility"]["message"].lower()


def test_inspect_newer_app_version_is_incompatible(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", lambda b, p: _FakeOpened(_manifest("99.0.0")))
    body = _post_inspect(client).json()
    assert body["compatibility"]["compatible"] is False
    assert "newer" in body["compatibility"]["message"].lower()


# --- real create -> inspect round trip (exercises true crypto path) ---------


def test_inspect_round_trip_with_real_crypto(client):
    # Build a real encrypted backup blob (no CCC dir needed) and inspect it.
    from backend.backup.archive import pack
    from backend.backup.collector import StagedItem, StagingSet
    from backend.backup.crypto import encrypt_archive

    staging = StagingSet(items=[
        StagedItem("ccc.db", b"\x00benign-db-bytes"),
        StagedItem("env.subset", b"ADMIN_USERNAME=admin\n"),
        StagedItem("config.json", b'{"ok": true}'),
    ])
    blob = encrypt_archive(pack(staging, "0.1.0"), _VALID_PASSPHRASE)
    r = _post_inspect(client, blob=blob)
    assert r.status_code == 200
    body = r.json()
    assert body["app_version"] == "0.1.0"
    names = {it["name"] for it in body["items"]}
    assert names == {"ccc.db", "env.subset", "config.json"}
    assert "sha256" not in r.text


# --- error mapping ----------------------------------------------------------


def test_inspect_wrong_passphrase_maps_to_400(client, monkeypatch):
    def boom(blob, pw):
        raise BackupCryptoError("generic")

    monkeypatch.setattr(backup_api, "open_backup", boom)
    r = _post_inspect(client)
    assert r.status_code == 400
    assert r.json()["detail"] == "Wrong passphrase or invalid backup file."
    assert _VALID_PASSPHRASE not in r.text


def test_inspect_malformed_maps_to_400(client, monkeypatch):
    def boom(blob, pw):
        raise BackupArchiveError("manifest is not valid JSON")

    monkeypatch.setattr(backup_api, "open_backup", boom)
    r = _post_inspect(client)
    assert r.status_code == 400
    assert r.json()["detail"] == "The file is not a valid CCC backup."


def test_inspect_newer_manifest_version_maps_to_400(client, monkeypatch):
    def boom(blob, pw):
        raise BackupArchiveError("manifest version is newer than supported")

    monkeypatch.setattr(backup_api, "open_backup", boom)
    r = _post_inspect(client)
    assert r.status_code == 400
    assert "newer version" in r.json()["detail"].lower()


def test_inspect_key_exclusion_maps_to_generic_4xx(client, monkeypatch):
    def boom(blob, pw):
        raise KeyExclusionError("pem")             # marker must not leak

    monkeypatch.setattr(backup_api, "open_backup", boom)
    r = _post_inspect(client)
    assert r.status_code in (400, 403)
    detail = r.json()["detail"]
    assert "safety" in detail.lower()
    assert "pem" not in detail


def test_inspect_unexpected_error_maps_to_500_generic(client, monkeypatch):
    def boom(blob, pw):
        raise RuntimeError("/etc/conduit-cc/ccc.db secret detail")

    monkeypatch.setattr(backup_api, "open_backup", boom)
    r = _post_inspect(client)
    assert r.status_code == 500
    assert r.json()["detail"] == "Could not inspect the backup."
    assert "ccc.db" not in r.text


# --- size guard -------------------------------------------------------------


def test_inspect_oversize_maps_to_413(client, monkeypatch):
    # Larger than the app-level cap -> 413, before open_backup is ever called.
    called = {"open": False}
    monkeypatch.setattr(backup_api, "open_backup",
                        lambda b, p: called.__setitem__("open", True) or _FakeOpened(_manifest()))
    big = b"x" * (backup_api._MAX_INSPECT_BYTES + 1)
    r = _post_inspect(client, blob=big)
    assert r.status_code == 413
    assert called["open"] is False


# --- auth / csrf / validation ----------------------------------------------


def test_inspect_requires_auth(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", lambda b, p: _FakeOpened(_manifest()))
    client.app.dependency_overrides.pop(get_current_user, None)
    r = _post_inspect(client)
    assert r.status_code == 401


def test_inspect_requires_csrf(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", lambda b, p: _FakeOpened(_manifest()))
    client.app.dependency_overrides.pop(require_csrf_token, None)
    r = _post_inspect(client)
    assert r.status_code == 403


def test_inspect_missing_file_maps_to_422(client):
    r = client.post("/api/backup/inspect", data={"passphrase": _VALID_PASSPHRASE})
    assert r.status_code == 422


def test_inspect_missing_passphrase_maps_to_422(client):
    r = client.post(
        "/api/backup/inspect",
        files={"file": ("backup.cccbak", _FAKE_BLOB, "application/octet-stream")},
    )
    assert r.status_code == 422
