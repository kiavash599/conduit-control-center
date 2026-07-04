# SPDX-License-Identifier: MIT
"""ADR-0003 Phase E2 — Operational State Model tests.

Proves the canonical state machine is correct and that the observation layer
(`unknown`) is strictly separate from runtime state:
  * closed canonical set + terminal set (frozen §4);
  * legal-transition table with single, correct ownership per edge;
  * the mandatory verify->authorize->install spine; enumerated impossibles;
  * rollback originates only from installing; retry re-enters only via checking;
  * runtime->canonical/observation mapping; `unknown` is an observation sentinel,
    never a canonical state and never a transition endpoint;
  * every helper is fail-safe on unknown/non-string input (never raises).
Definition-only: nothing is emitted, so no runtime behaviour changes.
"""
from __future__ import annotations

from backend import update_state as S


# --- canonical set & terminals --------------------------------------------- #

def test_canonical_state_set_is_the_frozen_nine():
    assert S.CANONICAL_STATES == {
        S.IDLE, S.CHECKING, S.AVAILABLE, S.VERIFYING, S.AUTHORIZING,
        S.INSTALLING, S.SUCCESS, S.FAILED, S.ROLLED_BACK,
    }
    assert S.TERMINAL_STATES == {S.SUCCESS, S.FAILED, S.ROLLED_BACK}
    assert S.TERMINAL_STATES <= S.CANONICAL_STATES


# --- transition table & ownership ------------------------------------------ #

def test_transitions_reference_only_canonical_states_with_valid_owners():
    for (frm, to), owner in S.TRANSITIONS.items():
        assert frm in S.CANONICAL_STATES and to in S.CANONICAL_STATES
        assert owner in (S.OWNER_BACKEND, S.OWNER_HELPER)


def test_transition_ownership_assignments():
    backend = {(S.IDLE, S.CHECKING), (S.CHECKING, S.AVAILABLE), (S.CHECKING, S.IDLE),
               (S.CHECKING, S.FAILED), (S.AVAILABLE, S.VERIFYING),
               (S.SUCCESS, S.IDLE), (S.FAILED, S.IDLE), (S.ROLLED_BACK, S.IDLE)}
    helper = {(S.VERIFYING, S.AUTHORIZING), (S.VERIFYING, S.FAILED),
              (S.AUTHORIZING, S.INSTALLING), (S.AUTHORIZING, S.FAILED),
              (S.INSTALLING, S.SUCCESS), (S.INSTALLING, S.ROLLED_BACK), (S.INSTALLING, S.FAILED)}
    for t in backend:
        assert S.owner_of(*t) == S.OWNER_BACKEND
    for t in helper:
        assert S.owner_of(*t) == S.OWNER_HELPER
    assert backend | helper == set(S.TRANSITIONS)


def test_mandatory_spine_and_impossible_transitions():
    # spine present
    for t in ((S.AVAILABLE, S.VERIFYING), (S.VERIFYING, S.AUTHORIZING),
              (S.AUTHORIZING, S.INSTALLING), (S.INSTALLING, S.SUCCESS)):
        assert S.is_legal_transition(*t)
    # enumerated impossibles (frozen §4) -> rejected
    for t in ((S.AVAILABLE, S.INSTALLING), (S.VERIFYING, S.INSTALLING),
              (S.AUTHORIZING, S.SUCCESS), (S.IDLE, S.SUCCESS),
              (S.VERIFYING, S.ROLLED_BACK), (S.AUTHORIZING, S.ROLLED_BACK),
              (S.SUCCESS, S.VERIFYING), (S.CHECKING, S.INSTALLING),
              (S.AVAILABLE, S.SUCCESS)):
        assert not S.is_legal_transition(*t)


# --- terminals, rollback, retry -------------------------------------------- #

def test_terminals_only_go_to_idle():
    for t in S.TERMINAL_STATES:
        assert S.legal_next_states(t) == {S.IDLE}


def test_rollback_origin_is_install_only():
    assert S.is_rollback_origin(S.INSTALLING) is True
    for s in (S.IDLE, S.CHECKING, S.AVAILABLE, S.VERIFYING, S.AUTHORIZING,
              S.SUCCESS, S.FAILED, S.ROLLED_BACK):
        assert S.is_rollback_origin(s) is False
    # the only edge producing rolled_back originates at installing
    to_rb = [(f, t) for (f, t) in S.TRANSITIONS if t == S.ROLLED_BACK]
    assert to_rb == [(S.INSTALLING, S.ROLLED_BACK)]


def test_retry_reenters_only_via_checking():
    assert S.RETRY_ENTRY_STATE == S.CHECKING
    assert S.is_retry_entry(S.IDLE, S.CHECKING) is True
    # no direct terminal -> active / terminal -> checking
    for t in S.TERMINAL_STATES:
        assert S.is_retry_entry(t, S.CHECKING) is False
        assert not S.is_legal_transition(t, S.CHECKING)
        assert not S.is_legal_transition(t, S.VERIFYING)


# --- observation layer: unknown is NOT a state ----------------------------- #

def test_observe_determinate_runtime_states():
    assert S.observe("idle").determinate and S.observe("idle").states == {S.IDLE}
    ip = S.observe("in_progress")
    assert ip.determinate and ip.states == {S.VERIFYING, S.AUTHORIZING, S.INSTALLING}
    assert S.observe("success").states == {S.SUCCESS}
    assert S.observe("failed").states == {S.FAILED}
    assert S.observe("rolled_back").states == {S.ROLLED_BACK}


def test_unknown_is_observation_sentinel_not_a_state():
    o = S.observe("unknown")
    assert o.determinate is False
    assert o.states == frozenset()
    assert o.sentinel == S.OBSERVATION_INDETERMINATE == "unknown"
    # structurally excluded from the canonical machine
    assert "unknown" not in S.CANONICAL_STATES
    assert S.is_canonical_state("unknown") is False
    endpoints = {s for pair in S.TRANSITIONS for s in pair}
    assert "unknown" not in endpoints
    assert not S.is_legal_transition("unknown", S.IDLE)
    assert not S.is_legal_transition(S.IDLE, "unknown")


# --- fail-safe: never raise on unknown / non-string input ------------------ #

def test_helpers_are_fail_safe():
    for bad in (None, 123, ("a",), ["x"], object(), "garbage"):
        assert S.is_canonical_state(bad) is False
        assert S.is_terminal(bad) is False
        assert S.is_legal_transition(bad, S.IDLE) is False
        assert S.is_legal_transition(S.IDLE, bad) is False
        assert S.owner_of(bad, bad) is None
        assert S.legal_next_states(bad) == frozenset()
        obs = S.observe(bad)
        assert obs.determinate is False and obs.sentinel == "unknown"


def test_runtime_span_map_excludes_unknown():
    assert "unknown" not in S._RUNTIME_TO_SPAN
    assert set(S._RUNTIME_TO_SPAN) == {"idle", "in_progress", "success", "failed", "rolled_back"}
