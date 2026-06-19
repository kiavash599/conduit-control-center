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
from backend.backup.collector import (
    ALLOWLIST,
    StagedItem,
    StagingSet,
    collect,
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

__all__ = [
    # S1
    "ALLOWLIST", "StagedItem", "StagingSet", "collect",
    "KeyExclusionError", "assert_path_allowed", "scan_content",
    # S2A
    "pack", "unpack", "read_manifest",
    "BackupArchiveError", "EXCLUDED", "FORMAT", "KIND", "MANIFEST_VERSION",
    "build_manifest", "parse_manifest", "serialize_manifest",
]
