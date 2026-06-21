# SPDX-License-Identifier: MIT
"""
backend/backup/archiver.py
--------------------------
Backup orchestration for Backup & Restore (Epic #4, S2C). Composes the existing
pieces -- NO restore-to-disk, NO API, NO UI:

  create_backup: collect (S1) -> pack (S2A) -> encrypt_archive (S2B)
  open_backup:   decrypt_archive (S2B) -> unpack (S2A) -> re-scan (S1)

Everything is in memory. The plaintext tar.gz never touches disk; the only
on-disk artifact anywhere is S1's transient SQLite snapshot, created + unlinked
inside collect(). The passphrase is passed through transiently and is never
logged, stored, or written to disk. The three existing typed errors propagate
unchanged (KeyExclusionError / BackupArchiveError / BackupCryptoError); no new
error wrapper is introduced.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from backend._version import APP_VERSION as _DEFAULT_APP_VERSION
from backend.backup.archive import pack, read_manifest, unpack
from backend.backup.collector import CCC_DIR, StagedItem, StagingSet, collect
from backend.backup.crypto import decrypt_archive, encrypt_archive
from backend.backup.exclusion import scan_content

# S4B-2.6: logical, synthetic backup item carrying the applied Conduit operator
# settings. ALWAYS present in a new backup (configured true/false); absence on
# restore therefore means a legacy (pre-2.6) backup. Never read from disk.
CONDUIT_SETTINGS_NAME = "conduit_settings.json"


@dataclass
class OpenedBackup:
    """The verified result of open_backup: the staged items + the manifest."""

    staging: StagingSet
    manifest: dict


def create_backup(passphrase, ccc_dir: str = CCC_DIR, app_version: str | None = None,
                  conduit_settings: dict | None = None) -> bytes:
    """Collect CCC state, archive it, and return the encrypted backup bytes.

    Fail-closed: a key-grade item makes collect() raise KeyExclusionError and no
    backup is produced. Nothing is written to disk; the plaintext tar.gz exists
    only in memory.

    `conduit_settings` (S4B-2.6) is a synthetic, non-secret dict captured by the
    async caller from the configured systemd environment. It is ALWAYS added as
    conduit_settings.json; a None/missing value defaults to {"schema": 1,
    "configured": False} so every new backup contains the item."""
    if app_version is None:
        app_version = _DEFAULT_APP_VERSION
    if conduit_settings is None:
        conduit_settings = {"schema": 1, "configured": False}
    staging = collect(ccc_dir)                  # S1: allowlist + fail-closed exclusion
    # Append the synthetic Conduit-settings item (not a filesystem source).
    staging.items.append(
        StagedItem(CONDUIT_SETTINGS_NAME,
                   json.dumps(conduit_settings).encode("utf-8"))
    )
    plain = pack(staging, app_version)          # S2A: tar.gz (in memory)
    return encrypt_archive(plain, passphrase)   # S2B: encrypted envelope


def open_backup(blob, passphrase) -> OpenedBackup:
    """Decrypt + unpack + re-scan a backup blob into an OpenedBackup.

    Raises BackupCryptoError (wrong password / corruption / tamper),
    BackupArchiveError (malformed archive/manifest), or KeyExclusionError (the
    re-scan found key-grade content). Nothing is written to disk."""
    plain = decrypt_archive(blob, passphrase)   # S2B: integrity + authenticity
    manifest = read_manifest(plain)             # S2A: validated manifest dict
    staging = unpack(plain)                     # S2A: members + per-item SHA-256
    for item in staging.items:                  # S1 re-scan: defense-in-depth
        scan_content(item.data)                 # raises KeyExclusionError on key-grade
    return OpenedBackup(staging=staging, manifest=manifest)
