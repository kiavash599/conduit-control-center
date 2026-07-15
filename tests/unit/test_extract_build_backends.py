# SPDX-License-Identifier: MIT
"""Behavioral tests for release/builder/extract_build_backends.py (findings 4 & 5):
lock-driven bijection, strict UTF-8, unambiguous archive layout, the real-TOML-parser
bootstrap (no regex fallback), and the isolated tomli venv bootstrap lifecycle."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import pathlib
import sys
import tarfile
import types
import zipfile

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "extract_build_backends", _ROOT / "release" / "builder" / "extract_build_backends.py")
ebb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ebb)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _tar(members):
    """members: dict of arcname -> bytes (or a (bytes, tarinfo_mutator) tuple)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, val in members.items():
            data = val if isinstance(val, bytes) else val[0]
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            if not isinstance(val, bytes):
                val[1](ti)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _zip(members, *, dup=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
        if dup:
            z.writestr(dup, b"second copy")   # duplicate member name
    return buf.getvalue()


def _lock(*pins):
    return "".join(f"{n}=={v} --hash=sha256:{h}\n" for n, v, h in pins)


def _fake_tomli():
    """A minimal stand-in for the TOML parser so the tomli branch is exercised on Python
    3.10 (no stdlib tomllib). On 3.11+ tomllib is used and this is ignored."""
    import re
    m = types.ModuleType("tomli")

    def loads(s):
        if "[build-system]" not in s:
            return {}
        mm = re.search(r"requires\s*=\s*\[(.*?)\]", s, re.S)
        if not mm:
            raise ValueError("malformed build-system.requires")
        items = re.findall(r'"([^"]*)"', mm.group(1))
        return {"build-system": {"requires": items}}
    m.loads = loads
    return m


def _has_real_parser():
    try:
        import tomllib  # noqa: F401
        return True
    except ImportError:
        try:
            import tomli  # noqa: F401
            return True
        except ImportError:
            return False


_LEGACY_SDIST = {"pkg-1.0/PKG-INFO": b"Metadata-Version: 2.1\n"}   # no pyproject -> legacy default


def test_valid_bijection_legacy_default(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar(_LEGACY_SDIST)
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    out = ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))
    assert "setuptools" in out and "wheel" in out


def test_extra_unauthorized_sdist_fails(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar(_LEGACY_SDIST)
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    (sd / "evil-9.tar.gz").write_bytes(_tar({"evil-9/PKG-INFO": b"x"}))
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


def test_missing_pin_fails(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar(_LEGACY_SDIST)
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb)), ("gone", "2.0", "c" * 64)))


def test_hash_mismatch_fails(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "pkg-1.0.tar.gz").write_bytes(_tar(_LEGACY_SDIST))
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", "a" * 64)))


def test_unrecognized_artifact_fails(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "README.txt").write_text("nope\n")
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", "a" * 64)))


def test_malformed_and_empty_lock_fail(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), "garbage line\n")
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), "# only a comment\n")


# --- archive ambiguity (no pyproject needed: rejected before any parse) --- #
def test_multiple_roots_rejected(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar({"a-1/PKG-INFO": b"x", "b-2/PKG-INFO": b"y"})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


def test_two_pyproject_candidates_rejected(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    # same root, two depth-1 pyproject.toml is impossible in one dir, so simulate via tar
    # allowing two members with the same logical path handled as duplicate; instead use one
    # root with pyproject.toml AND a second identical-depth candidate via a crafted name.
    tb = _tar({"pkg-1.0/pyproject.toml": b"[build-system]\n",
               "pkg-1.0/./pyproject.toml": b"[build-system]\n"})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


def test_unsafe_member_name_rejected(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar({"pkg-1.0/../evil": b"x", "pkg-1.0/PKG-INFO": b"y"})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


def test_duplicate_zip_member_rejected(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    zb = _zip({"pkg-1.0/PKG-INFO": b"x"}, dup="pkg-1.0/PKG-INFO")
    (sd / "pkg-1.0.zip").write_bytes(zb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(zb))))


def test_symlink_pyproject_rejected(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()

    def _sym(ti):
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
    tb = _tar({"pkg-1.0/pyproject.toml": (b"", _sym)})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


def test_invalid_utf8_pyproject_rejected(tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar({"pkg-1.0/pyproject.toml": b"\xff\xfe not utf-8"})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


# --- real-parser paths (fake tomli injected so the branch runs on 3.10) --- #
def test_valid_requires_parsed(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tomli", _fake_tomli())
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar({"pkg-1.0/pyproject.toml":
               b'[build-system]\nrequires = ["maturin>=1.0"]\nbuild-backend = "maturin"\n'})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    out = ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))
    assert "maturin>=1.0" in out


def test_malformed_toml_rejected(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tomli", _fake_tomli())
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar({"pkg-1.0/pyproject.toml": b'[build-system]\nrequires = ["setuptools"\n'})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


@pytest.mark.skipif(_has_real_parser(), reason="a TOML parser is available")
def test_no_parser_available_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "tomli", raising=False)
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar({"pkg-1.0/pyproject.toml": b"[build-system]\nrequires = []\n"})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


# --- tomli bootstrap lifecycle (pip + version injectable; no network) --- #
def test_bootstrap_verifies_pinned_version(tmp_path):
    lock = tmp_path / "requirements-extractor-tools.lock"
    lock.write_text("tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64))
    ev = tmp_path / "ev.env"
    ver, sha = ebb.bootstrap_extractor_venv(
        str(lock), str(tmp_path / "venv"), in_text="tomli==2.0.1\n",
        pip_install=lambda lp, vd: None, installed_version=lambda vd: "2.0.1",
        evidence_path=str(ev))
    assert ver == "2.0.1"
    txt = ev.read_text()
    assert "tomli_version=2.0.1" in txt and "extractor_tools_lock_sha256=" in txt
    assert "authorized_closure=tomli==2.0.1" in txt


def test_bootstrap_version_mismatch_fails(tmp_path):
    lock = tmp_path / "l.lock"
    lock.write_text("tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64))
    with pytest.raises(ebb.ExtractError):
        ebb.bootstrap_extractor_venv(
            str(lock), str(tmp_path / "venv"), in_text="tomli==2.0.1\n",
            pip_install=lambda lp, vd: None, installed_version=lambda vd: "1.2.3")


def test_bootstrap_lock_without_tomli_fails(tmp_path):
    lock = tmp_path / "l.lock"
    lock.write_text("wheel==0.43.0 --hash=sha256:%s\n" % ("7" * 64))
    with pytest.raises(ebb.ExtractError):                       # .in requests tomli, lock lacks it
        ebb.bootstrap_extractor_venv(
            str(lock), str(tmp_path / "venv"), in_text="tomli==2.0.1\n",
            pip_install=lambda lp, vd: None, installed_version=lambda vd: "0.43.0")


# --- F6: closed .in<->lock authorization --- #
def test_authorized_closure_accepts_exact_tomli():
    c = ebb.authorized_closure("tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64), "tomli==2.0.1\n")
    assert c == {"tomli": "2.0.1"}


def test_authorized_closure_rejects_extra_package():
    lock = ("tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
            + "evilpkg==9.9 --hash=sha256:%s\n" % ("8" * 64))
    with pytest.raises(ebb.ExtractError):
        ebb.authorized_closure(lock, "tomli==2.0.1\n")


def test_authorized_closure_rejects_drift_and_missing_and_unhashed():
    with pytest.raises(ebb.ExtractError):      # drift
        ebb.authorized_closure("tomli==1.0.0 --hash=sha256:%s\n" % ("7" * 64), "tomli==2.0.1\n")
    with pytest.raises(ebb.ExtractError):      # missing tomli
        ebb.authorized_closure("wheel==0.43 --hash=sha256:%s\n" % ("7" * 64), "tomli==2.0.1\n")
    with pytest.raises(ebb.ExtractError):      # unhashed
        ebb.authorized_closure("tomli==2.0.1\n", "tomli==2.0.1\n")


def test_bootstrap_rejects_extra_package(tmp_path):
    lock = tmp_path / "l.lock"
    lock.write_text("tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
                    + "evilpkg==9.9 --hash=sha256:%s\n" % ("8" * 64))
    with pytest.raises(ebb.ExtractError):
        ebb.bootstrap_extractor_venv(
            str(lock), str(tmp_path / "venv"), in_text="tomli==2.0.1\n",
            pip_install=lambda lp, vd: None, installed_version=lambda vd: "2.0.1")


# --- F5: zip symlink candidate + non-string TOML requires --- #
def test_zip_symlink_pyproject_rejected(tmp_path):
    import zipfile as _zf
    import stat as _st
    sd = tmp_path / "s"
    sd.mkdir()
    zp = sd / "pkg-1.0.zip"
    buf = _zf.ZipFile(zp, "w")
    zi = _zf.ZipInfo("pkg-1.0/pyproject.toml")
    zi.external_attr = (_st.S_IFLNK | 0o777) << 16     # symlink mode
    buf.writestr(zi, b"/etc/passwd")
    buf.close()
    zb = zp.read_bytes()
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(zb))))


def test_toml_requires_non_string_entry_rejected(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tomli", _fake_tomli_types())
    sd = tmp_path / "s"
    sd.mkdir()
    tb = _tar({"pkg-1.0/pyproject.toml": b"[build-system]\nrequires = [123]\n"})
    (sd / "pkg-1.0.tar.gz").write_bytes(tb)
    with pytest.raises(ebb.ExtractError):
        ebb.extract(str(sd), _lock(("pkg", "1.0", _sha(tb))))


def _fake_tomli_types():
    import types as _t
    m = _t.ModuleType("tomli")

    def loads(s):
        if "123" in s:
            return {"build-system": {"requires": [123]}}     # non-string entry
        if "empty" in s:
            return {"build-system": {"requires": [""]}}
        return {}
    m.loads = loads
    return m
