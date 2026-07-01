"""
tests/unit/test_capability_decision.py
--------------------------------------
Batch C (IDD-0002): behavioural contracts for the Capability Decision
Composition (backend/capability_decision.py).

Pure, dependency-light, platform-independent -> no skip markers. Batch A and
Batch B are imported READ-ONLY; the composition delegates to them, and this file
asserts both the delegation and the IDD-0002 §6 mapping. The delegation tests
temporarily wrap the composition's module-level references (restored in a
finally block); they do not modify Batch A or Batch B.
"""
from __future__ import annotations

import backend.capability_decision as cd
from backend.capability_decision import AdoptionDecision, decide
from backend.capability_extraction import absent, declared, malformed


# --------------------------------------------------------------------------- #
#  IDD-0002 §6 mapping over the composed pipeline                              #
# --------------------------------------------------------------------------- #
def test_absent_required_is_fail_closed():
    assert decide(absent(), ["a"]) is AdoptionDecision.FAIL_CLOSED


def test_malformed_required_is_fail_closed():
    assert decide(malformed(), ["a"]) is AdoptionDecision.FAIL_CLOSED


def test_declared_contained_is_no_op():
    assert decide(declared(["a"]), ["a", "b"]) is AdoptionDecision.NO_OP


def test_declared_empty_is_no_op():
    assert decide(declared([]), ["a"]) is AdoptionDecision.NO_OP


def test_declared_missing_is_adoption_required():
    assert decide(declared(["a", "z"]), ["a"]) is AdoptionDecision.ADOPTION_REQUIRED


def test_declared_all_absent_is_adoption_required():
    assert decide(declared(["x"]), ["a", "b"]) is AdoptionDecision.ADOPTION_REQUIRED


def test_illformed_declared_maps_undeterminable_to_fail_closed():
    # A Declared carrying a bare string (contract violation) -> evaluator
    # UNDETERMINABLE -> FAIL_CLOSED (I5). Batch A remains the fail-closed authority.
    assert decide(declared("ab"), ["a"]) is AdoptionDecision.FAIL_CLOSED


# --------------------------------------------------------------------------- #
#  Extraction fail-closed short-circuits (evaluator not called)               #
# --------------------------------------------------------------------------- #
def test_fail_closed_extraction_short_circuits_evaluator():
    calls = {"evaluate": 0}
    orig_eval = cd.evaluate_sufficiency
    try:
        def counting_eval(req, prov):
            calls["evaluate"] += 1
            return orig_eval(req, prov)

        cd.evaluate_sufficiency = counting_eval
        result = decide(absent(), ["a"])
    finally:
        cd.evaluate_sufficiency = orig_eval
    assert result is AdoptionDecision.FAIL_CLOSED
    assert calls["evaluate"] == 0  # evaluator not called when extraction fails closed


# --------------------------------------------------------------------------- #
#  Delegation: composes Batch B then Batch A (does not re-implement)          #
# --------------------------------------------------------------------------- #
def test_decide_delegates_to_extraction_and_evaluator():
    calls = {"extract": 0, "evaluate": 0}
    orig_extract = cd.extract_required
    orig_eval = cd.evaluate_sufficiency
    try:
        def counting_extract(decl):
            calls["extract"] += 1
            return orig_extract(decl)

        def counting_eval(req, prov):
            calls["evaluate"] += 1
            return orig_eval(req, prov)

        cd.extract_required = counting_extract
        cd.evaluate_sufficiency = counting_eval
        decide(declared(["a"]), ["a"])
    finally:
        cd.extract_required = orig_extract
        cd.evaluate_sufficiency = orig_eval
    assert calls["extract"] == 1
    assert calls["evaluate"] == 1


# --------------------------------------------------------------------------- #
#  Determinism / no side effects                                              #
# --------------------------------------------------------------------------- #
def test_decision_is_deterministic():
    first = decide(declared(["a"]), ["a", "b"])
    for _ in range(5):
        assert decide(declared(["a"]), ["a", "b"]) is first


def test_decision_does_not_mutate_inputs():
    req = ["a", "b"]
    prov = ["a", "b", "c"]
    req_copy, prov_copy = list(req), list(prov)
    decide(declared(req), prov)
    assert req == req_copy
    assert prov == prov_copy


# --------------------------------------------------------------------------- #
#  Decision surface                                                           #
# --------------------------------------------------------------------------- #
def test_adoption_decision_has_three_values():
    assert {d.name for d in AdoptionDecision} == {"NO_OP", "ADOPTION_REQUIRED", "FAIL_CLOSED"}
