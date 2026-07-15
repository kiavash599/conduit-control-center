#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/oci_manifest.py -- ONE shared, standard-library-only validator for a
single-image Docker schema-2 / OCI image manifest. Used identically at all three
trust boundaries (Phase A after `skopeo inspect --raw`, wheelhouse provenance
self-check, and the producer/signing boundary) so the semantic rules are never
duplicated or allowed to drift.

Fail-closed guarantees (see ``validate_image_manifest``): the raw bytes are valid
UTF-8 JSON describing a *single* image manifest (schemaVersion == 2, a supported
single-image mediaType -- manifest lists / OCI indexes are rejected), the config is
a valid descriptor of the corresponding image-config media type with a positive size
and a lowercase sha256 digest that EQUALS the recorded ``image_id``, every layer is a
valid descriptor, ``sha256(raw_bytes) == image_manifest_digest``, and ``image_id`` is
distinct from ``image_manifest_digest``. This binds the signed manifest digest to the
image config id that Phase B actually executes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

DOCKER_MANIFEST_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
OCI_MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
SUPPORTED_MANIFEST_TYPES = (DOCKER_MANIFEST_TYPE, OCI_MANIFEST_TYPE)

DOCKER_CONFIG_TYPE = "application/vnd.docker.container.image.v1+json"
OCI_CONFIG_TYPE = "application/vnd.oci.image.config.v1+json"
# The config media type must CORRESPOND to the manifest media type.
_CONFIG_FOR_MANIFEST = {
    DOCKER_MANIFEST_TYPE: DOCKER_CONFIG_TYPE,
    OCI_MANIFEST_TYPE: OCI_CONFIG_TYPE,
}

# Manifest-list / image-index media types are explicitly rejected (multi-image).
_INDEX_TYPES = (
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
)

_DOCKER_LAYER_TYPES = (
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
    "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip",
)
_OCI_LAYER_TYPES = (
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
    "application/vnd.oci.image.layer.nondistributable.v1.tar",
    "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
    "application/vnd.oci.image.layer.nondistributable.v1.tar+zstd",
)
ALLOWED_LAYER_TYPES = _DOCKER_LAYER_TYPES + _OCI_LAYER_TYPES


class ManifestError(ValueError):
    """Raised on any manifest-shape or binding violation (fail closed)."""


def _reject_duplicate_keys(pairs):
    """object_pairs_hook: reject duplicate keys at ANY nesting depth (fail closed)."""
    seen = set()
    for k, _v in pairs:
        if k in seen:
            raise ManifestError(f"duplicate JSON key {k!r} (fail closed)")
        seen.add(k)
    return dict(pairs)


def _reject_constant(const):
    """parse_constant: reject NaN / Infinity / -Infinity (non-standard JSON)."""
    raise ManifestError(f"non-standard JSON constant {const!r} rejected")


def strict_json_loads(text: str):
    """The ONE strict JSON parser shared by every manifest boundary. Rejects duplicate
    object keys (any depth) and NaN/Infinity/-Infinity; surfaces malformed JSON as a
    ManifestError."""
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_constant)
    except ManifestError:
        raise
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc


def is_sha256_digest(v: object) -> bool:
    return isinstance(v, str) and bool(_SHA256_RE.match(v))


def _check_descriptor(desc: object, *, allowed_types, what: str) -> str:
    if not isinstance(desc, dict):
        raise ManifestError(f"{what} must be an object")
    mt = desc.get("mediaType")
    if mt not in allowed_types:
        raise ManifestError(f"{what} mediaType {mt!r} is not an allowed type")
    dig = desc.get("digest")
    if not is_sha256_digest(dig):
        raise ManifestError(f"{what} digest must be a lowercase sha256 OCI digest")
    size = desc.get("size")
    # bool is a subclass of int -> exclude it explicitly.
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ManifestError(f"{what} size must be a positive integer")
    return dig


def validate_image_manifest(raw_bytes: bytes, *, image_manifest_digest: str,
                            image_id: str) -> dict:
    """Validate a single-image manifest and its binding to ``image_id``.

    Returns the parsed manifest object on success; raises ``ManifestError`` otherwise.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise ManifestError("raw manifest must be bytes")
    if not is_sha256_digest(image_manifest_digest):
        raise ManifestError("image_manifest_digest must be a lowercase sha256 OCI digest")
    if not is_sha256_digest(image_id):
        raise ManifestError("image_id must be a lowercase sha256 OCI digest")
    if image_id == image_manifest_digest:
        raise ManifestError("image_id must be distinct from image_manifest_digest")
    if "sha256:" + hashlib.sha256(bytes(raw_bytes)).hexdigest() != image_manifest_digest:
        raise ManifestError("sha256(raw manifest bytes) != image_manifest_digest")
    try:
        text = bytes(raw_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"manifest bytes are not valid UTF-8: {exc}") from exc
    obj = strict_json_loads(text)
    if not isinstance(obj, dict):
        raise ManifestError("manifest top-level must be a JSON object")
    mt = obj.get("mediaType")
    if mt in _INDEX_TYPES or "manifests" in obj:
        raise ManifestError("manifest list / OCI image index rejected (single image required)")
    if mt not in SUPPORTED_MANIFEST_TYPES:
        raise ManifestError(f"unsupported/foreign manifest mediaType {mt!r}")
    if obj.get("schemaVersion") != 2 or isinstance(obj.get("schemaVersion"), bool):
        raise ManifestError("schemaVersion must be exactly 2")
    config = obj.get("config")
    cfg_digest = _check_descriptor(config, allowed_types=(_CONFIG_FOR_MANIFEST[mt],),
                                   what="config")
    if cfg_digest != image_id:
        raise ManifestError("config.digest != image_id (manifest not bound to executed image)")
    layers = obj.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ManifestError("layers must be a non-empty list")
    for i, layer in enumerate(layers):
        _check_descriptor(layer, allowed_types=ALLOWED_LAYER_TYPES, what=f"layer[{i}]")
    return obj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oci_manifest.py",
                                 description="Validate a single-image OCI/Docker manifest "
                                             "and bind config.digest == image_id.")
    ap.add_argument("--manifest", required=True, help="path to the raw OCI/Docker manifest bytes")
    ap.add_argument("--image-id", required=True, help="Docker local image/config id (sha256:...)")
    ap.add_argument("--expect-manifest-digest",
                    help="optional: cross-check the recorded image_manifest_digest")
    a = ap.parse_args(argv)
    try:
        with open(a.manifest, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        sys.stderr.write(f"ERROR: cannot read manifest: {exc}\n")
        return 2
    computed = "sha256:" + hashlib.sha256(raw).hexdigest()
    expect = a.expect_manifest_digest or computed
    try:
        validate_image_manifest(raw, image_manifest_digest=expect, image_id=a.image_id)
    except ManifestError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    sys.stdout.write(computed + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
