# SPDX-License-Identifier: MIT
"""S1: unit tests for the Backup staging collector (backend/backup/collector.py).

Allowlist + fail-closed key exclusion + ephemeral-session purge + .env redaction,
exercised against a WAL-mode SQLite database (the production journal mode). Pure
stdlib; no archive/encryption/restore/API are exercised."""
from __future__ import annotations

import glob
import os
import sqlite3
import tempfile

import pytest

from backend.backup.collector import ALLOWLIST, collect
from backend.backup.exclusion import KeyExclusionError

_PEM = (
    b"-----BEGIN RSA PRIVATE " b"KEY-----\nMIIBOgIBAAJB\n"
    b"-----END RSA PRIVATE " b"KEY-----\n"
)


def _make_ccc_dir(tmp_path):
    d = tmp_path / "cccdir"
    d.mkdir()
    # WAL-mode DB with autocheckpoint disabled, leaving frames in -wal, and the
    # writer connection held open across the test -> realistic live-DB snapshot.
    con = sqlite3.connect(str(d / "ccc.db"))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE sessions (id TEXT)")
    con.execute("INSERT INTO sessions VALUES ('live-session')")
    con.execute("CREATE TABLE app_settings (k TEXT, v TEXT)")
    con.execute("INSERT INTO app_settings VALUES ('theme', 'dark')")
    con.commit()
    (d / ".env").write_text(
        "ADMIN_USERNAME=admin\n"
        "ADMIN_PASSWORD_HASH=$2b$12$abcdefghijklmnopqrstuv\n"
        "SESSION_SECRET=deadbeefdeadbeef\n"
        "CF_API_TOKEN=cf_secret_token\n"
        "TLS_CERT_PATH=/etc/conduit-cc/tls/origin.pem\n"
        "TLS_KEY_PATH=/etc/conduit-cc/tls/origin.key\n"
        "APP_PORT=8000\n"
    )
    (d / "config.json").write_text('{"traffic": {"collector_enabled": false}}')
    return d, con   # keep `con` open so the WAL is not checkpointed away


def _snap_temps():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "ccc-backup-snap-*")))


def test_allowlist_contains_no_key_path():
    for name in ALLOWLIST:
        low = name.lower()
        assert "key" not in low
        assert not low.endswith((".key", ".pem"))


def test_collector_stages_only_expected_items(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        ss = collect(ccc_dir=str(d))
        assert ss.names() == {"ccc.db", "env.subset", "config.json"}
    finally:
        con.close()


def test_collector_purges_sessions_on_wal_db(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        ss = collect(ccc_dir=str(d))
        db_item = next(i for i in ss.items if i.name == "ccc.db")
        out = tmp_path / "restored.db"
        out.write_bytes(db_item.data)
        rcon = sqlite3.connect(str(out))
        try:
            assert rcon.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
            assert rcon.execute("SELECT v FROM app_settings WHERE k='theme'").fetchone()[0] == "dark"
        finally:
            rcon.close()
    finally:
        con.close()


def test_collector_env_subset_redacted(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        ss = collect(ccc_dir=str(d))
        env = next(i for i in ss.items if i.name == "env.subset").data.decode("utf-8")
        assert "SESSION_SECRET" not in env
        assert "CF_API_TOKEN" not in env
        assert "TLS_KEY_PATH" not in env
        assert "TLS_CERT_PATH" not in env
        assert "ADMIN_PASSWORD_HASH" in env
    finally:
        con.close()


def test_planted_key_in_allowlisted_file_aborts(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        (d / "config.json").write_bytes(_PEM)   # key planted in an allowlisted file
        with pytest.raises(KeyExclusionError):
            collect(ccc_dir=str(d))
    finally:
        con.close()


def test_planted_key_json_in_env_value_aborts(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        # A private-key JSON field smuggled into the value of an allowlisted key.
        (d / ".env").write_text('ADMIN_PASSWORD_HASH={"private_key":"QUJD"}\n')
        with pytest.raises(KeyExclusionError):
            collect(ccc_dir=str(d))
    finally:
        con.close()


def test_abort_leaves_no_temp_snapshot(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        (d / "config.json").write_bytes(_PEM)
        before = _snap_temps()
        with pytest.raises(KeyExclusionError):
            collect(ccc_dir=str(d))
        assert _snap_temps() == before          # no snapshot left behind
    finally:
        con.close()


def test_success_leaves_no_temp_snapshot(tmp_path):
    d, con = _make_ccc_dir(tmp_path)
    try:
        before = _snap_temps()
        collect(ccc_dir=str(d))
        assert _snap_temps() == before          # snapshot unlinked after read
    finally:
        con.close()
