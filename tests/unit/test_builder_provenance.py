# SPDX-License-Identifier: MIT
"""Builder-provenance schema negatives (release/ccc_release._validate_builder).
Proves missing/forged/unbound builder evidence fails closed, that image_id is
REQUIRED and distinct from image_manifest_digest, that the manifest digest is
independently recomputed from the raw OCI manifest bytes (a local id cannot
masquerade), that the environment is bound to the authorized backend lock and to a
target-compatible glibc baseline, and that apt is required."""
from __future__ import annotations

import pytest

from release import ccc_release as R


def _manifest_bytes(image_id):
    import json as _json
    return _json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                   "digest": image_id, "size": 1234},
        "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": "sha256:" + "a" * 64, "size": 5678}],
    }).encode()

_RS = "a" * 64                                  # committed-recipe sha the validator is called with
_BB_LOCK = "maturin==1.5.1 --hash=sha256:%s\n" % ("7" * 64)
_BB_SHA = R.sha256_hex(_BB_LOCK.encode())
_APT_SHA = "1" * 64
_RUSTUP_SHA = "2" * 64
_EXT_SHA = "3" * 64
_APT_TEXT = "build-essential=12.9ubuntu3\n"
_ENV = {"os": "Ubuntu 22.04.5 LTS", "python": "Python 3.10.12", "rustc": "rustc 1.75.0",
        "cargo": "cargo 1.75.0", "gcc": "gcc 11.4.0", "glibc": "2.35",
        "os_id": "ubuntu", "os_version_id": "22.04", "arch": "armv7l", "apt_architecture": "armhf",
        "apt": {"build-essential": "12.9ubuntu3"}, "build_backends": {"maturin": "1.5.1", "wheel": "0.43.0"}}


def _kw(**over):
    d = {"recipe_sha256": _RS, "build_backends_lock_sha256": _BB_SHA, "build_backends_lock_text": _BB_LOCK,
         "apt_packages_sha256": _APT_SHA, "rustup_init_file_sha256": _RUSTUP_SHA,
         "apt_packages_text": _APT_TEXT, "extractor_tools_lock_sha256": _EXT_SHA}
    d.update(over)
    return d


def _builder(**over):
    b = {"identity": "ccc-armv7-builder", "recipe_path": R.CANONICAL_RECIPE_PATH, "recipe_sha256": _RS,
         "build_backends_lock_sha256": _BB_SHA, "apt_packages_sha256": _APT_SHA,
         "rustup_init_file_sha256": _RUSTUP_SHA, "extractor_tools_lock_sha256": _EXT_SHA,
         "base_image_digest": "sha256:" + "b" * 64,
         "image_manifest_digest": "sha256:" + "c" * 64, "image_id": "sha256:" + "d" * 64,
         "environment": dict(_ENV), "environment_sha256": R.sha256_hex(R._canonical_env_bytes(_ENV))}
    b.update(over)
    return b


def test_valid():
    R._validate_builder(_builder(), **_kw())


def test_identity_required():
    with pytest.raises(R.ReleaseError):
        R._validate_builder(_builder(identity=""), **_kw())


def test_legacy_image_digest_rejected():
    b = _builder()
    b["image_digest"] = "sha256:" + "e" * 64
    with pytest.raises(R.ReleaseError):
        R._validate_builder(b, **_kw())


def test_recipe_and_backend_lock_binding():
    with pytest.raises(R.ReleaseError):
        R._validate_builder(_builder(recipe_sha256="f" * 64), **_kw())          # recipe mismatch
    with pytest.raises(R.ReleaseError):
        R._validate_builder(_builder(build_backends_lock_sha256="f" * 64), **_kw())  # lock sha mismatch


def test_image_id_required_and_distinct():
    b = _builder()
    del b["image_id"]
    with pytest.raises(R.ReleaseError):                                          # local-id-only / missing
        R._validate_builder(b, **_kw())
    dig = "sha256:" + "c" * 64
    with pytest.raises(R.ReleaseError):                                          # id == manifest digest
        R._validate_builder(_builder(image_id=dig, image_manifest_digest=dig), **_kw())


def test_manifest_digest_recomputed_from_oci_bytes():
    manifest = _manifest_bytes("sha256:" + "d" * 64)
    good = "sha256:" + R.sha256_hex(manifest)
    R._validate_builder(_builder(image_manifest_digest=good), **_kw(manifest_bytes=manifest))   # ok
    with pytest.raises(R.ReleaseError):                                          # syntactically valid but != sha256(manifest)
        R._validate_builder(_builder(image_manifest_digest="sha256:" + "c" * 64), **_kw(manifest_bytes=manifest))


@pytest.mark.parametrize("bad", [
    "", "not-a-digest", "sha256:short", "sha256:" + "A" * 64, "sha256:" + "g" * 64,
    "sha512:" + "a" * 64, "image:latest", "sha256:" + "a" * 63,
])
def test_base_and_manifest_and_id_must_be_oci(bad):
    for field in ("base_image_digest", "image_manifest_digest", "image_id"):
        with pytest.raises(R.ReleaseError):
            R._validate_builder(_builder(**{field: bad}), **_kw())


def test_environment_completeness_apt_and_backends_and_glibc():
    with pytest.raises(R.ReleaseError):                                          # missing glibc
        e = dict(_ENV)
        del e["glibc"]
        R._validate_builder(_builder(environment=e, environment_sha256=R.sha256_hex(R._canonical_env_bytes(e))), **_kw())
    with pytest.raises(R.ReleaseError):                                          # apt missing/empty
        e = dict(_ENV)
        e["apt"] = {}
        R._validate_builder(_builder(environment=e, environment_sha256=R.sha256_hex(R._canonical_env_bytes(e))), **_kw())
    with pytest.raises(R.ReleaseError):                                          # glibc newer than target
        e = dict(_ENV)
        e["glibc"] = "2.38"
        R._validate_builder(_builder(environment=e, environment_sha256=R.sha256_hex(R._canonical_env_bytes(e))), **_kw())
    with pytest.raises(R.ReleaseError):                                          # authorized backend not installed
        e = dict(_ENV)
        e["build_backends"] = {"wheel": "0.43.0"}               # maturin missing
        R._validate_builder(_builder(environment=e, environment_sha256=R.sha256_hex(R._canonical_env_bytes(e))), **_kw())
    with pytest.raises(R.ReleaseError):                                          # backend wrong version
        e = dict(_ENV)
        e["build_backends"] = {"maturin": "9.9.9"}
        R._validate_builder(_builder(environment=e, environment_sha256=R.sha256_hex(R._canonical_env_bytes(e))), **_kw())


def test_environment_sha256_binding():
    with pytest.raises(R.ReleaseError):
        R._validate_builder(_builder(environment_sha256="0" * 64), **_kw())


def test_empty_backend_lock_rejected():
    with pytest.raises(R.ReleaseError):                                          # finding 1
        R._validate_builder(_builder(build_backends_lock_sha256=R.sha256_hex(b"# only comments\n")),
                            recipe_sha256=_RS, build_backends_lock_sha256=R.sha256_hex(b"# only comments\n"),
                            build_backends_lock_text="# only comments\n",
                            apt_packages_sha256=_APT_SHA, rustup_init_file_sha256=_RUSTUP_SHA,
                            apt_packages_text=_APT_TEXT, extractor_tools_lock_sha256=_EXT_SHA)


def test_apt_and_rustup_file_sha_binding():
    with pytest.raises(R.ReleaseError):                                          # apt sha mismatch
        R._validate_builder(_builder(apt_packages_sha256="9" * 64), **_kw())
    with pytest.raises(R.ReleaseError):                                          # rustup sha mismatch
        R._validate_builder(_builder(rustup_init_file_sha256="9" * 64), **_kw())


def test_structural_jammy_and_arch_enforced():
    for bad in ({"os_id": "debian"}, {"os_version_id": "24.04"}, {"arch": "aarch64"}):
        e = dict(_ENV)
        e.update(bad)
        with pytest.raises(R.ReleaseError):
            R._validate_builder(
                _builder(environment=e,
                         environment_sha256=R.sha256_hex(R._canonical_env_bytes(e))), **_kw())


def test_extractor_tools_lock_binding():
    with pytest.raises(R.ReleaseError):                 # sha mismatch / substituted
        R._validate_builder(_builder(extractor_tools_lock_sha256="9" * 64), **_kw())
    b = _builder()
    del b["extractor_tools_lock_sha256"]                # missing
    with pytest.raises(R.ReleaseError):
        R._validate_builder(b, **_kw())
