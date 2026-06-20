# SPDX-License-Identifier: MIT
"""Backup & Restore package (Epic #4).

S1: staging collector + key-exclusion guard.
S2A: manifest + tar.gz archive (no encryption, no restore, no API).
"""
from backend.backup.archive import (
    pack,
    read_manifest,
    unpack,
)
from backend.backup.archiver import (
    OpenedBackup,
    create_backup,
    open_backup,
)
from backend.backup.collector import (
    ALLOWLIST,
    StagedItem,
    StagingSet,
    collect,
)
from backend.backup.crypto import (
    BackupCryptoError,
    decrypt_archive,
    encrypt_archive,
)
from backend.backup.exclusion import (
    KeyExclusionError,
    assert_path_allowed,
    scan_content,
)
from backend.backup.manifest import (
    BackupArchiveError,
    EXCLUDED,
    FORMAT,
    KIND,
    MANIFEST_VERSION,
    build_manifest,
    parse_manifest,
    serialize_manifest,
)
from backend.backup.restore import (
    RestoreError,
    RestoreResult,
    restore_backup,
)

__all__ = [
    # S1
    "ALLOWLIST", "StagedItem", "StagingSet", "collect",
    "KeyExclusionError", "assert_path_allowed", "scan_content",
    # S2A
    "pack", "unpack", "read_manifest",
    "BackupArchiveError", "EXCLUDED", "FORMAT", "KIND", "MANIFEST_VERSION",
    "build_manifest", "parse_manifest", "serialize_manifest",
    # S2B
    "encrypt_archive", "decrypt_archive", "BackupCryptoError",
    # S2C
    "create_backup", "open_backup", "OpenedBackup",
    # S3
    "restore_backup", "RestoreResult", "RestoreError",
]
