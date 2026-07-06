# SPDX-License-Identifier: MIT
"""ADR-0003 Phase E3 — Audit Model tests.

Proves the audit library is IC-11-safe and append-only, without any runtime
wiring (only these tests write audit files, to temp paths):
  * allowlist redaction drops every unapproved field BY CONSTRUCTION; no trust
    material (keys/signatures/payload/tokens) can appear in a record;
  * per-record audit_schema_version, independent of the status schema;
  * append-only + ordering + prior-record immutability;
  * the append primitive is non-fatal and returns a CLOSED-set reason_code
    (never raw exception text);
  * the reader is tolerant (missing file, malformed lines, forward/backward
    compatible schema versions);
  * attempt/correlation ids are unique.
"""
from __future__ import annotations

from backend import update_audit as A


# --- schema/version -------------------------------------------------------- #

def test_audit_schema_version_is_per_record_int():
    assert isinstance(A.AUDIT_SCHEMA_VERSION, int)
    assert A.AUDIT_SCHEMA_VERSION == 1
    rec = A.build_audit_record({"outcome": "verified"})
    assert rec["audit_schema_version"] == 1  # present in every record


# --- allowlist redaction (IC-11) ------------------------------------------- #

def test_builder_drops_unapproved_fields_by_construction():
    forbidden = {
        "private_key": "SECRET_PRIVATE_KEY_MATERIAL",
        "signature": "SSHSIG_BYTES_ZZZ",
        "manifest_bytes": "RAW_MANIFEST_PAYLOAD",
        "allowed_signers": "ssh-ed25519 AAAAKEYBLOB",
        "artifact": "RAW_TARBALL_BYTES",
        "token": "API_TOKEN_XYZ",
        "password": "hunter2",
        "attempt_id": "SPOOFED",          # injected-only; must not be overridable
        "audit_schema_version": 999,      # injected-only; must not be overridable
    }
    approved = {
        "outcome": "reject_signature", "stage": "verify",
        "from_version": "0.3.12", "target_version": "0.3.13",
        "verified_version": None, "signing_principal": "conduit-control-center-publisher",
    }
    rec = A.build_audit_record({**forbidden, **approved})
    # only allowlisted keys survive
    assert set(rec) <= A.AUDIT_RECORD_FIELDS
    for bad in forbidden:
        if bad not in ("attempt_id", "audit_schema_version"):
            assert bad not in rec
    # injected fields cannot be spoofed from input
    assert rec["attempt_id"] != "SPOOFED"
    assert rec["audit_schema_version"] == 1
    # no forbidden VALUE appears anywhere in the serialised record
    blob = A.serialize_record(rec)
    for secret in ("SECRET_PRIVATE_KEY_MATERIAL", "SSHSIG_BYTES_ZZZ", "RAW_MANIFEST_PAYLOAD",
                   "AAAAKEYBLOB", "RAW_TARBALL_BYTES", "API_TOKEN_XYZ", "hunter2"):
        assert secret not in blob


def test_builder_drops_non_primitive_values():
    # bytes / objects under an APPROVED key must still be dropped (no key bytes)
    rec = A.build_audit_record({"signing_principal": b"KEYBYTES", "outcome": {"x": 1}})
    assert "signing_principal" not in rec
    assert "outcome" not in rec
    assert "KEYBYTES" not in A.serialize_record(rec)


def test_builder_injects_ids_and_timestamp():
    rec = A.build_audit_record({"outcome": "verified"}, now="2026-07-04T00:00:00Z")
    assert rec["timestamp"] == "2026-07-04T00:00:00Z"
    assert rec["attempt_id"] and rec["correlation_id"]
    assert set(rec) <= A.AUDIT_RECORD_FIELDS


# --- serialisation --------------------------------------------------------- #

def test_serialisation_is_deterministic_single_line():
    rec = A.build_audit_record({"outcome": "verified", "from_version": "0.3.12"},
                               attempt_id="a", correlation_id="c", now="T")
    s1 = A.serialize_record(rec)
    s2 = A.serialize_record(dict(reversed(list(rec.items()))))
    assert s1 == s2                 # key-order independent
    assert "\n" not in s1           # single JSONL line
    import json
    assert json.loads(s1) == rec    # round-trips


# --- append-only + ordering + immutability --------------------------------- #

def test_append_only_preserves_order_and_prior_records(tmp_path):
    path = str(tmp_path / "update-audit.jsonl")
    recs = [A.build_audit_record({"outcome": o}, attempt_id=str(i), correlation_id=str(i), now="T")
            for i, o in enumerate(("verified", "reject_digest", "reject_store"))]
    for r in recs:
        assert A.append_record(path, r).ok is True
    read1 = A.read_records(path)
    assert [r["outcome"] for r in read1] == ["verified", "reject_digest", "reject_store"]
    # append a fourth; the first three must be byte-for-byte unchanged
    assert A.append_record(path, A.build_audit_record({"outcome": "verified"},
                                                      attempt_id="3", correlation_id="3", now="T")).ok
    read2 = A.read_records(path)
    assert read2[:3] == read1
    assert len(read2) == 4


# --- non-fatal append + closed-set reason codes ---------------------------- #

def test_append_write_error_is_non_fatal_closed_code(tmp_path):
    bad_path = str(tmp_path / "no_such_dir" / "audit.jsonl")  # parent missing -> OSError
    res = A.append_record(bad_path, A.build_audit_record({"outcome": "verified"}))
    assert res.ok is False
    assert res.reason_code == A.APPEND_WRITE_ERROR
    assert res.reason_code in A.APPEND_REASON_CODES


def test_append_serialize_error_is_non_fatal_closed_code(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    res = A.append_record(path, {"bad": {1, 2, 3}})  # a set is not JSON-serialisable
    assert res.ok is False
    assert res.reason_code == A.APPEND_SERIALIZE_ERROR
    assert res.reason_code in A.APPEND_REASON_CODES


def test_all_reason_codes_are_from_the_closed_set(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    ok = A.append_record(path, A.build_audit_record({"outcome": "verified"}))
    assert ok.ok is True and ok.reason_code == A.APPEND_OK
    assert A.APPEND_REASON_CODES == {"ok", "serialize_error", "write_error"}


# --- tolerant reader ------------------------------------------------------- #

def test_reader_is_tolerant(tmp_path):
    assert A.read_records(str(tmp_path / "missing.jsonl")) == []
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"audit_schema_version":1,"outcome":"verified"}\n'
        "not-json-garbage\n"
        "\n"
        '{"audit_schema_version":99,"outcome":"future","brand_new_field":true}\n'  # forward compat
        '{"outcome":"old_only"}\n'                                                  # backward compat
    )
    recs = A.read_records(str(path))
    assert [r["outcome"] for r in recs] == ["verified", "future", "old_only"]
    assert recs[1]["audit_schema_version"] == 99  # higher version returned as-is


# --- ids ------------------------------------------------------------------- #

def test_ids_are_unique():
    assert A.new_attempt_id() != A.new_attempt_id()
    assert A.new_correlation_id() != A.new_correlation_id()


# --- runtime contract constants exist (defined, not provisioned) ----------- #

def test_runtime_contract_constants_defined():
    # Option 2-refined: audit lives under the ROOT-OWNED parent /var/log (so the
    # service cannot rename the dir), NOT under service-owned /var/log/conduit-cc
    # and NOT under the StateDirectory /var/lib/conduit-cc.
    assert A.AUDIT_DIR == "/var/log/conduit-cc-audit"
    assert A.AUDIT_FILE == "/var/log/conduit-cc-audit/update-audit.jsonl"
    assert not A.AUDIT_DIR.startswith("/var/lib/")           # not runtime state
    assert not A.AUDIT_DIR.startswith("/var/log/conduit-cc/")  # not the service-owned log dir
    assert A.AUDIT_FILE.endswith(".jsonl")
    assert A.AUDIT_OWNER == "root" and A.AUDIT_GROUP == "conduit-cc"
    assert A.AUDIT_DIR_MODE == 0o750 and A.AUDIT_FILE_MODE == 0o640
    assert isinstance(A.AUDIT_MAX_BYTES, int) and A.AUDIT_MAX_BYTES > 0
