#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/oci_manifest.py -- ONE shared, standard-library-only validator that binds a
captured OCI/Docker image manifest to the Docker RUNTIME image id the ceremony executes.

Store-agnostic identity model (empirically required by Docker 29's containerd image store,
where `docker inspect .Id` is the MANIFEST digest, not the config-blob digest):

  * runtime_image_id     -- `docker inspect .Id`; what `docker run` executes (store-dependent).
  * image_manifest_digest-- sha256 of the captured raw manifest/index bytes.
  * image_config_digest  -- the manifest's config descriptor digest (single-image only).
  * image_identity_mode  -- WHICH fail-closed relationship binds runtime to manifest:
        "containerd": runtime_image_id == image_manifest_digest
                      (config digest present + validated, but not the runtime id);
        "legacy":     runtime_image_id == image_config_digest
                      AND runtime_image_id != image_manifest_digest;
        "index":      runtime_image_id == image_manifest_digest for a multi-image OCI index /
                      manifest list -- accepted ONLY in the pre-build smoke test (allow_index),
                      never for the final builder image.

`validate_capture` derives the mode from which relationship actually holds. The two single-image
relationships are mutually exclusive (a config blob cannot equal its own manifest, and the
runtime id can match only one), so derivation is unambiguous; if NEITHER holds it fails closed.
An `expected_mode` may be supplied to reject mode confusion (a recorded mode that disagrees with
the derived one). Strict JSON (duplicate keys / NaN / Infinity rejected), UTF-8, schemaVersion,
media types, and descriptor/layer shapes are all fail-closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import re

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

DOCKER_MANIFEST_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
OCI_MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
SUPPORTED_MANIFEST_TYPES = (DOCKER_MANIFEST_TYPE, OCI_MANIFEST_TYPE)

DOCKER_CONFIG_TYPE = "application/vnd.docker.container.image.v1+json"
OCI_CONFIG_TYPE = "application/vnd.oci.image.config.v1+json"
_CONFIG_FOR_MANIFEST = {
    DOCKER_MANIFEST_TYPE: DOCKER_CONFIG_TYPE,
    OCI_MANIFEST_TYPE: OCI_CONFIG_TYPE,
}

DOCKER_INDEX_TYPE = "application/vnd.docker.distribution.manifest.list.v2+json"
OCI_INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
INDEX_TYPES = (DOCKER_INDEX_TYPE, OCI_INDEX_TYPE)

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

MODE_CONTAINERD = "containerd"
MODE_LEGACY = "legacy"
MODE_INDEX = "index"


class ManifestError(ValueError):
    """Raised on any manifest-shape or identity-binding violation (fail closed)."""


def _reject_duplicate_keys(pairs):
    seen = set()
    for k, _v in pairs:
        if k in seen:
            raise ManifestError(f"duplicate JSON key {k!r} (fail closed)")
        seen.add(k)
    return dict(pairs)


def _reject_constant(const):
    raise ManifestError(f"non-standard JSON constant {const!r} rejected")


def strict_json_loads(text: str):
    """The ONE strict JSON parser: rejects duplicate object keys (any depth) and
    NaN/Infinity/-Infinity; surfaces malformed JSON as a ManifestError."""
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
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ManifestError(f"{what} size must be a positive integer")
    return dig


def _derive_identity_mode(*, manifest_match: bool, config_match: bool) -> str:
    """Mechanically enforce the settled 'exactly one relationship' contract from two independent
    boolean comparisons. Returns the mode for exactly one match; fails closed for BOTH (ambiguous)
    or NEITHER (unbound) -- never silently prefers a branch. The both-match case is astronomically
    improbable (it needs sha256(manifest) == config.digest), but is rejected by construction so the
    validator never depends on that unenforced invariant."""
    if manifest_match and config_match:
        raise ManifestError("ambiguous runtime identity: runtime_image_id matches BOTH the "
                            "manifest digest and the config digest")
    if manifest_match:
        return MODE_CONTAINERD
    if config_match:
        return MODE_LEGACY
    raise ManifestError("no identity relationship holds: runtime_image_id equals neither the "
                        "manifest digest (containerd) nor the config digest (legacy)")


def validate_capture(raw_bytes: bytes, *, runtime_image_id: str, allow_index: bool = False,
                     expected_mode: str = None) -> dict:
    """Validate captured manifest bytes and bind them to ``runtime_image_id``.

    Returns a dict {is_index, media_type, manifest_digest, config_digest, identity_mode,
    runtime_image_id}. Raises ManifestError on any violation (fail closed). ``allow_index``
    permits a multi-image index (pre-build smoke only). ``expected_mode``, if given, must equal
    the derived mode (rejects mode confusion)."""
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise ManifestError("raw manifest must be bytes")
    if not is_sha256_digest(runtime_image_id):
        raise ManifestError("runtime_image_id must be a lowercase sha256 OCI digest")
    manifest_digest = "sha256:" + hashlib.sha256(bytes(raw_bytes)).hexdigest()
    try:
        text = bytes(raw_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"manifest bytes are not valid UTF-8: {exc}") from exc
    obj = strict_json_loads(text)
    if not isinstance(obj, dict):
        raise ManifestError("manifest top-level must be a JSON object")
    if obj.get("schemaVersion") != 2 or isinstance(obj.get("schemaVersion"), bool):
        raise ManifestError("schemaVersion must be exactly 2")
    mt = obj.get("mediaType")
    is_index = mt in INDEX_TYPES or "manifests" in obj

    if is_index:
        if not allow_index:
            raise ManifestError("multi-image index / manifest list rejected (single-image "
                                "manifest required for the builder image)")
        if mt not in INDEX_TYPES:
            raise ManifestError(f"unsupported/foreign index mediaType {mt!r}")
        manifests = obj.get("manifests")
        if not isinstance(manifests, list) or not manifests:
            raise ManifestError("index.manifests must be a non-empty list")
        for i, m in enumerate(manifests):
            _check_descriptor(m, allowed_types=SUPPORTED_MANIFEST_TYPES,
                              what=f"index.manifests[{i}]")
        # Index-digest binding: the runtime id must be the index's own digest.
        if runtime_image_id != manifest_digest:
            raise ManifestError("runtime_image_id != index digest (index not bound to runtime)")
        result = {"is_index": True, "media_type": mt, "manifest_digest": manifest_digest,
                  "config_digest": None, "identity_mode": MODE_INDEX,
                  "runtime_image_id": runtime_image_id}
    else:
        if mt not in SUPPORTED_MANIFEST_TYPES:
            raise ManifestError(f"unsupported/foreign manifest mediaType {mt!r}")
        cfg_digest = _check_descriptor(obj.get("config"),
                                       allowed_types=(_CONFIG_FOR_MANIFEST[mt],), what="config")
        layers = obj.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ManifestError("layers must be a non-empty list")
        for i, layer in enumerate(layers):
            _check_descriptor(layer, allowed_types=ALLOWED_LAYER_TYPES, what=f"layer[{i}]")
        # Enforce the 'exactly one relationship' contract: compute both matches independently and
        # let the decision helper reject BOTH (ambiguous) or NEITHER (unbound). Fail closed.
        mode = _derive_identity_mode(
            manifest_match=(runtime_image_id == manifest_digest),
            config_match=(runtime_image_id == cfg_digest))
        result = {"is_index": False, "media_type": mt, "manifest_digest": manifest_digest,
                  "config_digest": cfg_digest, "identity_mode": mode,
                  "runtime_image_id": runtime_image_id}

    if expected_mode is not None and result["identity_mode"] != expected_mode:
        raise ManifestError(f"identity mode mismatch: derived {result['identity_mode']!r} "
                            f"!= expected {expected_mode!r}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="oci_manifest.py",
        description="Validate a captured OCI/Docker manifest and bind it to the Docker runtime "
                    "image id (store-agnostic: containerd / legacy / index).")
    ap.add_argument("--manifest", required=True, help="path to the raw manifest/index bytes")
    ap.add_argument("--runtime-image-id", required=True, help="docker inspect .Id (sha256:...)")
    ap.add_argument("--allow-index", action="store_true",
                    help="permit a multi-image index (pre-build smoke ONLY)")
    ap.add_argument("--expect-mode", choices=(MODE_CONTAINERD, MODE_LEGACY, MODE_INDEX),
                    help="reject mode confusion: derived mode must equal this")
    a = ap.parse_args(argv)
    try:
        with open(a.manifest, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        sys.stderr.write(f"ERROR: cannot read manifest: {exc}\n")
        return 2
    try:
        r = validate_capture(raw, runtime_image_id=a.runtime_image_id,
                             allow_index=a.allow_index, expected_mode=a.expect_mode)
    except ManifestError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    sys.stdout.write("IDENTITY_MODE=%s\n" % r["identity_mode"])
    sys.stdout.write("MANIFEST_DIGEST=%s\n" % r["manifest_digest"])
    sys.stdout.write("CONFIG_DIGEST=%s\n" % (r["config_digest"] or ""))
    sys.stdout.write("MEDIA_TYPE=%s\n" % r["media_type"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
