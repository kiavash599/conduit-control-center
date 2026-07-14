# SPDX-License-Identifier: MIT
"""ADR-0003 Epic A — Signed Release Production tests (V2, hardened round 3).

Covers the four fail-open corrections: tagged-source-only provenance (no --source
bypass); closed lock grammar; OCI builder digest; exact platform cardinality --
plus the earlier V2 hardening (signed top-level allowlist, injected runtime lock,
strict provenance cross-checked against the wheelhouse AND the build-input lock)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from release import ccc_release as R

_HAS_SSH = shutil.which("ssh-keygen") is not None
_HAS_GIT = shutil.which("git") is not None
_ssh = pytest.mark.skipif(not _HAS_SSH, reason="ssh-keygen not available")
_git = pytest.mark.skipif(not _HAS_GIT, reason="git not available")

_COMMIT = "0" * 40
_SRC = {"vcs": "git", "commit": _COMMIT, "tag": "v0.3.16"}
_REQ, _ALOCK, _VLOCK, _BLOCK = "1" * 64, "2" * 64, "3" * 64, "4" * 64
_SDH = "e" * 64
_DIGEST = "sha256:" + "a" * 64        # valid OCI builder digest
_REQ_TXT = "fastapi>=0.133.0,<1.0.0\n"


def _wh():
    return {"path": "wheelhouse-armhf/", "bundle_sha256": "c" * 64,
            "requirements_sha256": _REQ, "lock_sha256": _VLOCK, "build_lock_sha256": _BLOCK,
            "provenance": "provenance/wheelhouse-armv7.json", "provenance_sha256": "d" * 64}


def _locks():
    return {"requirements_sha256": _REQ, "aarch64_lock_sha256": _ALOCK,
            "armv7_lock_sha256": _VLOCK, "armv7_build_lock_sha256": _BLOCK}


def _entry(platform, name):
    tl = (["backend", "requirements.txt"] if platform == "aarch64"
          else ["backend", "wheelhouse-armhf", "provenance", "requirements-armv7.lock"])
    return R.build_artifact_entry(platform=platform, name=name, artifact_bytes=b"\x1f\x8b" + platform.encode(),
                                  top_level=tl, wheelhouse=(_wh() if platform == "armv7l" else None))


def _entries(version="0.3.16"):
    return [_entry("aarch64", f"ccc-{version}-aarch64.tar.gz"),
            _entry("armv7l", f"ccc-{version}-armv7l.tar.gz")]


def _gen_key(path):
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
                   check=True, capture_output=True)


# --- schema + cardinality (defect 4) --------------------------------------- #

def test_build_manifest_v2_fields():
    m = R.build_manifest(version="0.3.16", source=_SRC, artifacts=_entries(), dependency_locks=_locks())
    assert m["format_version"] == 2
    assert [a["platform"] for a in m["artifacts"]] == ["aarch64", "armv7l"]
    assert m["artifacts"][0]["top_level"] == ["backend", "requirements.txt"]
    assert set(m["dependency_locks"]) == {"requirements_sha256", "aarch64_lock_sha256",
                                          "armv7_lock_sha256", "armv7_build_lock_sha256"}


def test_platform_cardinality_exactly_one_each():
    a = _entry("aarch64", "ccc-0.3.16-aarch64.tar.gz")
    v = _entry("armv7l", "ccc-0.3.16-armv7l.tar.gz")
    R.build_manifest(version="0.3.16", source=_SRC, artifacts=[a, v], dependency_locks=_locks())  # ok
    riscv = {"platform": "riscv64", "name": "ccc-0.3.16-riscv64.tar.gz",
             "digest": {"algorithm": "sha256", "value": "a" * 64}, "top_level": ["backend"]}
    for bad in ([a, a, v], [a, v, v], [a], [v], [], [a, v, riscv], [a, riscv]):
        with pytest.raises(R.ReleaseError):
            R.build_manifest(version="0.3.16", source=_SRC, artifacts=bad, dependency_locks=_locks())


def test_all_four_locks_mandatory():
    for drop in ("requirements_sha256", "aarch64_lock_sha256", "armv7_lock_sha256", "armv7_build_lock_sha256"):
        locks = _locks()
        del locks[drop]
        with pytest.raises(R.ReleaseError):
            R.build_manifest(version="0.3.16", source=_SRC, artifacts=_entries(), dependency_locks=locks)


def test_source_tag_must_equal_v_version():
    for bad_tag in ("main", "0.3.16", "v0.3.15"):
        with pytest.raises(R.ReleaseError):
            R.build_manifest(version="0.3.16", source={"vcs": "git", "commit": _COMMIT, "tag": bad_tag},
                             artifacts=_entries(), dependency_locks=_locks())


def test_secret_scan_binary_exemption_and_text_scanning():
    with pytest.raises(R.ReleaseError):
        R._secret_scan({".env": b"S=1\n"})
    with pytest.raises(R.ReleaseError):
        R._secret_scan({"wheelhouse-armhf/SHA256SUMS": b"abc\x00"})
    with pytest.raises(R.ReleaseError):
        R._secret_scan({"scripts/ccc-unlock": b"#!/bin/sh\x00"})
    R._secret_scan({"wheelhouse-armhf/x.whl": b"PK\x00\x00"})


# --- closed lock grammar (defect 2) ---------------------------------------- #

def test_parse_lock_pins_rejects_directives():
    good = "fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64)
    assert R._parse_lock_pins(good)["fastapi"][0] == "0.133.0"
    for bad in ("--index-url https://x/simple\n" + good,
                "--extra-index-url https://x\n" + good,
                "--find-links ./w\n" + good,
                "-r other.txt\n" + good,
                "-e .\n" + good,
                "https://x/pkg.whl\n" + good,
                "fastapi==0.133.0 ; python_version<'3'\n",
                "fastapi==0.133.0\n",                              # unhashed
                "fastapi>=0.133.0 --hash=sha256:%s\n" % ("a" * 64),  # unpinned
                "fastapi==0.133.0 --hash=sha256:xyz\n",            # malformed hash
                "fastapi==0.133.0 --hash=sha256:%s extra\n" % ("a" * 64),  # trailing token
                good + good):                                      # duplicate
        with pytest.raises(R.ReleaseError):
            R._parse_lock_pins(bad)


# --- provenance schema + builder digest (defect 3) + build-lock ------------ #

def _prov_case(*, wname="fastapi-0.133.0-py3-none-any.whl", sname="fastapi-0.133.0.tar.gz",
               sdh=_SDH, build="fastapi==0.133.0 --hash=sha256:%s\n" % _SDH,
               image=_DIGEST, dup=False, break_bundle=False):
    wheel = b"WHEELBYTES"
    wsha = hashlib.sha256(wheel).hexdigest()
    members = {f"wheelhouse-armhf/{wname}": wheel,
               "wheelhouse-armhf/SHA256SUMS": ("%s  %s\n" % (wsha, wname)).encode()}
    bundle = R.sha256_hex(R.pack_tree(members))
    wheels = [{"sdist_name": sname, "sdist_sha256": sdh, "wheel_filename": wname, "wheel_sha256": wsha}]
    if dup:
        wheels.append(dict(wheels[0]))
    prov = {"builder": {"identity": "b", "image_digest": image},
            "bundle": {"sha256": "f" * 64 if break_bundle else bundle}, "wheels": wheels}
    return prov, members, bundle, build


def test_provenance_valid():
    prov, members, bundle, build = _prov_case()
    R._validate_provenance(prov, members, bundle, build)


@pytest.mark.parametrize("bad_image", [
    "", "not-a-digest", "sha256:short", "sha256:" + "A" * 64, "sha256:" + "g" * 64,
    " sha256:" + "a" * 64, "sha512:" + "a" * 64, "image:latest", "sha256:" + "a" * 63,
])
def test_provenance_builder_digest_must_be_oci(bad_image):
    prov, members, bundle, build = _prov_case(image=bad_image)
    with pytest.raises(R.ReleaseError):
        R._validate_provenance(prov, members, bundle, build)


@pytest.mark.parametrize("kw", [
    {"break_bundle": True}, {"sdh": "a" * 64}, {"sname": "fastapi-0.134.0.tar.gz"}, {"dup": True},
])
def test_provenance_negatives(kw):
    prov, members, bundle, build = _prov_case(**kw)
    with pytest.raises(R.ReleaseError):
        R._validate_provenance(prov, members, bundle, build)


def test_provenance_unapproved_sdist_absent_from_build_lock():
    prov, members, bundle, _ = _prov_case()
    with pytest.raises(R.ReleaseError):
        R._validate_provenance(prov, members, bundle, "other==1.0.0 --hash=sha256:%s\n" % _SDH)


# --- runtime lock <-> embedded wheels (finding 1) -------------------------- #

def _rt_members(wheel=b"WHEELBYTES", wname="fastapi-0.133.0-py3-none-any.whl"):
    wsha = hashlib.sha256(wheel).hexdigest()
    return {f"wheelhouse-armhf/{wname}": wheel,
            "wheelhouse-armhf/SHA256SUMS": ("%s  %s\n" % (wsha, wname)).encode()}, wsha


def test_runtime_lock_bijection_ok():
    members, wsha = _rt_members()
    R._validate_runtime_lock_against_wheelhouse("fastapi==0.133.0 --hash=sha256:%s\n" % wsha, members, _REQ_TXT)


@pytest.mark.parametrize("lock", [
    "fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64),   # hash mismatch
    "other==1.0.0 --hash=sha256:%s\n" % ("c" * 64),       # extra pin / missing wheel
    "fastapi==0.134.0 --hash=sha256:%s\n" % hashlib.sha256(b"WHEELBYTES").hexdigest(),  # version mismatch
    "fastapi==0.133.0\n",                                  # unhashed
])
def test_runtime_lock_negatives(lock):
    members, _ = _rt_members()
    with pytest.raises(R.ReleaseError):
        R._validate_runtime_lock_against_wheelhouse(lock, members, _REQ_TXT)


# --- deterministic artifact ------------------------------------------------- #

def test_deterministic_artifact_byte_stable(tmp_path):
    src = tmp_path / "s"
    (src / "backend").mkdir(parents=True)
    (src / "backend" / "_version.py").write_text('APP_VERSION = "0.3.16"\n')
    assert R.build_deterministic_artifact(str(src)) == R.build_deterministic_artifact(str(src))


# --- tagged-source-only provenance (defect 1) + end to end ----------------- #

def _release_repo(tmp, version="0.3.16", aarch64_lock=None, build_lock=None):
    r = tmp / "repo"
    r.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def g(*a):
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, env=env)
    g("init", "-q")
    (r / "backend").mkdir()
    (r / "backend" / "_version.py").write_text(f'APP_VERSION = "{version}"\n')
    (r / "update.sh").write_text("#!/usr/bin/env bash\n")
    (r / "requirements.txt").write_text(_REQ_TXT)
    (r / "requirements-aarch64.lock").write_text(aarch64_lock or "fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64))
    (r / "requirements-armv7-build.lock").write_text(build_lock or "fastapi==0.133.0 --hash=sha256:%s\n" % _SDH)
    g("add", "-A")
    g("commit", "-q", "-m", "c")
    g("tag", f"v{version}")
    return str(r)


def _wheelhouse_prov_lock(tmp):
    wh = tmp / "wh"
    wh.mkdir()
    wname = "fastapi-0.133.0-py3-none-any.whl"
    wheel = b"WHEELBYTES"
    (wh / wname).write_bytes(wheel)
    wsha = hashlib.sha256(wheel).hexdigest()
    (wh / "SHA256SUMS").write_text("%s  %s\n" % (wsha, wname))
    bundle = R.sha256_hex(R.pack_tree(R._wheelhouse_members(str(wh))))
    prov = tmp / "prov.json"
    prov.write_text(json.dumps({"builder": {"identity": "b", "image_digest": _DIGEST},
                                "bundle": {"sha256": bundle},
                                "wheels": [{"sdist_name": "fastapi-0.133.0.tar.gz", "sdist_sha256": _SDH,
                                            "wheel_filename": wname, "wheel_sha256": wsha}]}))
    runtime = tmp / "requirements-armv7.lock"
    runtime.write_text("fastapi==0.133.0 --hash=sha256:%s\n" % wsha)
    return str(wh), str(prov), str(runtime)


@_git
def test_produce_release_rejects_caller_asserted_source(tmp_path):
    _release_repo(tmp_path)
    wh, prov, runtime = _wheelhouse_prov_lock(tmp_path)
    key = tmp_path / "k"
    _gen_key(key) if _HAS_SSH else key.write_text("x")
    with pytest.raises(R.ReleaseError):                 # --source path removed (I4)
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          wheelhouse_armv7_dir=wh, provenance_armv7_path=prov, armv7_runtime_lock_path=runtime,
                          source_dir=str(tmp_path), source_commit=_COMMIT, source_tag="v0.3.16")


@_git
def test_produce_release_requires_runtime_lock(tmp_path):
    repo = _release_repo(tmp_path)
    wh, prov, _ = _wheelhouse_prov_lock(tmp_path)
    key = tmp_path / "k"
    _gen_key(key) if _HAS_SSH else key.write_text("x")
    with pytest.raises(R.ReleaseError):
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          wheelhouse_armv7_dir=wh, provenance_armv7_path=prov,
                          armv7_runtime_lock_path=str(tmp_path / "missing.lock"),
                          git_ref="v0.3.16", repo_dir=repo)


@_git
def test_produce_release_rejects_malformed_committed_lock(tmp_path):
    bad = "--index-url https://evil.invalid/simple\nfastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64)
    repo = _release_repo(tmp_path, aarch64_lock=bad)
    wh, prov, runtime = _wheelhouse_prov_lock(tmp_path)
    key = tmp_path / "k"
    _gen_key(key) if _HAS_SSH else key.write_text("x")
    with pytest.raises(R.ReleaseError):
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          wheelhouse_armv7_dir=wh, provenance_armv7_path=prov, armv7_runtime_lock_path=runtime,
                          git_ref="v0.3.16", repo_dir=repo)


@_git
def test_resolve_source_rejects_branch_and_missing_ref(tmp_path):
    repo = _release_repo(tmp_path)
    subprocess.run(["git", "-C", repo, "branch", "feature"], check=True, capture_output=True)
    for ref in ("feature", "nope", "HEAD"):
        with pytest.raises(R.ReleaseError):
            R._resolve_source(ref, repo)


@_git
def test_resolve_source_lightweight_and_annotated_tag_peel(tmp_path):
    repo = _release_repo(tmp_path)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    _raw, src = R._resolve_source("v0.3.16", repo)                 # lightweight tag
    assert src["commit"] == head and src["tag"] == "v0.3.16"
    subprocess.run(["git", "-C", repo, "tag", "-a", "v0.4.0", "-m", "a"], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    _raw2, src2 = R._resolve_source("v0.4.0", repo)                # annotated -> peels to commit
    assert src2["commit"] == head


@_git
@_ssh
def test_produce_release_v2_end_to_end(tmp_path):
    from backend import update_verify as V
    key = tmp_path / "pub_key"
    _gen_key(key)
    repo = _release_repo(tmp_path)
    wh, prov, runtime = _wheelhouse_prov_lock(tmp_path)
    res = R.produce_release(version="0.3.16", out_dir=str(tmp_path / "dist"), key_path=str(key),
                            wheelhouse_armv7_dir=wh, provenance_armv7_path=prov, armv7_runtime_lock_path=runtime,
                            git_ref="v0.3.16", repo_dir=repo, recommended_conduit_core="2.0.0")
    manifest = json.loads(open(res["manifest"], "rb").read())
    req_bytes = open(os.path.join(repo, "requirements.txt"), "rb").read().replace(b"\r\n", b"\n")
    assert manifest["dependency_locks"]["requirements_sha256"] == R.sha256_hex(req_bytes)
    assert manifest["source"]["tag"] == "v0.3.16" and len(manifest["source"]["commit"]) == 40
    armv7 = next(a for a in manifest["artifacts"] if a["platform"] == "armv7l")
    assert "requirements-armv7.lock" in armv7["top_level"]
    aarch = next(a for a in manifest["artifacts"] if a["platform"] == "aarch64")
    assert "wheelhouse-armhf" not in aarch["top_level"]
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(R.public_allowed_signers_line(str(key), V.PUBLISHER_IDENTITY) + "\n")
    for plat, path in res["artifacts"].items():
        r = V.verify_release(manifest_path=res["manifest"], signature_path=res["signature"],
                             artifact_path=path, trust_store_path=str(allowed), platform=plat)
        assert r.ok and r.metadata["top_level"]
