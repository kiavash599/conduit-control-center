# SPDX-License-Identifier: MIT
"""ADR-0003 Epic B — Trusted Verification Path tests.

Proves the device-side verifier is correct and fail-closed:
  * genuine signed release verifies and yields authoritative metadata
  * tampered manifest, untrusted signer, missing/empty store, digest mismatch,
    malformed manifest -> REJECT with the correct reason (never a default pass)
  * manifest<->artifact version cross-check
SSH-dependent cases are skipped only if ssh-keygen is unavailable; pure parsing /
store / digest / cross-check cases run unconditionally.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from backend import update_verify as V
from release import ccc_release as R

_HAS_SSH = shutil.which("ssh-keygen") is not None
_ssh = pytest.mark.skipif(not _HAS_SSH, reason="ssh-keygen not available")


def _gen_key(path):
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
                   check=True, capture_output=True)


def _make_release(tmp_path, artifact_bytes=b"\x1f\x8bpayload", version="0.3.13", trusted=True):
    """Produce {manifest, sig, artifact, store} for a signer that is (or isn't)
    in the trust store. Returns paths."""
    key = tmp_path / "pub_key"
    _gen_key(key)
    store_key = key
    if not trusted:  # store lists a DIFFERENT key than the one that signs
        store_key = tmp_path / "store_key"
        _gen_key(store_key)
    store = tmp_path / "allowed_signers"
    store.write_text(R.public_allowed_signers_line(str(store_key), V.PUBLISHER_IDENTITY) + "\n")

    artifact = tmp_path / f"ccc-{version}.tar.gz"
    artifact.write_bytes(artifact_bytes)
    manifest = R.build_manifest(version=version, artifact_name=artifact.name,
                                artifact_bytes=artifact_bytes, recommended_conduit_core="1.2.3")
    mpath = tmp_path / f"ccc-{version}.manifest.json"
    mpath.write_bytes(R.canonical_manifest_bytes(manifest))
    sig = R.sign_manifest(str(mpath), str(key), namespace=V.SSHSIG_NAMESPACE)
    return {"manifest": str(mpath), "signature": sig, "artifact": str(artifact), "store": str(store)}


# --- pure (no ssh) --------------------------------------------------------- #

def test_read_trust_store_fail_closed(tmp_path):
    missing = tmp_path / "nope"
    assert V.read_trust_store(str(missing)) is None
    empty = tmp_path / "empty"; empty.write_text("# only a comment\n\n   \n")
    assert V.read_trust_store(str(empty)) is None
    good = tmp_path / "good"; good.write_text("principal ssh-ed25519 AAAA\n")
    assert V.read_trust_store(str(good)) == ["principal ssh-ed25519 AAAA"]


def test_parse_verified_manifest_rejects_malformed():
    import json
    good = {"format_version": 1, "product": V.PRODUCT, "version": "0.3.13",
            "compatibility": {}, "artifact": {"name": "a", "digest": {"algorithm": "sha256", "value": "ab"}}}
    assert V.parse_verified_manifest(json.dumps(good).encode())["version"] == "0.3.13"
    for bad in (
        b"not json",
        json.dumps({**good, "format_version": 99}).encode(),
        json.dumps({**good, "product": ""}).encode(),
        json.dumps({**good, "version": "v1"}).encode(),
        json.dumps({**good, "artifact": {"name": "a", "digest": {"algorithm": "md5", "value": "x"}}}).encode(),
        json.dumps({k: v for k, v in good.items() if k != "artifact"}).encode(),
    ):
        with pytest.raises(V.VerifyError):
            V.parse_verified_manifest(bad)


def test_content_digest_and_cross_check():
    import hashlib
    data = b"artifact-bytes"
    d = {"algorithm": "sha256", "value": hashlib.sha256(data).hexdigest()}
    assert V.content_digest_ok(data, d) is True
    assert V.content_digest_ok(b"other", d) is False
    assert V.content_digest_ok(data, {"algorithm": "md5", "value": "x"}) is False
    meta = {"product": V.PRODUCT, "version": "0.3.13"}
    assert V.cross_check_version(meta, "0.3.13") is True
    assert V.cross_check_version(meta, "0.3.14") is False
    assert V.cross_check_version(meta, "garbage") is False
    assert V.product_scope_ok(meta) is True
    assert V.product_scope_ok({"product": "other"}) is False


def test_verify_release_missing_store_is_fail_closed(tmp_path):
    # no store on disk -> REJECT_STORE even before any signature work
    r = V.verify_release(manifest_path=str(tmp_path / "m"), signature_path=str(tmp_path / "s"),
                         artifact_path=str(tmp_path / "a"), trust_store_path=str(tmp_path / "no_store"))
    assert r.ok is False and r.reason == V.REASON_STORE


# --- end-to-end (ssh) ------------------------------------------------------ #

@_ssh
def test_genuine_release_verifies(tmp_path):
    p = _make_release(tmp_path)
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["artifact"], trust_store_path=p["store"])
    assert r.ok is True and r.reason == V.REASON_VERIFIED
    assert r.metadata["product"] == V.PRODUCT
    assert r.metadata["version"] == "0.3.13"
    assert V.cross_check_version(r.metadata, "0.3.13") is True


@_ssh
def test_success_metadata_carries_signing_principal(tmp_path):
    # Phase B additive: the verified expected allowed-signers principal is exposed
    # on the SUCCESS path only; reject paths carry no metadata (and no principal).
    p = _make_release(tmp_path)
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["artifact"], trust_store_path=p["store"])
    assert r.metadata["signing_principal"] == V.PUBLISHER_IDENTITY
    for k in ("product", "version", "compatibility", "digest", "format_version"):
        assert k in r.metadata           # existing keys intact (additive-only)
    bad = _make_release(tmp_path, trusted=False)
    rj = V.verify_release(manifest_path=bad["manifest"], signature_path=bad["signature"],
                          artifact_path=bad["artifact"], trust_store_path=bad["store"])
    assert rj.ok is False and rj.metadata is None


@_ssh
def test_tampered_manifest_rejected(tmp_path):
    p = _make_release(tmp_path)
    # mutate the manifest bytes on disk after signing
    with open(p["manifest"], "ab") as fh:
        fh.write(b" ")
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["artifact"], trust_store_path=p["store"])
    assert r.ok is False and r.reason == V.REASON_SIGNATURE


@_ssh
def test_untrusted_signer_rejected(tmp_path):
    p = _make_release(tmp_path, trusted=False)  # store lists a different key
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["artifact"], trust_store_path=p["store"])
    assert r.ok is False and r.reason == V.REASON_SIGNATURE


@_ssh
def test_digest_mismatch_rejected(tmp_path):
    p = _make_release(tmp_path, artifact_bytes=b"\x1f\x8boriginal")
    # replace the artifact with different bytes; manifest+sig remain genuine
    with open(p["artifact"], "wb") as fh:
        fh.write(b"\x1f\x8btampered-artifact")
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["artifact"], trust_store_path=p["store"])
    assert r.ok is False and r.reason == V.REASON_DIGEST


@_ssh
def test_empty_store_rejected(tmp_path):
    p = _make_release(tmp_path)
    open(p["store"], "w").write("# emptied\n")
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["artifact"], trust_store_path=p["store"])
    assert r.ok is False and r.reason == V.REASON_STORE
