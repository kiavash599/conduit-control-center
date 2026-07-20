# SPDX-License-Identifier: MIT
"""Semantic lock validation (ADR-0003 Amendment A1 + A5 dual-origin).

Each lock is validated according to its ROLE. The COMPLETE-SOLUTION locks
(requirements-aarch64 / requirements-armv7 / requirements-armv7-solution) must be valid solutions of
requirements.txt. The DERIVED six-entry requirements-armv7-build.lock is the SOURCE-BUILD PARTITION --
it deliberately omits the 24 reused packages, so it is validated by
ccc_release.validate_build_partition_lock and must NEVER be judged as a complete solution (doing so
would reject the legal `generated` state). Also enforces the release-input gate and proves the
derived-input state machine (pre_generation / generated / no half-state) on the real tree."""
from __future__ import annotations

import pathlib

import pytest

from release import ccc_release as R
from release import lock_validate as LV

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REQ = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
_SCHEMA = _ROOT / "release" / "lock-schema"


def test_complete_solution_example_fixtures_are_valid_solutions():
    # ONLY the complete-solution locks are validated as complete solutions of requirements.txt.
    # requirements-armv7-build.lock.example is deliberately NOT here: it is the six-entry SOURCE-BUILD
    # PARTITION and is validated by its own role validator below.
    for fx in ("requirements-aarch64.lock.example",
               "requirements-armv7.lock.example"):
        problems = LV.validate(_REQ, (_SCHEMA / fx).read_text(encoding="utf-8"))
        assert problems == [], f"{fx}: {problems}"


def _build_example() -> str:
    return (_SCHEMA / "requirements-armv7-build.lock.example").read_text(encoding="utf-8")


def test_build_lock_example_is_the_approved_six_partition():
    # Role-appropriate validation: exactly the approved six, valid pin/hash grammar.
    pins = R.validate_build_partition_lock(_build_example())
    assert set(pins) == set(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES)
    assert len(pins) == R.WHEELHOUSE_BUILT_COUNT == 6
    for name, (version, hashes) in pins.items():
        assert version and hashes, name
        assert all(len(h) == 64 and all(c in "0123456789abcdef" for c in h) for h in hashes), name


def test_build_lock_example_is_not_a_complete_solution():
    # Guards the actual regression: the six-entry partition MUST NOT be judged as a full closure.
    # It legitimately omits the 24 reused packages, so the complete-solution validator reports
    # problems -- which is exactly why applying it to this role would reject the legal generated state.
    problems = LV.validate(_REQ, _build_example())
    assert problems, "the six-entry build lock is not a complete solution of requirements.txt"
    assert any("not pinned" in p for p in problems)


@pytest.mark.parametrize("mutate,reason", [
    # extra (non-approved) package in the source-build partition
    (lambda t: t + "evilpkg==1.0 --hash=sha256:%s\n" % ("a" * 64), "extra"),
    # missing an approved package
    (lambda t: "\n".join(ln for ln in t.splitlines() if not ln.startswith("uvloop==")) + "\n", "missing"),
])
def test_build_partition_membership_failures(mutate, reason):
    with pytest.raises(R.ReleaseError, match="EXACTLY the approved six") as ei:
        R.validate_build_partition_lock(mutate(_build_example()))
    assert reason in str(ei.value)


@pytest.mark.parametrize("bad", [
    "cffi==2.1.0\n",                                              # unhashed
    "cffi==2.1.0 --hash=sha256:tooshort\n",                       # malformed hash
    "cffi>=2.1.0 --hash=sha256:%s\n" % ("a" * 64),                # not pinned with ==
    "--index-url https://x\ncffi==2.1.0 --hash=sha256:%s\n" % ("a" * 64),   # forbidden directive
])
def test_build_partition_grammar_failures(bad):
    # Grammar is enforced by the SAME closed-grammar parser the producer uses (_parse_lock_pins),
    # so a malformed six-entry lock fails before any membership question is reached.
    with pytest.raises(R.ReleaseError):
        R.validate_build_partition_lock(bad)


def test_build_partition_rejects_duplicate_pin():
    dup = _build_example() + "cffi==9.9.9 --hash=sha256:%s\n" % ("b" * 64)
    with pytest.raises(R.ReleaseError):
        R.validate_build_partition_lock(dup)


def test_build_partition_wrong_version_rejected_against_solution():
    # Version agreement is owned by validate_armv7_solution(build_pins=...), the authoritative
    # cross-artifact validator -- not re-implemented here.
    sol = (_ROOT / "requirements-armv7-solution.lock")
    if not sol.exists():
        pytest.skip("durable solution lock not present")
    pins = R.validate_build_partition_lock(_build_example())
    bad = dict(pins)
    bad["uvloop"] = ("0.0.1-wrong", pins["uvloop"][1])            # disagrees with the solution
    with pytest.raises(R.ReleaseError, match="build-lock version"):
        R.validate_armv7_solution(sol.read_text(encoding="utf-8"), _REQ, build_pins=bad)


def test_validator_catches_missing_unhashed_and_violation():
    # missing a required dep entirely
    assert any("not pinned" in p for p in LV.validate(_REQ, "fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64)))
    # pinned but no hash -> caught by the closed grammar as an unrecognized line
    assert LV.validate("fastapi>=0.133.0,<1.0.0\n", "fastapi==0.133.0\n")
    # forbidden directive -> rejected
    assert any("unrecognized" in p for p in
               LV.validate("fastapi>=0.133.0,<1.0.0\n", "--index-url https://x\nfastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64)))
    # version violates the bound
    assert any("violates" in p for p in
               LV.validate("fastapi>=0.133.0,<1.0.0\n", "fastapi==2.0.0 --hash=sha256:%s\n" % ("a" * 64)))
    # duplicate pin
    dup = "fastapi==0.133.0 --hash=sha256:%s\nfastapi==0.134.0 --hash=sha256:%s\n" % ("a" * 64, "b" * 64)
    assert any("duplicate" in p for p in LV.validate("fastapi>=0.133.0,<1.0.0\n", dup))


def test_release_input_gate_active_complete_solution_locks():
    # Active COMPLETE-SOLUTION root locks must be ABSENT (honest pre-build state) OR valid solutions.
    # requirements-armv7-build.lock is intentionally NOT in this list: it is the six-entry partition,
    # and validating it here is precisely the defect that would reject the legal generated state.
    for lock in ("requirements-armv7.lock", "requirements-aarch64.lock",
                 "requirements-armv7-solution.lock"):
        path = _ROOT / lock
        if path.exists():
            problems = LV.validate(_REQ, path.read_text(encoding="utf-8"))
            assert problems == [], f"active {lock} is not a valid solution: {problems}"


def test_release_input_gate_active_build_lock_uses_partition_role():
    # If the Owner has generated the real six-entry build lock, it must satisfy its OWN role (and
    # must NOT be required to be a complete solution). Absent == the legal pre_generation state.
    path = _ROOT / R.BUILD_LOCK_PATH
    if path.exists():
        pins = R.validate_build_partition_lock(path.read_text(encoding="utf-8"))
        assert set(pins) == set(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES)


def test_real_repo_derived_state_machine_is_coherent():
    # Whatever state the tree is in, it must be one of the two LEGAL states -- never a half-state.
    build = (_ROOT / R.BUILD_LOCK_PATH).exists()
    authz = (_ROOT / "release" / "builder" / "armv7-reuse-authz.json").exists()
    assert build == authz, (
        "derived active inputs must be BOTH absent (pre_generation) or BOTH present (generated); "
        f"build_lock={build} reuse_authz={authz}")
    # And the authoritative gate must agree with that observation.
    st = R.release_preflight(str(_ROOT), require_present=False)
    assert st["derived_state"] == ("generated" if build else "pre_generation")


def test_real_repo_release_mode_matches_generation_state():
    # Release mode is green only once BOTH derived inputs exist; red (fail-closed) before that.
    generated = (_ROOT / R.BUILD_LOCK_PATH).exists()
    if generated:
        st = R.release_preflight(str(_ROOT), require_present=True)
        assert st["partition"] == "validated"
    else:
        with pytest.raises(R.ReleaseError):
            R.release_preflight(str(_ROOT), require_present=True)


def test_extractor_tools_in_requests_tomli():
    # The committed .in must exist and request tomli (the pinned connected-phase TOML parser).
    inp = _ROOT / "release" / "builder" / "requirements-extractor-tools.in"
    assert inp.exists(), "requirements-extractor-tools.in must be committed"
    text = inp.read_text(encoding="utf-8")
    assert any(ln.strip().lower().startswith("tomli==") for ln in text.splitlines())


def test_extractor_tools_lock_lifecycle():
    # Lifecycle-aware: if the .lock is committed it must pin the .in-requested tomli exactly;
    # if absent (blocked/pre-gate) this is a no-op (never a brittle 'must not exist').
    inp = _ROOT / "release" / "builder" / "requirements-extractor-tools.in"
    lock = _ROOT / "release" / "builder" / "requirements-extractor-tools.lock"
    in_text = inp.read_text(encoding="utf-8")
    if lock.exists():
        R.validate_extractor_tools_lock(lock.read_text(encoding="utf-8"), in_text)
    # Synthetic positive/negative always exercised (independent of the blocked real .lock):
    good = "tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
    R.validate_extractor_tools_lock(good, "tomli==2.0.1\n")
    import pytest
    with pytest.raises(R.ReleaseError):        # version drift vs .in
        R.validate_extractor_tools_lock("tomli==1.0.0 --hash=sha256:%s\n" % ("7" * 64), "tomli==2.0.1\n")
    with pytest.raises(R.ReleaseError):        # lock does not pin tomli
        R.validate_extractor_tools_lock("wheel==0.43.0 --hash=sha256:%s\n" % ("7" * 64), "tomli==2.0.1\n")
    with pytest.raises(R.ReleaseError):        # F6: unauthorized extra package in the closure
        R.validate_extractor_tools_lock(
            "tomli==2.0.1 --hash=sha256:%s\nevil==9.9 --hash=sha256:%s\n" % ("7" * 64, "8" * 64),
            "tomli==2.0.1\n")
