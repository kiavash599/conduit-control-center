# SPDX-License-Identifier: MIT
"""Semantic lock validation (ADR-0003 Amendment A1, finding #4).

Replaces the name-only drift check: proves the example schema fixtures are VALID
SOLUTIONS of requirements.txt (pinned, hashed, version-satisfying), that the
validator CATCHES missing/unhashed/violating locks, and enforces the release-input
gate (active root locks must be absent [pre-build] or valid [post-build])."""
from __future__ import annotations

import pathlib

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
