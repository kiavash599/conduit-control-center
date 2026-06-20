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

from dataclasses import dataclass

from backend._version import APP_VERSION as _DEFAULT_APP_VERSION
from backend.backup.archive import pack, read_manifest, unpack
from backend.backup.collector import CCC_DIR, StagingSet, collect
from backend.backup.crypto import decrypt_archive, encrypt_archive
from backend.backup.exclusion import scan_content


@dataclass
class OpenedBackup:
    """The verified result of open_backup: the staged items + the manifest."""

    staging: StagingSet
    manifest: dict


def create_backup(passphrase, ccc_dir: str = CCC_DIR, app_version: str | None = None) -> bytes:
    """Collect CCC state, archive it, and return the encrypted backup bytes.

    Fail-closed: a key-grade item makes collect() raise KeyExclusionError and no
    backup is produced. Nothing is written to disk; the plaintext tar.gz exists
    only in memory."""
    if app_version is None:
        app_version = _DEFAULT_APP_VERSION
    staging = collect(ccc_dir)                  # S1: allowlist + fail-closed exclusion
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
