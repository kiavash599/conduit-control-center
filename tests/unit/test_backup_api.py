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


# ===========================================================================
# POST /api/backup/restore + GET /api/backup/restore/status  (S4B-2.2)
# ===========================================================================
import subprocess  # noqa: E402  (used only by restore tests below)


def _restore_id_from_frame(frame: bytes) -> str:
    header = frame.split(b"\n\n", 1)[0].decode("ascii")
    for line in header.splitlines():
        if line.startswith("restore_id:"):
            return line.split(":", 1)[1].strip()
    return ""


def _accept_helper(frame):
    """Stub helper: echo a correct ack for the frame's restore_id (returncode 0)."""
    return 0, "accepted " + _restore_id_from_frame(frame)


def _ok_open(blob, pw):
    return object()


def _post_restore(client, blob=_FAKE_BLOB, passphrase=_VALID_PASSPHRASE, confirm="RESTORE"):
    data = {"passphrase": passphrase}
    if confirm is not None:
        data["confirm"] = confirm
    return client.post(
        "/api/backup/restore",
        files={"file": ("backup.cccbak", blob, "application/octet-stream")},
        data=data,
    )


# --- request validation matrix ----------------------------------------------


def test_restore_success_returns_202(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", _accept_helper)
    r = _post_restore(client)
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "scheduled"
    # restore_id is a uuid4 string
    import uuid as _uuid
    assert _uuid.UUID(body["restore_id"]).version == 4


def test_restore_wrong_confirm_422_no_work(client, monkeypatch):
    called = {"open": False, "helper": False}
    monkeypatch.setattr(backup_api, "open_backup",
                        lambda b, p: called.__setitem__("open", True) or object())
    monkeypatch.setattr(backup_api, "_invoke_restore_helper",
                        lambda f: called.__setitem__("helper", True) or (0, "x"))
    r = _post_restore(client, confirm="restore")          # wrong case
    assert r.status_code == 422
    assert called == {"open": False, "helper": False}


def test_restore_missing_confirm_422(client):
    r = _post_restore(client, confirm=None)
    assert r.status_code == 422


def test_restore_missing_file_422(client):
    r = client.post("/api/backup/restore",
                    data={"passphrase": _VALID_PASSPHRASE, "confirm": "RESTORE"})
    assert r.status_code == 422


def test_restore_missing_passphrase_422(client):
    r = client.post("/api/backup/restore",
                    files={"file": ("b.cccbak", _FAKE_BLOB, "application/octet-stream")},
                    data={"confirm": "RESTORE"})
    assert r.status_code == 422


def test_restore_oversize_413_before_validate(client, monkeypatch):
    called = {"open": False, "helper": False}
    monkeypatch.setattr(backup_api, "open_backup",
                        lambda b, p: called.__setitem__("open", True) or object())
    monkeypatch.setattr(backup_api, "_invoke_restore_helper",
                        lambda f: called.__setitem__("helper", True) or (0, "x"))
    big = b"x" * (backup_api._MAX_RESTORE_BYTES + 1)
    r = _post_restore(client, blob=big)
    assert r.status_code == 413
    assert called == {"open": False, "helper": False}


def test_restore_prevalidate_wrong_passphrase_400_no_helper(client, monkeypatch):
    helper_called = {"v": False}
    monkeypatch.setattr(backup_api, "open_backup",
                        lambda b, p: (_ for _ in ()).throw(BackupCryptoError("x")))
    monkeypatch.setattr(backup_api, "_invoke_restore_helper",
                        lambda f: helper_called.__setitem__("v", True) or (0, "x"))
    r = _post_restore(client)
    assert r.status_code == 400
    assert helper_called["v"] is False
    assert _VALID_PASSPHRASE not in r.text


def test_restore_prevalidate_key_exclusion_400(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup",
                        lambda b, p: (_ for _ in ()).throw(KeyExclusionError("pem")))
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", _accept_helper)
    r = _post_restore(client)
    assert r.status_code == 400
    assert "pem" not in r.text


def test_restore_prevalidate_unexpected_500(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup",
                        lambda b, p: (_ for _ in ()).throw(RuntimeError("/etc/conduit-cc secret")))
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", _accept_helper)
    r = _post_restore(client)
    assert r.status_code == 500
    assert "conduit-cc secret" not in r.text


# --- helper exit mapping matrix ---------------------------------------------


@pytest.mark.parametrize("code,expected", [
    (backup_api._EXIT_BUSY, 409),
    (backup_api._EXIT_PREFLIGHT, 400),
    (backup_api._EXIT_FS, 503),
    (backup_api._EXIT_USAGE, 500),
    (backup_api._EXIT_INTERNAL, 500),
])
def test_restore_exit_code_mapping(client, monkeypatch, code, expected):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", lambda f: (code, ""))
    r = _post_restore(client)
    assert r.status_code == expected
    assert _VALID_PASSPHRASE not in r.text


def test_restore_helper_timeout_500(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)

    def boom(frame):
        raise subprocess.TimeoutExpired(cmd="ccc-restore-apply", timeout=30)

    monkeypatch.setattr(backup_api, "_invoke_restore_helper", boom)
    r = _post_restore(client)
    assert r.status_code == 500


def test_restore_helper_launch_failure_500(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)

    def boom(frame):
        raise OSError("sudo not found")

    monkeypatch.setattr(backup_api, "_invoke_restore_helper", boom)
    r = _post_restore(client)
    assert r.status_code == 500
    assert "sudo" not in r.text


# --- ack validation matrix --------------------------------------------------


def test_restore_ack_mismatch_500(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)
    monkeypatch.setattr(backup_api, "_invoke_restore_helper",
                        lambda f: (0, "accepted not-the-right-id"))
    r = _post_restore(client)
    assert r.status_code == 500


def test_restore_ack_empty_500(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", lambda f: (0, ""))
    r = _post_restore(client)
    assert r.status_code == 500


# --- security: passphrase delivered on stdin, never leaked ------------------


def test_restore_passphrase_on_stdin_not_in_response(client, monkeypatch):
    captured = {}

    def capture(frame):
        captured["frame"] = frame
        return 0, "accepted " + _restore_id_from_frame(frame)

    monkeypatch.setattr(backup_api, "open_backup", _ok_open)
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", capture)
    r = _post_restore(client)
    assert r.status_code == 202
    # passphrase IS delivered to the helper on stdin (the frame) ...
    assert _VALID_PASSPHRASE.encode() in captured["frame"]
    # ... but never echoed back in the response.
    assert _VALID_PASSPHRASE not in r.text


# --- auth / csrf ------------------------------------------------------------


def test_restore_requires_auth(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", _accept_helper)
    client.app.dependency_overrides.pop(get_current_user, None)
    assert _post_restore(client).status_code == 401


def test_restore_requires_csrf(client, monkeypatch):
    monkeypatch.setattr(backup_api, "open_backup", _ok_open)
    monkeypatch.setattr(backup_api, "_invoke_restore_helper", _accept_helper)
    client.app.dependency_overrides.pop(require_csrf_token, None)
    assert _post_restore(client).status_code == 403


# --- status endpoint matrix -------------------------------------------------


def _set_outcome(monkeypatch, tmp_path, payload):
    path = tmp_path / "restore-status.json"
    if payload is not None:
        path.write_text(__import__("json").dumps(payload))
    monkeypatch.setattr(backup_api, "_OUTCOME_PATH", str(path))
    return path


def test_status_idle_when_absent(client, monkeypatch, tmp_path):
    _set_outcome(monkeypatch, tmp_path, None)
    r = client.get("/api/backup/restore/status")
    assert r.status_code == 200 and r.json()["state"] == "idle"


@pytest.mark.parametrize("state", ["in_progress", "restored", "rolled_back", "rollback_failed"])
def test_status_reports_helper_states(client, monkeypatch, tmp_path, state):
    _set_outcome(monkeypatch, tmp_path, {
        "schema": 1, "restore_id": "rid-1", "state": state,
        "started_utc": "t0", "finished_utc": "t1", "restart_ok": True,
        "message": "msg",
    })
    body = client.get("/api/backup/restore/status").json()
    assert body["state"] == state and body["restore_id"] == "rid-1"
    assert body["restart_ok"] is True


def test_status_corrupt_json_is_unknown(client, monkeypatch, tmp_path):
    path = tmp_path / "restore-status.json"
    path.write_text("{ not json")
    monkeypatch.setattr(backup_api, "_OUTCOME_PATH", str(path))
    assert client.get("/api/backup/restore/status").json()["state"] == "unknown"


def test_status_wrong_schema_is_unknown(client, monkeypatch, tmp_path):
    _set_outcome(monkeypatch, tmp_path, {"schema": 99, "state": "restored"})
    assert client.get("/api/backup/restore/status").json()["state"] == "unknown"


def test_status_requires_auth(client, monkeypatch, tmp_path):
    _set_outcome(monkeypatch, tmp_path, None)
    client.app.dependency_overrides.pop(get_current_user, None)
    assert client.get("/api/backup/restore/status").status_code == 401
