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
import fnmatch
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


# --- Canonicalization layer (.gitattributes-driven) ------------------------ #
#
# ADR-0003 defines a *Canonical Release Artifact*. Canonicality is a property of
# the ARTIFACT (deterministic, reproducible, platform-independent bytes), not of
# the storage backend. Git is therefore ONE valid producer of a source tree, not
# the definition of canonical. Every producer (--source, --git-ref) passes its
# collected {path -> bytes} tree through this layer before packing, so the same
# content yields byte-identical artifacts regardless of the OS/checkout that
# produced the tree (this is what a Windows CRLF checkout broke for 0.3.13).
#
# Classification is EXPLICIT-FIRST and FAIL-SAFE:
#   * the tree's own `.gitattributes` is the ruleset (text / -text / binary /
#     eol=lf) — the same declaration Git checkout and `git archive` honour;
#   * files with no explicit rule fall back to a conservative content sniff;
#   * anything uncertain or detected-binary is left BYTE-EXACT (never rewritten),
#     so a misclassification can never corrupt a binary.
# The ONLY transformation applied is CRLF/CR -> LF for text files. The canonical
# artifact is LF-only (Linux target).


def parse_gitattributes(text: str) -> list[tuple[str, dict]]:
    """Parse the subset of `.gitattributes` relevant to canonicalization.

    Returns an ordered list of (pattern, attrs). `attrs` may contain:
      * "text": True (text) | False (binary / -text) | "auto" (text=auto)
      * "eol": "lf" | "crlf"  (an explicit eol also implies text)
    Later matching rules override earlier ones (Git's last-match-wins).
    Unrecognised tokens (diff, filter, merge, …) are ignored.
    """
    rules: list[tuple[str, dict]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern, tokens = parts[0], parts[1:]
        attrs: dict = {}
        for tok in tokens:
            if tok in ("binary", "-text", "!text"):
                attrs["text"] = False
            elif tok == "text":
                attrs["text"] = True
            elif tok == "text=auto":
                attrs["text"] = "auto"
            elif tok.startswith("eol="):
                attrs["eol"] = tok.split("=", 1)[1]
                attrs.setdefault("text", True)   # an explicit eol implies text
        if attrs:
            rules.append((pattern, attrs))
    return rules


def _attr_match(pattern: str, path_rel: str) -> bool:
    """Pragmatic gitattributes/gitignore-style match (glob via fnmatch).

    A pattern without a slash matches the basename at any depth; a pattern with
    a slash (or a leading `/`) matches the full repo-relative path.
    """
    name = path_rel.rsplit("/", 1)[-1]
    if pattern.startswith("/"):
        return fnmatch.fnmatch(path_rel, pattern[1:])
    if "/" in pattern:
        return fnmatch.fnmatch(path_rel, pattern)
    return fnmatch.fnmatch(name, pattern)


def attrs_for(path_rel: str, rules: list[tuple[str, dict]]) -> dict:
    """Merge all rules matching `path_rel`; later rules win per attribute."""
    merged: dict = {}
    for pattern, attrs in rules:
        if _attr_match(pattern, path_rel):
            merged.update(attrs)
    return merged


def _looks_binary(data: bytes) -> bool:
    """Conservative binary sniff: a NUL byte in the first 8 KiB (Git's heuristic).
    Used only for files with no explicit text/binary declaration."""
    return b"\x00" in data[:8192]


def _to_lf(data: bytes) -> bytes:
    """Normalise CRLF and lone CR to LF."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def is_text(path_rel: str, data: bytes, rules: list[tuple[str, dict]]) -> bool:
    """Decide whether a file is text (and thus LF-normalised) or left byte-exact.

    Explicit declaration wins; otherwise a conservative content sniff decides,
    defaulting to "leave alone" (binary) when a NUL byte is present.
    """
    text = attrs_for(path_rel, rules).get("text")
    if text is False:          # binary / -text  -> byte-exact
        return False
    if text is True:           # text / eol=…    -> LF
        return True
    # text == "auto" or unset  -> conservative content sniff
    return not _looks_binary(data)


def canonicalize_tree(raw: dict[str, bytes]) -> dict[str, bytes]:
    """Apply the canonicalization ruleset to a collected {arcname -> bytes} tree.

    The ruleset is the tree's OWN `.gitattributes` (if present), so the same
    declaration used by Git checkout / `git archive` drives release production.
    Text files are LF-normalised; binary/uncertain files are untouched.
    """
    rules = parse_gitattributes(raw.get(".gitattributes", b"").decode("utf-8", "replace"))
    canon: dict[str, bytes] = {}
    for arcname in sorted(raw):
        data = raw[arcname]
        if is_text(arcname, data, rules):
            data = _to_lf(data)
        canon[arcname] = data
    return canon


# --- Tree collectors (producers) ------------------------------------------- #

def _raw_from_dir(source_dir: str) -> dict[str, bytes]:
    """Collect {arcname -> raw bytes} from a source directory (the `--source`
    producer). The `.git` directory, if present, is excluded."""
    src = os.path.abspath(source_dir)
    if not os.path.isdir(src):
        raise ReleaseError(f"source is not a directory: {source_dir!r}")
    raw: dict[str, bytes] = {}
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != ".git"]
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            arcname = os.path.relpath(path, src).replace(os.sep, "/")
            with open(path, "rb") as fh:
                raw[arcname] = fh.read()
    return raw


def _raw_from_git_ref(ref: str, repo_dir: str = ".") -> dict[str, bytes]:
    """Collect {path -> blob bytes} for all tracked files at `ref` straight from
    the Git object database (the canonical `--git-ref` producer).

    Blob bytes are read with `git cat-file`, so they are the normalised content
    stored on commit — independent of the working tree's OS/checkout. They still
    pass through `canonicalize_tree` (idempotent belt-and-suspenders)."""
    listing = _run(["git", "-C", repo_dir, "ls-tree", "-r", "-z", "--name-only", ref])
    if listing.returncode != 0:
        raise ReleaseError(
            f"git ls-tree failed for ref {ref!r}: {listing.stderr.decode(errors='replace').strip()}"
        )
    names = [n for n in listing.stdout.decode("utf-8").split("\0") if n]
    raw: dict[str, bytes] = {}
    for name in names:
        blob = _run(["git", "-C", repo_dir, "cat-file", "blob", f"{ref}:{name}"])
        if blob.returncode != 0:
            raise ReleaseError(
                f"git cat-file failed for {ref}:{name}: {blob.stderr.decode(errors='replace').strip()}"
            )
        raw[name] = blob.stdout
    return raw


# --- Content-fixed artifact packer ----------------------------------------- #

def pack_tree(mapping: dict[str, bytes]) -> bytes:
    """Pack a {arcname -> bytes} mapping into a content-fixed .tar.gz.

    Determinism is enforced by sorting members and normalising metadata
    (mtime=0, uid/gid=0, empty owner names, canonical mode 0644) plus a gzip
    header with mtime=0. Two runs over identical content yield identical bytes,
    so the content digest is stable.
    """
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for arcname in sorted(mapping):
            data = mapping[arcname]
            info = tarfile.TarInfo(name=arcname)
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


def build_deterministic_artifact(source_dir: str) -> bytes:
    """Canonicalize a source directory and pack it into a content-fixed .tar.gz.

    Backwards-compatible convenience wrapper: collect -> canonicalize -> pack.
    Line endings are normalised per the tree's `.gitattributes`, so a CRLF
    working-tree checkout can no longer contaminate the artifact.
    """
    return pack_tree(canonicalize_tree(_raw_from_dir(source_dir)))


def build_canonical_artifact_from_git_ref(ref: str, repo_dir: str = ".") -> bytes:
    """Canonical production build: object-DB tree at `ref` -> canonicalize -> pack."""
    return pack_tree(canonicalize_tree(_raw_from_git_ref(ref, repo_dir)))


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
    git_ref: Optional[str] = None,
    repo_dir: str = ".",
    recommended_conduit_core: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """Produce the canonical release asset set: {artifact, manifest, manifest.sig}.

    Exactly one producer must be given:
      * git_ref      — PREFERRED production mode; canonical tree from the object DB.
      * source_dir   — canonical only AFTER canonicalization (relies on the tree's
                       own `.gitattributes` + content detection).
      * artifact_path— a prebuilt artifact, consumed byte-exact (expert use).
    """
    if sum(bool(x) for x in (source_dir, artifact_path, git_ref)) != 1:
        raise ReleaseError("provide exactly one of --git-ref, --source, or --artifact")

    if artifact_path:
        with open(artifact_path, "rb") as fh:
            artifact_bytes = fh.read()
        artifact_name = os.path.basename(artifact_path)
    elif git_ref:
        artifact_bytes = build_canonical_artifact_from_git_ref(git_ref, repo_dir)
        artifact_name = f"ccc-{version}.tar.gz"
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


# --- Trust-store helper ----------------------------------------------------- #

def emit_trusted_publishers(out_path: str, key_path: str, identity: str = PRODUCT + "-publisher") -> str:
    """Write a safe `trusted_publishers` (allowed-signers) file for `key_path`.

    OpenSSH allowed-signers files must be plain UTF-8 with NO BOM and LF line
    endings. Hand-authoring on Windows (e.g. PowerShell `Set-Content -Encoding
    utf8`) injects a BOM and CRLF, which breaks `ssh-keygen -Y verify`. Writing
    the bytes here (mode "wb", trailing "\\n") guarantees UTF-8/no-BOM/LF. Only
    the PUBLIC key is read (`ssh-keygen -y`); the private key is never embedded.
    """
    line = public_allowed_signers_line(key_path, identity)
    with open(out_path, "wb") as fh:
        fh.write((line + "\n").encode("utf-8"))
    return out_path


# --- CLI -------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ADR-0003 Epic A — produce a signed CCC release.")
    p.add_argument("--version", help="release semver X.Y.Z (required unless --emit-trusted-publishers)")
    p.add_argument("--sign-key", required=True, help="path to the publisher Ed25519 private key")
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--git-ref", help="PREFERRED: build the canonical artifact from this Git ref/commit")
    src.add_argument("--source", help="source tree (canonical only after canonicalization)")
    src.add_argument("--artifact", help="prebuilt release artifact, consumed byte-exact (expert)")
    p.add_argument("--repo", default=".", help="repository directory for --git-ref (default: .)")
    p.add_argument("--recommended-core", default=None, help="advisory recommended Conduit Core version")
    p.add_argument("--platform", default=None, help="advisory target platform")
    p.add_argument("--out", default="dist", help="output directory for the release asset set")
    p.add_argument("--emit-trusted-publishers", metavar="PATH",
                   help="write a safe UTF-8/no-BOM/LF trusted_publishers file for --sign-key and exit")
    p.add_argument("--identity", default=PRODUCT + "-publisher",
                   help="allowed-signers principal for --emit-trusted-publishers")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.emit_trusted_publishers:
            out = emit_trusted_publishers(args.emit_trusted_publishers, args.sign_key, args.identity)
            print(f"trusted_publishers: {out}")
            return 0
        if not args.version:
            raise ReleaseError("--version is required")
        if args.source:
            # Library stays silent; the advisory note belongs to the CLI layer.
            print(
                "note: --source is canonicalized via the tree's .gitattributes + "
                "content detection; --git-ref <ref> is the preferred production producer.",
                file=sys.stderr,
            )
        # Producer-count validation ("exactly one of …") is centralised in
        # produce_release(); it raises ReleaseError, caught below.
        result = produce_release(
            version=args.version,
            out_dir=args.out,
            key_path=args.sign_key,
            source_dir=args.source,
            artifact_path=args.artifact,
            git_ref=args.git_ref,
            repo_dir=args.repo,
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
