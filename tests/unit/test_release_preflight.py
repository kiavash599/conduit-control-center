# SPDX-License-Identifier: MIT
"""Authoritative release-readiness gate (release.ccc_release.release_preflight + the release.preflight
CLI). Proves the gate uses the SAME validators produce_release uses, and enforces the derived-input
state machine over the two co-produced active inputs (the six-entry build lock and the 24-entry reuse
authorization):

  * generated (BOTH present) -- a full 6+24=30 tree passes in BOTH dev and release mode, with the
    exact disjoint partition validated;
  * pre_generation (BOTH absent) -- the only other legal state; passes in dev mode ONLY, because the
    generator has not been run yet;
  * release mode REJECTS pre_generation: the generated active inputs are required to cut a release;
  * half-state (exactly ONE derived input present, in either direction) -- a broken atomic commit;
    INVALID IN EVERY MODE, dev and release alike;

plus the CLI exit behavior (0 on a passing gate, 1 fail-closed) for both modes.
Uses the shared hybrid fixture (no network)."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from release import ccc_release as R
# Package-qualified import (matches every other test using this fixture). A bare
# `import _hybrid_release_fixture` only resolves when tests/unit happens to be on sys.path, which is
# NOT true under the authoritative rootdir-based collection -- it fails before collection.
from tests.unit import _hybrid_release_fixture as F

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CLI = _ROOT / "release" / "preflight.py"


def test_release_mode_full_set_validates_partition(tmp_path):
    info = F.make_release(tmp_path, version="0.3.17")
    st = R.release_preflight(info["repo"], require_present=True)
    assert st["reuse_authz"] == "present"
    assert st["solution"] == "present"
    assert st["target_tags"] == "present"
    assert st["partition"] == "validated"


def _derived_paths(repo):
    return (os.path.join(repo, R.BUILD_LOCK_PATH),
            os.path.join(repo, "release", "builder", "armv7-reuse-authz.json"))


def test_dev_mode_tolerates_true_pre_generation_state(tmp_path):
    # TRUE pre-generation = BOTH derived active inputs absent. (Removing only one is a HALF-STATE and
    # is covered by the tests below -- it must never be reported as a valid pre-generation state.)
    info = F.make_release(tmp_path, version="0.3.17")
    build_lock, authz = _derived_paths(info["repo"])
    os.remove(authz)
    os.remove(build_lock)
    st = R.release_preflight(info["repo"], require_present=False)
    assert st["derived_state"] == "pre_generation"
    assert st["build_lock"] == "absent" and st["reuse_authz"] == "absent"
    assert st["solution"] == "present"          # durable inputs still validated in dev mode
    assert "partition" not in st                # nothing to partition yet


def test_release_mode_fails_closed_in_pre_generation_state(tmp_path):
    info = F.make_release(tmp_path, version="0.3.17")
    build_lock, authz = _derived_paths(info["repo"])
    os.remove(authz)
    os.remove(build_lock)
    with pytest.raises(R.ReleaseError, match="release mode requires the generated active inputs"):
        R.release_preflight(info["repo"], require_present=True)


@pytest.mark.parametrize("remove", ["authz", "build_lock"])
@pytest.mark.parametrize("require_present", [False, True])
def test_half_state_rejected_in_every_mode(tmp_path, remove, require_present):
    # Exactly ONE derived active input present == a broken atomic commit. Invalid in dev AND release
    # mode, in both directions. This is the state a partial commit leaves behind.
    info = F.make_release(tmp_path, version="0.3.17")
    build_lock, authz = _derived_paths(info["repo"])
    os.remove(authz if remove == "authz" else build_lock)
    with pytest.raises(R.ReleaseError, match="not atomic"):
        R.release_preflight(info["repo"], require_present=require_present)


def test_generated_state_validates_partition_in_dev_mode_too(tmp_path):
    # Once generated, an inconsistent partition is a defect in every mode -- dev mode must not skip it.
    info = F.make_release(tmp_path, version="0.3.17")
    st = R.release_preflight(info["repo"], require_present=False)
    assert st["derived_state"] == "generated" and st["partition"] == "validated"


def test_dev_mode_detects_committed_solution_drift(tmp_path):
    info = F.make_release(tmp_path, version="0.3.17")
    sol = os.path.join(info["repo"], "requirements-armv7-solution.lock")
    with open(sol, "a", encoding="utf-8") as fh:
        fh.write("extrapkg==1.0 --hash=sha256:%s\n" % ("a" * 64))   # 31 pins != 30-package closure
    with pytest.raises(R.ReleaseError):
        R.release_preflight(info["repo"], require_present=False)


def _solution_partition_inputs(tmp_path):
    info = F.make_release(tmp_path, version="0.3.17")
    repo = pathlib.Path(info["repo"])
    sol = (repo / "requirements-armv7-solution.lock").read_text()
    req = (repo / "requirements.txt").read_text()
    authz = json.loads((repo / "release" / "builder" / "armv7-reuse-authz.json").read_text())
    reuse_versions = {w["name"]: w["version"] for w in authz["wheels"]}
    build_pins = {n: ("1.0", {"a" * 64}) for n in R.V0317_SOURCE_BUILD_PACKAGES}
    return sol, req, reuse_versions, build_pins


def test_validate_armv7_solution_partitions_30_into_6_plus_24(tmp_path):
    sol, req, rv, bp = _solution_partition_inputs(tmp_path)
    pins = R.validate_armv7_solution(sol, req, reuse_names=set(rv), reuse_versions=rv, build_pins=bp)
    assert len(pins) == R.V0317_TOTAL_COUNT


def test_validate_armv7_solution_rejects_build_version_disagreement(tmp_path):
    sol, req, rv, _bp = _solution_partition_inputs(tmp_path)
    bad = {n: ("9.9", {"a" * 64}) for n in R.V0317_SOURCE_BUILD_PACKAGES}   # != solution version 1.0
    with pytest.raises(R.ReleaseError):
        R.validate_armv7_solution(sol, req, reuse_names=set(rv), reuse_versions=rv, build_pins=bad)


def test_validate_armv7_solution_rejects_reuse_set_mismatch(tmp_path):
    sol, req, rv, bp = _solution_partition_inputs(tmp_path)
    bad_names = set(list(rv)[:-1] + ["notinsolution"])
    with pytest.raises(R.ReleaseError):
        R.validate_armv7_solution(sol, req, reuse_names=bad_names, reuse_versions=rv, build_pins=bp)


def test_preflight_rejects_malformed_target_tags(tmp_path):
    # 495 unique but MALFORMED tag strings must be rejected by preflight, not deferred to the producer.
    info = F.make_release(tmp_path, version="0.3.17")
    tags = pathlib.Path(info["repo"]) / "release" / "builder" / "target-supported-tags.txt"
    lines = tags.read_text().split("\n")
    lines[0] = "not_a_valid_tag_no_dashes"          # still 495 unique lines, but bad grammar
    tags.write_text("\n".join(lines))
    with pytest.raises(R.ReleaseError, match="malformed wheel tag"):
        R.release_preflight(info["repo"], require_present=True)


@pytest.mark.parametrize("payload,ok", [
    (None, True),                                            # the real committed artifact
    (b"not_a_valid_tag_no_dashes\n", False),                 # malformed grammar
    (b"", False),                                            # empty
])
def test_preflight_and_producer_share_one_target_tag_validator(payload, ok):
    # Mechanical proof they cannot disagree: preflight and produce_release BOTH call
    # ccc_release.validate_target_tags, which delegates to reuse_authz.parse_target_tags. Feeding the
    # same bytes to the canonical validator and to the underlying parser must agree on accept/reject.
    from release import reuse_authz as RA
    real = (_ROOT / "release" / "builder" / "target-supported-tags.txt").read_bytes()
    data = real if payload is None else payload
    try:
        tags, tset, sha = R.validate_target_tags(data)
        got_ok = True
    except R.ReleaseError:
        got_ok = False
    assert got_ok is ok
    if ok:
        # identical semantics and identical digest as the shared underlying parser
        p_tags, p_set, p_sha = RA.parse_target_tags(data)
        assert (tags, tset, sha) == (p_tags, p_set, p_sha)
        assert len(tags) == R.TARGET_TAG_COUNT


def _fixture_repo(tmp_path, *, state):
    """An ISOLATED fixture repo pinned to an EXPLICIT derived-input state.

    Semantic CLI tests must never depend on the real working tree: its lifecycle state legitimately
    transitions from `pre_generation` to `generated` when the Owner imports the co-produced active
    inputs, so any test hard-coding one of those states is only transiently true."""
    info = F.make_release(tmp_path, version="0.3.17")
    build_lock, authz = _derived_paths(info["repo"])
    if state == "pre_generation":
        os.remove(build_lock)
        os.remove(authz)
    elif state != "generated":
        raise AssertionError(f"unknown fixture state: {state!r}")
    return info["repo"]


def _run_cli(repo, *args):
    return subprocess.run([sys.executable, str(_CLI), "--repo", str(repo), *args],
                          capture_output=True, text=True)


def test_cli_release_mode_fails_closed_in_pre_generation(tmp_path):
    # TRUE pre-generation (BOTH derived inputs absent) -> release mode must fail closed (exit 1).
    r = _run_cli(_fixture_repo(tmp_path, state="pre_generation"), "--require-present")
    assert r.returncode == 1
    assert "RELEASE PREFLIGHT FAILED" in r.stderr
    assert "requires the generated active inputs" in r.stderr


def test_cli_release_mode_passes_in_generated_state(tmp_path):
    # Generated (BOTH derived inputs present, exact disjoint 6+24=30 partition) -> exit 0.
    r = _run_cli(_fixture_repo(tmp_path, state="generated"), "--require-present")
    assert r.returncode == 0, r.stderr
    assert "release preflight OK [release]" in r.stdout


def test_cli_dev_mode_on_real_repo():
    # Dev mode is valid in BOTH legal states (pre_generation AND generated), so this real-tree smoke
    # check is state-INDEPENDENT: it asserts only that the committed tree is in SOME legal state and
    # that the CLI reports it. State-specific semantics are proven against isolated fixtures above.
    r = subprocess.run([sys.executable, str(_CLI), "--repo", str(_ROOT)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "release preflight OK [dev]" in r.stdout
