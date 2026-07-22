# SPDX-License-Identifier: MIT
"""S2C: unit tests for backup orchestration (backend/backup/archiver.py).

Full create/open round trip over a WAL-mode fixture CCC dir, fail-closed key
exclusion (at create and at the open re-scan), and the no-disk-artifact
guarantee. Uses sqlite3 (stdlib) + the cryptography dependency."""
from __future__ import annotations

import glob
import os
import sqlite3
import tempfile

import pytest

from backend._version import APP_VERSION
from backend.backup.archive import pack
from backend.backup.archiver import OpenedBackup, create_backup, open_backup
from backend.backup.collector import StagedItem, StagingSet
from backend.backup.crypto import BackupCryptoError, encrypt_archive
from backend.backup.exclusion import KeyExclusionError, scan_content

_PW = "correct horse battery staple"
_PEM = (
    b"-----BEGIN RSA PRIVATE " b"KEY-----\nMIIBOgIBAAJB\n"
    b"-----END RSA PRIVATE " b"KEY-----\n"
)


def _make_ccc_dir(tmp_path, *, config=None):
    d = tmp_path / "cccdir"
    d.mkdir()
    con = sqlite3.connect(str(d / "ccc.db"))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE sessions (id TEXT)")
    con.execute("INSERT INTO sessions VALUES ('live')")
    con.execute("CREATE TABLE app_settings (k TEXT, v TEXT)")
    con.execute("INSERT INTO app_settings VALUES ('theme', 'dark')")
    con.commit()
    (d / ".env").write_text(
        "ADMIN_USERNAME=admin\nADMIN_PASSWORD_HASH=$2b$12$x\n"
        "SESSION_SECRET=deadbeef\nCF_API_TOKEN=tok\nTLS_KEY_PATH=/x/o.key\nAPP_PORT=8000\n"
    )
    (d / "config.json").write_text(config or '{"traffic": {"collector_enabled": false}}')
    return d, con


def _snaps():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "ccc-backup-snap-*")))


# 1
def test_full_round_trip(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        blob = create_backup(_PW, ccc_dir=str(d), app_version="0.4.0")
        opened = open_backup(blob, _PW)
        assert isinstance(opened, OpenedBackup)
        # S4B-2.6: conduit_settings.json is always present (defaults to
        # configured=false here since no conduit_settings dict was passed).
        assert opened.staging.names() == {
            "ccc.db", "env.subset", "config.json", "conduit_settings.json"}
        db = next(i for i in opened.staging.items if i.name == "ccc.db").data
        out = tmp_path / "r.db"
        out.write_bytes(db)
        rc = sqlite3.connect(str(out))
        try:
            assert rc.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
            assert rc.execute("SELECT v FROM app_settings WHERE k='theme'").fetchone()[0] == "dark"
        finally:
            rc.close()
        env = next(i for i in opened.staging.items if i.name == "env.subset").data.decode()
        assert "SESSION_SECRET" not in env and "CF_API_TOKEN" not in env and "TLS_KEY_PATH" not in env
        assert "ADMIN_PASSWORD_HASH" in env
    finally:
        con.close()


# 2
def test_wrong_password(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        blob = create_backup(_PW, ccc_dir=str(d))
        with pytest.raises(BackupCryptoError):
            open_backup(blob, "wrong")
    finally:
        con.close()


# 3
def test_planted_key_in_source_aborts_create(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        (d / "config.json").write_bytes(_PEM)        # PEM in an allowlisted source
        with pytest.raises(KeyExclusionError):
            create_backup(_PW, ccc_dir=str(d))
    finally:
        con.close()


# 4
def test_tampered_blob(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        blob = bytearray(create_backup(_PW, ccc_dir=str(d)))
        blob[50] ^= 0x01                             # flip a ciphertext byte
        with pytest.raises(BackupCryptoError):
            open_backup(bytes(blob), _PW)
    finally:
        con.close()


# 5
def test_marker_not_in_blob(tmp_path):
    d, con = _make_ccc_dir(tmp_path, config='{"marker": "UNIQUE-MARKER-9f3a"}')
    try:
        blob = create_backup(_PW, ccc_dir=str(d))
        assert b"UNIQUE-MARKER-9f3a" not in blob
    finally:
        con.close()


# 6
def test_open_rescan_passes_legit_items(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        opened = open_backup(create_backup(_PW, ccc_dir=str(d)), _PW)
        for item in opened.staging.items:
            scan_content(item.data)                  # no raise
    finally:
        con.close()


# 7
def test_open_rescan_catches_crafted_pem():
    # A correctly encrypted, structurally valid archive whose member carries a
    # PEM private key -- the open re-scan must reject it.
    ss = StagingSet(items=[StagedItem("ccc.db", b"\x89benign"), StagedItem("smuggled", _PEM)])
    blob = encrypt_archive(pack(ss, "0.4.0"), _PW)
    with pytest.raises(KeyExclusionError):
        open_backup(blob, _PW)


# 8
def test_app_version_explicit_in_manifest(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        opened = open_backup(create_backup(_PW, ccc_dir=str(d), app_version="9.9.9"), _PW)
        assert opened.manifest["app_version"] == "9.9.9"
    finally:
        con.close()


# 9
def test_app_version_default_resolves(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        opened = open_backup(create_backup(_PW, ccc_dir=str(d)), _PW)
        assert opened.manifest["app_version"] == APP_VERSION
    finally:
        con.close()


# 10
def test_no_disk_artifact(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        before = _snaps()
        blob = create_backup(_PW, ccc_dir=str(d))
        open_backup(blob, _PW)
        assert _snaps() == before                    # S1 transient snapshot cleaned up
    finally:
        con.close()


# --- S4B-2.6: synthetic conduit_settings.json item --------------------------

import json as _json  # noqa: E402


def _conduit_item(opened):
    return next(i for i in opened.staging.items if i.name == "conduit_settings.json")


# 11
def test_conduit_settings_default_configured_false(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        # No conduit_settings passed -> item still present, configured=false.
        opened = open_backup(create_backup(_PW, ccc_dir=str(d)), _PW)
        assert "conduit_settings.json" in opened.staging.names()
        cfg = _json.loads(_conduit_item(opened).data.decode())
        assert cfg == {"schema": 1, "configured": False}
    finally:
        con.close()


# 12
def test_conduit_settings_configured_passthrough(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        settings = {
            "schema": 1, "configured": True,
            "max_common_clients": 50, "bandwidth_mbps": 100, "max_personal_clients": 2,
            "reduced": {"enabled": True, "start": "23:00", "end": "06:00",
                        "max_common": 10, "bandwidth_mbps": 20},
        }
        opened = open_backup(
            create_backup(_PW, ccc_dir=str(d), conduit_settings=settings), _PW)
        cfg = _json.loads(_conduit_item(opened).data.decode())
        assert cfg == settings                         # round-trips unchanged
    finally:
        con.close()
