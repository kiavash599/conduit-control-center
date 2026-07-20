# SPDX-License-Identifier: MIT
"""Shared full 6+24=30 dual-origin release fixture (NOT a test module -- no test_ prefix, so pytest
does not collect it). Builds a tagged git repo + a Phase-B-style bundle (wheelhouse + provenance +
runtime lock + image manifest) that satisfies the approved dual-origin partition policy, so the
produce_release round-trip / verify / update tests exercise a REAL release rather than a 1+1 demo."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess

from release import ccc_release as R
from release import reuse_authz as RA
from release import transfer_manifest as TM

_ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET_TAGS_TEXT = (_ROOT / "release" / "builder" / "target-supported-tags.txt").read_text(encoding="utf-8")
TARGET_TAGS_SHA = hashlib.sha256(TARGET_TAGS_TEXT.encode()).hexdigest()

_BB_LOCK = "maturin==1.5.1 --hash=sha256:%s\n" % ("7" * 64)
_APT = "build-essential=12.9ubuntu3\n"
_RUSTUP = "f" * 64 + "  rustup-init\n"
_EXT_IN = "tomli==2.0.1\n"
_EXT_LOCK = "tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
_ALLOWLIST = "maturin\n"
_PARTITION_BACKENDS = "# synthetic partition_backends.py (image-context entry)\n"
_RECIPE = "FROM base\nRUN true\n"
# The six-entry image context, computed from the SAME bytes this fixture commits, so provenance
# describes exactly the committed build context (what produce_release recomputes and requires).
IMAGE_CONTEXT = {
    "release/builder/Containerfile": R.canonical_file_sha256(_RECIPE.encode()),
    "release/builder/apt-packages.list": R.canonical_file_sha256(_APT.encode()),
    "release/builder/rustup-init.sha256": R.canonical_file_sha256(_RUSTUP.encode()),
    "release/builder/requirements-build-backends.lock": R.canonical_file_sha256(_BB_LOCK.encode()),
    "release/builder/requirements-build-backends.source-allowlist": R.canonical_file_sha256(_ALLOWLIST.encode()),
    "release/builder/partition_backends.py": R.canonical_file_sha256(_PARTITION_BACKENDS.encode()),
}
_ENV = {"os": "Ubuntu 22.04.5 LTS", "python": "Python 3.10.12", "rustc": "rustc 1.75.0",
        "cargo": "cargo 1.75.0", "gcc": "gcc 11.4.0", "glibc": "2.35",
        "os_id": "ubuntu", "os_version_id": "22.04", "arch": "armv7l", "apt_architecture": "armhf",
        "apt": {"build-essential": "12.9ubuntu3"}, "build_backends": {"maturin": "1.5.1"}}

BUILT = sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES)              # 6 source-built package names
REUSED = ["reusepkg%02d" % i for i in range(1, 25)]        # 24 synthetic reused packages
ALL30 = sorted(BUILT + REUSED)


def _manifest_bytes(cfg):
    return json.dumps({
        "schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": cfg, "size": 1234},
        "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": "sha256:" + "a" * 64, "size": 5678}]}).encode()


CONFIG_DIGEST = "sha256:" + "c" * 64
MANIFEST = _manifest_bytes(CONFIG_DIGEST)
RUNTIME_ID = "sha256:" + hashlib.sha256(MANIFEST).hexdigest()   # containerd: .Id == manifest digest


def _builder(recipe_sha):
    return {"identity": "ccc-armv7-builder", "recipe_path": R.CANONICAL_RECIPE_PATH,
            "recipe_sha256": recipe_sha, "build_backends_lock_sha256": R.sha256_hex(_BB_LOCK.encode()),
            "apt_packages_sha256": R.sha256_hex(_APT.encode()),
            "rustup_init_file_sha256": R.sha256_hex(_RUSTUP.encode()),
            "extractor_tools_lock_sha256": R.sha256_hex(_EXT_LOCK.encode()),
            "build_backends_source_allowlist_sha256": R.sha256_hex(_ALLOWLIST.encode()),
            "image_context": dict(IMAGE_CONTEXT),
            "image_context_sha256": R.image_context_digest(IMAGE_CONTEXT),
            "base_image_digest": "sha256:" + "b" * 64, "image_manifest_digest": RUNTIME_ID,
            "image_config_digest": CONFIG_DIGEST, "image_identity_mode": "containerd",
            "runtime_image_id": RUNTIME_ID, "environment": dict(_ENV),
            "environment_sha256": R.sha256_hex(R._canonical_env_bytes(_ENV))}


def make_release(base: pathlib.Path, *, version="0.3.16"):
    """Return dict(repo, wheelhouse_dir, provenance_path, runtime_lock_path, image_manifest_path)."""
    repo = base / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env=env)

    # 6 built: name-1.0-py3-none-any.whl from name-1.0.tar.gz; 24 reused: reusepkgNN-1.0-py3-none-any.whl
    build_lines, built_wheels = [], {}
    for n in BUILT:
        sd = ("SDIST:" + n).encode()
        ssha = hashlib.sha256(sd).hexdigest()
        wb = ("BUILT:" + n).encode()
        wf = "%s-1.0-py3-none-any.whl" % n
        built_wheels[n] = (wf, wb, "%s-1.0.tar.gz" % n, ssha)
        build_lines.append("%s==1.0 --hash=sha256:%s" % (n, ssha))
    reuse_wheels, authz_wheels = {}, []
    for n in REUSED:
        wb = ("REUSE:" + n).encode()
        wf = "%s-1.0-py3-none-any.whl" % n
        wsha = hashlib.sha256(wb).hexdigest()
        reuse_wheels[n] = (wf, wb, wsha)
        authz_wheels.append({"name": n, "version": "1.0", "filename": wf, "sha256": wsha,
                             "tags": ["py3-none-any"], "requires_python": ">=3.9"})
    authz = {"schema": RA.SCHEMA_ID, "origin": "pypi", "target": dict(RA.TARGET_PROFILE),
             "wheels": authz_wheels}
    authz_bytes = (json.dumps(authz, indent=2, sort_keys=True) + "\n").encode()
    req_txt = "".join("%s>=0\n" % n for n in ALL30)

    (repo / "backend").mkdir()
    (repo / "backend" / "_version.py").write_text('APP_VERSION = "%s"\n' % version)
    (repo / "update.sh").write_text("#!/usr/bin/env bash\n")
    (repo / "requirements.txt").write_text(req_txt)
    (repo / "requirements-aarch64.lock").write_text("".join(
        "%s==1.0 --hash=sha256:%s\n" % (n, "a" * 64) for n in ALL30))
    (repo / "requirements-armv7-build.lock").write_text("\n".join(build_lines) + "\n")
    # durable 30-pin solution (distinct file): the full closure the build-lock+authz partition
    (repo / "requirements-armv7-solution.lock").write_text(
        "".join("%s==1.0 --hash=sha256:%s\n" % (n, "a" * 64) for n in ALL30))
    b = repo / "release" / "builder"
    b.mkdir(parents=True)
    (b / "armv7-reuse-authz.json").write_bytes(authz_bytes)
    (b / "target-supported-tags.txt").write_text(TARGET_TAGS_TEXT)
    (b / "Containerfile").write_text(_RECIPE)
    (b / "requirements-build-backends.lock").write_text(_BB_LOCK)
    (b / "apt-packages.list").write_text(_APT)
    (b / "rustup-init.sha256").write_text(_RUSTUP)
    (b / "requirements-extractor-tools.in").write_text(_EXT_IN)
    (b / "requirements-extractor-tools.lock").write_text(_EXT_LOCK)
    (b / "requirements-build-backends.source-allowlist").write_text(_ALLOWLIST)
    (b / "partition_backends.py").write_text(_PARTITION_BACKENDS)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-q", "-m", "c")
    g("tag", "v%s" % version)

    bundle = base / "bundle"
    wh = bundle / "wheelhouse-armhf"
    wh.mkdir(parents=True)
    sums, wheels, runtime = [], [], []
    for n in BUILT:
        wf, wb, sdn, ssha = built_wheels[n]
        (wh / wf).write_bytes(wb)
        wsha = hashlib.sha256(wb).hexdigest()
        sums.append((wf, wsha))
        wheels.append({"origin": "built", "sdist_name": sdn, "sdist_sha256": ssha,
                       "wheel_filename": wf, "wheel_sha256": wsha})
        runtime.append("%s==1.0 --hash=sha256:%s" % (n, wsha))
    for n in REUSED:
        wf, wb, wsha = reuse_wheels[n]
        (wh / wf).write_bytes(wb)
        sums.append((wf, wsha))
        wheels.append({"origin": "reused", "name": n, "version": "1.0",
                       "wheel_filename": wf, "wheel_sha256": wsha, "tags": ["py3-none-any"]})
        runtime.append("%s==1.0 --hash=sha256:%s" % (n, wsha))
    # Canonical form Phase B emits: sorted by WHEEL FILENAME (not by the whole line/hash).
    (wh / "SHA256SUMS").write_text(
        "".join("%s  %s\n" % (d, n) for n, d in sorted(sums)), newline="")
    validated = RA.load_and_validate(authz_bytes, target_tags=set(TARGET_TAGS_TEXT.split()))
    members = R._wheelhouse_members(str(wh))
    tree_sha = R.wheelhouse_tree_digest(members)
    # REAL Phase-B bundle layout, so the MANDATORY producer transfer-manifest gate can verify it:
    #   <bundle>/{wheelhouse-armhf/, wheelhouse-armv7.json, requirements-armv7.lock,
    #             build-evidence.json}  +  the manifest OUTSIDE the bundle.
    prov = bundle / "wheelhouse-armv7.json"
    prov.write_text(json.dumps({
        "builder": _builder(R.sha256_hex(_RECIPE.encode())),
        "bundle": {"tree_digest": {"scheme": R._ltree.SCHEME, "sha256": tree_sha},
                   "member_count": len(members)},
        "authorizers": {"reuse_authz_sha256": RA.sha256_hex(RA.canonical_bytes(validated)),
                        "target_tags_sha256": TARGET_TAGS_SHA},
        "wheels": sorted(wheels, key=lambda w: w["wheel_filename"])}))
    rlock = bundle / "requirements-armv7.lock"
    # EXACT production form: the canonical header build_wheelhouse.py emits, then the pins.
    rlock.write_text(TM.RUNTIME_LOCK_HEADER + "\n" + "".join(ln + "\n" for ln in sorted(runtime)),
                     newline="")
    (bundle / "build-evidence.json").write_text(json.dumps(
        {"bundle_tree_sha256": tree_sha, "tree_scheme": R._ltree.SCHEME,
         "member_count": len(members), "wheel_count": len(wheels),
         "built": sorted(BUILT), "reused": sorted(REUSED),
         "authorizers": {}, "partition_policy_enforced": True}, sort_keys=True))
    xfer = base / "phase-b-bundle-transfer-manifest.json"
    TM.generate(str(bundle), str(xfer))
    man = base / "image-manifest.json"
    man.write_bytes(MANIFEST)
    return {"repo": str(repo), "wheelhouse_dir": str(wh), "provenance_path": str(prov),
            "runtime_lock_path": str(rlock), "image_manifest_path": str(man),
            "bundle_dir": str(bundle), "transfer_manifest_path": str(xfer)}


def recommit(repo: str, *, version="0.3.16"):
    """Re-commit working-tree edits and move the release tag (for negative fixtures that mutate a
    committed file after make_release)."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def g(*a):
        subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, env=env)
    g("add", "-A")
    g("commit", "-q", "--amend", "--no-edit")
    g("tag", "-f", "v%s" % version)
