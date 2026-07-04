#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
release/ccc_release.py
----------------------
ADR-0003 Epic A — Signed Release Production (publisher side).

Produces the CCC canonical Signed Object for the Trusted Update Engine:

    Release Artifact          a content-fixed tarball of the release
    Manifest                  a canonical, minimal metadata document (S2)
    Manifest signature        an SSH (SSHSIG) Ed25519 signature over the manifest

Normative inputs (frozen by ADR-0003, do NOT change here):
  * Signed Object model = S2  (signed manifest binds metadata + content digest;
    the artifact is content-addressed by that digest).
  * Technology          = SSH signatures, Ed25519 (Cluster A).
  * The manifest carries NO trust material (no keys, no anchor) — Invariant §8.1.
  * The manifest is CANONICAL: its on-disk bytes are exactly the signed bytes.

Scope boundary: this is the PUBLISHER-side producer. It never contacts the
network, never verifies on a device, and never touches the on-device trust store
(that is Epic B). It runs on the publisher's machine with a private signing key
supplied by the operator (key custody is off-infrastructure, ADR-0003 Stage 3).

The private signing key is NEVER generated, embedded, or logged by this tool; it
is provided by path and handed only to `ssh-keygen`.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from typing import Optional

# --- Normative constants (ADR-0003) ---------------------------------------- #

PRODUCT = "conduit-control-center"          # Product identity (authoritative)
MANIFEST_FORMAT_VERSION = 1                  # Manifest schema version (evolvable)
DIGEST_ALGORITHM = "sha256"                  # Content-digest algorithm
SSHSIG_NAMESPACE = "ccc-update-manifest"     # Fixed SSHSIG namespace (sign+verify)

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")   # matches backend/_version.py format


# --- Errors ---------------------------------------------------------------- #

class ReleaseError(Exception):
    """Raised for any producer-side release-production failure."""


# --- Canonicalisation & digest --------------------------------------------- #

def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Deterministic byte representation of a manifest.

    The bytes returned here are EXACTLY the bytes written to disk and EXACTLY the
    bytes that get signed. Determinism (sorted keys, no insignificant
    whitespace, UTF-8) is required so signing and verification operate on an
    identical, unambiguous input.
    """
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Manifest construction (S2) -------------------------------------------- #

def build_manifest(
    *,
    version: str,
    artifact_name: str,
    artifact_bytes: bytes,
    recommended_conduit_core: Optional[str] = None,
    platform: Optional[str] = None,
    product: str = PRODUCT,
    format_version: int = MANIFEST_FORMAT_VERSION,
) -> dict:
    """Assemble the manifest that binds identity + version + compatibility to
    the artifact's content digest. Carries no trust material."""
    if not _SEMVER_RE.match(version):
        raise ReleaseError(f"version must be strict semver X.Y.Z, got {version!r}")
    if not artifact_name or "/" in artifact_name or "\\" in artifact_name:
        raise ReleaseError(f"artifact_name must be a bare filename, got {artifact_name!r}")
    return {
        "format_version": format_version,
        "product": product,
        "version": version,
        "compatibility": {
            "recommended_conduit_core": recommended_conduit_core,
            "platform": platform,
        },
        "artifact": {
            "name": artifact_name,
            "digest": {"algorithm": DIGEST_ALGORITHM, "value": sha256_hex(artifact_bytes)},
        },
    }


# --- Content-fixed artifact builder ---------------------------------------- #

def build_deterministic_artifact(source_dir: str) -> bytes:
    """Build a content-fixed .tar.gz from a source tree.

    Determinism is enforced by sorting members and normalising metadata
    (mtime=0, uid/gid=0, empty owner names, canonical modes) plus a gzip header
    with mtime=0. Two runs over identical content yield identical bytes, so the
    content digest is stable. (Full cross-machine reproducibility of the *source
    tree itself* is out of ADR-0003 scope — the T6 residual.)
    """
    src = os.path.abspath(source_dir)
    if not os.path.isdir(src):
        raise ReleaseError(f"source is not a directory: {source_dir!r}")

    members: list[str] = []
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(files):
            members.append(os.path.join(root, name))
    members.sort()

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for path in members:
            arcname = os.path.relpath(path, src).replace(os.sep, "/")
            info = tarfile.TarInfo(name=arcname)
            with open(path, "rb") as fh:
                data = fh.read()
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))

    out = io.BytesIO()
    # gzip with mtime=0 so the gzip header is deterministic.
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


# --- Signing (SSH / Ed25519) ----------------------------------------------- #

def _run(cmd: list[str], *, stdin: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, input=stdin, capture_output=True, shell=False,
    )


def public_allowed_signers_line(key_path: str, principal: str) -> str:
    """Derive the allowed-signers (trust-store) entry for a signing key.

    The publisher publishes THIS public line; the device trust store (Epic B) is
    built from it. The private key is never read by this helper — `ssh-keygen -y`
    derives the public key only.
    """
    proc = _run(["ssh-keygen", "-y", "-f", key_path])
    if proc.returncode != 0:
        raise ReleaseError(f"cannot derive public key: {proc.stderr.decode(errors='replace').strip()}")
    pub = proc.stdout.decode().strip()
    # allowed_signers: "<principal> <keytype> <base64>"; drop any trailing comment.
    parts = pub.split()
    if len(parts) < 2:
        raise ReleaseError("unexpected public-key format from ssh-keygen -y")
    return f"{principal} {parts[0]} {parts[1]}"


def sign_manifest(manifest_path: str, key_path: str, *, namespace: str = SSHSIG_NAMESPACE) -> str:
    """Sign the manifest file with ssh-keygen -Y sign; returns the .sig path.

    ssh-keygen writes `<manifest_path>.sig` (armored SSHSIG). The signed bytes are
    exactly the manifest file's bytes (which are canonical, see above).

    Note: `ssh-keygen -Y sign` does NOT overwrite an existing signature file, so a
    stale `.sig` would silently survive a re-sign. To guarantee the returned
    signature is the one just produced by `key_path`, any pre-existing signature
    is removed first."""
    sig_path = manifest_path + ".sig"
    try:
        os.remove(sig_path)
    except FileNotFoundError:
        pass
    proc = _run(["ssh-keygen", "-Y", "sign", "-f", key_path, "-n", namespace, manifest_path])
    if proc.returncode != 0:
        raise ReleaseError(f"signing failed: {proc.stderr.decode(errors='replace').strip()}")
    if not os.path.exists(sig_path):
        raise ReleaseError("signing produced no signature file")
    return sig_path


def verify_signed_manifest(
    manifest_path: str,
    sig_path: str,
    allowed_signers_path: str,
    *,
    identity: str,
    namespace: str = SSHSIG_NAMESPACE,
) -> bool:
    """Producer-side QA verification (proves the Signed Object is verifiable
    offline). The DEVICE verifier is Epic B; this helper is not the device path.
    Returns True iff the signature verifies against the allowed-signers store."""
    with open(manifest_path, "rb") as fh:
        data = fh.read()
    proc = _run(
        ["ssh-keygen", "-Y", "verify", "-f", allowed_signers_path,
         "-I", identity, "-n", namespace, "-s", sig_path],
        stdin=data,
    )
    return proc.returncode == 0


# --- Release production ----------------------------------------------------- #

def produce_release(
    *,
    version: str,
    out_dir: str,
    key_path: str,
    source_dir: Optional[str] = None,
    artifact_path: Optional[str] = None,
    recommended_conduit_core: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """Produce the canonical release asset set: {artifact, manifest, manifest.sig}."""
    if bool(source_dir) == bool(artifact_path):
        raise ReleaseError("provide exactly one of --source or --artifact")

    if artifact_path:
        with open(artifact_path, "rb") as fh:
            artifact_bytes = fh.read()
        artifact_name = os.path.basename(artifact_path)
    else:
        artifact_bytes = build_deterministic_artifact(source_dir)  # type: ignore[arg-type]
        artifact_name = f"ccc-{version}.tar.gz"

    manifest = build_manifest(
        version=version,
        artifact_name=artifact_name,
        artifact_bytes=artifact_bytes,
        recommended_conduit_core=recommended_conduit_core,
        platform=platform,
    )

    os.makedirs(out_dir, exist_ok=True)
    artifact_out = os.path.join(out_dir, artifact_name)
    manifest_out = os.path.join(out_dir, f"ccc-{version}.manifest.json")

    with open(artifact_out, "wb") as fh:
        fh.write(artifact_bytes)
    with open(manifest_out, "wb") as fh:
        fh.write(canonical_manifest_bytes(manifest))

    sig_out = sign_manifest(manifest_out, key_path)
    return {"artifact": artifact_out, "manifest": manifest_out, "signature": sig_out}


# --- CLI -------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ADR-0003 Epic A — produce a signed CCC release.")
    p.add_argument("--version", required=True, help="release semver X.Y.Z")
    p.add_argument("--sign-key", required=True, help="path to the publisher Ed25519 private key")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--source", help="source tree to build a content-fixed artifact from")
    src.add_argument("--artifact", help="path to a prebuilt release artifact")
    p.add_argument("--recommended-core", default=None, help="advisory recommended Conduit Core version")
    p.add_argument("--platform", default=None, help="advisory target platform")
    p.add_argument("--out", default="dist", help="output directory for the release asset set")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = produce_release(
            version=args.version,
            out_dir=args.out,
            key_path=args.sign_key,
            source_dir=args.source,
            artifact_path=args.artifact,
            recommended_conduit_core=args.recommended_core,
            platform=args.platform,
        )
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for k, v in result.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
