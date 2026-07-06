# SPDX-License-Identifier: MIT
"""
backend/update_audit.py
-----------------------
ADR-0003 Phase E3 — Audit Model (definition-only, stdlib-only).

The durable, append-only, structurally-redacted forensic record of update
attempts. This module is DEFINITION-ONLY and is NOT wired into the helper or the
API; it emits nothing during real updates and changes no runtime behaviour. Only
tests write audit files (to temp paths). Live helper emission and on-device
provisioning are a separate, Pi-validated wiring step, out of E3 scope.

Architecture (frozen):
  * Audit is APPEND-ONLY structured forensic history (this module) — distinct
    from STATUS (a single overwritten record: backend/update-status.json) and
    from LOGS (unstructured diagnostics: update-worker.log).
  * `audit_schema_version` is PER-RECORD and INDEPENDENT of the status `schema`.
  * Redaction is ALLOWLIST-based: the builder emits ONLY approved fields and
    drops everything else BY CONSTRUCTION. No trust material can appear (IC-11).
  * The append primitive is NON-FATAL: it never raises to the caller and returns
    a structured AppendResult(ok, reason_code) with reason_code from a CLOSED
    set (never raw exception text). An append failure NEVER alters a trust or
    deploy outcome (audit is non-authorising).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

# --- Schema version (per-record; independent of the status doc's "schema") --- #

AUDIT_SCHEMA_VERSION = 1

# --- Record field allowlist (IC-11: only these keys may ever be serialised) -- #
# Injected by the builder (never taken from caller input, so they cannot be
# spoofed): audit_schema_version, timestamp, attempt_id, correlation_id.
# Caller-supplied, allowlisted: everything in _INPUT_FIELDS below. Any other key
# in the caller's input is dropped by construction.

_INPUT_FIELDS = (
    "outcome",           # taxonomy code (e.g. "verified" / "reject_signature")
    "stage",             # origin stage (verify / authorize / deploy / ...)
    "from_version",      # installed version at attempt start
    "target_version",    # claimed version being attempted
    "verified_version",  # authoritative version (only when manifest verified)
    "signing_principal", # allowed-signers principal identity (only when verified)
)

AUDIT_RECORD_FIELDS = frozenset(
    {"audit_schema_version", "timestamp", "attempt_id", "correlation_id"} | set(_INPUT_FIELDS)
)

# --- Runtime contract constants -------------------------------------------- #
# The audit directory sits under the ROOT-OWNED parent /var/log so the service
# CANNOT rename/remove it; the dir is root:conduit-cc 0750 (service traverses +
# reads, cannot write/unlink) and the file root:conduit-cc 0640. It is NOT under
# /var/log/conduit-cc (that dir is service-owned for diagnostic logs) and NOT
# under the StateDirectory /var/lib/conduit-cc (overwrite-only runtime state).
# conduit-cc.service grants a narrow ReadWritePaths= for this dir so the apply
# phase (ProtectSystem=strict) can append; install.sh/update.sh provision it.

AUDIT_DIR = "/var/log/conduit-cc-audit"          # root-owned parent (/var/log); no rename/unlink by service
AUDIT_FILE = f"{AUDIT_DIR}/update-audit.jsonl"    # one JSON object per line
AUDIT_DIR_MODE = 0o750                            # root:conduit-cc, service-read/exec
AUDIT_FILE_MODE = 0o640                           # root writes, service reads
AUDIT_OWNER = "root"
AUDIT_GROUP = "conduit-cc"
AUDIT_MAX_BYTES = 5 * 1024 * 1024                 # retention: rotate whole-record at ~5 MiB
AUDIT_RETAIN_ROTATIONS = 4                        # keep this many rotated files

# --- Append result (structured, closed-set reason codes) -------------------- #

APPEND_OK = "ok"
APPEND_SERIALIZE_ERROR = "serialize_error"
APPEND_WRITE_ERROR = "write_error"
APPEND_REASON_CODES = frozenset({APPEND_OK, APPEND_SERIALIZE_ERROR, APPEND_WRITE_ERROR})


@dataclass(frozen=True)
class AppendResult:
    """Outcome of a non-fatal append. `reason_code` is always a member of
    APPEND_REASON_CODES — never raw exception text."""
    ok: bool
    reason_code: str


# --- Identity helpers ------------------------------------------------------- #

def new_attempt_id() -> str:
    """A unique id for one update attempt."""
    return uuid.uuid4().hex


def new_correlation_id() -> str:
    """A unique id to correlate records/events for one attempt."""
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Record builder (allowlist redaction, IC-11 by construction) ------------ #

def build_audit_record(
    fields: dict,
    *,
    attempt_id: str | None = None,
    correlation_id: str | None = None,
    now: str | None = None,
) -> dict:
    """Build a redacted audit record.

    The output is assembled by iterating the ALLOWLIST — never by copying the
    caller's dict — so any unapproved key (keys, signatures, payload bytes, trust
    store, tokens, …) is dropped by construction. Allowlisted values are included
    only if they are primitive (str / int / None); anything else (bytes, objects)
    is dropped, so raw key/signature bytes cannot leak even under an approved key.
    The schema version, timestamp, and ids are injected here and cannot be
    overridden by `fields`.
    """
    record = {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "timestamp": now or _utcnow_iso(),
        "attempt_id": attempt_id or new_attempt_id(),
        "correlation_id": correlation_id or new_correlation_id(),
    }
    for key in _INPUT_FIELDS:
        if key in fields:
            value = fields[key]
            if value is None or isinstance(value, (str, int)):
                record[key] = value
    return record


# --- Serialisation (JSONL, deterministic) ----------------------------------- #

def serialize_record(record: dict) -> str:
    """One deterministic JSON line (no embedded newlines; JSON escapes them).
    Raises on a non-serialisable record — caught by append_record."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- Append-only write primitive (non-fatal; never raises) ------------------ #

def append_record(path: str, record: dict) -> AppendResult:
    """Append one record to `path` (append-only). Never raises; returns a
    structured AppendResult with a closed-set reason_code. A failure here must
    NOT change any trust/deploy outcome (audit is non-authorising)."""
    try:
        line = serialize_record(record)
    except Exception:
        return AppendResult(False, APPEND_SERIALIZE_ERROR)
    try:
        with open(path, "a", encoding="utf-8") as fh:  # append mode (O_APPEND semantics)
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return AppendResult(True, APPEND_OK)
    except Exception:
        return AppendResult(False, APPEND_WRITE_ERROR)


# --- Tolerant reader (forward/backward compatible) -------------------------- #

def read_records(path: str) -> list[dict]:
    """Read all audit records. Tolerant and never raises: a missing file yields
    []; malformed lines are skipped; records with an unknown/higher
    audit_schema_version or extra fields are returned as-is (forward compat);
    older records with fewer fields are returned as-is (backward compat)."""
    records: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # skip malformed; never raise
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        return []
    return records
