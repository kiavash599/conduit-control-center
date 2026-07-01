"""
backend/capability_decision.py
------------------------------
Batch C (IDD-0002): Capability Decision Composition.

A pure, isolated, UNWIRED composition that produces the Engine-Adoption decision
from an abstract three-state required declaration and an abstract provided
capability collection, by composing the two frozen building blocks:

    Batch B  extract_required(...)        (backend/capability_extraction.py)
        |            fail-closed  -> FAIL_CLOSED
        v
    Batch A  evaluate_sufficiency(...)    (backend/capability_sufficiency.py)
        |            SUFFICIENT     -> NO_OP
        |            INSUFFICIENT   -> ADOPTION_REQUIRED
        |            UNDETERMINABLE -> FAIL_CLOSED      (IDD-0002 §6)
        v
    AdoptionDecision

It imports Batch A and Batch B READ-ONLY and modifies neither. It performs no
I/O and mutates neither its inputs nor any shared state.

Scope (Batch C, frozen): this module composes existing pure blocks only. It does
NOT decode a concrete payload (Layer 1, deferred), does NOT enumerate or express
provided capabilities (deferred), and is NOT wired into any runtime flow. It
introduces no UpdateTransaction or Engine-Adoption executor concept; it yields a
decision value only.
"""
from __future__ import annotations

import enum

from backend.capability_extraction import extract_required
from backend.capability_sufficiency import Sufficiency, evaluate_sufficiency


class AdoptionDecision(enum.Enum):
    """The Engine-Adoption decision (IDD-0002 §6 operational outcomes)."""

    NO_OP = "no-op"                          # Engine Adoption is a no-op
    ADOPTION_REQUIRED = "adoption-required"  # Engine Adoption is required
    FAIL_CLOSED = "fail-closed"              # refuse (I5)


def decide(required_declaration, provided) -> AdoptionDecision:
    """Compose Batch B, then Batch A, then the §6 mapping, into a decision.

    ``required_declaration`` is the frozen three-state decode result (Batch B's
    ``DecodeResult``); ``provided`` is an abstract provided-capability collection.
    Pure and side-effect-free. Fail-closed propagates from extraction, and an
    ``UNDETERMINABLE`` sufficiency maps to ``FAIL_CLOSED`` (I5).
    """
    extraction = extract_required(required_declaration)
    if extraction.fail_closed:
        return AdoptionDecision.FAIL_CLOSED

    sufficiency = evaluate_sufficiency(extraction.required, provided)
    if sufficiency is Sufficiency.SUFFICIENT:
        return AdoptionDecision.NO_OP
    if sufficiency is Sufficiency.INSUFFICIENT:
        return AdoptionDecision.ADOPTION_REQUIRED
    return AdoptionDecision.FAIL_CLOSED
