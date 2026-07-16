# SPDX-License-Identifier: MIT
"""release/build_wheelhouse.py tests (hardened builder block). Verifies the manifest
digest is recomputed from the raw OCI manifest file, image_id is required + distinct,
the committed build-backends lock is bound + cross-checked, the environment is CAPTURED
from the executing runtime (injectable env_probe), and the whole thing self-checks
through the strict producer validator. Portable (no ssh/Linux/Docker)."""
from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile

import pytest

from release import build_wheelhouse as B
from release import ccc_release as R

_BB_LOCK = "maturin==1.5.1 --hash=sha256:%s\n" % ("7" * 64)
_ENV = {"os": "Ubuntu 22.04.5 LTS", "python": "Python 3.10.12", "rustc": "rustc 1.75.0",
        "cargo": "cargo 1.75.0", "gcc": "gcc 11.4.0", "glibc": "2.35",
        "os_id": "ubuntu", "os_version_id": "22.04", "arch": "armv7l", "apt_architecture": "armhf",
        "apt": {"build-essential": "12.9ubuntu3"}, "build_backends": {"maturin": "1.5.1", "wheel": "0.43.0"}}
_APT = "build-essential=12.9ubuntu3\n"
_RUSTUP = "f" * 64 + "  rustup-init\n"
_APT_SHA = R.sha256_hex(R._to_lf(_APT.encode()))
_RUSTUP_SHA = R.sha256_hex(R._to_lf(_RUSTUP.encode()))
_EXT_IN = "tomli==2.0.1\n"
_EXT_LOCK = "tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
_EXT_LOCK_SHA = R.sha256_hex(_EXT_LOCK.encode())
_ALLOWLIST = "maturin\n"
_ALLOWLIST_SHA = R.sha256_hex(_ALLOWLIST.encode())
_BASE = "sha256:" + "b" * 64
_IMAGE_ID = "sha256:" + "d" * 64
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
_MANIFEST_BYTES = _manifest_bytes(_IMAGE_ID)


def _sdist(d, name, data):
    with open(os.path.join(d, name), "wb") as fh:
        fh.write(data)
    return hashlib.sha256(data).hexdigest()


def _good_build_fn(spath, sfn, name, ver):
    return "%s-%s-py3-none-any.whl" % (name, ver), b"WHEEL:" + name.encode()


def _probe():
    return dict(_ENV)


def _setup(tmp_path, *, lock=None, sdists=None, bb_lock=_BB_LOCK, manifest=_MANIFEST_BYTES,
           allowlist=_ALLOWLIST):
    base = pathlib.Path(tempfile.mkdtemp(dir=str(tmp_path)))
    sdir = base / "sdists"
    sdir.mkdir()
    (base / "Containerfile").write_text("FROM base\nRUN true\n")
    (base / "requirements-build-backends.lock").write_text(bb_lock)
    (base / "apt-packages.list").write_text(_APT)
    (base / "rustup-init.sha256").write_text(_RUSTUP)
    (base / "requirements-extractor-tools.lock").write_text(_EXT_LOCK)
    (base / "requirements-build-backends.source-allowlist").write_text(allowlist)
    (base / "image-manifest.json").write_bytes(manifest)
    sh = {}
    for nm, data in (sdists or {"fastapi-0.133.0.tar.gz": b"SDIST"}).items():
        sh[nm] = _sdist(str(sdir), nm, data)
    lockp = base / "requirements-armv7-build.lock"
    lockp.write_text(lock or "fastapi==0.133.0 --hash=sha256:%s\n" % sh["fastapi-0.133.0.tar.gz"])
    return base, sdir, sh


def _run(tmp_path, *, build_fn=_good_build_fn, identity="ccc-builder", base=_BASE,
         image_id=_IMAGE_ID, env_probe=None, **kw):
    d, sdir, sh = _setup(tmp_path, **kw)
    res = B.build_wheelhouse(
        build_lock_path=str(d / "requirements-armv7-build.lock"), sdist_dir=str(sdir),
        out_dir=str(d / "wh"), recipe_path=str(d / "Containerfile"),
        build_backends_lock_path=str(d / "requirements-build-backends.lock"),
        apt_packages_path=str(d / "apt-packages.list"), rustup_sha_path=str(d / "rustup-init.sha256"),
        extractor_tools_lock_path=str(d / "requirements-extractor-tools.lock"),
        build_backends_source_allowlist_path=str(d / "requirements-build-backends.source-allowlist"),
        builder_identity=identity, base_image_digest=base,
        image_manifest_path=str(d / "image-manifest.json"), image_id=image_id,
        env_probe=env_probe or _probe, build_fn=build_fn)
    return res, d, sh


def test_build_ok_and_round_trips(tmp_path):
    res, d, _sh = _run(tmp_path)
    b = res["provenance"]["builder"]
    assert b["image_manifest_digest"] == "sha256:" + hashlib.sha256(_MANIFEST_BYTES).hexdigest()
    assert b["image_id"] == _IMAGE_ID and b["image_id"] != b["image_manifest_digest"]
    assert b["build_backends_lock_sha256"] == R.sha256_hex(_BB_LOCK.encode())
    assert b["environment"]["glibc"] == "2.35"
    R._validate_provenance(res["provenance"], R._wheelhouse_members(str(d / "wh")), res["bundle_sha256"],
                           open(d / "requirements-armv7-build.lock").read(),
                           R.sha256_hex(b"FROM base\nRUN true\n"), R.sha256_hex(_BB_LOCK.encode()), _BB_LOCK,
                           _APT_SHA, _RUSTUP_SHA, _APT, _EXT_LOCK_SHA, _ALLOWLIST_SHA,
                           image_manifest_bytes=_MANIFEST_BYTES)


def test_default_env_probe_shape():
    env = B._default_env_probe()               # captured from THIS runtime; structure must be complete
    for k in ("os", "os_id", "os_version_id", "arch", "python", "rustc", "cargo", "gcc",
              "glibc", "apt", "build_backends"):
        assert k in env
    assert env["python"]                        # python is present in any runtime we run in
    assert isinstance(env["build_backends"], dict)


def test_image_id_required_and_distinct(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, image_id="")
    with pytest.raises(R.ReleaseError):        # id == derived manifest digest
        _run(tmp_path, image_id="sha256:" + hashlib.sha256(_MANIFEST_BYTES).hexdigest())


def test_missing_or_empty_manifest_file(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, manifest=b"")


def test_bad_base_digest(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, base="not-a-digest")


def test_empty_backend_lock_rejected(tmp_path):
    with pytest.raises(R.ReleaseError):        # finding 1
        _run(tmp_path, bb_lock="# only comments\n")


def test_environment_glibc_and_backend_binding(tmp_path):
    with pytest.raises(R.ReleaseError):        # glibc newer than target
        _run(tmp_path, env_probe=lambda: {**_ENV, "glibc": "2.38"})
    with pytest.raises(R.ReleaseError):        # authorized backend not captured in env
        _run(tmp_path, env_probe=lambda: {**_ENV, "build_backends": {"wheel": "0.43.0"}})


def test_sdist_authorization_and_build_output(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, lock="fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64))   # unauthorized hash
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, sdists={"fastapi-0.133.0.tar.gz": b"S", "extra-1.0.0.tar.gz": b"x"})  # extra

    def _bad(spath, sfn, name, ver):
        return "wrongname-9.9.9-py3-none-any.whl", b"W"
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, build_fn=_bad)          # ambiguous build output


def test_dpkg_status_parser_excludes_non_installed():
    # ${db:Status-Status} filtering: only 'installed' rows survive; config-files/removed excluded.
    lines = ("installed\tbuild-essential\t12.9ubuntu3\n"
             "config-files\told-pkg\t1.0\n"
             "not-installed\tghost\t2.0\n"
             "installed\tlibssl-dev:armhf\t3.0.2\n")
    apt = B._parse_dpkg_status_lines(lines)
    assert apt == {"build-essential": "12.9ubuntu3", "libssl-dev:armhf": "3.0.2"}
    assert "old-pkg" not in apt and "ghost" not in apt


def test_build_wheelhouse_rejects_unused_allowlist(tmp_path):
    # 'evilpkg' is not pinned in the backend lock (maturin) -> semantic self-check fails
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, allowlist="evilpkg\n")


def test_build_wheelhouse_rejects_noncanonical_allowlist(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, allowlist="MATURIN\n")             # noncanonical spelling


def test_build_wheelhouse_rejects_empty_allowlist(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, allowlist="# only a comment\n")
