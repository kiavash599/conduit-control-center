# SPDX-License-Identifier: MIT
"""Tests for the shared release/oci_manifest.py store-agnostic identity validator (stdlib-only).

Covers the two single-image identity modes (containerd: runtime_image_id == manifest digest;
legacy: runtime_image_id == config digest and != manifest digest), the fail-closed "neither
relationship" case, mode-confusion rejection (expected_mode), the index-aware smoke path
(allow_index), and every shape rejection (bad descriptors, wrong config mediaType, foreign media
types, schemaVersion, duplicate keys, NaN/Infinity, non-UTF-8, malformed JSON)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("oci_manifest", _ROOT / "release" / "oci_manifest.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

_LAYER = "sha256:" + "a" * 64
_CONFIG = "sha256:" + "c" * 64


def _dig(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _docker(config_digest=_CONFIG, **over):
    doc = {
        "schemaVersion": 2,
        "mediaType": M.DOCKER_MANIFEST_TYPE,
        "config": {"mediaType": M.DOCKER_CONFIG_TYPE, "digest": config_digest, "size": 1234},
        "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": _LAYER, "size": 5678}],
    }
    doc.update(over)
    return json.dumps(doc).encode("utf-8")


def _oci(config_digest=_CONFIG, **over):
    doc = {
        "schemaVersion": 2,
        "mediaType": M.OCI_MANIFEST_TYPE,
        "config": {"mediaType": M.OCI_CONFIG_TYPE, "digest": config_digest, "size": 1234},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": _LAYER, "size": 42}],
    }
    doc.update(over)
    return json.dumps(doc).encode("utf-8")


def _index(**over):
    doc = {
        "schemaVersion": 2,
        "mediaType": M.OCI_INDEX_TYPE,
        "manifests": [{"mediaType": M.OCI_MANIFEST_TYPE, "digest": "sha256:" + "b" * 64, "size": 100}],
    }
    doc.update(over)
    return json.dumps(doc).encode("utf-8")


# --------------------------------------------------------------------------- #
#  Single-image identity modes                                                #
# --------------------------------------------------------------------------- #
def test_containerd_mode_runtime_equals_manifest_digest():
    raw = _docker()
    r = M.validate_capture(raw, runtime_image_id=_dig(raw))
    assert r["identity_mode"] == M.MODE_CONTAINERD
    assert r["manifest_digest"] == _dig(raw) and r["config_digest"] == _CONFIG
    assert not r["is_index"]


def test_containerd_mode_oci_manifest():
    raw = _oci()
    r = M.validate_capture(raw, runtime_image_id=_dig(raw))
    assert r["identity_mode"] == M.MODE_CONTAINERD and r["media_type"] == M.OCI_MANIFEST_TYPE


def test_legacy_mode_runtime_equals_config_digest():
    raw = _docker(config_digest="sha256:" + "e" * 64)
    r = M.validate_capture(raw, runtime_image_id="sha256:" + "e" * 64)
    assert r["identity_mode"] == M.MODE_LEGACY
    assert r["config_digest"] == "sha256:" + "e" * 64
    assert r["manifest_digest"] != r["runtime_image_id"]


def test_neither_relationship_fails_closed():
    raw = _docker()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id="sha256:" + "7" * 64)   # != manifest, != config


def test_derive_identity_mode_four_cases():
    # the settled 'exactly one relationship' contract, tested at the pure decision seam so the
    # (collision-only) both-match case is reachable without forging digests.
    assert M._derive_identity_mode(manifest_match=True, config_match=False) == M.MODE_CONTAINERD
    assert M._derive_identity_mode(manifest_match=False, config_match=True) == M.MODE_LEGACY
    with pytest.raises(M.ManifestError):                     # neither -> unbound
        M._derive_identity_mode(manifest_match=False, config_match=False)
    with pytest.raises(M.ManifestError):                     # both -> ambiguous (fail closed)
        M._derive_identity_mode(manifest_match=True, config_match=True)


def test_validate_capture_wires_matches_into_decision(monkeypatch):
    # the seam cannot drift from the real inputs: validate_capture must feed the two actual digest
    # comparisons into the decision helper.
    seen = {}
    _orig = M._derive_identity_mode

    def _spy(*, manifest_match, config_match):
        seen["manifest_match"] = manifest_match
        seen["config_match"] = config_match
        return _orig(manifest_match=manifest_match, config_match=config_match)
    monkeypatch.setattr(M, "_derive_identity_mode", _spy)
    raw = _docker()                                          # containerd: runtime == manifest digest
    M.validate_capture(raw, runtime_image_id=_dig(raw))
    assert seen == {"manifest_match": True, "config_match": False}


def test_expected_mode_confusion_rejected():
    raw = _docker()
    # actually containerd, but caller declares legacy
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw), expected_mode=M.MODE_LEGACY)
    # actually legacy, but caller declares containerd
    raw2 = _docker(config_digest="sha256:" + "e" * 64)
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw2, runtime_image_id="sha256:" + "e" * 64, expected_mode=M.MODE_CONTAINERD)


def test_expected_mode_match_accepted():
    raw = _docker()
    M.validate_capture(raw, runtime_image_id=_dig(raw), expected_mode=M.MODE_CONTAINERD)


# --------------------------------------------------------------------------- #
#  OCI index (smoke) vs single-image (build)                                   #
# --------------------------------------------------------------------------- #
def test_index_rejected_without_allow_index():
    raw = _index()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))            # build path: single-image only


def test_index_accepted_in_smoke_bound_to_digest():
    raw = _index()
    r = M.validate_capture(raw, runtime_image_id=_dig(raw), allow_index=True)
    assert r["is_index"] and r["identity_mode"] == M.MODE_INDEX and r["config_digest"] is None


def test_index_runtime_id_must_equal_index_digest():
    raw = _index()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id="sha256:" + "9" * 64, allow_index=True)


def test_index_with_manifests_key_but_manifest_mediatype_rejected():
    # "manifests" present -> treated as index even if mediaType is a manifest type
    raw = json.dumps({"schemaVersion": 2, "mediaType": M.OCI_MANIFEST_TYPE,
                      "manifests": [{"mediaType": M.OCI_MANIFEST_TYPE,
                                     "digest": "sha256:" + "b" * 64, "size": 1}]}).encode()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))            # rejected (build path)


# --------------------------------------------------------------------------- #
#  Shape / descriptor rejections                                              #
# --------------------------------------------------------------------------- #
def test_runtime_image_id_must_be_sha256():
    raw = _docker()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id="not-a-digest")


def test_missing_config_rejected():
    d = json.loads(_docker())
    del d["config"]
    raw = json.dumps(d).encode()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))


def test_wrong_config_mediatype_rejected():
    raw = _docker(config={"mediaType": M.OCI_CONFIG_TYPE, "digest": _CONFIG, "size": 9})
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))


@pytest.mark.parametrize("size", [0, -1, True, "10", 1.5, None])
def test_config_size_must_be_positive_int(size):
    raw = _docker(config={"mediaType": M.DOCKER_CONFIG_TYPE, "digest": _CONFIG, "size": size})
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))


def test_layers_missing_or_empty_rejected():
    with pytest.raises(M.ManifestError):
        M.validate_capture(_docker(layers=[]), runtime_image_id=_dig(_docker(layers=[])))
    d = json.loads(_docker())
    del d["layers"]
    raw = json.dumps(d).encode()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))


@pytest.mark.parametrize("layer", [
    {"mediaType": "application/x-foreign", "digest": _LAYER, "size": 1},
    {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": "nope", "size": 1},
    {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": _LAYER, "size": 0},
])
def test_invalid_layer_descriptor_rejected(layer):
    raw = _oci(layers=[layer])
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))


def test_foreign_or_missing_manifest_mediatype_rejected():
    d = json.loads(_docker())
    del d["mediaType"]
    raw = json.dumps(d).encode()
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))
    raw2 = _docker(mediaType="application/json")
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw2, runtime_image_id=_dig(raw2))


def test_schema_version_must_be_two():
    for raw in (_docker(schemaVersion=1), _docker(schemaVersion=True)):
        with pytest.raises(M.ManifestError):
            M.validate_capture(raw, runtime_image_id=_dig(raw))


def test_duplicate_json_key_rejected():
    raw = (b'{"schemaVersion":2,"schemaVersion":2,'
           b'"mediaType":"application/vnd.docker.distribution.manifest.v2+json",'
           b'"config":{"mediaType":"application/vnd.docker.container.image.v1+json",'
           b'"digest":"sha256:' + b"c" * 64 + b'","size":1},'
           b'"layers":[{"mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip",'
           b'"digest":"sha256:' + b"a" * 64 + b'","size":1}]}')
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))


@pytest.mark.parametrize("const", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_standard_json_constants_rejected(const):
    raw = (b'{"schemaVersion":2,'
           b'"mediaType":"application/vnd.docker.distribution.manifest.v2+json",'
           b'"config":{"mediaType":"application/vnd.docker.container.image.v1+json",'
           b'"digest":"sha256:' + b"c" * 64 + b'","size":' + const + b'},'
           b'"layers":[{"mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip",'
           b'"digest":"sha256:' + b"a" * 64 + b'","size":1}]}')
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))


def test_malformed_truncated_and_non_utf8_rejected():
    with pytest.raises(M.ManifestError):
        M.validate_capture(b"not json", runtime_image_id=_dig(b"not json"))
    raw = _docker()[:-5]
    with pytest.raises(M.ManifestError):
        M.validate_capture(raw, runtime_image_id=_dig(raw))
    bad = b"\xff\xfe not utf-8"
    with pytest.raises(M.ManifestError):
        M.validate_capture(bad, runtime_image_id=_dig(bad))


# --------------------------------------------------------------------------- #
#  CLI                                                                         #
# --------------------------------------------------------------------------- #
def test_cli_containerd_and_index_and_mode_mismatch(tmp_path):
    m = tmp_path / "m.json"
    raw = _docker()
    m.write_bytes(raw)
    assert M.main(["--manifest", str(m), "--runtime-image-id", _dig(raw)]) == 0
    # mode mismatch -> exit 1
    assert M.main(["--manifest", str(m), "--runtime-image-id", _dig(raw),
                   "--expect-mode", M.MODE_LEGACY]) == 1
    # index needs --allow-index
    idx = tmp_path / "i.json"
    iraw = _index()
    idx.write_bytes(iraw)
    assert M.main(["--manifest", str(idx), "--runtime-image-id", _dig(iraw)]) == 1
    assert M.main(["--manifest", str(idx), "--runtime-image-id", _dig(iraw), "--allow-index"]) == 0
