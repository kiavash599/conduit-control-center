# SPDX-License-Identifier: MIT
"""
backend/backup/archive.py
-------------------------
Archive layer for Backup & Restore (Epic #4, S2A). Packs an in-memory StagingSet
plus a manifest into deterministic `tar.gz` bytes, and unpacks/verifies them.

S2A scope: tar.gz only (stdlib `tarfile` + `gzip` over `io.BytesIO`). NO
encryption, NO restore, NO API, NO filesystem writes. Unpack reads members into
memory by name (never `extractall`), so there is no path-traversal surface.
"""
from __future__ import annotations

import hashlib
import io
import tarfile

from backend.backup.collector import StagedItem, StagingSet
from backend.backup.manifest import (
    MANIFEST_NAME,
    BackupArchiveError,
    build_manifest,
    parse_manifest,
    serialize_manifest,
)


def _add(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0          # deterministic archives (no wall-clock leakage)
    info.mode = 0o600
    tf.addfile(info, io.BytesIO(data))


def _read_member(tf: tarfile.TarFile, name: str) -> bytes:
    f = tf.extractfile(name)
    if f is None:
        raise BackupArchiveError(f"cannot read member: {name}")
    try:
        return f.read()
    finally:
        f.close()


def pack(staging: StagingSet, app_version: str) -> bytes:
    """Pack a StagingSet into tar.gz bytes with the manifest as the first member."""
    manifest = build_manifest(staging.items, app_version)
    manifest_bytes = serialize_manifest(manifest)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        _add(tf, MANIFEST_NAME, manifest_bytes)
        for item in staging.items:
            _add(tf, item.name, item.data)
    return buf.getvalue()


def read_manifest(blob: bytes) -> dict:
    """Open the archive and return the validated manifest dict (no member
    verification)."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupArchiveError("archive is not a valid tar.gz") from exc
    try:
        if MANIFEST_NAME not in tf.getnames():
            raise BackupArchiveError("archive missing manifest")
        return parse_manifest(_read_member(tf, MANIFEST_NAME))
    finally:
        tf.close()


def unpack(blob: bytes) -> StagingSet:
    """Unpack + verify tar.gz bytes back into a StagingSet. The member set must
    match the manifest exactly (no missing, no extra), and every member's
    SHA-256 + size must match. Any inconsistency raises BackupArchiveError."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupArchiveError("archive is not a valid tar.gz") from exc
    try:
        names = tf.getnames()
        if MANIFEST_NAME not in names:
            raise BackupArchiveError("archive missing manifest")
        manifest = parse_manifest(_read_member(tf, MANIFEST_NAME))

        expected = {entry["name"]: entry for entry in manifest["items"]}
        members = [n for n in names if n != MANIFEST_NAME]

        for name in members:
            if name not in expected:
                raise BackupArchiveError(f"unexpected member: {name}")
        for name in expected:
            if name not in members:
                raise BackupArchiveError(f"missing member: {name}")

        items = []
        for name in members:
            data = _read_member(tf, name)
            entry = expected[name]
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise BackupArchiveError(f"member content hash mismatch: {name}")
            if len(data) != entry["size"]:
                raise BackupArchiveError(f"member size mismatch: {name}")
            items.append(StagedItem(name, data))
        return StagingSet(items=items)
    finally:
        tf.close()
