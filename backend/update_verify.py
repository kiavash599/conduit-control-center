# SPDX-License-Identifier: MIT
"""
backend/update_verify.py
------------------------
ADR-0003 Epic B — Trusted Verification Path (device side).

The single, offline, fail-closed verification decision performed by the installed
engine BEFORE the version-increase gate and BEFORE any privilege / namespace
crossing (Baseline Invariants §8.8, §8.13, §8.14). It authenticates a release by
validating the signed manifest against the on-device trust store and binds the
artifact to the manifest by content digest.

Design constraints realised here (frozen — do NOT change):
  * M2 trust store   : a local allowed-signers set (SSH principals).
  * S2 signed object : a signed manifest + content-addressed artifact.
  * Technology       : SSH signatures, Ed25519 (verify via `ssh-keygen -Y verify`).
  * Fail-closed      : any missing/invalid input, missing store, missing tooling,
                       or failed check yields a REJECT — never a default pass.
  * No authority from the payload: the trust store is on-device only; the manifest
    is trusted only AFTER its signature verifies.

Scope boundary: this module performs VERIFICATION only. It never authorises
(version-increase, product-scope acceptance) — that is the separate Authorization
stage owned by the installed engine — and it never deploys. It reads authoritative
metadata ONLY from the verified manifest (§8.9). It is stdlib-only so the
stdlib-only update helper can consume it without pulling heavy dependencies.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

# --- Contract constants (must match release/ccc_release.py) ----------------- #

PRODUCT = "conduit-control-center"
SSHSIG_NAMESPACE = "ccc-update-manifest"
DIGEST_ALGORITHM = "sha256"
PUBLISHER_IDENTITY = "conduit-control-center-publisher"   # allowed-signers principal
SUPPORTED_MANIFEST_FORMATS = frozenset({1})

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# --- Fail-closed reject reason codes (failure taxonomy; IC-11) -------------- #

REASON_VERIFIED = "verified"
REASON_TOOLING = "reject_tooling"         # verification tool unavailable
REASON_STORE = "reject_store"             # trust store missing / empty / unreadable
REASON_SIGNATURE = "reject_signature"     # signature not from a trusted publisher
REASON_MANIFEST = "reject_manifest"       # manifest missing / malformed / unsupported
REASON_DIGEST = "reject_digest"           # artifact content does not match manifest


class VerifyError(Exception):
    """Internal parse/validation failure (mapped to a REJECT reason)."""


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str
    metadata: Optional[dict] = None  # present only when ok is True


# --- Trust store (M2) ------------------------------------------------------- #

def read_trust_store(path: str) -> Optional[list[str]]:
    """Return the non-empty allowed-signers entries, or None (fail-closed).

    None is returned if the store is missing, unreadable, empty, or contains no
    usable (non-comment, non-blank) entry. The caller MUST treat None as a
    REJECT_STORE — verification never proceeds against an absent/empty anchor set.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    entries = [ln.strip() for ln in raw.splitlines()
               if ln.strip() and not ln.lstrip().startswith("#")]
    return entries or None


# --- Signature verification (SSH / Ed25519) --------------------------------- #

def _ssh_available() -> bool:
    return shutil.which("ssh-keygen") is not None


def verify_manifest_signature(
    manifest_path: str,
    signature_path: str,
    trust_store_path: str,
    *,
    identity: str = PUBLISHER_IDENTITY,
    namespace: str = SSHSIG_NAMESPACE,
) -> bool:
    """True iff the manifest's signature verifies against the on-device store.

    Fail-closed: any tooling/IO error, or a non-zero verifier exit, is False.
    The signed bytes are exactly the manifest file's bytes (canonical, per Epic A)."""
    try:
        with open(manifest_path, "rb") as fh:
            data = fh.read()
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["ssh-keygen", "-Y", "verify", "-f", trust_store_path,
             "-I", identity, "-n", namespace, "-s", signature_path],
            input=data, capture_output=True, shell=False,
        )
        return proc.returncode == 0
    except OSError:
        return False


# --- Manifest parsing (only after signature verification) ------------------- #

def parse_verified_manifest(manifest_bytes: bytes) -> dict:
    """Parse and structurally validate a manifest whose signature has ALREADY
    verified. Raises VerifyError on any malformed/unsupported content."""
    try:
        obj = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise VerifyError(f"manifest not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise VerifyError("manifest is not an object")
    if obj.get("format_version") not in SUPPORTED_MANIFEST_FORMATS:
        raise VerifyError(f"unsupported manifest format_version: {obj.get('format_version')!r}")
    if not obj.get("product"):
        raise VerifyError("manifest missing product")
    version = obj.get("version")
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise VerifyError(f"manifest version not semver: {version!r}")
    artifact = obj.get("artifact")
    if not isinstance(artifact, dict):
        raise VerifyError("manifest missing artifact")
    digest = artifact.get("digest")
    if not isinstance(digest, dict) or digest.get("algorithm") != DIGEST_ALGORITHM:
        raise VerifyError(f"unsupported digest algorithm: {digest!r}")
    if not isinstance(digest.get("value"), str) or not digest["value"]:
        raise VerifyError("manifest missing digest value")
    return obj


def content_digest_ok(artifact_bytes: bytes, expected_digest: dict) -> bool:
    """Recompute the artifact digest and compare to the (verified) manifest."""
    if expected_digest.get("algorithm") != DIGEST_ALGORITHM:
        return False
    actual = hashlib.sha256(artifact_bytes).hexdigest()
    return _consteq(actual, str(expected_digest.get("value", "")))


def _consteq(a: str, b: str) -> bool:
    # constant-time comparison of two hex strings
    import hmac
    return hmac.compare_digest(a, b)


# --- Composite verification decision ---------------------------------------- #

def verify_release(
    *,
    manifest_path: str,
    signature_path: str,
    artifact_path: str,
    trust_store_path: str,
) -> VerifyResult:
    """The single, ordered, fail-closed verification decision (IC-2):

        1. trust store present & non-empty                (else REJECT_STORE)
        2. verification tooling present                   (else REJECT_TOOLING)
        3. manifest signature verifies against the store  (else REJECT_SIGNATURE)
        4. parse the now-verified manifest                (else REJECT_MANIFEST)
        5. artifact content digest matches the manifest   (else REJECT_DIGEST)

    On success returns VerifyResult(ok=True, "verified", metadata). The metadata
    (product, version, compatibility, digest, format_version) is authoritative and
    is the ONLY source the caller may use for downstream Authorization. The
    manifest-version <-> artifact-version cross-check is `cross_check_version`,
    applied by the caller after it reads the artifact's own version.
    """
    if read_trust_store(trust_store_path) is None:
        return VerifyResult(False, REASON_STORE)
    if not _ssh_available():
        return VerifyResult(False, REASON_TOOLING)
    if not verify_manifest_signature(manifest_path, signature_path, trust_store_path):
        return VerifyResult(False, REASON_SIGNATURE)
    try:
        with open(manifest_path, "rb") as fh:
            manifest_bytes = fh.read()
        manifest = parse_verified_manifest(manifest_bytes)
        with open(artifact_path, "rb") as fh:
            artifact_bytes = fh.read()
    except (OSError, VerifyError) as exc:
        return VerifyResult(False, REASON_MANIFEST if isinstance(exc, VerifyError) else REASON_STORE)
    if not content_digest_ok(artifact_bytes, manifest["artifact"]["digest"]):
        return VerifyResult(False, REASON_DIGEST)
    metadata = {
        "product": manifest["product"],
        "version": manifest["version"],
        "compatibility": manifest.get("compatibility", {}),
        "digest": manifest["artifact"]["digest"],
        "format_version": manifest["format_version"],
    }
    return VerifyResult(True, REASON_VERIFIED, metadata)


def cross_check_version(metadata: dict, artifact_declared_version: str) -> bool:
    """§8.10 cross-check: the authoritative (verified) manifest version must equal
    the artifact's own declared version. Prevents a signed manifest describing a
    different version than the code that would be deployed."""
    return (
        isinstance(artifact_declared_version, str)
        and _SEMVER_RE.match(artifact_declared_version) is not None
        and metadata.get("version") == artifact_declared_version
    )


def product_scope_ok(metadata: dict, *, product: str = PRODUCT) -> bool:
    """Product identity match (used by Authorization; provided for convenience)."""
    return metadata.get("product") == product
