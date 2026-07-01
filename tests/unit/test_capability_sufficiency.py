"""
tests/unit/test_capability_sufficiency.py
-----------------------------------------
Batch A (IDD-0002): behavioural contracts for the Capability Sufficiency
decision (backend/capability_sufficiency.py).

These tests exercise the pure containment evaluator only. The module is
unwired, dependency-free, and platform-independent, so no skip markers apply.
Capability identities are treated as opaque, equality-comparable values
(strings and tuples are used only as stand-ins; the encoding is deferred).
"""
from __future__ import annotations

from backend.capability_sufficiency import Sufficiency, evaluate_sufficiency


# --------------------------------------------------------------------------- #
#  Outcome enum                                                               #
# --------------------------------------------------------------------------- #
def test_outcomes_are_the_three_defined_values():
    assert {s.name for s in Sufficiency} == {"SUFFICIENT", "INSUFFICIENT", "UNDETERMINABLE"}


# --------------------------------------------------------------------------- #
#  Empty-requirement rule -> no-op                                            #
# --------------------------------------------------------------------------- #
def test_empty_required_is_sufficient_against_empty_provided():
    assert evaluate_sufficiency([], []) is Sufficiency.SUFFICIENT


def test_empty_required_is_sufficient_against_any_provided():
    assert evaluate_sufficiency([], ["a", "b"]) is Sufficiency.SUFFICIENT


# --------------------------------------------------------------------------- #
#  Containment (I3)                                                           #
# --------------------------------------------------------------------------- #
def test_subset_required_is_sufficient():
    assert evaluate_sufficiency(["a"], ["a", "b", "c"]) is Sufficiency.SUFFICIENT


def test_equal_sets_are_sufficient():
    assert evaluate_sufficiency(["a", "b"], ["b", "a"]) is Sufficiency.SUFFICIENT


def test_missing_one_required_is_insufficient():
    assert evaluate_sufficiency(["a", "z"], ["a", "b", "c"]) is Sufficiency.INSUFFICIENT


def test_all_required_absent_is_insufficient():
    assert evaluate_sufficiency(["x", "y"], ["a", "b"]) is Sufficiency.INSUFFICIENT


def test_empty_provided_with_nonempty_required_is_insufficient():
    assert evaluate_sufficiency(["a"], []) is Sufficiency.INSUFFICIENT


def test_duplicate_identities_do_not_change_containment():
    assert evaluate_sufficiency(["a", "a"], ["a", "a", "b"]) is Sufficiency.SUFFICIENT


# --------------------------------------------------------------------------- #
#  Unknown / unrecognized required capability -> treated as absent            #
# --------------------------------------------------------------------------- #
def test_unknown_required_capability_is_insufficient():
    # An identity the provided class does not carry is simply absent (IDD-0002 §8).
    assert evaluate_sufficiency(["future-only-capability"], ["a", "b"]) is Sufficiency.INSUFFICIENT


# --------------------------------------------------------------------------- #
#  Exact-identity matching only (no approximate/semantic match)               #
# --------------------------------------------------------------------------- #
def test_similar_but_unequal_identity_does_not_satisfy():
    assert evaluate_sufficiency(["cap"], ["cap "]) is Sufficiency.INSUFFICIENT  # trailing space


def test_identity_type_is_opaque_tuples_work_by_equality():
    assert evaluate_sufficiency([("cap", 1)], [("cap", 1), ("cap", 2)]) is Sufficiency.SUFFICIENT
    assert evaluate_sufficiency([("cap", 3)], [("cap", 1), ("cap", 2)]) is Sufficiency.INSUFFICIENT


# --------------------------------------------------------------------------- #
#  Fail-closed on undeterminable input (I5)                                   #
# --------------------------------------------------------------------------- #
def test_required_none_is_undeterminable():
    assert evaluate_sufficiency(None, ["a"]) is Sufficiency.UNDETERMINABLE


def test_provided_none_is_undeterminable():
    assert evaluate_sufficiency(["a"], None) is Sufficiency.UNDETERMINABLE


def test_bare_string_required_is_undeterminable():
    # A bare string must not be silently iterated as characters.
    assert evaluate_sufficiency("ab", ["a", "b"]) is Sufficiency.UNDETERMINABLE


def test_bare_string_provided_is_undeterminable():
    assert evaluate_sufficiency(["a"], "abc") is Sufficiency.UNDETERMINABLE


def test_non_iterable_required_is_undeterminable():
    assert evaluate_sufficiency(5, ["a"]) is Sufficiency.UNDETERMINABLE


def test_comparison_failure_fails_closed():
    class _Hostile:
        def __eq__(self, other):
            raise RuntimeError("comparison not permitted")

    # A required identity whose equality comparison raises -> cannot establish
    # containment -> fail-closed (never SUFFICIENT).
    assert evaluate_sufficiency([_Hostile()], ["a"]) is Sufficiency.UNDETERMINABLE


# --------------------------------------------------------------------------- #
#  Determinism + side-effect freedom                                          #
# --------------------------------------------------------------------------- #
def test_evaluation_is_deterministic():
    req, prov = ["a", "b"], ["a", "b", "c"]
    first = evaluate_sufficiency(req, prov)
    for _ in range(5):
        assert evaluate_sufficiency(req, prov) is first


def test_evaluation_does_not_mutate_inputs():
    req = ["a", "b"]
    prov = ["a", "b", "c"]
    req_copy = list(req)
    prov_copy = list(prov)
    evaluate_sufficiency(req, prov)
    assert req == req_copy
    assert prov == prov_copy
