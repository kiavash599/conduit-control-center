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
import pathlib
import shutil
import subprocess

import pytest

from release import ccc_release as R
from tests.unit import _hybrid_release_fixture as _HF

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
_RECIPE_CONTENT = "FROM base\nRUN true\n"                 # committed builder recipe (LF)
_RECIPE_SHA = R.sha256_hex(_RECIPE_CONTENT.encode())
_BB_LOCK = "maturin==1.5.1 --hash=sha256:%s\n" % ("7" * 64)   # committed build-backends lock
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


# Dual-origin (Amendment A5) fixture: one REUSED official wheel authorized alongside the BUILT one.
_REUSE_WHEEL = "idna-3.18-py3-none-any.whl"
_REUSE_BYTES = b"IDNA-REUSED-BYTES"
_REUSE_SHA = hashlib.sha256(_REUSE_BYTES).hexdigest()
_REUSE_AUTHZ_JSON = json.dumps({
    "schema": "ccc-armv7-reuse-authz/1", "origin": "pypi",
    "target": {"python": "cp310", "platform": "armv7l", "glibc": "2.35"},
    "wheels": [{"name": "idna", "version": "3.18", "filename": _REUSE_WHEEL,
                "sha256": _REUSE_SHA, "tags": ["py3-none-any"], "requires_python": ">=3.9"}]})


def _builder(recipe_sha=_RECIPE_SHA, **over):
    b = {"identity": "ccc-armv7-builder", "recipe_path": R.CANONICAL_RECIPE_PATH,
         "recipe_sha256": recipe_sha, "build_backends_lock_sha256": _BB_SHA,
         "apt_packages_sha256": _APT_SHA, "rustup_init_file_sha256": _RUSTUP_SHA,
         "extractor_tools_lock_sha256": _EXT_LOCK_SHA,
         "build_backends_source_allowlist_sha256": _ALLOWLIST_SHA,
         "base_image_digest": "sha256:" + "b" * 64, "image_manifest_digest": _MANIFEST_DIGEST,
         "image_config_digest": _CONFIG_DIGEST, "image_identity_mode": "containerd",
         "runtime_image_id": _RUNTIME_ID, "environment": dict(_ENV),
         "environment_sha256": R.sha256_hex(R._canonical_env_bytes(_ENV))}
    b.update(over)
    return b


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
               builder=None, dup=False, break_bundle=False):
    wheel = b"WHEELBYTES"
    wsha = hashlib.sha256(wheel).hexdigest()
    members = {f"wheelhouse-armhf/{wname}": wheel,
               "wheelhouse-armhf/SHA256SUMS": ("%s  %s\n" % (wsha, wname)).encode()}
    bundle = R.sha256_hex(R.pack_tree(members))
    # Dual-origin (A5): every provenance wheel record MUST declare its origin.
    wheels = [{"origin": "built", "sdist_name": sname, "sdist_sha256": sdh,
               "wheel_filename": wname, "wheel_sha256": wsha}]
    if dup:
        wheels.append(dict(wheels[0]))
    prov = {"builder": builder if builder is not None else _builder(),
            "bundle": {"sha256": "f" * 64 if break_bundle else bundle}, "wheels": wheels}
    return prov, members, bundle, build


def test_provenance_valid():
    prov, members, bundle, build = _prov_case()
    R._validate_provenance(prov, members, bundle, build, _RECIPE_SHA, _BB_SHA, _BB_LOCK, _APT_SHA, _RUSTUP_SHA, _APT, _EXT_LOCK_SHA, _ALLOWLIST_SHA)


@pytest.mark.parametrize("kw", [
    {"break_bundle": True}, {"sdh": "a" * 64}, {"sname": "fastapi-0.134.0.tar.gz"}, {"dup": True},
])
def test_provenance_negatives(kw):
    prov, members, bundle, build = _prov_case(**kw)
    with pytest.raises(R.ReleaseError):
        R._validate_provenance(prov, members, bundle, build, _RECIPE_SHA, _BB_SHA, _BB_LOCK, _APT_SHA, _RUSTUP_SHA, _APT, _EXT_LOCK_SHA, _ALLOWLIST_SHA)


def test_provenance_unapproved_sdist_absent_from_build_lock():
    prov, members, bundle, _ = _prov_case()
    with pytest.raises(R.ReleaseError):
        R._validate_provenance(prov, members, bundle, "other==1.0.0 --hash=sha256:%s\n" % _SDH, _RECIPE_SHA, _BB_SHA, _BB_LOCK, _APT_SHA, _RUSTUP_SHA, _APT, _EXT_LOCK_SHA, _ALLOWLIST_SHA)


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

def _release_repo(tmp, version="0.3.16", aarch64_lock=None, build_lock=None, with_extractor=True):
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
    (r / "release" / "builder").mkdir(parents=True)
    (r / "release" / "builder" / "armv7-reuse-authz.json").write_text(_REUSE_AUTHZ_JSON)
    (r / "release" / "builder" / "Containerfile").write_text(_RECIPE_CONTENT)
    (r / "release" / "builder" / "requirements-build-backends.lock").write_text(_BB_LOCK)
    (r / "release" / "builder" / "apt-packages.list").write_text(_APT)
    (r / "release" / "builder" / "rustup-init.sha256").write_text(_RUSTUP)
    if with_extractor:
        (r / "release" / "builder" / "requirements-extractor-tools.in").write_text(_EXT_IN)
        (r / "release" / "builder" / "requirements-extractor-tools.lock").write_text(_EXT_LOCK)
        (r / "release" / "builder" / "requirements-build-backends.source-allowlist").write_text(_ALLOWLIST)
    g("add", "-A")
    g("commit", "-q", "-m", "c")
    g("tag", f"v{version}")
    return str(r)


def _wheelhouse_prov_lock(tmp):
    from release import reuse_authz as _RA
    wh = tmp / "wh"
    wh.mkdir()
    wname = "fastapi-0.133.0-py3-none-any.whl"
    wheel = b"WHEELBYTES"
    (wh / wname).write_bytes(wheel)
    wsha = hashlib.sha256(wheel).hexdigest()
    (wh / _REUSE_WHEEL).write_bytes(_REUSE_BYTES)                        # dual-origin: reused wheel
    (wh / "SHA256SUMS").write_text("%s  %s\n%s  %s\n" % (wsha, wname, _REUSE_SHA, _REUSE_WHEEL))
    bundle = R.sha256_hex(R.pack_tree(R._wheelhouse_members(str(wh))))
    # target_tags is MANDATORY: the committed 495-tag artifact is the single target-compat source.
    authz_sha = _RA.sha256_hex(_RA.canonical_bytes(_RA.load_and_validate(
        _REUSE_AUTHZ_JSON.encode(), target_tags=set(_HF.TARGET_TAGS_TEXT.split()))))
    prov = tmp / "prov.json"
    prov.write_text(json.dumps({
        "builder": _builder(), "bundle": {"sha256": bundle},
        "authorizers": {"reuse_authz_sha256": authz_sha},
        "wheels": [
            {"origin": "built", "sdist_name": "fastapi-0.133.0.tar.gz", "sdist_sha256": _SDH,
             "wheel_filename": wname, "wheel_sha256": wsha},
            {"origin": "reused", "name": "idna", "version": "3.18",
             "wheel_filename": _REUSE_WHEEL, "wheel_sha256": _REUSE_SHA, "tags": ["py3-none-any"]},
        ]}))
    runtime = tmp / "requirements-armv7.lock"
    runtime.write_text("fastapi==0.133.0 --hash=sha256:%s\nidna==3.18 --hash=sha256:%s\n" % (wsha, _REUSE_SHA))
    (tmp / "image-manifest.json").write_bytes(_MANIFEST)
    return str(wh), str(prov), str(runtime)


@_git
def test_produce_release_rejects_caller_asserted_source(tmp_path):
    r = _HF.make_release(tmp_path)
    key = tmp_path / "k"
    _gen_key(key) if _HAS_SSH else key.write_text("x")
    with pytest.raises(R.ReleaseError):                 # --source path removed (I4)
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          image_manifest_path=r["image_manifest_path"], wheelhouse_armv7_dir=r["wheelhouse_dir"],
                          provenance_armv7_path=r["provenance_path"], armv7_runtime_lock_path=r["runtime_lock_path"],
                          source_dir=str(tmp_path), source_commit=_COMMIT, source_tag="v0.3.16")


@_git
def test_produce_release_requires_runtime_lock(tmp_path):
    r = _HF.make_release(tmp_path)
    key = tmp_path / "k"
    _gen_key(key) if _HAS_SSH else key.write_text("x")
    with pytest.raises(R.ReleaseError):
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          image_manifest_path=r["image_manifest_path"], wheelhouse_armv7_dir=r["wheelhouse_dir"],
                          provenance_armv7_path=r["provenance_path"],
                          armv7_runtime_lock_path=str(tmp_path / "missing.lock"),
                          git_ref="v0.3.16", repo_dir=r["repo"])


@_git
def test_produce_release_rejects_malformed_committed_lock(tmp_path):
    r = _HF.make_release(tmp_path)
    bad = "--index-url https://evil.invalid/simple\nfastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64)
    (pathlib.Path(r["repo"]) / "requirements-aarch64.lock").write_text(bad)
    _HF.recommit(r["repo"])
    key = tmp_path / "k"
    _gen_key(key) if _HAS_SSH else key.write_text("x")
    with pytest.raises(R.ReleaseError):
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          image_manifest_path=r["image_manifest_path"], wheelhouse_armv7_dir=r["wheelhouse_dir"],
                          provenance_armv7_path=r["provenance_path"], armv7_runtime_lock_path=r["runtime_lock_path"],
                          git_ref="v0.3.16", repo_dir=r["repo"])


@_git
def test_produce_release_full_dual_origin_6_24_30(tmp_path):
    # production-policy round trip: exactly six built + 24 reused = 30, one manifest + one signature.
    r = _HF.make_release(tmp_path)
    key = tmp_path / "k"
    if not _HAS_SSH:
        pytest.skip("ssh-keygen required")
    _gen_key(key)
    res = R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                            image_manifest_path=r["image_manifest_path"], wheelhouse_armv7_dir=r["wheelhouse_dir"],
                            provenance_armv7_path=r["provenance_path"], armv7_runtime_lock_path=r["runtime_lock_path"],
                            git_ref="v0.3.16", repo_dir=r["repo"])
    assert os.path.isfile(res["manifest"]) and os.path.isfile(res["signature"])
    assert os.path.isfile(res["artifacts"]["aarch64"]) and os.path.isfile(res["artifacts"]["armv7l"])


@_git
def test_produce_release_rejects_target_tags_sha_mismatch(tmp_path):
    # the committed target-tag artifact is recomputed from canonical source bytes and must match
    # provenance.authorizers.target_tags_sha256 exactly ("in Git" is not sufficient).
    r = _HF.make_release(tmp_path)
    if not _HAS_SSH:
        pytest.skip("ssh-keygen required")
    key = tmp_path / "k"
    _gen_key(key)
    prov = pathlib.Path(r["provenance_path"])
    obj = json.loads(prov.read_text())
    obj["authorizers"]["target_tags_sha256"] = "0" * 64
    prov.write_text(json.dumps(obj))
    with pytest.raises(R.ReleaseError):
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          image_manifest_path=r["image_manifest_path"], wheelhouse_armv7_dir=r["wheelhouse_dir"],
                          provenance_armv7_path=r["provenance_path"], armv7_runtime_lock_path=r["runtime_lock_path"],
                          git_ref="v0.3.16", repo_dir=r["repo"])


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
    # Uses the FULL 6+24=30 fixture: produce_release enforces the approved-partition policy, so a
    # reduced 1+1 demo repo is no longer a producible release by design.
    from backend import update_verify as V
    key = tmp_path / "pub_key"
    _gen_key(key)
    r = _HF.make_release(tmp_path)
    repo = r["repo"]
    res = R.produce_release(version="0.3.16", out_dir=str(tmp_path / "dist"), key_path=str(key),
                            image_manifest_path=r["image_manifest_path"],
                            wheelhouse_armv7_dir=r["wheelhouse_dir"], provenance_armv7_path=r["provenance_path"],
                            armv7_runtime_lock_path=r["runtime_lock_path"],
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


@_git
@_ssh
def test_producer_requires_extractor_tools_lock(tmp_path):
    # F1: the release/tag producer gate must REJECT an absent .in/.lock pair. Built on the full
    # 6+24=30 fixture (otherwise the partition policy, not the extractor gate, would be the rejecter),
    # with the extractor pair removed and re-committed so the ONLY defect is the missing lock.
    r = _HF.make_release(tmp_path)
    repo = r["repo"]
    os.remove(os.path.join(repo, "release", "builder", "requirements-extractor-tools.lock"))
    os.remove(os.path.join(repo, "release", "builder", "requirements-extractor-tools.in"))
    _HF.recommit(repo)
    key = tmp_path / "k"
    _gen_key(key) if _HAS_SSH else key.write_text("x")
    with pytest.raises(R.ReleaseError):
        R.produce_release(version="0.3.16", out_dir=str(tmp_path / "d"), key_path=str(key),
                          image_manifest_path=r["image_manifest_path"],
                          wheelhouse_armv7_dir=r["wheelhouse_dir"], provenance_armv7_path=r["provenance_path"],
                          armv7_runtime_lock_path=r["runtime_lock_path"],
                          git_ref="v0.3.16", repo_dir=repo)
