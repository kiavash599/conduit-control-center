"""
backend/capability_sufficiency.py
---------------------------------
Batch A (IDD-0002 §6): the Capability Sufficiency decision.

Pure, deterministic, side-effect-free evaluation of whether an Engine
Capability Class's *provided* capabilities contain a payload's *required*
capabilities (Capability Sufficiency, ADR-0002 invariant I3), by containment.

This module is INTENTIONALLY UNWIRED. In Batch A no runtime component imports
it: it is not part of the update helper, the updater script, the backend
request path, or any privileged flow. It performs no I/O and mutates neither
its inputs nor any shared state.

It fixes NO concrete capability encoding. A capability identity is opaque and
is compared only by exact equality; the representation of an identity is
supplied by the caller and remains deferred (IDD-0002 §12). Membership uses
equality only (no hashing requirement, no approximate/semantic matching).

Outcomes (IDD-0002 §6):
  SUFFICIENT      -> Engine Adoption is a no-op
  INSUFFICIENT    -> Engine Adoption is required
  UNDETERMINABLE  -> fail-closed (containment could not be established, I5)

Containment rule (I3):
  * Empty required collection is contained by any provided collection
    -> SUFFICIENT (no-op).
  * Otherwise SUFFICIENT iff every required identity is present (by exact
    equality) in the provided collection; else INSUFFICIENT.
  * If containment cannot be established for any reason (absent input, a bare
    string/bytes in place of a collection, a non-iterable, or an identity whose
    equality comparison itself fails) -> UNDETERMINABLE. The evaluator never
    returns SUFFICIENT when it cannot establish containment.
"""
from __future__ import annotations

import enum


class Sufficiency(enum.Enum):
    """The Capability Sufficiency decision (IDD-0002 §6)."""

    SUFFICIENT = "sufficient"          # Engine Adoption is a no-op
    INSUFFICIENT = "insufficient"      # Engine Adoption is required
    UNDETERMINABLE = "undeterminable"  # fail-closed (I5)


class _IllFormed(Exception):
    """Internal marker: inputs from which containment cannot be established."""


def evaluate_sufficiency(required, provided) -> Sufficiency:
    """Return the Capability Sufficiency decision for a payload's ``required``
    capabilities against an Engine Capability Class's ``provided`` capabilities.

    Pure and side-effect-free: reads the two collections and returns a decision;
    performs no I/O and mutates nothing. See module docstring for the rule.
    """
    try:
        required_items = _as_collection(required)
        provided_items = _as_collection(provided)
    except _IllFormed:
        return Sufficiency.UNDETERMINABLE

    # Empty requirement is contained by anything -> no-op (I3).
    if len(required_items) == 0:
        return Sufficiency.SUFFICIENT

    # Containment by exact-equality membership only.
    try:
        for identity in required_items:
            if not _is_member(identity, provided_items):
                return Sufficiency.INSUFFICIENT
    except _IllFormed:
        return Sufficiency.UNDETERMINABLE
    return Sufficiency.SUFFICIENT


def _as_collection(collection) -> list:
    """Normalise an input into a finite list of identities, or fail closed.

    Rejects absent input, a bare string/bytes (which is not a collection of
    identities), and non-iterables. Does not require identities to be hashable.
    """
    if collection is None:
        raise _IllFormed("collection is absent")
    if isinstance(collection, (str, bytes)):
        raise _IllFormed("a bare string/bytes is not a collection of identities")
    try:
        return list(collection)
    except TypeError as exc:
        raise _IllFormed(f"collection is not iterable: {exc}") from exc


def _is_member(identity, collection) -> bool:
    """Membership by exact equality only; no hashing, no approximate match.

    If an equality comparison itself raises, containment cannot be established
    and the caller fails closed.
    """
    for candidate in collection:
        try:
            if identity == candidate:
                return True
        except Exception as exc:  # noqa: BLE001 - any comparison failure fails closed
            raise _IllFormed(f"identity comparison failed: {exc}") from exc
    return False
