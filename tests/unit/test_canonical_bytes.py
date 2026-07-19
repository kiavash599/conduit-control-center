# SPDX-License-Identifier: MIT
"""release/canonical_bytes.py -- THE single canonical text-byte normalisation + digest primitive.

Covers the module API and the fail-closed CLI entry point that the Phase-B shell invokes on the
RPi2 host, including the stdlib-only guarantee that keeps that host path free of third-party
dependencies."""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

import pytest

from release import canonical_bytes as CB
from release import ccc_release as R

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_BODY = b"FROM base\nRUN true\nlast line without newline"


def _variants():
    lf = _BODY
    return {"lf": lf, "crlf": lf.replace(b"\n", b"\r\n"), "lone_cr": lf.replace(b"\n", b"\r")}


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "release.canonical_bytes", *args],
                          capture_output=True, text=True, cwd=str(_ROOT))


def test_lf_crlf_lone_cr_are_equivalent():
    digests = {CB.canonical_file_sha256(b) for b in _variants().values()}
    assert digests == {hashlib.sha256(_BODY).hexdigest()}


def test_to_lf_normalises_both_forms():
    assert CB.to_lf(b"a\r\nb\rc\nd") == b"a\nb\nc\nd"


def test_ccc_release_reexports_the_same_implementation():
    # Exactly ONE implementation: the ccc_release names must BE the canonical_bytes functions.
    assert R._to_lf is CB.to_lf
    assert R.canonical_file_sha256 is CB.canonical_file_sha256


@pytest.mark.parametrize("kind", ["lf", "crlf", "lone_cr"])
def test_cli_digest_matches_across_line_endings(tmp_path, kind):
    # Exercises the EXACT command path the Phase-B shell uses (not just the Python API), so a
    # future shell-local reimplementation could not silently diverge.
    p = tmp_path / f"recipe-{kind}"
    p.write_bytes(_variants()[kind])
    r = _cli("sha256-file", str(p))
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip()
    assert out == hashlib.sha256(_BODY).hexdigest()
    assert len(out) == 64 and out == out.lower()
    assert all(c in "0123456789abcdef" for c in out)
    assert r.stdout.count("\n") == 1                      # exactly one digest line


def test_cli_on_real_containerfile_matches_shared_api():
    recipe = _ROOT / "release" / "builder" / "Containerfile"
    r = _cli("sha256-file", str(recipe))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == R.canonical_file_sha256(recipe.read_bytes())


@pytest.mark.parametrize("make", [
    lambda tmp: tmp / "does-not-exist",                   # missing
    lambda tmp: tmp,                                      # a directory -> non-regular
])
def test_cli_fails_closed_with_no_digest_on_stdout(tmp_path, make):
    r = _cli("sha256-file", str(make(tmp_path)))
    assert r.returncode != 0
    assert r.stdout.strip() == ""                         # never a digest on failure
    assert "ERROR" in r.stderr


def test_cli_rejects_bad_usage(tmp_path):
    assert _cli().returncode != 0
    assert _cli("nope", str(tmp_path)).returncode != 0


def test_sha256_file_rejects_symlink(tmp_path):
    target = tmp_path / "real"
    target.write_bytes(_BODY)
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:         # Windows without symlink privilege
        import os
        if os.name == "nt":
            pytest.skip(f"symlink creation unavailable on this Windows host: {exc}")
        raise
    with pytest.raises(ValueError, match="symlink"):
        CB.sha256_file(str(link))


def test_module_is_stdlib_only():
    # The Phase-B RPi2 host imports this module directly; it must not drag in any third-party
    # package (notably `packaging`, which release.ccc_release pulls in via release.reuse_authz).
    code = ("import sys; import release.canonical_bytes as m; "
            "bad = [n for n in ('packaging', 'release.ccc_release', 'release.reuse_authz') "
            "if n in sys.modules]; "
            "print('LEAKED:' + ','.join(bad) if bad else 'STDLIB_ONLY')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(_ROOT))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "STDLIB_ONLY", r.stdout
