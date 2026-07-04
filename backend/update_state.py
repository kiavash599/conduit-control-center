# SPDX-License-Identifier: MIT
"""
backend/update_state.py
-----------------------
ADR-0003 Phase E2 — canonical Operational State Model (definition-only).

The single authoritative definition of the update lifecycle state machine
(frozen Design / Architecture Pressure-Test §4): the canonical state set,
per-transition ownership, the legal-transition table, terminal states, the
rollback-origin rule, the retry-entry rule, and a behaviour-preserving mapping
from the current persisted runtime status vocabulary to canonical states.

This module is DEFINITION-ONLY and stdlib-only. It emits nothing, persists
nothing, and is not yet wired into the helper or the API — exactly like the E1
taxonomy registry. It changes no runtime behaviour. Emission of the fine-grained
states (helper writing verifying/authorizing/installing) is intentionally NOT
implemented here.

Runtime State vs Observation State (frozen clarification):
  * A RUNTIME (lifecycle) state is one the engine actually entered; it belongs to
    the closed CANONICAL_STATES set and obeys the transition table. Only producers
    (backend / helper) author these.
  * `unknown` is NOT a canonical state. It is an OBSERVATION-LAYER sentinel — a
    degraded/indeterminate read result produced only by a reader. No transition
    may enter or leave it; it appears only via `observe(...)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# --- Canonical lifecycle states (closed set) -------------------------------- #

IDLE = "idle"
CHECKING = "checking"
AVAILABLE = "available"
VERIFYING = "verifying"
AUTHORIZING = "authorizing"
INSTALLING = "installing"
SUCCESS = "success"
FAILED = "failed"
ROLLED_BACK = "rolled_back"

CANONICAL_STATES = frozenset({
    IDLE, CHECKING, AVAILABLE, VERIFYING, AUTHORIZING, INSTALLING,
    SUCCESS, FAILED, ROLLED_BACK,
})

TERMINAL_STATES = frozenset({SUCCESS, FAILED, ROLLED_BACK})

# --- Transition ownership --------------------------------------------------- #
# Which component authors each legal transition. Backend owns orchestration
# (check / availability / hand-off / terminal acknowledgement); the privileged
# helper owns the decision+execution transitions (verify / authorize / install /
# outcome). Producers emit lifecycle states ONLY.

OWNER_BACKEND = "backend"
OWNER_HELPER = "helper"

# --- Legal-transition table ------------------------------------------------- #
# (from, to) -> owner. Any pair absent here is an IMPOSSIBLE transition. The
# mandatory spine verify -> authorize -> install is unskippable, and no edge
# touches the observation sentinel `unknown`.

TRANSITIONS: dict[tuple[str, str], str] = {
    (IDLE, CHECKING): OWNER_BACKEND,
    (CHECKING, AVAILABLE): OWNER_BACKEND,
    (CHECKING, IDLE): OWNER_BACKEND,          # no update / up-to-date
    (CHECKING, FAILED): OWNER_BACKEND,        # check error (operational)
    (AVAILABLE, VERIFYING): OWNER_BACKEND,    # hand-off: backend triggers, helper enters verifying
    (VERIFYING, AUTHORIZING): OWNER_HELPER,
    (VERIFYING, FAILED): OWNER_HELPER,
    (AUTHORIZING, INSTALLING): OWNER_HELPER,
    (AUTHORIZING, FAILED): OWNER_HELPER,
    (INSTALLING, SUCCESS): OWNER_HELPER,
    (INSTALLING, ROLLED_BACK): OWNER_HELPER,  # rollback originates ONLY here
    (INSTALLING, FAILED): OWNER_HELPER,
    (SUCCESS, IDLE): OWNER_BACKEND,           # terminal acknowledgement / next cycle
    (FAILED, IDLE): OWNER_BACKEND,
    (ROLLED_BACK, IDLE): OWNER_BACKEND,
}

# A new attempt re-enters ONLY via checking (operator-initiated; no auto-retry).
# From a terminal the only legal move is -> idle, then idle -> checking.
RETRY_ENTRY_STATE = CHECKING

# --- Observation layer (separate from runtime state) ------------------------ #
# `unknown` lives here, never in CANONICAL_STATES or TRANSITIONS.

OBSERVATION_INDETERMINATE = "unknown"

# Persisted runtime status string -> canonical lifecycle span. `in_progress` is
# the coarse projection of the verify/authorize/install span (the current engine
# does not yet emit the fine-grained states). `unknown` is intentionally ABSENT,
# so it resolves to an indeterminate observation.
_RUNTIME_TO_SPAN: dict[str, frozenset] = {
    "idle": frozenset({IDLE}),
    "in_progress": frozenset({VERIFYING, AUTHORIZING, INSTALLING}),
    "success": frozenset({SUCCESS}),
    "failed": frozenset({FAILED}),
    "rolled_back": frozenset({ROLLED_BACK}),
}


@dataclass(frozen=True)
class Observation:
    """Result of reading/reconciling the persisted status record.

    `determinate` True  -> a lifecycle state (span) was observed (`states`).
    `determinate` False -> indeterminate/degraded read; `sentinel` == "unknown".
    An Observation is NOT a state and never participates in a transition.
    """
    determinate: bool
    states: frozenset
    sentinel: str


# --- Fail-safe query helpers (never raise on any input) --------------------- #

def is_canonical_state(state) -> bool:
    """True iff `state` is a canonical lifecycle state (never raises)."""
    return isinstance(state, str) and state in CANONICAL_STATES


def is_terminal(state) -> bool:
    """True iff `state` is a terminal lifecycle state (never raises)."""
    return isinstance(state, str) and state in TERMINAL_STATES


def is_legal_transition(frm, to) -> bool:
    """True iff (frm, to) is a legal transition. Impossible pairs -> False.
    Non-string / unknown input -> False (never raises)."""
    if not (isinstance(frm, str) and isinstance(to, str)):
        return False
    return (frm, to) in TRANSITIONS


def owner_of(frm, to) -> Optional[str]:
    """The owner ('backend'/'helper') of a legal transition, else None
    (never raises)."""
    if not (isinstance(frm, str) and isinstance(to, str)):
        return None
    return TRANSITIONS.get((frm, to))


def legal_next_states(frm) -> frozenset:
    """The set of states legally reachable from `frm` (never raises)."""
    if not isinstance(frm, str):
        return frozenset()
    return frozenset(to for (f, to) in TRANSITIONS if f == frm)


def is_rollback_origin(state) -> bool:
    """True iff a rollback (-> rolled_back) may originate from `state`.
    Rollback originates ONLY from the install phase."""
    return state == INSTALLING


def is_retry_entry(frm, to) -> bool:
    """True iff (frm, to) is the canonical retry re-entry (idle -> checking).
    Retry is operator-initiated and re-enters only via checking; there is no
    direct terminal -> active transition (never raises)."""
    return frm == IDLE and to == CHECKING


def observe(runtime_state) -> Observation:
    """Map a persisted runtime status string to an Observation.

    Determinate for the known runtime strings (idle / in_progress / success /
    failed / rolled_back). Any unrecognised value — including the reader's own
    'unknown', a non-string, or a stale/garbage read — yields an INDETERMINATE
    observation (the 'unknown' sentinel), never an exception. `unknown` is an
    observation-layer result, never a canonical state or a transition node.
    """
    if isinstance(runtime_state, str):
        span = _RUNTIME_TO_SPAN.get(runtime_state)
        if span is not None:
            return Observation(True, span, "")
    return Observation(False, frozenset(), OBSERVATION_INDETERMINATE)
