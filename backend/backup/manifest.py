# SPDX-License-Identifier: MIT
"""
backend/backup/manifest.py
--------------------------
Backup manifest for Backup & Restore (Epic #4, S2A). The manifest is stored
INSIDE the archive (as manifest.json) and records the format, versions, creation
time, and a per-item SHA-256 + size for integrity verification on unpack.

S2A scope: manifest build / serialize / validate only -- no encryption, no
restore, no filesystem writes. Pure stdlib.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

FORMAT = "ccc-backup"
MANIFEST_VERSION = 1
KIND = "ccc-state"                       # CCC State Recovery, not Full Node Recovery
MANIFEST_NAME = "manifest.json"

# Informational record of what is permanently excluded (locked product policy).
EXCLUDED = (
    "conduit_private_key",
    "ryve_identity",
    "tls_private_key",
    "SESSION_SECRET",
    "CF_API_TOKEN",
)

_REQUIRED_FIELDS = (
    "format", "manifest_version", "app_version",
    "created_utc", "kind", "items", "excluded",
)


class BackupArchiveError(Exception):
    """A backup archive or its manifest is malformed, inconsistent, or a newer
    format than this build supports. The message is generic and operator-safe."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(items, app_version: str, created_utc: str | None = None) -> dict:
    """Build the manifest dict for the given staged items (StagedItem)."""
    if created_utc is None:
        created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "format": FORMAT,
        "manifest_version": MANIFEST_VERSION,
        "app_version": app_version,
        "created_utc": created_utc,
        "kind": KIND,
        "items": [
            {"name": it.name, "sha256": _sha256(it.data), "size": len(it.data)}
            for it in items
        ],
        "excluded": list(EXCLUDED),
    }


def serialize_manifest(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_manifest(data: bytes) -> dict:
    """Parse + validate manifest bytes. Raise BackupArchiveError if malformed or
    a newer manifest_version than this build supports."""
    try:
        m = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupArchiveError("manifest is not valid JSON") from exc
    if not isinstance(m, dict):
        raise BackupArchiveError("manifest is not an object")
    for field in _REQUIRED_FIELDS:
        if field not in m:
            raise BackupArchiveError(f"manifest missing field: {field}")
    if m["format"] != FORMAT:
        raise BackupArchiveError("unexpected backup format")
    mv = m["manifest_version"]
    if not isinstance(mv, int) or isinstance(mv, bool) or mv > MANIFEST_VERSION:
        raise BackupArchiveError("manifest version is newer than supported")
    if not isinstance(m["items"], list):
        raise BackupArchiveError("manifest items must be a list")
    return m
