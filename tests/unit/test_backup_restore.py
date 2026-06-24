# SPDX-License-Identifier: MIT
"""S3: unit tests for the restore primitive (backend/backup/restore.py).

Transactional restore-to-disk with .env merge, rollback checkpoint, key
re-validation, and permission/sidecar handling. Pure stdlib (sqlite3); the
OpenedBackup is constructed directly (no encrypt/decrypt)."""
from __future__ import annotations

import os
import re
import sqlite3
import stat
import tempfile

import pytest

import backend.backup.restore as restore
from backend.backup.archiver import OpenedBackup
from backend.backup.collector import StagedItem, StagingSet
from backend.backup.exclusion import KeyExclusionError
from backend.backup.manifest import MANIFEST_VERSION, BackupArchiveError, build_manifest
from backend.backup.restore import RestoreResult, restore_backup


# BCA-2: POSIX file-mode invariants (Contract v1 I6) are asserted only where the
# filesystem actually honours POSIX permission bits. On Linux CI / Raspberry Pi
# this holds; on a Windows / non-POSIX developer filesystem chmod is not faithful,
# so the assertion is skipped there -- never weakened, never removed.
def _posix_modes_supported() -> bool:
    try:
        fd, p = tempfile.mkstemp()
        os.close(fd)
        os.chmod(p, 0o600)
        ok = stat.S_IMODE(os.stat(p).st_mode) == 0o600
        os.unlink(p)
        return ok
    except OSError:
        return False


_POSIX_MODES = _posix_modes_supported()

_ENV_LIVE = (
    "SESSION_SECRET=LIVE_SECRET\nCF_API_TOKEN=LIVE_TOKEN\n"
    "TLS_CERT_PATH=/etc/conduit-cc/tls/origin.pem\n"
    "TLS_KEY_PATH=/etc/conduit-cc/tls/origin.key\nADMIN_PASSWORD_HASH=OLDHASH\n"
)
_ENV_SUBSET = b"ADMIN_USERNAME=admin\nADMIN_PASSWORD_HASH=NEWHASH\nAPP_PORT=8000\n"
_PEM = b"-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----\n"


def _make_db(path, sessions, theme):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sessions (id TEXT)")
    for s in sessions:
        con.execute("INSERT INTO sessions VALUES (?)", (s,))
    con.execute("CREATE TABLE app_settings (k TEXT, v TEXT)")
    con.execute("INSERT INTO app_settings VALUES ('theme', ?)", (theme,))
    con.commit()
    con.close()


def _backup_db_bytes(tmp_path):
    # Unique file per call: a single test may build several backups, and reusing
    # one path would re-open an existing DB and fail the CREATE TABLE.
    fd, name = tempfile.mkstemp(dir=str(tmp_path), suffix=".db")
    os.close(fd)
    _make_db(name, [], "dark")         # empty sessions (purged), theme=dark
    with open(name, "rb") as fh:
        return fh.read()


def _target(tmp_path, *, with_env=True, with_config=True, with_sidecars=False):
    d = tmp_path / "target"
    d.mkdir()
    con = sqlite3.connect(str(d / "ccc.db"))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE sessions (id TEXT)")
    con.execute("INSERT INTO sessions VALUES ('old')")
    con.execute("CREATE TABLE app_settings (k TEXT, v TEXT)")
    con.execute("INSERT INTO app_settings VALUES ('theme', 'light')")
    con.commit()
    con.close()
    if with_env:
        (d / ".env").write_text(_ENV_LIVE)
    if with_config:
        (d / "config.json").write_text('{"old": true}')
    if with_sidecars:
        (d / "ccc.db-wal").write_bytes(b"staleWAL")
        (d / "ccc.db-shm").write_bytes(b"staleSHM")
    return d


def _opened(tmp_path, *, db=None, env=_ENV_SUBSET, config=b'{"new": true}', extra=None, manifest_version=None):
    items = []
    if db is None:
        db = _backup_db_bytes(tmp_path)
    if db is not False:
        items.append(StagedItem("ccc.db", db))
    if env is not False:
        items.append(StagedItem("env.subset", env))
    if config is not False:
        items.append(StagedItem("config.json", config))
    if extra is not None:
        items.append(extra)
    m = build_manifest(items, "0.4.0")
    if manifest_version is not None:
        m["manifest_version"] = manifest_version
    return OpenedBackup(staging=StagingSet(items=items), manifest=m)


def _ckpts(d):
    return [n for n in os.listdir(d) if n.startswith(".ccc-restore-ckpt-")]


def _read_env(d):
    return (d / ".env").read_text()


# 1
def test_restore_round_trip(tmp_path):
    d = _target(tmp_path)
    res = restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert isinstance(res, RestoreResult) and res.status == "restored" and res.restart_required
    con = sqlite3.connect(str(d / "ccc.db"))
    try:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert con.execute("SELECT v FROM app_settings WHERE k='theme'").fetchone()[0] == "dark"
    finally:
        con.close()
    assert (d / "config.json").read_text() == '{"new": true}'


# 2
def test_env_merge_preserves_live_secrets(tmp_path):
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    env = _read_env(d)
    assert "SESSION_SECRET=LIVE_SECRET" in env
    assert "CF_API_TOKEN=LIVE_TOKEN" in env
    assert "TLS_CERT_PATH=/etc/conduit-cc/tls/origin.pem" in env
    assert "TLS_KEY_PATH=/etc/conduit-cc/tls/origin.key" in env


# 3
def test_env_merge_restores_admin(tmp_path):
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    env = _read_env(d)
    assert "ADMIN_PASSWORD_HASH=NEWHASH" in env and "OLDHASH" not in env
    assert "ADMIN_USERNAME=admin" in env


# 4
def test_fresh_target_generates_session_secret(tmp_path):
    d = _target(tmp_path, with_env=False)
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    env = _read_env(d)
    m = re.search(r"^SESSION_SECRET=([0-9a-f]+)$", env, re.M)
    assert m and len(m.group(1)) == 64 and m.group(1) != "LIVE_SECRET"


# 5
def test_db_replaced_sessions_purged(tmp_path):
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    con = sqlite3.connect(str(d / "ccc.db"))
    try:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        con.close()


# 6
def test_config_restored_when_present(tmp_path):
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path, config=b'{"k": 1}'), ccc_dir=str(d))
    assert (d / "config.json").read_text() == '{"k": 1}'


# 7
def test_config_untouched_when_absent(tmp_path):
    d = _target(tmp_path)
    res = restore_backup(_opened(tmp_path, config=False), ccc_dir=str(d))
    assert res.status == "restored"
    assert (d / "config.json").read_text() == '{"old": true}'


# 8
def test_reject_unexpected_item_name(tmp_path):
    d = _target(tmp_path)
    op = _opened(tmp_path, extra=StagedItem("evil.txt", b"x"))
    with pytest.raises(BackupArchiveError):
        restore_backup(op, ccc_dir=str(d))


# 9
def test_reject_missing_ccc_db(tmp_path):
    d = _target(tmp_path)
    with pytest.raises(BackupArchiveError):
        restore_backup(_opened(tmp_path, db=False), ccc_dir=str(d))


# 10
def test_reject_missing_env_subset(tmp_path):
    d = _target(tmp_path)
    with pytest.raises(BackupArchiveError):
        restore_backup(_opened(tmp_path, env=False), ccc_dir=str(d))


# 11
def test_reject_key_material_before_apply(tmp_path):
    d = _target(tmp_path)
    op = _opened(tmp_path, config=_PEM)          # PEM in the (validly named) config.json item
    with pytest.raises(KeyExclusionError):
        restore_backup(op, ccc_dir=str(d))
    assert (d / "config.json").read_text() == '{"old": true}'   # unchanged


# 12
def test_rollback_on_db_replace_failure(tmp_path, monkeypatch):
    d = _target(tmp_path)
    orig = restore._atomic_write

    def fake(path, data, mode):
        if path.endswith("ccc.db"):
            raise OSError("simulated DB write failure")
        return orig(path, data, mode)

    monkeypatch.setattr(restore, "_atomic_write", fake)
    res = restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert res.status == "rolled_back"
    con = sqlite3.connect(str(d / "ccc.db"))
    try:                                          # original DB restored
        assert con.execute("SELECT v FROM app_settings WHERE k='theme'").fetchone()[0] == "light"
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    finally:
        con.close()
    assert (d / "config.json").read_text() == '{"old": true}'   # config reverted too
    assert _ckpts(d) == []


# 13
def test_rollback_on_config_write_failure(tmp_path, monkeypatch):
    d = _target(tmp_path)
    orig = restore._atomic_write

    def fake(path, data, mode):
        if path.endswith("config.json"):
            raise OSError("simulated config write failure")
        return orig(path, data, mode)

    monkeypatch.setattr(restore, "_atomic_write", fake)
    res = restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert res.status == "rolled_back"
    assert (d / "config.json").read_text() == '{"old": true}'
    con = sqlite3.connect(str(d / "ccc.db"))
    try:
        assert con.execute("SELECT v FROM app_settings WHERE k='theme'").fetchone()[0] == "light"
    finally:
        con.close()


# 14
def test_checkpoint_deleted_after_success(tmp_path):
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert _ckpts(d) == []


# 15
def test_checkpoint_deleted_after_rollback(tmp_path, monkeypatch):
    d = _target(tmp_path)
    orig = restore._atomic_write
    monkeypatch.setattr(
        restore, "_atomic_write",
        lambda p, dt, m: (_ for _ in ()).throw(OSError("x")) if p.endswith("ccc.db") else orig(p, dt, m),
    )
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert _ckpts(d) == []


# 16
@pytest.mark.skipif(
    not _POSIX_MODES,
    reason="POSIX file-mode invariant (Contract v1 I6); enforced on Linux CI / "
           "Raspberry Pi. Skipped where the filesystem does not honour POSIX modes.",
)
def test_permissions(tmp_path):
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert (os.stat(d / "ccc.db").st_mode & 0o777) == 0o600
    assert (os.stat(d / ".env").st_mode & 0o777) == 0o640
    assert (os.stat(d / "config.json").st_mode & 0o777) == 0o640


# 17
def test_stale_sidecars_removed(tmp_path):
    d = _target(tmp_path, with_sidecars=True)
    restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert not (d / "ccc.db-wal").exists()
    assert not (d / "ccc.db-shm").exists()


# 18
def test_reject_target_under_excluded_location(tmp_path):
    # ccc_dir pointing under the TLS dir -> the target path-guard refuses before any write.
    op = _opened(tmp_path)
    with pytest.raises(KeyExclusionError):
        restore_backup(op, ccc_dir="/etc/conduit-cc/tls")


# 19 (version compat): newer manifest_version rejected; older app_version accepted
def test_version_compat(tmp_path):
    d = _target(tmp_path)
    with pytest.raises(BackupArchiveError):
        restore_backup(_opened(tmp_path, manifest_version=MANIFEST_VERSION + 1), ccc_dir=str(d))
    # older app_version is accepted (build_manifest sets app_version="0.4.0"; treated as same-or-older)
    res = restore_backup(_opened(tmp_path), ccc_dir=str(d))
    assert res.status == "restored"


# --- Patch 1: env.subset allowlist enforced inside restore (defense-in-depth) ---

# 20: a crafted env.subset carrying CF_API_TOKEN must not overwrite the live one,
#     and must not create one on a target that has none.
def test_env_subset_cannot_inject_cf_api_token(tmp_path):
    crafted = _ENV_SUBSET + b"CF_API_TOKEN=EVIL\n"
    # live token present -> preserved, EVIL ignored
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path, env=crafted), ccc_dir=str(d))
    env = _read_env(d)
    assert "CF_API_TOKEN=LIVE_TOKEN" in env and "EVIL" not in env
    # no live token -> none created (target .env has no CF_API_TOKEN)
    sub = tmp_path / "second"
    sub.mkdir()
    d2 = _target(sub)
    (d2 / ".env").write_text("SESSION_SECRET=LIVE_SECRET\nADMIN_PASSWORD_HASH=OLDHASH\n")
    restore_backup(_opened(tmp_path, env=crafted), ccc_dir=str(d2))
    assert "CF_API_TOKEN" not in _read_env(d2)


# 21: a crafted env.subset carrying TLS paths must not overwrite/create them.
def test_env_subset_cannot_inject_tls_paths(tmp_path):
    crafted = _ENV_SUBSET + b"TLS_CERT_PATH=/evil/c.pem\nTLS_KEY_PATH=/evil/k.key\n"
    d = _target(tmp_path)  # live TLS paths present
    restore_backup(_opened(tmp_path, env=crafted), ccc_dir=str(d))
    env = _read_env(d)
    assert "TLS_CERT_PATH=/etc/conduit-cc/tls/origin.pem" in env
    assert "TLS_KEY_PATH=/etc/conduit-cc/tls/origin.key" in env
    assert "/evil/" not in env


# 22: a crafted env.subset carrying SESSION_SECRET must not overwrite the live one.
def test_env_subset_cannot_overwrite_live_session_secret(tmp_path):
    crafted = _ENV_SUBSET + b"SESSION_SECRET=EVILSECRET\n"
    d = _target(tmp_path)
    restore_backup(_opened(tmp_path, env=crafted), ccc_dir=str(d))
    env = _read_env(d)
    assert "SESSION_SECRET=LIVE_SECRET" in env and "EVILSECRET" not in env


# 23: on a fresh target, SESSION_SECRET is generated fresh -- never copied from the backup.
def test_fresh_target_session_secret_not_copied_from_backup(tmp_path):
    crafted = _ENV_SUBSET + b"SESSION_SECRET=EVILSECRET\n"
    d = _target(tmp_path, with_env=False)
    restore_backup(_opened(tmp_path, env=crafted), ccc_dir=str(d))
    env = _read_env(d)
    assert "EVILSECRET" not in env
    m = re.search(r"^SESSION_SECRET=([0-9a-f]+)$", env, re.M)
    assert m and len(m.group(1)) == 64


# --- Patch 2: post-validate is integrity-only (no specific-table requirement) ---

# 24: a backup DB that lacks the 'sessions' table still restores (integrity is the
#     only hard invariant; startup create_tables() recreates missing core tables).
def test_post_validate_accepts_db_without_sessions_table(tmp_path):
    p = tmp_path / "no_sessions.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE app_settings (k TEXT, v TEXT)")
    con.execute("INSERT INTO app_settings VALUES ('theme', 'dark')")
    con.commit()
    con.close()
    d = _target(tmp_path)
    res = restore_backup(_opened(tmp_path, db=p.read_bytes()), ccc_dir=str(d))
    assert res.status == "restored"


# --- S4B-2.6: conduit_settings.json allowed but never written to disk --------

def test_conduit_settings_item_allowed_and_not_written(tmp_path):
    d = _target(tmp_path)
    item = StagedItem("conduit_settings.json", b'{"schema": 1, "configured": false}')
    # Presence must NOT KeyError the path-guard loop and must NOT be written.
    res = restore_backup(_opened(tmp_path, extra=item), ccc_dir=str(d))
    assert res.status == "restored"
    assert not (d / "conduit_settings.json").exists()   # never a disk target


def test_conduit_settings_configured_item_still_not_written(tmp_path):
    d = _target(tmp_path)
    item = StagedItem("conduit_settings.json", b'{"schema": 1, "configured": true,'
                      b' "max_common_clients": 10, "bandwidth_mbps": 50,'
                      b' "max_personal_clients": 0, "reduced": {"enabled": false}}')
    res = restore_backup(_opened(tmp_path, extra=item), ccc_dir=str(d))
    assert res.status == "restored"                     # restore_backup ignores it
    assert not (d / "conduit_settings.json").exists()
