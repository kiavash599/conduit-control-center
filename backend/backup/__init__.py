# SPDX-License-Identifier: MIT
"""Backup & Restore package (Epic #4). S1: staging collector + key-exclusion
guard only -- no archive, encryption, restore, API, or final artifact."""
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

__all__ = [
    "ALLOWLIST",
    "StagedItem",
    "StagingSet",
    "collect",
    "KeyExclusionError",
    "assert_path_allowed",
    "scan_content",
]
