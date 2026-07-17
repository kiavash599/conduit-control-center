# SPDX-License-Identifier: MIT
"""ADR-0003 Epic B — Trusted Verification Path tests (V2, hardened).

Fail-closed verifier: genuine per-platform verify + the full reject taxonomy,
including platform mismatch (no fallback), unknown platform, both-platform
completeness, the FOUR mandatory dependency locks (incl. armv7 build-input lock),
strict wheelhouse block, wheelhouse<->locks cross-consistency, and canonical names.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from backend import update_verify as V
from release import ccc_release as R

_HAS_SSH = shutil.which("ssh-keygen") is not None
_HAS_GIT = shutil.which("git") is not None
_ssh = pytest.mark.skipif(not _HAS_SSH, reason="ssh-keygen not available")
_e2e = pytest.mark.skipif(not (_HAS_SSH and _HAS_GIT), reason="need ssh-keygen + git")

_COMMIT = "0" * 40
_REQ, _ALOCK, _VLOCK, _BLOCK = "1" * 64, "2" * 64, "3" * 64, "4" * 64
_SDH = "e" * 64
_RECIPE = "FROM base\nRUN true\n"
_RECIPE_SHA = R.sha256_hex(_RECIPE.encode())
_BB_LOCK = "maturin==1.5.1 --hash=sha256:%s\n" % ("7" * 64)
_BB_SHA = R.sha256_hex(_BB_LOCK.encode())
_APT = "build-essential=12.9ubuntu3\n"
_RUSTUP = "f" * 64 + "  rustup-init\n"
_APT_SHA = R.sha256_hex(_APT.encode())
_RUSTUP_SHA = R.sha256_hex(_RUSTUP.encode())
def _manifest_bytes(config_digest):
    import json as _json
    return _json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                   "digest": config_digest, "size": 1234},
        "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": "sha256:" + "a" * 64, "size": 5678}],
    }).encode()
_CONFIG_DIGEST = "sha256:" + "c" * 64
_MANIFEST = _manifest_bytes(_CONFIG_DIGEST)
_MANIFEST_DIGEST = "sha256:" + hashlib.sha256(_MANIFEST).hexdigest()
_RUNTIME_ID = _MANIFEST_DIGEST                      # containerd store: .Id == manifest digest
_EXT_IN = "tomli==2.0.1\n"
_EXT_LOCK = "tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
_EXT_LOCK_SHA = R.sha256_hex(_EXT_LOCK.encode())
_ALLOWLIST = "maturin\n"
_ALLOWLIST_SHA = R.sha256_hex(_ALLOWLIST.encode())
_ENV = {"os": "Ubuntu 22.04.5 LTS", "python": "Python 3.10.12", "rustc": "rustc 1.75.0",
        "cargo": "cargo 1.75.0", "gcc": "gcc 11.4.0", "glibc": "2.35",
        "os_id": "ubuntu", "os_version_id": "22.04", "arch": "armv7l", "apt_architecture": "armhf",
        "apt": {"build-essential": "12.9ubuntu3"}, "build_backends": {"maturin": "1.5.1"}}


def _builder():
    return {"identity": "ccc-armv7-builder", "recipe_path": R.CANONICAL_RECIPE_PATH,
            "recipe_sha256": _RECIPE_SHA, "build_backends_lock_sha256": _BB_SHA,
            "apt_packages_sha256": _APT_SHA, "rustup_init_file_sha256": _RUSTUP_SHA,
            "extractor_tools_lock_sha256": _EXT_LOCK_SHA,
            "build_backends_source_allowlist_sha256": _ALLOWLIST_SHA,
            "base_image_digest": "sha256:" + "b" * 64, "image_manifest_digest": _MANIFEST_DIGEST,
            "image_config_digest": _CONFIG_DIGEST, "image_identity_mode": "containerd",
            "runtime_image_id": _RUNTIME_ID, "environment": dict(_ENV),
            "environment_sha256": R.sha256_hex(R._canonical_env_bytes(_ENV))}
_REL_SEQ = 0


def _gen_key(path):
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
                   check=True, capture_output=True)


def _make_release(tmp_path, version="0.3.16", trusted=True):
    global _REL_SEQ
    _REL_SEQ += 1
    base = tmp_path / f"rel{_REL_SEQ}"
    base.mkdir()
    key = base / "pub_key"
    _gen_key(key)
    store_key = key if trusted else base / "store_key"
    if not trusted:
        _gen_key(store_key)
    store = base / "allowed_signers"
    store.write_text(R.public_allowed_signers_line(str(store_key), V.PUBLISHER_IDENTITY) + "\n")
    repo = base / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env=env)
    g("init", "-q")
    (repo / "backend").mkdir()
    (repo / "backend" / "_version.py").write_text(f'APP_VERSION = "{version}"\n')
    (repo / "update.sh").write_text("#!/usr/bin/env bash\n")
    (repo / "requirements.txt").write_text("fastapi>=0.133.0,<1.0.0\n")
    (repo / "requirements-aarch64.lock").write_text("fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64))
    (repo / "requirements-armv7-build.lock").write_text("fastapi==0.133.0 --hash=sha256:%s\n" % _SDH)
    (repo / "release" / "builder").mkdir(parents=True)
    (repo / "release" / "builder" / "Containerfile").write_text(_RECIPE)
    (repo / "release" / "builder" / "requirements-build-backends.lock").write_text(_BB_LOCK)
    (repo / "release" / "builder" / "apt-packages.list").write_text(_APT)
    (repo / "release" / "builder" / "rustup-init.sha256").write_text(_RUSTUP)
    (repo / "release" / "builder" / "requirements-extractor-tools.in").write_text(_EXT_IN)
    (repo / "release" / "builder" / "requirements-extractor-tools.lock").write_text(_EXT_LOCK)
    (repo / "release" / "builder" / "requirements-build-backends.source-allowlist").write_text(_ALLOWLIST)
    g("add", "-A")
    g("commit", "-q", "-m", "c")
    g("tag", f"v{version}")
    wh = base / "wh"
    wh.mkdir()
    wname = "fastapi-0.133.0-py3-none-any.whl"
    wheel = b"WHEELBYTES"
    (wh / wname).write_bytes(wheel)
    wsha = hashlib.sha256(wheel).hexdigest()
    (wh / "SHA256SUMS").write_text("%s  %s\n" % (wsha, wname))
    bundle = R.sha256_hex(R.pack_tree(R._wheelhouse_members(str(wh))))
    prov = base / "prov.json"
    prov.write_text(json.dumps({"builder": _builder(),
                                "bundle": {"sha256": bundle},
                                "wheels": [{"sdist_name": "fastapi-0.133.0.tar.gz", "sdist_sha256": _SDH,
                                            "wheel_filename": wname, "wheel_sha256": wsha}]}))
    runtime = base / "requirements-armv7.lock"
    runtime.write_text("fastapi==0.133.0 --hash=sha256:%s\n" % wsha)
    manifest = base / "image-manifest.json"
    manifest.write_bytes(_MANIFEST)
    res = R.produce_release(version=version, out_dir=str(base / "dist"), key_path=str(key),
                            wheelhouse_armv7_dir=str(wh), provenance_armv7_path=str(prov),
                            armv7_runtime_lock_path=str(runtime),
                            image_manifest_path=str(manifest),
                            git_ref=f"v{version}", repo_dir=str(repo),
                            recommended_conduit_core="2.0.0")
    return {"manifest": res["manifest"], "signature": res["signature"],
            "aarch64": res["artifacts"]["aarch64"], "armv7l": res["artifacts"]["armv7l"],
            "store": str(store)}


def _good_manifest():
    return {
        "format_version": 2, "product": V.PRODUCT, "version": "0.3.16",
        "source": {"vcs": "git", "commit": _COMMIT, "tag": "v0.3.16"},
        "artifacts": [
            {"platform": "aarch64", "name": "ccc-0.3.16-aarch64.tar.gz",
             "top_level": ["backend", "requirements.txt"],
             "digest": {"algorithm": "sha256", "value": "a" * 64}},
            {"platform": "armv7l", "name": "ccc-0.3.16-armv7l.tar.gz",
             "top_level": ["backend", "wheelhouse-armhf", "provenance", "requirements-armv7.lock"],
             "digest": {"algorithm": "sha256", "value": "b" * 64},
             "wheelhouse": {"path": "wheelhouse-armhf/", "bundle_sha256": "c" * 64,
                            "requirements_sha256": _REQ, "lock_sha256": _VLOCK,
                            "build_lock_sha256": _BLOCK,
                            "provenance": "provenance/wheelhouse-armv7.json",
                            "provenance_sha256": "d" * 64}},
        ],
        "dependency_locks": {"requirements_sha256": _REQ, "aarch64_lock_sha256": _ALOCK,
                             "armv7_lock_sha256": _VLOCK, "armv7_build_lock_sha256": _BLOCK},
        "compatibility": {},
    }


def test_read_trust_store_fail_closed(tmp_path):
    assert V.read_trust_store(str(tmp_path / "nope")) is None
    good = tmp_path / "good"
    good.write_text("p ssh-ed25519 AAAA\n")
    assert V.read_trust_store(str(good)) == ["p ssh-ed25519 AAAA"]


def test_parse_accepts_good_and_selects():
    obj = V.parse_verified_manifest(json.dumps(_good_manifest()).encode())
    assert V.select_platform_entry(obj, "armv7l")["name"] == "ccc-0.3.16-armv7l.tar.gz"
    assert V.select_platform_entry(obj, "riscv64") is None


def test_parse_rejects_malformed():
    g = _good_manifest()

    def mut(fn):
        m = json.loads(json.dumps(g))
        fn(m)
        return json.dumps(m).encode()

    bads = [
        b"not json",
        mut(lambda m: m.update(format_version=1)),
        mut(lambda m: m.update(product="other")),
        mut(lambda m: m["source"].update(commit="xyz")),
        mut(lambda m: m["source"].update(tag="0.3.16")),       # tag must be v{version}
        mut(lambda m: m["source"].update(tag="v0.3.15")),
        mut(lambda m: m.__setitem__("artifacts", [m["artifacts"][0]])),        # both required
        mut(lambda m: m["artifacts"][0].__setitem__("wheelhouse", {"path": "x"})),
        mut(lambda m: m["artifacts"][0].__setitem__("name", "wrong.tar.gz")),
        mut(lambda m: m["dependency_locks"].__delitem__("armv7_build_lock_sha256")),  # 4th lock required
        mut(lambda m: m["dependency_locks"].__setitem__("requirements_sha256", "x")),
        mut(lambda m: m["artifacts"][1]["wheelhouse"].__delitem__("build_lock_sha256")),  # strict wheelhouse
        mut(lambda m: m["artifacts"][1]["wheelhouse"].__setitem__("bundle_sha256", "x")),
        mut(lambda m: m["artifacts"][1]["wheelhouse"].__setitem__("build_lock_sha256", "9" * 64)),  # disagree w/ locks
        mut(lambda m: m["artifacts"].append(m["artifacts"][0])),                # duplicate platform
        mut(lambda m: m["artifacts"][0].__setitem__("top_level", [])),          # empty allowlist
        mut(lambda m: m["artifacts"][0].__setitem__("top_level", ["a/b"])),     # non-bare allowlist entry
    ]
    for bad in bads:
        with pytest.raises(V.VerifyError):
            V.parse_verified_manifest(bad)


def test_verify_missing_store_fail_closed(tmp_path):
    r = V.verify_release(manifest_path=str(tmp_path / "m"), signature_path=str(tmp_path / "s"),
                         artifact_path=str(tmp_path / "a"), trust_store_path=str(tmp_path / "no"),
                         platform="aarch64")
    assert r.ok is False and r.reason == V.REASON_STORE


@_e2e
def test_genuine_verifies_each_platform(tmp_path):
    p = _make_release(tmp_path)
    for plat in ("aarch64", "armv7l"):
        r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                             artifact_path=p[plat], trust_store_path=p["store"], platform=plat)
        assert r.ok and r.metadata["platform"] == plat
        assert r.metadata["dependency_locks"]["armv7_build_lock_sha256"]


@_e2e
def test_platform_mismatch_rejected_no_fallback(tmp_path):
    p = _make_release(tmp_path)
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["aarch64"], trust_store_path=p["store"], platform="armv7l")
    assert r.ok is False and r.reason == V.REASON_DIGEST


@_e2e
def test_unknown_platform_fails_closed(tmp_path):
    p = _make_release(tmp_path)
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["aarch64"], trust_store_path=p["store"], platform="riscv64")
    assert r.ok is False and r.reason == V.REASON_PLATFORM


@_e2e
def test_tampered_manifest_rejected(tmp_path):
    p = _make_release(tmp_path)
    with open(p["manifest"], "ab") as fh:
        fh.write(b" ")
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["aarch64"], trust_store_path=p["store"], platform="aarch64")
    assert r.ok is False and r.reason == V.REASON_SIGNATURE


@_e2e
def test_untrusted_signer_rejected(tmp_path):
    p = _make_release(tmp_path, trusted=False)
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["aarch64"], trust_store_path=p["store"], platform="aarch64")
    assert r.ok is False and r.reason == V.REASON_SIGNATURE and r.metadata is None


@_e2e
def test_digest_mismatch_rejected(tmp_path):
    p = _make_release(tmp_path)
    with open(p["aarch64"], "ab") as fh:
        fh.write(b"tampered")
    r = V.verify_release(manifest_path=p["manifest"], signature_path=p["signature"],
                         artifact_path=p["aarch64"], trust_store_path=p["store"], platform="aarch64")
    assert r.ok is False and r.reason == V.REASON_DIGEST
