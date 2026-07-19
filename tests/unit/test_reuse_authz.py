# SPDX-License-Identifier: MIT
"""Strict reused-wheel authorization (release/reuse_authz): exact-identity binding, MANDATORY
independent target compatibility (committed 495-tag intersection + fixed profile + requires_python
admits 3.10.12), and every fail-closed path."""
from __future__ import annotations

import json
import pathlib

import pytest

from release import reuse_authz as RA

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TT = set((_ROOT / "release" / "builder" / "target-supported-tags.txt").read_text(encoding="utf-8").split())


def _rec(**over):
    d = {"name": "bcrypt", "version": "4.3.0",
         "filename": "bcrypt-4.3.0-cp39-abi3-manylinux_2_28_armv7l.manylinux_2_31_armv7l.whl",
         "sha256": "d9af79d322e735b1fc33404b5765108ae0ff232d4b54666d46730f8ac1a43676",
         "tags": ["cp39-abi3-manylinux_2_28_armv7l", "cp39-abi3-manylinux_2_31_armv7l"],
         "requires_python": ">=3.8"}
    d.update(over)
    return d


def _doc(wheels, **over):
    d = {"schema": RA.SCHEMA_ID, "origin": "pypi", "target": dict(RA.TARGET_PROFILE), "wheels": wheels}
    d.update(over)
    return json.dumps(d).encode()


def test_valid_against_committed_495_and_canonical_deterministic():
    v = RA.load_and_validate(_doc([_rec()]), target_tags=_TT)
    assert len(v["wheels"]) == 1
    assert RA.sha256_hex(RA.canonical_bytes(v)) == RA.sha256_hex(RA.canonical_bytes(v))


def test_committed_example_fixture_is_valid():
    raw = (_ROOT / "release" / "lock-schema" / "armv7-reuse-authz.example.json").read_bytes()
    v = RA.load_and_validate(raw, target_tags=_TT)
    assert {w["name"] for w in v["wheels"]} == {"idna", "bcrypt"}


def test_target_tags_are_mandatory():
    with pytest.raises(RA.AuthzError):
        RA.load_and_validate(_doc([_rec()]), target_tags=None)


def test_target_profile_must_be_exact():
    bad = json.dumps({"schema": RA.SCHEMA_ID, "origin": "pypi",
                      "target": {"python": "cp311", "platform": "armv7l", "glibc": "2.35"},
                      "wheels": [_rec()]}).encode()
    with pytest.raises(RA.AuthzError):
        RA.load_and_validate(bad, target_tags=_TT)


def test_requires_python_must_admit_3_10_12():
    with pytest.raises(RA.AuthzError):
        RA.load_and_validate(_doc([_rec(requires_python=">=3.11")]), target_tags=_TT)


def test_incompatible_target_tag_rejected():
    x86 = _rec(filename="bcrypt-4.3.0-cp39-abi3-manylinux_2_31_x86_64.whl",
               tags=["cp39-abi3-manylinux_2_31_x86_64"])
    with pytest.raises(RA.AuthzError):
        RA.load_and_validate(_doc([x86]), target_tags=_TT)


def test_reject_duplicate_json_key():
    raw = (b'{"schema":"' + RA.SCHEMA_ID.encode() + b'","schema":"x","origin":"pypi",'
           b'"target":{"python":"cp310","platform":"armv7l","glibc":"2.35"},"wheels":[]}')
    with pytest.raises(RA.AuthzError):
        RA.load_and_validate(raw, target_tags=_TT)


def test_reject_nan():
    with pytest.raises(RA.AuthzError):
        RA.load_and_validate(_doc([_rec()]).replace(b'">=3.8"', b'NaN'), target_tags=_TT)


@pytest.mark.parametrize("mut", [
    lambda: _doc([_rec()], extra=1),                                   # unknown top field
    lambda: _doc([_rec()], origin="evil"),                            # bad origin
    lambda: _doc([_rec(name="BCrypt")]),                              # noncanonical name
    lambda: _doc([dict(_rec(), extra="x")]),                         # unknown wheel field
    lambda: _doc([_rec(name="click")]),                              # name != filename
    lambda: _doc([_rec(version="9.9.9")]),                           # version != filename
    lambda: _doc([_rec(sha256="XYZ")]),                              # malformed sha
    lambda: _doc([_rec(tags=["cp39-abi3-manylinux_2_31_armv7l"])]),  # tags != filename
    lambda: _doc([_rec(filename="../evil-4.3.0-cp39-abi3-manylinux_2_31_armv7l.whl")]),  # traversal
    lambda: _doc([_rec(filename="bcrypt-4.3.0.tar.gz")]),            # non-wheel filename
    lambda: _doc([_rec(filename="a/b-4.3.0-cp39-abi3-manylinux_2_31_armv7l.whl")]),  # path separator
    lambda: _doc([_rec(), _rec()]),                                  # duplicate normalized name
])
def test_fail_closed_paths(mut):
    with pytest.raises(RA.AuthzError):
        RA.load_and_validate(mut(), target_tags=_TT)


def test_pure_wheel_compatible():
    idna = _rec(name="idna", version="3.18", filename="idna-3.18-py3-none-any.whl",
               sha256="7f952cbe720b688055e3f87de14f5c3e5fdaa8bc3928985c4077ca689de849a2",
               tags=["py3-none-any"], requires_python=">=3.9")
    v = RA.load_and_validate(_doc([idna]), target_tags=_TT)
    assert v["wheels"][0]["name"] == "idna"


@pytest.mark.parametrize("spec,ok", [
    (">=3.8", True), (">=3.11", False), ("!=3.9.0,>=3.9", True), (">=3.9,<4", True),
    ("==3.10.*", True), ("<3.10", False), ("~=3.10", True)])
def test_requires_python_evaluator(spec, ok):
    assert RA._requires_python_ok(spec) is ok


def test_load_target_tags_shape():
    tags, tset, sha = RA.load_target_tags(str(_ROOT / "release" / "builder" / "target-supported-tags.txt"))
    assert len(tags) == 495 and len(tset) == 495 and len(sha) == 64
