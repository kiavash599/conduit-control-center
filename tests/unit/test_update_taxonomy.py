# SPDX-License-Identifier: MIT
"""ADR-0003 Phase E1 — Outcome Taxonomy Registry tests.

Proves the single canonical registry is correct and behaviour-preserving:
  * the six verification REASON_* keep their EXACT legacy string values;
  * each is registered with a valid stage/category/recoverability + message key;
  * the registry is the closed verification set (no authorization/deploy codes
    added yet); codes and message keys are unique;
  * TAXONOMY_VERSION exists, is 1, and is independent of the manifest format;
  * outcome_for() is fail-safe on unknown codes (never raises).
This phase adds NO behaviour: verify_release output is unchanged (covered by
test_update_verify.py, which must remain green).
"""
from __future__ import annotations

from backend import update_verify as V


# --- reason strings are frozen (behaviour-preserving) ---------------------- #

def test_reason_string_values_unchanged():
    assert V.REASON_VERIFIED == "verified"
    assert V.REASON_TOOLING == "reject_tooling"
    assert V.REASON_STORE == "reject_store"
    assert V.REASON_SIGNATURE == "reject_signature"
    assert V.REASON_MANIFEST == "reject_manifest"
    assert V.REASON_DIGEST == "reject_digest"


# --- registry is the closed verification set ------------------------------- #

def test_registry_is_exactly_the_six_verification_codes():
    expected = {
        V.REASON_VERIFIED, V.REASON_STORE, V.REASON_TOOLING,
        V.REASON_SIGNATURE, V.REASON_MANIFEST, V.REASON_DIGEST,
    }
    assert set(V.outcome_codes()) == expected
    # guard: no authorization / cross-check / deploy / operational codes yet
    for premature in ("reject_product_scope", "reject_version_not_newer",
                      "reject_version_mismatch", "reject_extract",
                      "reject_transient_unit", "rolled_back", "reject_record_write"):
        assert premature not in V.outcome_codes()


def test_every_entry_has_valid_metadata_and_unique_keys():
    codes, keys = [], []
    for code in V.outcome_codes():
        e = V.outcome_for(code)
        assert e.code == code
        assert e.stage in V.OUTCOME_STAGES
        assert e.category in V.OUTCOME_CATEGORIES
        assert e.recoverability in V.OUTCOME_RECOVERABILITY
        assert isinstance(e.message_key, str) and e.message_key
        codes.append(e.code)
        keys.append(e.message_key)
    assert len(codes) == len(set(codes))   # unique codes
    assert len(keys) == len(set(keys))     # unique message keys


def test_category_and_recoverability_mapping_matches_freeze():
    # success
    v = V.outcome_for(V.REASON_VERIFIED)
    assert (v.stage, v.category, v.recoverability) == ("verify", "success", "none")
    # readiness (recoverable)
    for code in (V.REASON_STORE, V.REASON_TOOLING):
        e = V.outcome_for(code)
        assert (e.stage, e.category, e.recoverability) == ("verify", "readiness", "recoverable")
    # trust-integrity (permanent for the artifact)
    for code in (V.REASON_SIGNATURE, V.REASON_MANIFEST, V.REASON_DIGEST):
        e = V.outcome_for(code)
        assert (e.stage, e.category, e.recoverability) == ("verify", "trust-integrity", "permanent-for-artifact")


# --- taxonomy_version ------------------------------------------------------ #

def test_taxonomy_version_present_and_independent_of_manifest_format():
    assert isinstance(V.TAXONOMY_VERSION, int)
    assert V.TAXONOMY_VERSION == 1
    # distinct concept from the manifest schema version: different types,
    # different objects — taxonomy_version must never alias format handling.
    assert isinstance(V.SUPPORTED_MANIFEST_FORMATS, frozenset)
    assert V.TAXONOMY_VERSION is not V.SUPPORTED_MANIFEST_FORMATS


# --- fail-safe lookup ------------------------------------------------------ #

def test_outcome_for_unknown_is_fail_safe():
    e = V.outcome_for("no_such_code")
    assert e is V.UNKNOWN_OUTCOME
    assert e.code == "unknown"
    assert e.category == "operational"
    assert e.recoverability == "informational"
    assert e.message_key == "update.outcome.unknown"
    # unknown sentinel is NOT part of the closed set
    assert "unknown" not in V.outcome_codes()


def test_registry_entries_are_immutable():
    e = V.outcome_for(V.REASON_STORE)
    try:
        e.code = "mutated"  # frozen dataclass -> should raise
        raised = False
    except Exception:
        raised = True
    assert raised
