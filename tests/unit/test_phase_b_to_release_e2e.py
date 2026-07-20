# SPDX-License-Identifier: MIT
"""END-TO-END production path (no network, no Docker, no Pi).

Drives the REAL `build_wheelhouse.build_wheelhouse()` producer -- the same function the offline
Phase-B ceremony runs inside the builder image -- so its ACTUAL output shapes (canonical SHA256SUMS,
the runtime lock with its canonical header, format-3 provenance, build evidence) flow through the
committed `release.transfer_manifest` generator/verifier and into the release input gate.

This exists because hand-built fixtures previously diverged from the producer: the runtime lock was
written WITHOUT its canonical header, so the validator would have rejected genuine Phase-B output
only after an expensive hardware ceremony. Fixtures must reproduce production formats, not a
simplified idea of them."""
from __future__ import annotations

import json
import pathlib

import pytest

from release import ccc_release as R
from release import logical_tree as LT
from release import transfer_manifest as TM
from tests.unit import test_build_wheelhouse as BW      # reuse the committed producer harness


def _real_phase_b_bundle(tmp_path):
    """Run the REAL producer and return its published bundle directory."""
    d, sdir, rdir, ap, reqs = BW._policy_inputs(
        tmp_path, built_names=sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES))
    res = BW._call_policy(d, sdir, rdir, ap, build_fn=BW._bfn, requirements_text=reqs)
    return pathlib.Path(res["bundle_dir"]), res


def test_real_producer_output_passes_transfer_manifest_generate_and_verify(tmp_path):
    bundle, res = _real_phase_b_bundle(tmp_path)

    # The producer's runtime lock really does carry the canonical header (the F1 mismatch).
    lock_text = (bundle / TM.RUNTIME_LOCK).read_text()
    assert lock_text.splitlines()[0] == TM.RUNTIME_LOCK_HEADER
    assert len(lock_text.splitlines()) == 1 + TM.EXPECTED_WHEELS

    out = tmp_path / "phase-b-bundle-transfer-manifest.json"
    m = TM.generate(str(bundle), str(out))           # generator over REAL producer bytes
    assert m["file_count"] == TM.EXPECTED_FILES
    assert m["bind"]["tree_scheme"] == LT.SCHEME
    # The manifest's recorded identity is the RECOMPUTED digest and equals what the producer emitted.
    assert m["bind"]["tree_sha256"] == res["bundle_tree_sha256"]
    prov = json.loads((bundle / TM.PROVENANCE).read_text())
    assert prov["bundle"]["tree_digest"]["sha256"] == m["bind"]["tree_sha256"]
    assert prov["bundle"]["member_count"] == TM.EXPECTED_MEMBERS

    TM.verify(str(bundle), str(out))                 # verifier over the same REAL bytes


def test_real_producer_bundle_is_the_exact_expected_layout(tmp_path):
    bundle, _ = _real_phase_b_bundle(tmp_path)
    files = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    dirs = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_dir())
    assert dirs == [TM.WHEELHOUSE_DIR]               # exactly one directory
    assert len(files) == TM.EXPECTED_FILES
    assert set(TM.EXPECTED_TOP_LEVEL) <= set(files)


def test_tampering_real_producer_bundle_is_detected(tmp_path):
    bundle, _ = _real_phase_b_bundle(tmp_path)
    out = tmp_path / "m.json"
    TM.generate(str(bundle), str(out))
    wheel = next(p for p in (bundle / TM.WHEELHOUSE_DIR).iterdir() if p.suffix == ".whl")
    wheel.write_bytes(b"TAMPERED-IN-TRANSIT")
    with pytest.raises(TM.TransferManifestError):
        TM.verify(str(bundle), str(out))


def test_empty_foreign_directory_in_real_bundle_is_detected(tmp_path):
    bundle, _ = _real_phase_b_bundle(tmp_path)
    (bundle / "sneaky-empty").mkdir()                # no files -> invisible to a file-only collector
    with pytest.raises(TM.TransferManifestError, match="exactly one directory"):
        TM.build_manifest(str(bundle))


# --- the REAL release input gate over REAL producer output --------------------------------------- #
#
# SEAM BINDING. `R.verify_phase_b_transfer_inputs` is the ONE definition of the release producer's
# Phase-B transfer gate. `produce_release()` contains no transfer/binding logic of its own -- it
# calls this helper and nothing else. The two therefore cannot drift, and
# `test_gate_seam_is_the_one_produce_release_uses` below proves the call really happens, before any
# artifact bytes are constructed.

def _gate_inputs(bundle, out):
    return dict(wheelhouse_armv7_dir=str(bundle / TM.WHEELHOUSE_DIR),
                provenance_armv7_path=str(bundle / TM.PROVENANCE),
                armv7_runtime_lock_path=str(bundle / TM.RUNTIME_LOCK),
                transfer_manifest_path=str(out))


def test_real_producer_output_is_accepted_by_the_release_input_gate(tmp_path):
    """No network. Real producer bundle + real generated manifest -> the actual release gate."""
    bundle, res = _real_phase_b_bundle(tmp_path)
    out = tmp_path / "phase-b-bundle-transfer-manifest.json"
    TM.generate(str(bundle), str(out))

    m = R.verify_phase_b_transfer_inputs(**_gate_inputs(bundle, out))

    # Jointly accepted: wheelhouse dir, provenance INSIDE the verified bundle, canonical-header
    # runtime lock, and the manifest generated from that same bundle.
    assert m["file_count"] == TM.EXPECTED_FILES
    assert m["bind"]["tree_sha256"] == res["bundle_tree_sha256"]
    assert (bundle / TM.RUNTIME_LOCK).read_text().splitlines()[0] == TM.RUNTIME_LOCK_HEADER


def test_gate_seam_is_the_one_produce_release_uses(monkeypatch):
    """produce_release must reach the shared helper, and must not construct artifacts first."""
    import inspect
    src = inspect.getsource(R.produce_release)
    assert "verify_phase_b_transfer_inputs(" in src
    # produce_release owns NO duplicate gate logic -- the helper is the only place these appear.
    assert "_tmanifest.verify(" not in src
    assert "_tmanifest.PROVENANCE" not in src
    # ...and the call precedes artifact construction in the production source order.
    assert src.index("verify_phase_b_transfer_inputs(") < src.index("pack_tree(canon)")


def test_gate_rejects_provenance_from_a_different_bundle(tmp_path):
    bundle, _ = _real_phase_b_bundle(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    other, _ = _real_phase_b_bundle(second)
    out = tmp_path / "phase-b-bundle-transfer-manifest.json"
    TM.generate(str(bundle), str(out))
    args = _gate_inputs(bundle, out)
    args["provenance_armv7_path"] = str(other / TM.PROVENANCE)
    with pytest.raises(R.ReleaseError, match="provenance must be the canonical file"):
        R.verify_phase_b_transfer_inputs(**args)


def test_gate_rejects_a_tampered_real_bundle(tmp_path):
    bundle, _ = _real_phase_b_bundle(tmp_path)
    out = tmp_path / "phase-b-bundle-transfer-manifest.json"
    TM.generate(str(bundle), str(out))
    wheel = sorted((bundle / TM.WHEELHOUSE_DIR).glob("*.whl"))[0]
    wheel.write_bytes(wheel.read_bytes() + b"x")
    with pytest.raises(R.ReleaseError, match="verification failed"):
        R.verify_phase_b_transfer_inputs(**_gate_inputs(bundle, out))


def test_gate_requires_the_manifest(tmp_path):
    bundle, _ = _real_phase_b_bundle(tmp_path)
    args = _gate_inputs(bundle, tmp_path / "phase-b-bundle-transfer-manifest.json")
    args["transfer_manifest_path"] = ""
    with pytest.raises(R.ReleaseError, match="required"):
        R.verify_phase_b_transfer_inputs(**args)
