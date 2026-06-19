# SPDX-License-Identifier: MIT
"""S2A: unit tests for the Backup manifest + tar.gz archive (backend/backup).

Pack/unpack round-trip + manifest schema + per-item SHA-256 + fail-closed
rejection of tampered / mismatched archives. Pure stdlib (tarfile/gzip/json);
no encryption, no restore, no filesystem writes."""
from __future__ import annotations

import io
import tarfile

import pytest

from backend.backup.archive import pack, read_manifest, unpack
from backend.backup.collector import StagedItem, StagingSet
from backend.backup.manifest import (
    MANIFEST_NAME,
    MANIFEST_VERSION,
    BackupArchiveError,
    build_manifest,
    serialize_manifest,
)

_APP = "0.4.0"


def _staging():
    return StagingSet(items=[
        StagedItem("ccc.db", b"\x00sqlite-snapshot\x00"),
        StagedItem("env.subset", b"ADMIN_USERNAME=admin\n"),
        StagedItem("config.json", b'{"traffic": {"collector_enabled": false}}'),
    ])


def _raw_targz(members):
    """Build a tar.gz from (name, bytes) pairs -- used to craft malformed inputs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# 1. round trip
def test_pack_unpack_round_trip():
    ss = _staging()
    out = unpack(pack(ss, _APP))
    assert {(i.name, i.data) for i in out.items} == {(i.name, i.data) for i in ss.items}


# 2. manifest fields exist
def test_manifest_fields_exist():
    m = read_manifest(pack(_staging(), _APP))
    for field in ("format", "manifest_version", "app_version",
                  "created_utc", "kind", "items", "excluded"):
        assert field in m
    assert m["format"] == "ccc-backup"
    assert m["kind"] == "ccc-state"
    assert m["app_version"] == _APP


# 3. sha256 verification (manifest records correct digests)
def test_manifest_sha256_matches_members():
    import hashlib
    ss = _staging()
    m = read_manifest(pack(ss, _APP))
    by_name = {e["name"]: e for e in m["items"]}
    for it in ss.items:
        assert by_name[it.name]["sha256"] == hashlib.sha256(it.data).hexdigest()
        assert by_name[it.name]["size"] == len(it.data)


# 4. reject modified member content
def test_reject_modified_member_content():
    manifest = serialize_manifest(build_manifest([StagedItem("a", b"AAA")], _APP))
    blob = _raw_targz([(MANIFEST_NAME, manifest), ("a", b"BBB")])  # content != manifest hash
    with pytest.raises(BackupArchiveError):
        unpack(blob)


# 5. reject manifest_version newer than supported
def test_reject_manifest_version_too_new():
    m = build_manifest([StagedItem("a", b"AAA")], _APP)
    m["manifest_version"] = MANIFEST_VERSION + 1
    blob = _raw_targz([(MANIFEST_NAME, serialize_manifest(m)), ("a", b"AAA")])
    with pytest.raises(BackupArchiveError):
        unpack(blob)


# 6. reject missing member
def test_reject_missing_member():
    manifest = serialize_manifest(
        build_manifest([StagedItem("a", b"AAA"), StagedItem("b", b"BBB")], _APP)
    )
    blob = _raw_targz([(MANIFEST_NAME, manifest), ("a", b"AAA")])  # "b" missing
    with pytest.raises(BackupArchiveError):
        unpack(blob)


# 7. reject unexpected extra member
def test_reject_unexpected_extra_member():
    manifest = serialize_manifest(build_manifest([StagedItem("a", b"AAA")], _APP))
    blob = _raw_targz([(MANIFEST_NAME, manifest), ("a", b"AAA"), ("b", b"BBB")])  # "b" extra
    with pytest.raises(BackupArchiveError):
        unpack(blob)


# 8. excluded[] list present and non-empty
def test_excluded_list_present():
    m = read_manifest(pack(_staging(), _APP))
    assert isinstance(m["excluded"], list) and m["excluded"]
    for token in ("tls_private_key", "SESSION_SECRET", "CF_API_TOKEN"):
        assert token in m["excluded"]


# 9. archive contains only expected members
def test_archive_contains_only_expected_members():
    ss = _staging()
    blob = pack(ss, _APP)
    tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    try:
        names = set(tf.getnames())
    finally:
        tf.close()
    assert names == {MANIFEST_NAME} | {i.name for i in ss.items}


# extra: a non-tar blob is rejected (not one of the 9, but cheap defense check)
def test_reject_non_targz_blob():
    with pytest.raises(BackupArchiveError):
        unpack(b"not a tar.gz at all")
