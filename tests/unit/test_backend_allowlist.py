# SPDX-License-Identifier: MIT
"""Authorized backend-sdist allowlist (cffi): partitioning, fail-closed target-wheel probe,
mixed-closure dist-dir bijection, producer validation/binding, and CRLF/LF canonical hashing.

Covers the four correction-pass gaps: (1) tri-state fail-closed probe with official-PyPI
isolation; (2) exact mixed dist-dir/lock bijection + file-type policy; (3) canonical-form
enforcement; (4) behavioral coverage. Docker-gated two-pass ORDERING is asserted against the
Containerfile in test_builder_scripts.py."""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib

import pytest

from release import ccc_release as R

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "release" / "builder" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PB = _load("partition_backends")
GB = _load("gen_build_backends")

_H = "7" * 64


def _pin(name, ver, h=_H):
    return "%s==%s --hash=sha256:%s\n" % (name, ver, h)


# --------------------------------------------------------------------------- #
#  Partition: valid cffi case + exact/disjoint + fail-closed + canonical       #
# --------------------------------------------------------------------------- #
def test_partition_valid_cffi_source_authorized():
    lock = _pin("setuptools", "69.5.1") + _pin("cffi", "2.1.0") + _pin("wheel", "0.43.0")
    wheel, source = PB.partition(lock, "cffi\n")
    assert [w.split("==")[0] for w in wheel] == ["setuptools", "wheel"]
    assert len(source) == 1 and source[0].startswith("cffi==2.1.0 ")


def test_partition_is_exact_disjoint_cover():
    lock = _pin("a", "1") + _pin("cffi", "2.1.0") + _pin("b", "2")
    wheel, source = PB.partition(lock, "cffi\n")
    names = {ln.split("==")[0] for ln in wheel} | {ln.split("==")[0] for ln in source}
    assert names == {"a", "cffi", "b"}
    assert len(wheel) + len(source) == 3
    assert not ({ln.split("==")[0] for ln in wheel} & {ln.split("==")[0] for ln in source})


def test_partition_unauthorized_allowlist_not_in_lock_fails():
    with pytest.raises(PB.PartitionError):
        PB.partition(_pin("a", "1") + _pin("b", "2"), "cffi\n")


def test_partition_rejects_unhashed_lock_line():
    with pytest.raises(PB.PartitionError):
        PB.partition("cffi==2.1.0\n", "cffi\n")


def test_partition_rejects_duplicate_pin():
    with pytest.raises(PB.PartitionError):
        PB.partition(_pin("cffi", "2.1.0") + _pin("cffi", "2.0.0"), "cffi\n")


@pytest.mark.parametrize("bad", ["-bad\n", "a b\n", "\n\n", "# only comment\n"])
def test_partition_rejects_malformed_or_empty_allowlist(bad):
    with pytest.raises(PB.PartitionError):
        PB.partition(_pin("cffi", "2.1.0"), bad)


@pytest.mark.parametrize("bad", ["CFFI\n", "c_ffi\n", "C.ffi\n"])
def test_partition_rejects_noncanonical_allowlist(bad):
    with pytest.raises(PB.PartitionError):
        PB.partition(_pin("cffi", "2.1.0"), bad)


# --------------------------------------------------------------------------- #
#  Fail-closed tri-state target-wheel probe (gap 1)                            #
# --------------------------------------------------------------------------- #
def test_probe_wheel_exists():
    assert GB.probe_target_wheel("cffi", "2.1.0",
                                 wheel_probe=lambda n, v: True,
                                 sdist_probe=lambda n, v: True) == "wheel"


def test_probe_no_wheel_positively_established_via_sdist():
    assert GB.probe_target_wheel("cffi", "2.1.0",
                                 wheel_probe=lambda n, v: False,
                                 sdist_probe=lambda n, v: True) == "no-wheel"


def test_probe_indeterminate_when_neither_resolves_raises():
    # network/index/TLS/tool failure -> neither wheel nor sdist -> hard failure (never no-wheel)
    with pytest.raises(GB.GenError):
        GB.probe_target_wheel("cffi", "2.1.0",
                              wheel_probe=lambda n, v: False,
                              sdist_probe=lambda n, v: False)


def test_probe_commands_disable_cache_on_both_probes():
    # --no-cache-dir forces a live fetch so a cached index response/artifact cannot answer the
    # probe (retaining --isolated + official index + exact version + --no-deps).
    for only_binary in (True, False):
        cmd = GB._pip_probe_cmd("cffi", "2.1.0", "/tmp/d", only_binary=only_binary)
        assert "--no-cache-dir" in cmd and "--isolated" in cmd and "--no-deps" in cmd
        assert cmd[cmd.index("--index-url") + 1] == "https://pypi.org/simple/"


def test_index_failure_with_cache_disabled_yields_gen_error_no_evidence(tmp_path):
    # Simulated official-index outage with cache disabled: NEITHER a live wheel nor a live sdist
    # resolves -> indeterminate -> GenError, and a cached sdist can no longer pose as no-wheel.
    ev = tmp_path / "e"
    with pytest.raises(GB.GenError):
        GB.assert_allowlist_no_drift(
            ["cffi"], {"cffi": "2.1.0"}, evidence_path=str(ev),
            tags_fn=lambda: ["py3-none-any"],
            probe_fn=lambda n, v: GB.probe_target_wheel(
                n, v, wheel_probe=lambda a, b: False, sdist_probe=lambda a, b: False))
    assert not ev.exists()


def test_probe_command_uses_official_pypi_and_isolation():
    # ambient/custom pip index cannot silently replace official PyPI, and pip config is ignored.
    wcmd = GB._pip_probe_cmd("cffi", "2.1.0", "/tmp/d", only_binary=True)
    assert "--isolated" in wcmd and "--no-deps" in wcmd
    assert "--index-url" in wcmd and wcmd[wcmd.index("--index-url") + 1] == "https://pypi.org/simple/"
    assert "--only-binary=:all:" in wcmd and "cffi==2.1.0" in wcmd
    scmd = GB._pip_probe_cmd("cffi", "2.1.0", "/tmp/d", only_binary=False)
    assert "--no-binary=:all:" in scmd and "--isolated" in scmd
    assert scmd[scmd.index("--index-url") + 1] == "https://pypi.org/simple/"


def test_no_drift_records_positive_evidence(tmp_path):
    ev = tmp_path / "wheel-availability.evidence"
    GB.assert_allowlist_no_drift(
        ["cffi"], {"cffi": "2.1.0"}, evidence_path=str(ev),
        tags_fn=lambda: ["cp310-cp310-linux_armv7l", "py3-none-any"],
        probe_fn=lambda n, v: "no-wheel")
    txt = ev.read_text()
    assert "official_index=https://pypi.org/simple/" in txt
    assert "probe_cache=disabled" in txt
    assert "target_compatible_tags=cp310-cp310-linux_armv7l" in txt
    assert "no_compatible_wheel_confirmed_via_sdist=cffi==2.1.0" in txt


def test_compatible_wheel_is_drift_failure_no_evidence(tmp_path):
    ev = tmp_path / "e"
    with pytest.raises(GB.GenError):
        GB.assert_allowlist_no_drift(["cffi"], {"cffi": "2.1.0"}, evidence_path=str(ev),
                                     tags_fn=lambda: ["py3-none-any"],
                                     probe_fn=lambda n, v: "wheel")
    assert not ev.exists()


def test_indeterminate_probe_writes_no_evidence(tmp_path):
    ev = tmp_path / "e"

    def _boom(n, v):
        raise GB.GenError("network/index/TLS error")
    with pytest.raises(GB.GenError):
        GB.assert_allowlist_no_drift(["cffi"], {"cffi": "2.1.0"}, evidence_path=str(ev),
                                     tags_fn=lambda: ["py3-none-any"], probe_fn=_boom)
    assert not ev.exists()


def test_parse_compatible_tags_full_set():
    tags = GB._parse_compatible_tags(
        "Compatible tags:\n  cp310-cp310-linux_armv7l\n  cp310-abi3-linux_armv7l\n  py3-none-any\n")
    assert tags == ["cp310-cp310-linux_armv7l", "cp310-abi3-linux_armv7l", "py3-none-any"]


# --------------------------------------------------------------------------- #
#  Mixed dist-dir <-> lock exact bijection + file-type policy (gap 2)          #
# --------------------------------------------------------------------------- #
def _mk(d, fn, data):
    (d / fn).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _closure(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    hw = _mk(d, "maturin-1.5.1-py3-none-any.whl", b"WHEELBYTES")
    hs = _mk(d, "cffi-2.1.0.tar.gz", b"SDISTBYTES")
    lock = _pin("maturin", "1.5.1", hw) + _pin("cffi", "2.1.0", hs)
    return d, lock


def test_mixed_closure_valid_bijection(tmp_path):
    d, lock = _closure(tmp_path)
    GB.verify_mixed_closure(str(d), lock, ["cffi"])          # cffi sdist, maturin wheel -> OK


def test_mixed_closure_wrong_file_type_rejected(tmp_path):
    # cffi delivered as a WHEEL but it is allowlisted (must be sdist) -> fail
    d = tmp_path / "dist"
    d.mkdir()
    hw = _mk(d, "maturin-1.5.1-py3-none-any.whl", b"W")
    hc = _mk(d, "cffi-2.1.0-cp310-cp310-linux_armv7l.whl", b"C")
    lock = _pin("maturin", "1.5.1", hw) + _pin("cffi", "2.1.0", hc)
    with pytest.raises(GB.GenError):
        GB.verify_mixed_closure(str(d), lock, ["cffi"])


def test_mixed_closure_non_allowlisted_sdist_rejected(tmp_path):
    # maturin delivered as an sdist but it is NOT allowlisted (must be a wheel) -> fail
    d = tmp_path / "dist"
    d.mkdir()
    hm = _mk(d, "maturin-1.5.1.tar.gz", b"M")
    hs = _mk(d, "cffi-2.1.0.tar.gz", b"C")
    lock = _pin("maturin", "1.5.1", hm) + _pin("cffi", "2.1.0", hs)
    with pytest.raises(GB.GenError):
        GB.verify_mixed_closure(str(d), lock, ["cffi"])


def test_mixed_closure_extra_file_rejected(tmp_path):
    d, lock = _closure(tmp_path)
    _mk(d, "evil-9.9.tar.gz", b"X")                          # not in lock
    with pytest.raises(GB.GenError):
        GB.verify_mixed_closure(str(d), lock, ["cffi"])


def test_mixed_closure_missing_file_rejected(tmp_path):
    d, lock = _closure(tmp_path)
    lock += _pin("extra", "1.0", "a" * 64)                   # pinned but no file
    with pytest.raises(GB.GenError):
        GB.verify_mixed_closure(str(d), lock, ["cffi"])


def test_mixed_closure_hash_mismatch_rejected(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    _mk(d, "maturin-1.5.1-py3-none-any.whl", b"W")
    _mk(d, "cffi-2.1.0.tar.gz", b"C")
    lock = _pin("maturin", "1.5.1", "b" * 64) + _pin("cffi", "2.1.0", "c" * 64)  # wrong hashes
    with pytest.raises(GB.GenError):
        GB.verify_mixed_closure(str(d), lock, ["cffi"])


def test_mixed_closure_duplicate_distribution_rejected(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    h1 = _mk(d, "cffi-2.1.0.tar.gz", b"C1")
    h2 = _mk(d, "cffi-2.1.0.zip", b"C2")
    lock = _pin("cffi", "2.1.0", h1) + _pin("cffi", "2.1.0", h2)  # dup pin -> lock parse fails first
    with pytest.raises(GB.GenError):
        GB.verify_mixed_closure(str(d), lock, ["cffi"])


# --------------------------------------------------------------------------- #
#  Producer validation + canonical enforcement + CRLF/LF hashing (gap 3)       #
# --------------------------------------------------------------------------- #
def test_producer_allowlist_valid_and_exact_use():
    R.validate_backend_source_allowlist("cffi\n", _pin("cffi", "2.1.0") + _pin("wheel", "0.43.0"))


def test_producer_allowlist_grammar_only_when_no_lock():
    R.validate_backend_source_allowlist("cffi\n")            # lock_text=None -> grammar-only


def test_producer_allowlist_unused_entry_rejected():
    with pytest.raises(R.ReleaseError):
        R.validate_backend_source_allowlist("cffi\n", _pin("wheel", "0.43.0"))


@pytest.mark.parametrize("bad", ["-bad\n", "cffi\ncffi\n", "\n", "# c\n"])
def test_producer_allowlist_malformed_dup_empty_rejected(bad):
    with pytest.raises(R.ReleaseError):
        R.validate_backend_source_allowlist(bad, _pin("cffi", "2.1.0"))


@pytest.mark.parametrize("bad", ["CFFI\n", "c_ffi\n", "C.ffi\n", "Cffi\n"])
def test_producer_allowlist_noncanonical_rejected(bad):
    with pytest.raises(R.ReleaseError):
        R.validate_backend_source_allowlist(bad, _pin("cffi", "2.1.0"))


def test_crlf_lf_canonical_allowlist_hash_consistency():
    lf, crlf = b"cffi\n", b"cffi\r\n"
    assert R.sha256_hex(R._to_lf(crlf)) == R.sha256_hex(R._to_lf(lf))
    assert R.sha256_hex(R._to_lf(crlf)) == hashlib.sha256(lf).hexdigest()
