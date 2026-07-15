# SPDX-License-Identifier: MIT
"""Semantic lock validation (ADR-0003 Amendment A1, finding #4).

Replaces the name-only drift check: proves the example schema fixtures are VALID
SOLUTIONS of requirements.txt (pinned, hashed, version-satisfying), that the
validator CATCHES missing/unhashed/violating locks, and enforces the release-input
gate (active root locks must be absent [pre-build] or valid [post-build])."""
from __future__ import annotations

import pathlib

from release import ccc_release as R
from release import lock_validate as LV

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REQ = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
_SCHEMA = _ROOT / "release" / "lock-schema"


def test_example_fixtures_are_valid_solutions():
    for fx in ("requirements-aarch64.lock.example",
               "requirements-armv7.lock.example",
               "requirements-armv7-build.lock.example"):
        problems = LV.validate(_REQ, (_SCHEMA / fx).read_text(encoding="utf-8"))
        assert problems == [], f"{fx}: {problems}"


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


def test_release_input_gate_active_locks_absent_or_valid():
    # Active root locks must be ABSENT (honest pre-build state) OR valid solutions.
    # This prevents merging placeholder/invalid locks that would break installation.
    for lock in ("requirements-armv7.lock", "requirements-aarch64.lock",
                 "requirements-armv7-build.lock"):
        path = _ROOT / lock
        if path.exists():
            problems = LV.validate(_REQ, path.read_text(encoding="utf-8"))
            assert problems == [], f"active {lock} is not a valid solution: {problems}"


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
