# SPDX-License-Identifier: MIT
"""ADR-0003 Epic A — Signed Release Production tests.

Covers the producer-side contract:
  * canonical manifest bytes are deterministic and key-order independent
  * content digest correctness
  * manifest schema + strict-semver validation
  * manifest carries NO trust material (Invariant §8.1)
  * content-fixed artifact is byte-stable
  * SSH/Ed25519 sign -> verify round trip, with tamper and wrong-signer rejection
    (skipped only if ssh-keygen is unavailable)
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from release import ccc_release as R

_HAS_SSH = shutil.which("ssh-keygen") is not None
_ssh = pytest.mark.skipif(not _HAS_SSH, reason="ssh-keygen not available")


# --- canonicalisation & digest --------------------------------------------- #

def test_canonical_bytes_are_deterministic_and_order_independent():
    a = {"b": 1, "a": {"y": 2, "x": 1}}
    b = {"a": {"x": 1, "y": 2}, "b": 1}
    assert R.canonical_manifest_bytes(a) == R.canonical_manifest_bytes(b)
    # stable across calls, and free of insignificant whitespace
    assert R.canonical_manifest_bytes(a) == R.canonical_manifest_bytes(a)
    assert b" " not in R.canonical_manifest_bytes(a)


def test_sha256_hex_matches_hashlib():
    import hashlib
    assert R.sha256_hex(b"payload") == hashlib.sha256(b"payload").hexdigest()


# --- manifest schema -------------------------------------------------------- #

def test_build_manifest_fields():
    m = R.build_manifest(
        version="0.3.13",
        artifact_name="ccc-0.3.13.tar.gz",
        artifact_bytes=b"\x1f\x8bcontent",
        recommended_conduit_core="1.2.3",
        platform="raspberry-pi-arm64",
    )
    assert m["product"] == "conduit-control-center"
    assert m["version"] == "0.3.13"
    assert m["format_version"] == R.MANIFEST_FORMAT_VERSION
    assert m["compatibility"]["recommended_conduit_core"] == "1.2.3"
    assert m["artifact"]["digest"]["algorithm"] == "sha256"
    assert m["artifact"]["digest"]["value"] == R.sha256_hex(b"\x1f\x8bcontent")


@pytest.mark.parametrize("bad", ["0.3", "v0.3.13", "0.3.13-rc1", "abc", "0.3.13.1"])
def test_build_manifest_rejects_non_semver(bad):
    with pytest.raises(R.ReleaseError):
        R.build_manifest(version=bad, artifact_name="a.tgz", artifact_bytes=b"x")


def test_build_manifest_rejects_pathful_artifact_name():
    with pytest.raises(R.ReleaseError):
        R.build_manifest(version="0.3.13", artifact_name="../a.tgz", artifact_bytes=b"x")


def test_manifest_carries_no_trust_material():
    # Invariant §8.1: the manifest must never contain keys, an anchor, or the
    # signature itself. Scan the full canonical text for forbidden markers.
    m = R.build_manifest(version="0.3.13", artifact_name="a.tgz", artifact_bytes=b"x")
    text = R.canonical_manifest_bytes(m).decode().lower()
    for forbidden in ("private", "ssh-ed25519", "begin openssh", "anchor", "allowed_signers", "signature"):
        assert forbidden not in text, forbidden
    # structurally: only the agreed top-level keys exist
    assert set(m.keys()) == {"format_version", "product", "version", "compatibility", "artifact"}


# --- content-fixed artifact ------------------------------------------------- #

def test_deterministic_artifact_is_byte_stable(tmp_path):
    src = tmp_path / "src"
    (src / "backend").mkdir(parents=True)
    (src / "backend" / "_version.py").write_text('APP_VERSION = "0.3.13"\n')
    (src / "README.md").write_text("hello\n")
    a1 = R.build_deterministic_artifact(str(src))
    a2 = R.build_deterministic_artifact(str(src))
    assert a1 == a2                      # deterministic
    assert a1[:2] == b"\x1f\x8b"         # gzip magic
    assert R.sha256_hex(a1) == R.sha256_hex(a2)


# --- SSH / Ed25519 sign -> verify round trip -------------------------------- #

def _gen_key(path):
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
        check=True, capture_output=True,
    )


@_ssh
def test_sign_verify_round_trip_and_rejections(tmp_path):
    identity = "ccc-test-publisher"
    key = tmp_path / "pub_key"
    _gen_key(key)

    # publisher publishes the allowed-signers (trust-store) entry
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(R.public_allowed_signers_line(str(key), identity) + "\n")

    # write a canonical manifest and sign it
    manifest = R.build_manifest(version="0.3.13", artifact_name="ccc-0.3.13.tar.gz",
                                artifact_bytes=b"\x1f\x8bpayload")
    mpath = tmp_path / "ccc-0.3.13.manifest.json"
    mpath.write_bytes(R.canonical_manifest_bytes(manifest))
    sig = R.sign_manifest(str(mpath), str(key))

    # genuine object verifies
    assert R.verify_signed_manifest(str(mpath), sig, str(allowed), identity=identity) is True

    # tampered manifest fails
    tampered = tmp_path / "tampered.manifest.json"
    tampered.write_bytes(R.canonical_manifest_bytes(manifest) + b"x")
    assert R.verify_signed_manifest(str(tampered), sig, str(allowed), identity=identity) is False

    # signature from an untrusted signer fails (fresh file: independent of any
    # stale-signature behaviour)
    other = tmp_path / "other_key"
    _gen_key(other)
    other_manifest = tmp_path / "other.manifest.json"
    other_manifest.write_bytes(R.canonical_manifest_bytes(manifest))
    other_sig = R.sign_manifest(str(other_manifest), str(other))
    assert R.verify_signed_manifest(str(other_manifest), other_sig, str(allowed), identity=identity) is False

    # re-signing the SAME manifest with a different key must not keep the stale
    # signature (sign_manifest removes it first)
    resigned = R.sign_manifest(str(mpath), str(other))
    assert R.verify_signed_manifest(str(mpath), resigned, str(allowed), identity=identity) is False


@_ssh
def test_produce_release_end_to_end(tmp_path):
    identity = "ccc-test-publisher"
    key = tmp_path / "pub_key"
    _gen_key(key)
    src = tmp_path / "src"
    (src / "backend").mkdir(parents=True)
    (src / "backend" / "_version.py").write_text('APP_VERSION = "0.3.13"\n')

    out = tmp_path / "dist"
    result = R.produce_release(
        version="0.3.13", out_dir=str(out), key_path=str(key), source_dir=str(src),
        recommended_conduit_core="1.2.3", platform="raspberry-pi-arm64",
    )
    # asset set produced
    for k in ("artifact", "manifest", "signature"):
        assert result[k]
    # manifest digest actually matches the produced artifact bytes
    with open(result["artifact"], "rb") as fh:
        artifact_bytes = fh.read()
    manifest = json.loads(open(result["manifest"], "rb").read())
    assert manifest["artifact"]["digest"]["value"] == R.sha256_hex(artifact_bytes)
    assert manifest["version"] == "0.3.13"

    # the produced signed object verifies against the publisher's allowed-signers
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(R.public_allowed_signers_line(str(key), identity) + "\n")
    assert R.verify_signed_manifest(result["manifest"], result["signature"],
                                    str(allowed), identity=identity) is True
