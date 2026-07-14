# SPDX-License-Identifier: MIT
"""release/build_wheelhouse.py tests (finding 2). Deterministic provenance builder:
consumes only build-lock-authorized sdists, verifies sdist hashes before build,
records sdist->wheel mapping, writes SHA256SUMS, emits strict provenance that
round-trips through ccc_release._validate_provenance. Refuses unauthorized / missing
/ extra / duplicate / ambiguous outputs and empty builder identity. Uses tiny local
fixtures + an injected build function (no real build). Portable (no ssh/Linux)."""
from __future__ import annotations

import hashlib
import os

import pytest

from release import build_wheelhouse as B
from release import ccc_release as R


def _sdist(d, name, data):
    with open(os.path.join(d, name), "wb") as fh:
        fh.write(data)
    return hashlib.sha256(data).hexdigest()


def _good_build_fn(spath, sfn, name, ver):
    return "%s-%s-py3-none-any.whl" % (name, ver), b"WHEEL:" + name.encode()


def _setup(tmp_path, *, lock=None, sdists=None):
    sdir = tmp_path / "sdists"
    sdir.mkdir()
    odir = tmp_path / "wh"
    lockp = tmp_path / "requirements-armv7-build.lock"
    sh = {}
    for nm, data in (sdists or {"fastapi-0.133.0.tar.gz": b"SDIST-fastapi"}).items():
        sh[nm] = _sdist(str(sdir), nm, data)
    if lock is None:
        lock = "fastapi==0.133.0 --hash=sha256:%s\n" % sh["fastapi-0.133.0.tar.gz"]
    lockp.write_text(lock)
    return str(lockp), str(sdir), str(odir), sh


def _run(tmp_path, build_fn=_good_build_fn, identity="ccc-builder",
         image="sha256:" + "a" * 64, **kw):
    lockp, sdir, odir, sh = _setup(tmp_path, **kw)
    return B.build_wheelhouse(build_lock_path=lockp, sdist_dir=sdir, out_dir=odir,
                              builder_identity=identity, builder_image_digest=image,
                              build_fn=build_fn), odir, lockp, sh


def test_build_ok_and_provenance_round_trips(tmp_path):
    res, odir, lockp, _ = _run(tmp_path)
    assert os.path.isfile(os.path.join(odir, "SHA256SUMS"))
    assert os.path.isfile(os.path.join(odir, "fastapi-0.133.0-py3-none-any.whl"))
    prov = res["provenance"]
    assert prov["builder"]["identity"] and prov["builder"]["image_digest"]
    assert len(prov["wheels"]) == 1
    # emitted provenance passes the strict device-side validator
    members = R._wheelhouse_members(odir)
    R._validate_provenance(prov, members, res["bundle_sha256"], open(lockp).read())


@pytest.mark.parametrize("bad_image", [
    "", "not-a-digest", "sha256:short", "sha256:" + "A" * 64, "sha256:" + "g" * 64,
    " sha256:" + "a" * 64, "sha512:" + "a" * 64, "image:latest", "sha256:" + "a" * 63,
])
def test_builder_image_digest_must_be_oci(tmp_path, bad_image):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, image=bad_image)


def test_valid_oci_digest_accepted(tmp_path):
    res, _o, _l, _s = _run(tmp_path, image="sha256:" + "b" * 64)
    assert res["provenance"]["builder"]["image_digest"] == "sha256:" + "b" * 64


def test_unauthorized_sdist_hash_rejected(tmp_path):
    # build lock lists a DIFFERENT hash than the actual sdist
    lock = "fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64)
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, lock=lock)


def test_extra_sdist_rejected(tmp_path):
    sdists = {"fastapi-0.133.0.tar.gz": b"SDIST-fastapi", "extra-1.0.0.tar.gz": b"SDIST-extra"}
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, sdists=sdists)   # lock covers only fastapi -> 'extra' unauthorized


def test_missing_sdist_rejected(tmp_path):
    lock = ("fastapi==0.133.0 --hash=sha256:%s\nother==1.0.0 --hash=sha256:%s\n"
            % (hashlib.sha256(b"SDIST-fastapi").hexdigest(), "b" * 64))
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, lock=lock)       # build lock has 'other' but no sdist for it


def test_ambiguous_build_output_rejected(tmp_path):
    def _bad(spath, sfn, name, ver):
        return "wrongname-9.9.9-py3-none-any.whl", b"W"   # name/version mismatch
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, build_fn=_bad)


def test_duplicate_sdist_rejected(tmp_path):
    # two sdist files for the same package
    sdists = {"fastapi-0.133.0.tar.gz": b"A", "fastapi-0.133.0.zip": b"B"}
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, sdists=sdists)
