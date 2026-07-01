"""
tests/unit/test_capability_extraction.py
----------------------------------------
Batch B (IDD-0002, Option B): behavioural contracts for the Layer-2 required-
capability extraction (backend/capability_extraction.py), with the R2
reconciliation applied.

The module is unwired, dependency-free at runtime, and platform-independent, so
no skip markers apply. Batch A is imported here READ-ONLY, solely to assert that
extraction output conforms to the evaluator's ``required`` input contract; this
is a test-only reference, not runtime wiring.
"""
from __future__ import annotations

from backend.capability_extraction import (
    DecodeResult,
    DecodeState,
    Extraction,
    absent,
    declared,
    extract_required,
    malformed,
)
from backend.capability_sufficiency import Sufficiency, evaluate_sufficiency


# --------------------------------------------------------------------------- #
#  Three-state mapping (R2)                                                    #
# --------------------------------------------------------------------------- #
def test_absent_is_fail_closed():
    out = extract_required(absent())
    assert out.fail_closed is True
    assert out.required is None


def test_malformed_is_fail_closed():
    out = extract_required(malformed())
    assert out.fail_closed is True
    assert out.required is None


def test_declared_empty_is_not_fail_closed_and_yields_empty_required():
    out = extract_required(declared([]))
    assert out.fail_closed is False
    assert list(out.required) == []


def test_declared_nonempty_preserves_identities():
    out = extract_required(declared(["a", "b"]))
    assert out.fail_closed is False
    assert list(out.required) == ["a", "b"]


# --------------------------------------------------------------------------- #
#  R2 correctness: Absent is NOT the application-only no-op path               #
# --------------------------------------------------------------------------- #
def test_absent_never_yields_a_required_collection():
    # The no-op path arrives only via Declared(empty); Absent always refuses.
    out = extract_required(absent())
    assert out.fail_closed is True
    assert out.required is None


def test_declared_empty_is_the_no_op_path_not_absent():
    # Declared(empty) is not fail-closed (it is the application-only no-op input,
    # delivered by Layer 1 per R2), unlike Absent.
    assert extract_required(declared([])).fail_closed is False
    assert extract_required(absent()).fail_closed is True


# --------------------------------------------------------------------------- #
#  Output conforms to Batch A's `required` input contract                      #
# --------------------------------------------------------------------------- #
def test_declared_output_is_accepted_by_evaluator_sufficient():
    req = extract_required(declared(["a"])).required
    assert evaluate_sufficiency(req, ["a", "b"]) is Sufficiency.SUFFICIENT


def test_declared_output_is_accepted_by_evaluator_insufficient():
    req = extract_required(declared(["a", "z"])).required
    assert evaluate_sufficiency(req, ["a", "b"]) is Sufficiency.INSUFFICIENT


def test_declared_empty_output_is_no_op_via_evaluator():
    req = extract_required(declared([])).required
    assert evaluate_sufficiency(req, ["a", "b"]) is Sufficiency.SUFFICIENT


# --------------------------------------------------------------------------- #
#  Defense in depth: ill-formed Declared (contract violation) is caught        #
#  downstream by the evaluator, not re-validated here                          #
# --------------------------------------------------------------------------- #
def test_illformed_declared_is_forwarded_and_fails_closed_downstream():
    """Deliberate contract-violation test (defense in depth).

    This test INTENTIONALLY violates the Layer 1 decode contract (IDD-0002 §12)
    by constructing an impossible ``Declared`` payload directly -- a bare string
    in place of a well-formed collection. Under the frozen contract, Layer 1
    never produces such a value, so this is NOT a valid runtime input; it is a
    deliberate fault injection, not a case that can occur in normal operation.

    Its purpose is to prove two properties:
      1. Batch B does NOT silently normalize or "repair" invalid data -- the
         ill-formed payload is forwarded UNCHANGED (it is not turned into a
         collection of characters or otherwise masked).
      2. Batch A remains the final fail-closed authority -- the evaluator, seeing
         the forwarded ill-formed value, returns UNDETERMINABLE (fail-closed).
    """
    # Bare string forwarded unchanged (Batch B does not mask or normalize it).
    req = extract_required(declared("ab")).required
    assert req == "ab"  # forwarded unchanged, not normalized
    # Batch A is the final fail-closed authority for residual ill-formedness.
    assert evaluate_sufficiency(req, ["a"]) is Sufficiency.UNDETERMINABLE


# --------------------------------------------------------------------------- #
#  Robustness / determinism / no side effects                                 #
# --------------------------------------------------------------------------- #
def test_non_decoderesult_input_fails_closed():
    out = extract_required(object())
    assert out.fail_closed is True
    assert out.required is None


def test_extraction_is_deterministic():
    r = declared(["a", "b"])
    first = extract_required(r)
    for _ in range(5):
        assert extract_required(r) == first


def test_extraction_does_not_mutate_input_collection():
    coll = ["a", "b"]
    coll_copy = list(coll)
    extract_required(declared(coll))
    assert coll == coll_copy


# --------------------------------------------------------------------------- #
#  Type surface                                                               #
# --------------------------------------------------------------------------- #
def test_decode_states_are_the_three_defined_values():
    assert {s.name for s in DecodeState} == {"ABSENT", "MALFORMED", "DECLARED"}


def test_constructors_produce_expected_decoderesults():
    assert absent() == DecodeResult(DecodeState.ABSENT)
    assert malformed() == DecodeResult(DecodeState.MALFORMED)
    assert declared(["x"]) == DecodeResult(DecodeState.DECLARED, ["x"])


def test_extraction_result_shape():
    assert extract_required(absent()) == Extraction(fail_closed=True, required=None)
