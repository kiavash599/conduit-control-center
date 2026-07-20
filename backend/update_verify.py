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
# FORMAT-3-ONLY: the platform-artifact manifest with a single, compressor-independent wheelhouse
# identity (Logical Tree Digest v1). Earlier formats are intentionally NOT accepted -- supporting
# them would keep a platform-unbound or gzip-derived-identity shape valid and create a
# format-downgrade / platform-gate bypass vector. There are no external users (the RPi2/RPi4 are
# Owner-controlled and upgrade over SSH), so the trusted surface is narrowed to {3}.
SUPPORTED_MANIFEST_FORMATS = frozenset({3})   # V3: single logical-tree wheelhouse identity
SUPPORTED_TREE_SCHEMES = frozenset({"ccc-logical-tree-v1"})   # exact allowlist; else fail closed

# Canonical host-platform tokens (raw `uname -m`); the manifest's per-artifact
# `platform` field uses exactly these strings, removing any producer<->device
# mapping ambiguity. armv6 (Pi Zero / Pi 1) is intentionally unsupported.
SUPPORTED_PLATFORMS = frozenset({"aarch64", "armv7l"})

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

# --- Fail-closed reject reason codes (failure taxonomy; IC-11) -------------- #

REASON_VERIFIED = "verified"
REASON_TOOLING = "reject_tooling"         # verification tool unavailable
REASON_STORE = "reject_store"             # trust store missing / empty / unreadable
REASON_SIGNATURE = "reject_signature"     # signature not from a trusted publisher
REASON_MANIFEST = "reject_manifest"       # manifest missing / malformed / unsupported
REASON_DIGEST = "reject_digest"           # artifact content does not match manifest
REASON_PLATFORM = "reject_platform"       # manifest has no signed entry for this host

# --- E1.1 additive outcome codes (authorization / cross-check / deploy / operational) --- #
# Registered for Phase B audit consumption; no producer emits these yet.
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECT_PRODUCT_SCOPE = "reject_product_scope"
OUTCOME_REJECT_NON_UPGRADE = "reject_non_upgrade"
OUTCOME_REJECT_VERSION_MISMATCH = "reject_version_mismatch"
OUTCOME_REJECT_FRAME = "reject_frame"
OUTCOME_REJECT_PAYLOAD_SHAPE = "reject_payload_shape"
OUTCOME_LAUNCH_FAILED = "launch_failed"
OUTCOME_APPLIED = "applied"
OUTCOME_REVERTED = "reverted"
OUTCOME_APPLY_FAILED = "apply_failed"


# --- Outcome Taxonomy Registry (Phase E1) ----------------------------------- #
#
# The single canonical enumeration of update outcome classes. Taxonomy CUSTODY
# lives here (the registry + its version); outcome PRODUCTION stays with the
# stages that emit a class (verify_release below emits the verification codes via
# the REASON_* constants). Every observable code MUST exist in this registry; no
# component may invent a code at runtime. Evolution is additive-only under design
# review, bumping TAXONOMY_VERSION. Authorization / cross-check / deploy /
# operational classes are intentionally NOT defined yet (added by their owning
# phases). This registry carries no wording — only stable message KEYS (IC-11).
#
# NOTE: TAXONOMY_VERSION is unrelated to the manifest SUPPORTED_MANIFEST_FORMATS
# (format_version); they version different things and must not be conflated.

TAXONOMY_VERSION = 3   # +reject_platform (V2 platform-artifact authorization)

OUTCOME_STAGES = frozenset({"verify", "cross-check", "authorize", "deploy", "operational"})
OUTCOME_CATEGORIES = frozenset({
    "success", "trust-integrity", "readiness", "authorization-informational", "operational",
})
OUTCOME_RECOVERABILITY = frozenset({
    "none", "permanent-for-artifact", "recoverable", "informational",
})


@dataclass(frozen=True)
class OutcomeClass:
    """One immutable taxonomy entry. `code` is the stable machine identity (the
    same string emitted in VerifyResult.reason); `message_key` is a stable
    identifier for the presentation layer — never human wording."""
    code: str
    stage: str
    category: str
    recoverability: str
    message_key: str


# Built from the REASON_* constants so each code string is defined exactly once.
_OUTCOME_REGISTRY = {
    entry.code: entry
    for entry in (
        OutcomeClass(REASON_VERIFIED,  "verify", "success",         "none",                   "update.verify.verified"),
        OutcomeClass(REASON_STORE,     "verify", "readiness",       "recoverable",            "update.verify.store"),
        OutcomeClass(REASON_TOOLING,   "verify", "readiness",       "recoverable",            "update.verify.tooling"),
        OutcomeClass(REASON_SIGNATURE, "verify", "trust-integrity", "permanent-for-artifact", "update.verify.signature"),
        OutcomeClass(REASON_MANIFEST,  "verify", "trust-integrity", "permanent-for-artifact", "update.verify.manifest"),
        OutcomeClass(REASON_DIGEST,    "verify", "trust-integrity", "permanent-for-artifact", "update.verify.digest"),
        OutcomeClass(REASON_PLATFORM,  "verify", "trust-integrity", "permanent-for-artifact", "update.verify.platform"),
        # --- E1.1 additive codes (authorization / cross-check / deploy / operational) ---
        OutcomeClass(OUTCOME_ACCEPTED,                "authorize",   "authorization-informational", "informational",          "update.authorize.accepted"),
        OutcomeClass(OUTCOME_REJECT_PRODUCT_SCOPE,    "authorize",   "authorization-informational", "informational",          "update.authorize.product_scope"),
        OutcomeClass(OUTCOME_REJECT_NON_UPGRADE,      "authorize",   "authorization-informational", "informational",          "update.authorize.non_upgrade"),
        OutcomeClass(OUTCOME_REJECT_VERSION_MISMATCH, "cross-check", "trust-integrity",             "permanent-for-artifact", "update.crosscheck.version_mismatch"),
        OutcomeClass(OUTCOME_REJECT_FRAME,            "operational", "operational",                 "recoverable",            "update.operational.frame"),
        OutcomeClass(OUTCOME_REJECT_PAYLOAD_SHAPE,    "deploy",      "operational",                 "permanent-for-artifact", "update.deploy.payload_shape"),
        OutcomeClass(OUTCOME_LAUNCH_FAILED,           "deploy",      "operational",                 "recoverable",            "update.deploy.launch_failed"),
        OutcomeClass(OUTCOME_APPLIED,                 "deploy",      "success",                     "none",                   "update.deploy.applied"),
        OutcomeClass(OUTCOME_REVERTED,                "deploy",      "operational",                 "recoverable",            "update.deploy.reverted"),
        OutcomeClass(OUTCOME_APPLY_FAILED,            "deploy",      "operational",                 "recoverable",            "update.deploy.apply_failed"),
    )
}

# Fail-safe sentinel for an unrecognised code (never raise): consumers render a
# safe generic outcome rather than crashing or implying success.
UNKNOWN_OUTCOME = OutcomeClass(
    "unknown", "operational", "operational", "informational", "update.outcome.unknown",
)


def outcome_for(code: str) -> "OutcomeClass":
    """Return the registry entry for `code`, or UNKNOWN_OUTCOME (fail-safe; never raises)."""
    return _OUTCOME_REGISTRY.get(code, UNKNOWN_OUTCOME)


def outcome_codes() -> frozenset:
    """The closed set of defined outcome codes."""
    return frozenset(_OUTCOME_REGISTRY)


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

def _is_hex64(v: object) -> bool:
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) is not None


def _validate_digest(digest: object) -> None:
    if not isinstance(digest, dict) or digest.get("algorithm") != DIGEST_ALGORITHM:
        raise VerifyError(f"unsupported digest algorithm: {digest!r}")
    if not _is_hex64(digest.get("value")):
        raise VerifyError(f"malformed sha256 digest value: {digest.get('value')!r}")


def _validate_wheelhouse(wh: object) -> None:
    """Fail-closed structural + semantic validation of the armv7l wheelhouse block.
    Every digest field must be a real lowercase sha256; the path is fixed; the
    provenance record reference must be a non-empty bare-ish path."""
    if not isinstance(wh, dict):
        raise VerifyError("wheelhouse block must be an object")
    if wh.get("path") != "wheelhouse-armhf/":
        raise VerifyError(f"wheelhouse path must be 'wheelhouse-armhf/': {wh.get('path')!r}")
    # FORMAT 3: exactly one wheelhouse identity -- the compressor-independent Logical Tree Digest.
    # The legacy gzip-derived bundle_sha256 is rejected outright (no compatibility mode).
    if "bundle_sha256" in wh:
        raise VerifyError("legacy wheelhouse bundle_sha256 is not accepted in format 3")
    td = wh.get("tree_digest")
    if not isinstance(td, dict):
        raise VerifyError("wheelhouse tree_digest must be an object")
    if set(td) != {"scheme", "sha256"}:
        raise VerifyError("wheelhouse tree_digest must have EXACTLY the keys "
                          f"{{'scheme','sha256'}}; got {sorted(td)}")
    if td.get("scheme") not in SUPPORTED_TREE_SCHEMES:
        raise VerifyError(f"unsupported wheelhouse tree_digest scheme: {td.get('scheme')!r}")
    _v = td.get("sha256")
    if not _is_hex64(_v) or _v != _v.lower():
        raise VerifyError(f"wheelhouse tree_digest.sha256 must be canonical lowercase 64-hex: {_v!r}")
    for field in ("requirements_sha256", "lock_sha256",
                  "build_lock_sha256", "provenance_sha256"):
        if not _is_hex64(wh.get(field)):
            raise VerifyError(f"wheelhouse {field} is not a sha256: {wh.get(field)!r}")
    prov = wh.get("provenance")
    if not isinstance(prov, str) or not prov or prov.startswith("/") or ".." in prov:
        raise VerifyError(f"wheelhouse provenance reference invalid: {prov!r}")


def _validate_artifact_entry(entry: object, version: str) -> None:
    if not isinstance(entry, dict):
        raise VerifyError("artifact entry is not an object")
    platform = entry.get("platform")
    if platform not in SUPPORTED_PLATFORMS:
        raise VerifyError(f"unsupported artifact platform: {platform!r}")
    name = entry.get("name")
    expected = f"ccc-{version}-{platform}.tar.gz"
    # Name/platform/version self-consistency WITHIN the signed manifest (not a
    # received-filename check -- the frame carries no independent name; see
    # verify_release: digest-to-platform-entry is the root authorization).
    if name != expected:
        raise VerifyError(f"artifact name {name!r} != canonical {expected!r}")
    _validate_digest(entry.get("digest"))
    tl = entry.get("top_level")
    if not isinstance(tl, list) or not tl or not all(
            isinstance(x, str) and x and "/" not in x and "\\" not in x and x not in (".", "..")
            for x in tl):
        raise VerifyError("artifact top_level must be a non-empty list of bare names")
    wh = entry.get("wheelhouse")
    if platform == "armv7l":
        _validate_wheelhouse(wh)
    elif wh is not None:
        raise VerifyError("aarch64 artifact must not declare a wheelhouse (isolation)")


def parse_verified_manifest(manifest_bytes: bytes) -> dict:
    """Parse and structurally validate a V2 platform-artifact manifest whose
    signature has ALREADY verified. Raises VerifyError on any malformed/unsupported
    content. Fail-closed policy enforced here (Owner-approved):
      * format_version == 2, product == PRODUCT, semver version;
      * source {vcs:git, commit:<40hex>, tag};
      * BOTH aarch64 AND armv7l artifact entries are MANDATORY (a full release);
      * each entry name == canonical `ccc-<version>-<platform>.tar.gz`;
      * armv7l carries a fully-validated wheelhouse block; aarch64 carries none;
      * dependency_locks binds requirements.txt + both platform-lock sha256 digests,
        and must agree with the armv7l wheelhouse block's own lock references.
    """
    try:
        obj = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise VerifyError(f"manifest not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise VerifyError("manifest is not an object")
    if obj.get("format_version") not in SUPPORTED_MANIFEST_FORMATS:
        raise VerifyError(f"unsupported manifest format_version: {obj.get('format_version')!r}")
    if obj.get("product") != PRODUCT:
        raise VerifyError(f"manifest product not {PRODUCT!r}: {obj.get('product')!r}")
    version = obj.get("version")
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        raise VerifyError(f"manifest version not semver: {version!r}")
    source = obj.get("source")
    if not isinstance(source, dict) or source.get("vcs") != "git" \
       or not _COMMIT_RE.match(str(source.get("commit", ""))) \
       or not isinstance(source.get("tag"), str) or not source.get("tag"):
        raise VerifyError(f"manifest missing/invalid source provenance: {source!r}")
    if source["tag"] != f"v{version}":
        raise VerifyError(f"source.tag must be 'v{version}', got {source['tag']!r}")
    artifacts = obj.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise VerifyError("manifest missing artifacts[]")
    seen: set = set()
    for entry in artifacts:
        _validate_artifact_entry(entry, version)
        if entry["platform"] in seen:
            raise VerifyError(f"duplicate platform entry: {entry['platform']!r}")
        seen.add(entry["platform"])
    if seen != set(SUPPORTED_PLATFORMS):
        raise VerifyError(f"incomplete release: platforms {sorted(seen)} != {sorted(SUPPORTED_PLATFORMS)}")
    locks = obj.get("dependency_locks")
    if not isinstance(locks, dict):
        raise VerifyError("manifest missing dependency_locks")
    for field in ("requirements_sha256", "aarch64_lock_sha256",
                  "armv7_lock_sha256", "armv7_build_lock_sha256"):
        if not _is_hex64(locks.get(field)):
            raise VerifyError(f"dependency_locks.{field} is not a sha256: {locks.get(field)!r}")
    # cross-consistency: the armv7l wheelhouse must reference the SAME requirements,
    # runtime-lock, and build-lock digests bound at the top level.
    armv7 = next(a for a in artifacts if a["platform"] == "armv7l")
    wh = armv7["wheelhouse"]
    if wh["requirements_sha256"] != locks["requirements_sha256"]:
        raise VerifyError("wheelhouse requirements_sha256 disagrees with dependency_locks")
    if wh["lock_sha256"] != locks["armv7_lock_sha256"]:
        raise VerifyError("wheelhouse lock_sha256 disagrees with dependency_locks.armv7_lock_sha256")
    if wh["build_lock_sha256"] != locks["armv7_build_lock_sha256"]:
        raise VerifyError("wheelhouse build_lock_sha256 disagrees with dependency_locks.armv7_build_lock_sha256")
    return obj


def select_platform_entry(manifest: dict, platform: str) -> Optional[dict]:
    """Return the signed artifact entry for `platform` (raw uname -m), or None.
    None is the caller's REJECT_PLATFORM signal -- there is NEVER a fallback to a
    different platform's artifact (Owner-approved platform-authority invariant)."""
    if platform not in SUPPORTED_PLATFORMS:
        return None
    for entry in manifest.get("artifacts", []):
        if entry.get("platform") == platform:
            return entry
    return None


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
    platform: str,
) -> VerifyResult:
    """The single, ordered, fail-closed verification decision (IC-2), V2:

        1. trust store present & non-empty                  (else REJECT_STORE)
        2. verification tooling present                     (else REJECT_TOOLING)
        3. manifest signature verifies against the store    (else REJECT_SIGNATURE)
        4. parse the now-verified V2 manifest                (else REJECT_MANIFEST)
        5. a signed artifact entry EXISTS for THIS host      (else REJECT_PLATFORM)
        6. received artifact digest == that platform entry   (else REJECT_DIGEST)

    `platform` is the host's real `uname -m`, supplied by the privileged caller
    which detects it INDEPENDENTLY of the manifest and the untrusted backend. The
    received bytes are bound to the entry for THAT platform only; an artifact whose
    digest matches a DIFFERENT platform's signed entry is rejected (no fallback).

    On success the metadata (product, version, source, platform, artifact name +
    digest, compatibility, format_version, signing_principal) is authoritative and
    is the ONLY source the caller may use for downstream Authorization. The
    manifest-version <-> payload-version cross-check is `cross_check_version`.
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
    except (OSError, VerifyError) as exc:
        return VerifyResult(False, REASON_MANIFEST if isinstance(exc, VerifyError) else REASON_STORE)
    entry = select_platform_entry(manifest, platform)
    if entry is None:
        return VerifyResult(False, REASON_PLATFORM)
    try:
        with open(artifact_path, "rb") as fh:
            artifact_bytes = fh.read()
    except OSError:
        return VerifyResult(False, REASON_DIGEST)
    if not content_digest_ok(artifact_bytes, entry["digest"]):
        return VerifyResult(False, REASON_DIGEST)
    metadata = {
        "product": manifest["product"],
        "version": manifest["version"],
        "source": manifest["source"],
        "platform": entry["platform"],
        "artifact_name": entry["name"],
        "top_level": entry["top_level"],
        "wheelhouse": entry.get("wheelhouse"),
        "compatibility": manifest.get("compatibility", {}),
        "digest": entry["digest"],
        "dependency_locks": manifest.get("dependency_locks", {}),
        "format_version": manifest["format_version"],
        # E3/Phase B: the verified expected allowed-signers principal (verification
        # ran `-Y verify -I PUBLISHER_IDENTITY`). Additive metadata; NOT dynamic
        # signer discovery. Present only on the success path.
        "signing_principal": PUBLISHER_IDENTITY,
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
