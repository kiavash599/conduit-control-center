# SPDX-License-Identifier: MIT
"""
backend/backup/collector.py
---------------------------
Backup staging collector for Backup & Restore (Epic #4, S1). Assembles the
backup *staging set* from a FILE-LEVEL ALLOWLIST (never a recursive directory
walk), running the key-exclusion guard on every item plus the .env redaction
allowlist. Fail-closed: any KeyExclusionError propagates, leaving no staged
result and no temp snapshot behind.

S1 scope: NO archive, NO encryption, NO restore, NO API, NO final artifact. The
only on-disk touch is a transient SQLite snapshot, unlinked immediately after it
is read into memory. Pure stdlib.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass, field

from backend.backup.exclusion import (
    KeyExclusionError,
    assert_path_allowed,
    scan_content,
)

# Production CCC config directory (overridable for tests).
CCC_DIR = "/etc/conduit-cc"

# FILE-LEVEL ALLOWLIST: the only sources the collector ever reads. No key path
# appears here, and no directory is ever walked recursively.
DB_NAME = "ccc.db"
ENV_NAME = ".env"
CONFIG_NAME = "config.json"
ALLOWLIST = (DB_NAME, ENV_NAME, CONFIG_NAME)

# .env keys copied into the backup. DELIBERATELY EXCLUDED:
#   - SESSION_SECRET  (regenerate on restore)
#   - CF_API_TOKEN    (re-enter on restore)
#   - TLS_CERT_PATH / TLS_KEY_PATH  (install-managed TLS; backing up the key PATH
#     would reconstruct a config pointing at a private key that is, by policy,
#     never backed up -- this is CCC State Recovery, not Full Node Recovery).
ENV_ALLOWED_KEYS = (
    "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH",
    "APP_PORT", "LOG_LEVEL", "SECURE_COOKIES",
    "CF_ZONE_NAME", "CF_RECORD_NAME",
)

_SNAPSHOT_PREFIX = "ccc-backup-snap-"


@dataclass(frozen=True)
class StagedItem:
    name: str
    data: bytes


@dataclass
class StagingSet:
    items: list = field(default_factory=list)

    def names(self) -> set:
        return {i.name for i in self.items}


def _snapshot_db(db_src: str) -> bytes:
    """Consistent SQLite snapshot with ephemeral sessions purged. The temp file
    is unlinked before returning (success OR error) -- no snapshot is left."""
    fd, snap_path = tempfile.mkstemp(prefix=_SNAPSHOT_PREFIX, suffix=".db")
    os.close(fd)
    try:
        # Normal (read-write) open by the ccc.db owner: WAL-safe (a read-only
        # open of a WAL database can fail because it must touch the -shm file).
        # The online backup() reads the live DB (incl. WAL frames) and does NOT
        # mutate the source; we only back up + close.
        src = sqlite3.connect(db_src)
        try:
            dst = sqlite3.connect(snap_path)
            try:
                src.backup(dst)
                try:
                    dst.execute("DELETE FROM sessions")
                    dst.commit()
                except sqlite3.OperationalError:
                    pass  # no sessions table in this DB
                dst.execute("VACUUM")
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
        with open(snap_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(snap_path)
        except OSError:
            pass


def _redact_env(env_src: str) -> bytes:
    """Parse .env and keep ONLY ENV_ALLOWED_KEYS (drops SESSION_SECRET,
    CF_API_TOKEN, comments, blanks). Never copies the file verbatim."""
    out = []
    with open(env_src, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key = s.split("=", 1)[0].strip()
            if key in ENV_ALLOWED_KEYS:
                out.append(s)
    return ("\n".join(out) + "\n").encode("utf-8")


def collect(ccc_dir: str = CCC_DIR) -> StagingSet:
    """Build the in-memory staging set from the allowlist. Fail-closed: a
    KeyExclusionError on any item propagates and nothing is returned."""
    staged = []

    db_src = os.path.join(ccc_dir, DB_NAME)
    assert_path_allowed(db_src)
    db_bytes = _snapshot_db(db_src)
    scan_content(db_bytes)
    staged.append(StagedItem(DB_NAME, db_bytes))

    env_src = os.path.join(ccc_dir, ENV_NAME)
    assert_path_allowed(env_src)
    env_bytes = _redact_env(env_src)
    scan_content(env_bytes)
    staged.append(StagedItem("env.subset", env_bytes))

    cfg_src = os.path.join(ccc_dir, CONFIG_NAME)
    if os.path.exists(cfg_src):
        assert_path_allowed(cfg_src)
        with open(cfg_src, "rb") as fh:
            cfg_bytes = fh.read()
        scan_content(cfg_bytes)
        staged.append(StagedItem(CONFIG_NAME, cfg_bytes))

    return StagingSet(items=staged)
