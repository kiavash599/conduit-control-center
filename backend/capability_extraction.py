"""
backend/capability_extraction.py
--------------------------------
Batch B (IDD-0002, Option B): Layer 2 -- Abstract Payload Required Capability
Extraction.

Consumes the frozen three-state decode result (IDD-0002 §12) and returns either
a fail-closed signal or a required-collection suitable as the ``required``
argument to the Batch A sufficiency evaluator. It does NOT decode a concrete
payload (that is Layer 1, deferred), does NOT express provided capabilities, and
does NOT call or modify Batch A.

Three-state mapping (R2 reconciliation, frozen):
  Absent               -> fail-closed
  Malformed            -> fail-closed
  Declared(collection) -> the required-collection (empty permitted)

R2 note: an application-only / legacy payload that intentionally has no
capability requirements is decoded by Layer 1 as ``Declared(∅)`` (which the
evaluator later treats as SUFFICIENT / no-op), NOT as ``Absent``. ``Absent`` is
reserved for a genuinely missing, indeterminate, or invalid declaration and is
therefore fail-closed here (ADR-0002 §18 / IDD-0002 §10 no-op behaviour is
delivered via ``Declared(∅)``, not via ``Absent``).

This module is INTENTIONALLY UNWIRED: no runtime component imports it in Batch B.
It performs no I/O and mutates neither its input nor any shared state. For a
``Declared`` result the underlying collection is forwarded UNCHANGED (not
transformed), so the evaluator's own fail-closed validation still applies
downstream to any residual ill-formedness (defense in depth); this module does
not re-implement that validation.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class DecodeState(enum.Enum):
    """The frozen three-state decode result (IDD-0002 §12)."""

    ABSENT = "absent"        # no declaration present / indeterminate / invalid
    MALFORMED = "malformed"  # present but unreadable / ambiguous / non-canonical
    DECLARED = "declared"    # a well-formed set (possibly empty)


@dataclass(frozen=True)
class DecodeResult:
    """A decode result. ``collection`` is meaningful only for ``DECLARED``.

    Constructed by Layer 1 (deferred) or, in tests, directly. The concrete
    capability encoding is frozen but not represented here; identities are
    opaque values inside ``collection``.
    """

    state: DecodeState
    collection: object | None = None


@dataclass(frozen=True)
class Extraction:
    """The Layer-2 extraction outcome.

    * ``fail_closed is True``  -> refuse; ``required`` is ``None``.
    * ``fail_closed is False`` -> ``required`` is the required-collection to be
      passed as the ``required`` argument of the Batch A evaluator.
    """

    fail_closed: bool
    required: object | None = None


def absent() -> DecodeResult:
    """Construct an ``Absent`` decode result."""
    return DecodeResult(DecodeState.ABSENT)


def malformed() -> DecodeResult:
    """Construct a ``Malformed`` decode result."""
    return DecodeResult(DecodeState.MALFORMED)


def declared(collection) -> DecodeResult:
    """Construct a ``Declared(collection)`` decode result."""
    return DecodeResult(DecodeState.DECLARED, collection)


def extract_required(result) -> Extraction:
    """Map a three-state decode result to a Layer-2 extraction outcome.

    Pure and side-effect-free. See module docstring for the R2 mapping. Any
    input that is not a well-formed ``DecodeResult`` fails closed (I5).
    """
    if not isinstance(result, DecodeResult):
        return Extraction(fail_closed=True, required=None)

    if result.state is DecodeState.DECLARED:
        # Forward the well-formed collection UNCHANGED (see module docstring):
        # no transformation, so downstream evaluator validation still applies.
        return Extraction(fail_closed=False, required=result.collection)

    # ABSENT and MALFORMED both fail closed (R2: Absent is not application-only).
    return Extraction(fail_closed=True, required=None)
