# SPDX-License-Identifier: MIT
"""Tests for the shared release/oci_manifest.py validator (stdlib-only). Exercises the
Docker schema-2 and OCI single-image shapes, the config.digest == image_id binding, and
every fail-closed rejection (bad descriptors, manifest lists/indexes, foreign media types,
malformed/truncated/non-UTF-8 JSON, and digest/id relationship errors)."""
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

_IMAGE_ID = "sha256:" + "d" * 64
_LAYER = "sha256:" + "a" * 64


def _docker(image_id=_IMAGE_ID, **over):
    doc = {
        "schemaVersion": 2,
        "mediaType": M.DOCKER_MANIFEST_TYPE,
        "config": {"mediaType": M.DOCKER_CONFIG_TYPE, "digest": image_id, "size": 1234},
        "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": _LAYER, "size": 5678}],
    }
    doc.update(over)
    return json.dumps(doc).encode("utf-8")


def _oci(image_id=_IMAGE_ID, **over):
    doc = {
        "schemaVersion": 2,
        "mediaType": M.OCI_MANIFEST_TYPE,
        "config": {"mediaType": M.OCI_CONFIG_TYPE, "digest": image_id, "size": 1234},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": _LAYER, "size": 42}],
    }
    doc.update(over)
    return json.dumps(doc).encode("utf-8")


def _digest(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _ok(raw, image_id=_IMAGE_ID):
    return M.validate_image_manifest(raw, image_manifest_digest=_digest(raw), image_id=image_id)


def test_valid_docker_schema2():
    obj = _ok(_docker())
    assert obj["config"]["digest"] == _IMAGE_ID


def test_valid_oci_image_manifest():
    obj = _ok(_oci())
    assert obj["schemaVersion"] == 2


def test_config_digest_must_equal_image_id():
    raw = _docker(config={"mediaType": M.DOCKER_CONFIG_TYPE, "digest": "sha256:" + "e" * 64, "size": 9})
    with pytest.raises(M.ManifestError):
        _ok(raw)


def test_missing_config_rejected():
    d = json.loads(_docker())
    del d["config"]
    raw = json.dumps(d).encode()
    with pytest.raises(M.ManifestError):
        _ok(raw)


def test_config_bad_digest_rejected():
    raw = _docker(config={"mediaType": M.DOCKER_CONFIG_TYPE, "digest": "sha256:XYZ", "size": 9})
    with pytest.raises(M.ManifestError):
        _ok(raw)


def test_config_wrong_mediatype_rejected():
    # OCI config type inside a Docker manifest -> not the corresponding type.
    raw = _docker(config={"mediaType": M.OCI_CONFIG_TYPE, "digest": _IMAGE_ID, "size": 9})
    with pytest.raises(M.ManifestError):
        _ok(raw)


@pytest.mark.parametrize("size", [0, -1, True, "10", 1.5, None])
def test_config_size_must_be_positive_int(size):
    raw = _docker(config={"mediaType": M.DOCKER_CONFIG_TYPE, "digest": _IMAGE_ID, "size": size})
    with pytest.raises(M.ManifestError):
        _ok(raw)


def test_layers_missing_or_empty_rejected():
    with pytest.raises(M.ManifestError):
        _ok(_docker(layers=[]))
    d = json.loads(_docker())
    del d["layers"]
    with pytest.raises(M.ManifestError):
        _ok(json.dumps(d).encode())


@pytest.mark.parametrize("layer", [
    {"mediaType": "application/x-foreign", "digest": _LAYER, "size": 1},
    {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": "nope", "size": 1},
    {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": _LAYER, "size": 0},
])
def test_invalid_layer_descriptor_rejected(layer):
    with pytest.raises(M.ManifestError):
        _ok(_oci(layers=[layer]))


def test_manifest_list_rejected():
    raw = _docker(mediaType="application/vnd.docker.distribution.manifest.list.v2+json")
    with pytest.raises(M.ManifestError):
        _ok(raw)


def test_oci_index_manifests_key_rejected():
    d = json.loads(_oci())
    d["manifests"] = []
    d.pop("mediaType", None)
    with pytest.raises(M.ManifestError):
        _ok(json.dumps(d).encode())


def test_missing_or_foreign_mediatype_rejected():
    d = json.loads(_docker())
    del d["mediaType"]
    with pytest.raises(M.ManifestError):
        _ok(json.dumps(d).encode())
    with pytest.raises(M.ManifestError):
        _ok(_docker(mediaType="application/json"))


def test_schema_version_must_be_two():
    with pytest.raises(M.ManifestError):
        _ok(_docker(schemaVersion=1))
    with pytest.raises(M.ManifestError):
        _ok(_docker(schemaVersion=True))


def test_malformed_and_truncated_json_rejected():
    with pytest.raises(M.ManifestError):
        _ok(b"not json at all")
    raw = _docker()[:-5]           # truncated
    with pytest.raises(M.ManifestError):
        M.validate_image_manifest(raw, image_manifest_digest=_digest(raw), image_id=_IMAGE_ID)


def test_invalid_utf8_rejected():
    raw = b"\xff\xfe not utf-8"
    with pytest.raises(M.ManifestError):
        M.validate_image_manifest(raw, image_manifest_digest=_digest(raw), image_id=_IMAGE_ID)


def test_digest_mismatch_rejected():
    raw = _docker()
    with pytest.raises(M.ManifestError):
        M.validate_image_manifest(raw, image_manifest_digest="sha256:" + "0" * 64, image_id=_IMAGE_ID)


def test_image_id_must_differ_from_manifest_digest():
    raw = _docker()
    dg = _digest(raw)
    with pytest.raises(M.ManifestError):
        M.validate_image_manifest(raw, image_manifest_digest=dg, image_id=dg)


def test_cli_valid_and_invalid(tmp_path):
    good = tmp_path / "m.json"
    good.write_bytes(_docker())
    assert M.main(["--manifest", str(good), "--image-id", _IMAGE_ID]) == 0
    bad = tmp_path / "b.json"
    bad.write_bytes(_docker(config={"mediaType": M.DOCKER_CONFIG_TYPE,
                                    "digest": "sha256:" + "e" * 64, "size": 9}))
    assert M.main(["--manifest", str(bad), "--image-id", _IMAGE_ID]) == 1


# --- F4: strict JSON parsing (duplicate keys, NaN/Infinity) --- #
def _raw_digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_duplicate_json_key_rejected():
    # a syntactically valid manifest with a DUPLICATE top-level key
    raw = (b'{"schemaVersion":2,"schemaVersion":2,'
           b'"mediaType":"application/vnd.docker.distribution.manifest.v2+json",'
           b'"config":{"mediaType":"application/vnd.docker.container.image.v1+json",'
           b'"digest":"sha256:' + b"d" * 64 + b'","size":1},'
           b'"layers":[{"mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip",'
           b'"digest":"sha256:' + b"a" * 64 + b'","size":1}]}')
    with pytest.raises(M.ManifestError):
        M.validate_image_manifest(raw, image_manifest_digest=_raw_digest(raw), image_id=_IMAGE_ID)


def test_duplicate_nested_json_key_rejected():
    raw = (b'{"schemaVersion":2,'
           b'"mediaType":"application/vnd.docker.distribution.manifest.v2+json",'
           b'"config":{"mediaType":"application/vnd.docker.container.image.v1+json",'
           b'"digest":"sha256:' + b"d" * 64 + b'","size":1,"size":2},'
           b'"layers":[{"mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip",'
           b'"digest":"sha256:' + b"a" * 64 + b'","size":1}]}')
    with pytest.raises(M.ManifestError):
        M.validate_image_manifest(raw, image_manifest_digest=_raw_digest(raw), image_id=_IMAGE_ID)


@pytest.mark.parametrize("const", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_standard_json_constants_rejected(const):
    raw = (b'{"schemaVersion":2,'
           b'"mediaType":"application/vnd.docker.distribution.manifest.v2+json",'
           b'"config":{"mediaType":"application/vnd.docker.container.image.v1+json",'
           b'"digest":"sha256:' + b"d" * 64 + b'","size":' + const + b'},'
           b'"layers":[{"mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip",'
           b'"digest":"sha256:' + b"a" * 64 + b'","size":1}]}')
    with pytest.raises(M.ManifestError):
        M.validate_image_manifest(raw, image_manifest_digest=_raw_digest(raw), image_id=_IMAGE_ID)


def test_strict_json_loads_directly():
    import pytest as _pt
    with _pt.raises(M.ManifestError):
        M.strict_json_loads('{"a":1,"a":2}')
    with _pt.raises(M.ManifestError):
        M.strict_json_loads('{"x": NaN}')
    assert M.strict_json_loads('{"a":1,"b":2}') == {"a": 1, "b": 2}
