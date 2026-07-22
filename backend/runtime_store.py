"""backend/runtime_store.py -- immutable versioned runtime store + selector (Epic 2).

Layout (all root-owned, non-service-writable):

    <app-root>/.venvs/<runtime-id>/            immutable runtime (a real venv)
    <app-root>/.venvs/<runtime-id>.manifest.json   runtime manifest (0600)
    <app-root>/venv  ->  .venvs/<runtime-id>   THE selector (single-hop symlink)

Selector gate (B1) -- an activation target must pass ALL of:
  * the selector is a symlink whose LINK TEXT is exactly `.venvs/<runtime-id>`
    (relative, two fixed components, no `/` prefix, no `..`);
  * `.venvs` is a real directory; the target is a REAL directory (lstat;
    a second symlink hop is refused -- termination at one hop);
  * canonical containment: realpath(target) is directly under realpath(.venvs);
  * owner-uid ownership and no group/other write on the target and target/bin;
  * `pyvenv.cfg` present; `bin/python3` present and executable;
  * a VALIDATED manifest bound to the runtime id;
  * unvalidated/partial runtimes can never become active.

Legacy conversion (B3) is write-ahead recorded in the PRIVATE state dir and is
idempotent + resumable: every interruption boundary leaves a classifiable state
(record present -> diagnose/resume; record absent -> either unconverted or
fully converted). Deletion anywhere (GC, B4) is manifest-authorized -- never
name/prefix-authorized; foreign, unrecorded, active or previous runtimes
survive.

Deterministic IDs: the legacy conversion uses the fixed id ``legacy-0`` (there
is exactly one legacy runtime); future built runtimes use ids derived from
their dependency-input digest (Epic 3+). An existing conflicting id fails
closed unless manifest-identical.

stdlib-only (consumed by the root CLI ``ccc-runtime`` and by tests).
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile

# Committed runtime smoke probes (finding 6): the application entry stack plus
# every ABI-sensitive/optional server backend shipped by the two platform
# locks. Importing ``uvicorn`` alone does *not* load uvloop/httptools/websockets,
# and importing a pure wrapper does not prove its native extension can load, so
# those modules are named explicitly. PyYAML is deliberately different: the
# release contract accepts its pure-Python implementation, so its probe checks
# public safe-load/safe-dump behavior and never requires the optional
# ``yaml._yaml`` accelerator. Candidate finalization and collision revalidation
# consume this exact tuple through ``_run_import_smoke``.
SMOKE_PROBES = (
    ("fastapi", "import fastapi"),
    ("uvicorn", "import uvicorn"),
    ("pydantic", "import pydantic"),
    ("pydantic_core", "import pydantic_core"),
    ("aiosqlite", "import aiosqlite"),
    ("cryptography.hazmat.bindings._rust",
     "import cryptography.hazmat.bindings._rust"),
    ("bcrypt._bcrypt", "import bcrypt._bcrypt"),
    ("_cffi_backend", "import _cffi_backend"),
    ("httptools", "import httptools"),
    ("markupsafe._speedups", "import markupsafe._speedups"),
    ("psutil", "import psutil"),
    ("yaml",
     "import yaml; data = yaml.safe_load('enabled: true\\n'); "
     "assert data == {'enabled': True}; "
     "assert yaml.safe_load(yaml.safe_dump(data)) == data"),
    ("uvloop", "import uvloop"),
    ("watchfiles", "import watchfiles"),
    ("websockets", "import websockets"),
)
SMOKE_IMPORTS = tuple(label for label, _code in SMOKE_PROBES)

STORE_NAME = ".venvs"
SELECTOR_NAME = "venv"
LEGACY_ID = "legacy-0"
TRANSITION_RECORD = "runtime-transition.json"   # inside the PRIVATE state dir
BOOTSTRAP_RESERVE_DIR = "bootstrap-reserves"
SUPPORTED_BOOTSTRAP_BASELINES = frozenset({"0.3.14", "0.3.15", "0.3.18"})
# Runtime IDs: either the explicit legacy id, or the FULL lowercase 64-hex
# SHA-256 of the canonical candidate inputs (no truncation -- the complete
# identity is preserved; `kind` lives in the manifest, never in the id).
_ID_RE = re.compile(r"^(legacy-0|[0-9a-f]{64})$")
_ATTEMPT_RE = re.compile(r"^[0-9a-f]{12,32}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
OWNER_UID = 0
BACKUP_ROOT = "/var/backups/conduit-cc"

UPDATE_PHASES = frozenset({
    "begun", "ownership_intent", "ownership_complete", "backup_intent",
    "backup_complete",
    "candidate_intent", "candidate_ready",
    "downtime_intent", "downtime_started", "conversion_intent",
    "conversion_complete", "trust_intent", "trust_complete",
    "activation_intent", "activated", "deploy_intent", "deployed",
    "service_start_intent", "service_started", "health_verified",
    "rollback_started", "runtime_restored", "files_restored",
    "service_restore_intent", "rolled_back", "diagnostic_failure", "success",
})
UPDATE_TERMINAL_PHASES = frozenset({"rolled_back", "diagnostic_failure", "success"})
UPDATE_FACT_KEYS = frozenset({
    "backup_dir", "previous_version", "candidate_id", "previous_runtime",
    "converted_by_attempt", "activation_done", "trust_done", "downtime_started",
})
_NORMAL_UPDATE_SEQUENCE = (
    "begun", "ownership_intent", "ownership_complete", "backup_intent",
    "backup_complete",
    "candidate_intent", "candidate_ready",
    "downtime_intent", "downtime_started", "conversion_intent",
    "conversion_complete", "trust_intent", "trust_complete",
    "activation_intent", "activated", "deploy_intent", "deployed",
    "service_start_intent", "service_started", "health_verified", "success",
)
UPDATE_TRANSITIONS = {
    current: frozenset({following})
    for current, following in zip(_NORMAL_UPDATE_SEQUENCE, _NORMAL_UPDATE_SEQUENCE[1:])
}
UPDATE_TRANSITIONS.update({
    "rollback_started": frozenset({"runtime_restored", "diagnostic_failure"}),
    "runtime_restored": frozenset({"files_restored", "diagnostic_failure"}),
    "files_restored": frozenset({"service_restore_intent", "diagnostic_failure"}),
    "service_restore_intent": frozenset({"rolled_back", "diagnostic_failure"}),
})
UPDATE_FACT_PHASE = {
    "backup_dir": "backup_intent",
    "previous_version": "backup_complete",
    "candidate_id": "candidate_intent",
    "converted_by_attempt": "conversion_intent",
    "trust_done": "trust_complete",
    "previous_runtime": "activation_intent",
    "activation_done": "activated",
    "downtime_started": "downtime_started",
}


class RuntimeStoreError(Exception):
    """Fail-closed runtime-store violation (specific, testable)."""


def _allowed_update_successors(phase: str) -> set:
    if phase in UPDATE_TERMINAL_PHASES:
        return set()
    allowed = set(UPDATE_TRANSITIONS.get(phase, ()))
    allowed.update(("diagnostic_failure", "rollback_started"))
    return allowed


def _validate_update_facts(facts: dict) -> None:
    if not isinstance(facts, dict) or not set(facts) <= UPDATE_FACT_KEYS:
        raise RuntimeStoreError("update transaction has invalid facts")
    if "candidate_id" in facts and not _ID_RE.fullmatch(str(facts["candidate_id"])):
        raise RuntimeStoreError("candidate_id fact is invalid")
    if "previous_runtime" in facts and not _ID_RE.fullmatch(str(facts["previous_runtime"])):
        raise RuntimeStoreError("previous_runtime fact is invalid")
    for key in ("converted_by_attempt", "activation_done", "trust_done", "downtime_started"):
        if key in facts and not isinstance(facts[key], bool):
            raise RuntimeStoreError(f"{key} fact must be boolean")
    if "backup_dir" in facts:
        value = facts["backup_dir"]
        if not isinstance(value, str) or not value.startswith("/") \
                or any(ord(char) < 32 for char in value):
            raise RuntimeStoreError("backup_dir fact must be an absolute control-free path")
    if "previous_version" in facts:
        value = facts["previous_version"]
        if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
            raise RuntimeStoreError("previous_version fact is invalid")


def _validate_attempt_backup_fact(facts: dict, attempt_id: str) -> None:
    backup_dir = facts.get("backup_dir")
    if backup_dir is None:
        return
    expected = re.fullmatch(
        rf"{re.escape(BACKUP_ROOT)}/[0-9]{{8}}-[0-9]{{6}}-{re.escape(attempt_id)}",
        backup_dir,
    )
    if expected is None:
        raise RuntimeStoreError("backup_dir fact is not bound to its update attempt")


# --------------------------------------------------------------------------- #
#  small atomic primitives                                                     #
# --------------------------------------------------------------------------- #

def _fsync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: str) -> None:
    """Durably flush a validated tree without following symlinks.

    Candidate publication must not make a manifest/selector durable while the
    dependency bytes it authenticates remain only in volatile writeback cache.
    Regular files are fsynced first, then directories bottom-up.
    """
    directories = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directories.append(dirpath)
        for name in filenames:
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode):
                continue
            fd = os.open(path, os.O_RDONLY | nofollow | cloexec)
            try:
                fst = os.fstat(fd)
                if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
                    raise RuntimeStoreError(
                        f"candidate file changed during durability flush: {path!r}")
                os.fsync(fd)
            finally:
                os.close(fd)
        # os.walk lists directory symlinks in dirnames but does not traverse
        # them with followlinks=False. Only real directories enter the fsync set.
        dirnames[:] = [
            name for name in dirnames
            if stat.S_ISDIR(os.lstat(os.path.join(dirpath, name)).st_mode)
            and not stat.S_ISLNK(os.lstat(os.path.join(dirpath, name)).st_mode)
        ]
    for path in reversed(directories):
        _fsync_dir(path)


def _write_json_atomic(path: str, doc: dict, mode: int = 0o600) -> None:
    parent = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".rt-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(doc, sort_keys=True) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), mode)
        os.replace(tmp, path)
        _fsync_dir(parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: str) -> "dict | None":
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RuntimeStoreError(f"unreadable record {path!r}: {exc}") from exc
    if not isinstance(doc, dict):
        raise RuntimeStoreError(f"malformed record {path!r}")
    return doc


# --------------------------------------------------------------------------- #
#  per-attempt update transaction (write-ahead coordination record)            #
# --------------------------------------------------------------------------- #

def _assert_private_dir(private_dir: str, owner_uid: int = OWNER_UID) -> None:
    st = os.lstat(private_dir)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeStoreError(f"private state {private_dir!r} is not a real directory")
    if st.st_uid != owner_uid or st.st_mode & 0o077:
        raise RuntimeStoreError(
            f"private state {private_dir!r} must be owner-owned mode 0700")


def _transactions_dir(private_dir: str, owner_uid: int, *, create: bool) -> str:
    _assert_private_dir(private_dir, owner_uid)
    path = os.path.join(private_dir, "transactions")
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        if not create:
            raise RuntimeStoreError("update transaction directory is absent")
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
        _fsync_dir(private_dir)
        st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeStoreError(f"transaction state {path!r} is not a real directory")
    if st.st_uid != owner_uid or st.st_mode & 0o077:
        raise RuntimeStoreError(f"transaction state {path!r} must be owner-owned mode 0700")
    return path


def update_attempt_path(private_dir: str, attempt_id: str) -> str:
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise RuntimeStoreError(f"invalid update attempt id: {attempt_id!r}")
    return os.path.join(private_dir, "transactions", f"{attempt_id}.json")


def _validate_update_attempt(doc: dict, attempt_id: str) -> dict:
    required = {
        "schema", "attempt_id", "target_version", "source_commit", "source_tag",
        "phase", "history", "facts",
    }
    if set(doc) != required or doc.get("schema") != 1 or doc.get("attempt_id") != attempt_id:
        raise RuntimeStoreError(f"malformed update transaction for {attempt_id!r}")
    version = doc.get("target_version")
    commit = doc.get("source_commit")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise RuntimeStoreError("update transaction has invalid target_version")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeStoreError("update transaction has invalid source_commit")
    if doc.get("source_tag") != f"v{version}":
        raise RuntimeStoreError("update transaction source_tag/version mismatch")
    phase = doc.get("phase")
    history = doc.get("history")
    facts = doc.get("facts")
    if phase not in UPDATE_PHASES or not isinstance(history, list) or not history \
       or history[0] != "begun" or history[-1] != phase \
       or any(p not in UPDATE_PHASES for p in history):
        raise RuntimeStoreError("update transaction has invalid phase history")
    for current, following in zip(history, history[1:]):
        if following not in _allowed_update_successors(current):
            raise RuntimeStoreError("update transaction has impossible phase history")
    _validate_update_facts(facts)
    _validate_attempt_backup_fact(facts, attempt_id)
    for key, fact_phase in UPDATE_FACT_PHASE.items():
        if (key in facts) != (fact_phase in history):
            raise RuntimeStoreError("update transaction facts/history mismatch")
    return doc


def read_update_attempt(private_dir: str, attempt_id: str,
                        owner_uid: int = OWNER_UID) -> dict:
    txdir = _transactions_dir(private_dir, owner_uid, create=False)
    path = update_attempt_path(private_dir, attempt_id)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise RuntimeStoreError(f"update transaction {path!r} is not a regular file")
    if st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o600:
        raise RuntimeStoreError(f"update transaction {path!r} must be owner-owned mode 0600")
    doc = _read_json(path)
    if doc is None:  # pragma: no cover - lstat/open race maps to fail closed
        raise RuntimeStoreError(f"update transaction disappeared: {path!r}")
    # Keep the validated parent live in this scope; documents why it is checked.
    assert txdir == os.path.dirname(path)
    return _validate_update_attempt(doc, attempt_id)


def begin_update_attempt(private_dir: str, attempt_id: str, *, target_version: str,
                         source_commit: str, source_tag: str,
                         owner_uid: int = OWNER_UID) -> dict:
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise RuntimeStoreError(f"invalid update attempt id: {attempt_id!r}")
    if not _VERSION_RE.fullmatch(target_version):
        raise RuntimeStoreError(f"invalid target version: {target_version!r}")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise RuntimeStoreError("source commit must be 40 lowercase hex")
    if source_tag != f"v{target_version}":
        raise RuntimeStoreError(f"source tag must equal v{target_version}")
    _transactions_dir(private_dir, owner_uid, create=True)
    path = update_attempt_path(private_dir, attempt_id)
    doc = {
        "schema": 1,
        "attempt_id": attempt_id,
        "target_version": target_version,
        "source_commit": source_commit,
        "source_tag": source_tag,
        "phase": "begun",
        "history": ["begun"],
        "facts": {},
    }
    if os.path.lexists(path):
        existing = read_update_attempt(private_dir, attempt_id, owner_uid)
        if existing != doc:
            raise RuntimeStoreError("attempt id collision with different transaction state")
        return existing
    _write_json_atomic(path, doc, 0o600)
    return doc


def mark_update_attempt(private_dir: str, attempt_id: str, phase: str, *,
                        facts: "dict | None" = None,
                        owner_uid: int = OWNER_UID) -> dict:
    if phase not in UPDATE_PHASES:
        raise RuntimeStoreError(f"unsupported update phase: {phase!r}")
    doc = read_update_attempt(private_dir, attempt_id, owner_uid)
    updates = dict(facts or {})
    if not set(updates) <= UPDATE_FACT_KEYS:
        raise RuntimeStoreError("unsupported update transaction fact")
    if any(UPDATE_FACT_PHASE[key] != phase for key in updates):
        raise RuntimeStoreError("update fact is not valid at this transaction phase")
    _validate_update_facts(updates)
    new_facts = dict(doc["facts"])
    for key, value in updates.items():
        if key in new_facts and new_facts[key] != value:
            raise RuntimeStoreError(f"immutable update fact changed: {key}")
        new_facts[key] = value
    _validate_attempt_backup_fact(new_facts, attempt_id)

    current = doc["phase"]
    if phase == current:
        if new_facts != doc["facts"]:
            raise RuntimeStoreError("same-phase retry cannot add transaction facts")
        return doc
    if current in UPDATE_TERMINAL_PHASES:
        raise RuntimeStoreError("terminal update transaction is immutable")
    # A runtime failure after downtime may enter rollback from any unfinished
    # phase; diagnostic_failure is the terminal classification for any failed
    # attempt that cannot or need not roll back shared state.
    allowed = _allowed_update_successors(current)
    if phase not in allowed:
        raise RuntimeStoreError(f"invalid update phase transition: {current} -> {phase}")
    required = {key for key, fact_phase in UPDATE_FACT_PHASE.items()
                if fact_phase == phase}
    if not required <= set(updates):
        raise RuntimeStoreError(f"phase {phase!r} is missing required transaction facts")
    doc["history"].append(phase)
    doc["phase"] = phase
    doc["facts"] = new_facts
    _write_json_atomic(update_attempt_path(private_dir, attempt_id), doc, 0o600)
    return doc


def incomplete_update_attempts(private_dir: str,
                               owner_uid: int = OWNER_UID) -> list:
    try:
        txdir = _transactions_dir(private_dir, owner_uid, create=False)
    except RuntimeStoreError as exc:
        if "is absent" in str(exc):
            return []
        raise
    result = []
    for name in sorted(os.listdir(txdir)):
        if not re.fullmatch(r"[0-9a-f]{12,32}\.json", name):
            raise RuntimeStoreError(f"foreign object in transaction directory: {name!r}")
        attempt_id = name[:-5]
        doc = read_update_attempt(private_dir, attempt_id, owner_uid)
        if doc["phase"] not in UPDATE_TERMINAL_PHASES:
            result.append(doc)
    return result


def completed_update_backups(private_dir: str,
                             owner_uid: int = OWNER_UID) -> list:
    """Return the exact terminal, record-authorized backup retention set.

    A directory name alone never grants deletion authority. Only a strictly
    validated transaction that reached ``backup_complete`` and then a terminal
    phase can nominate its immutable, attempt-bound backup path.
    """
    try:
        txdir = _transactions_dir(private_dir, owner_uid, create=False)
    except RuntimeStoreError as exc:
        if "is absent" in str(exc):
            return []
        raise
    result = []
    for name in sorted(os.listdir(txdir)):
        if not re.fullmatch(r"[0-9a-f]{12,32}\.json", name):
            raise RuntimeStoreError(f"foreign object in transaction directory: {name!r}")
        attempt_id = name[:-5]
        doc = read_update_attempt(private_dir, attempt_id, owner_uid)
        if doc["phase"] in UPDATE_TERMINAL_PHASES \
                and "backup_complete" in doc["history"]:
            result.append({
                "attempt_id": attempt_id,
                "backup_dir": doc["facts"]["backup_dir"],
            })
    return sorted(result, key=lambda item: (item["backup_dir"], item["attempt_id"]))


# --------------------------------------------------------------------------- #
#  first-transition rollback reserve acceptance                               #
# --------------------------------------------------------------------------- #

def _bootstrap_reserve_dir(private_dir: str, owner_uid: int) -> str:
    _assert_private_dir(private_dir, owner_uid)
    path = os.path.join(private_dir, BOOTSTRAP_RESERVE_DIR)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeStoreError("bootstrap reserve record root is not a real directory")
    if st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeStoreError("bootstrap reserve record root must be owner-owned mode 0700")
    return path


def _bootstrap_reserve_path(private_dir: str, attempt_id: str) -> str:
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise RuntimeStoreError(f"invalid bootstrap attempt id: {attempt_id!r}")
    return os.path.join(private_dir, BOOTSTRAP_RESERVE_DIR, f"{attempt_id}.json")


def read_bootstrap_reserve(private_dir: str, attempt_id: str,
                           owner_uid: int = OWNER_UID) -> dict:
    """Read and strictly validate the bootstrap ceremony's write-ahead record."""
    records = _bootstrap_reserve_dir(private_dir, owner_uid)
    path = _bootstrap_reserve_path(private_dir, attempt_id)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise RuntimeStoreError("bootstrap reserve record is not a regular file")
    if st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o600:
        raise RuntimeStoreError("bootstrap reserve record must be owner-owned mode 0600")
    doc = _read_json(path)
    required = {
        "schema", "attempt_id", "work", "source_commit", "source_tag",
        "target_version", "expected_installed_version", "state", "history",
    }
    if doc is None or set(doc) != required or doc.get("schema") != 2 \
            or doc.get("attempt_id") != attempt_id:
        raise RuntimeStoreError("malformed bootstrap reserve record")
    version = doc.get("target_version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version) \
            or doc.get("source_tag") != f"v{version}" \
            or not re.fullmatch(r"[0-9a-f]{40}", str(doc.get("source_commit"))):
        raise RuntimeStoreError("bootstrap reserve source identity is invalid")
    if doc.get("expected_installed_version") not in SUPPORTED_BOOTSTRAP_BASELINES:
        raise RuntimeStoreError("bootstrap reserve legacy baseline is invalid")
    expected_work = os.path.join(os.path.realpath(private_dir), f"bootstrap-{attempt_id}")
    if doc.get("work") != expected_work:
        raise RuntimeStoreError("bootstrap reserve work path is not the exact attempt path")
    allowed_histories = (
        ["staged"],
        ["staged", "ready"],
        ["staged", "ready", "acceptance_intent"],
        ["staged", "ready", "acceptance_intent", "accepted"],
        ["staged", "failure_discard_intent"],
        ["staged", "failure_discard_intent", "failure_discarded"],
    )
    history = doc.get("history")
    if history not in allowed_histories or doc.get("state") != history[-1]:
        raise RuntimeStoreError("bootstrap reserve has impossible state history")
    assert records == os.path.dirname(path)
    return doc


def mark_bootstrap_reserve_ready(private_dir: str, attempt_id: str,
                                 owner_uid: int = OWNER_UID) -> dict:
    """Bind a preserved bootstrap staging tree to a successful update attempt."""
    doc = read_bootstrap_reserve(private_dir, attempt_id, owner_uid)
    if doc["state"] == "ready":
        return doc
    if doc["state"] != "staged":
        raise RuntimeStoreError("bootstrap reserve cannot be marked ready from this state")
    tx = read_update_attempt(private_dir, attempt_id, owner_uid)
    if tx["phase"] != "success":
        raise RuntimeStoreError("bootstrap reserve requires a successful update transaction")
    if tx["source_commit"] != doc["source_commit"] \
            or tx["source_tag"] != doc["source_tag"] \
            or tx["target_version"] != doc["target_version"] \
            or tx["facts"].get("previous_version") \
            != doc["expected_installed_version"]:
        raise RuntimeStoreError("bootstrap reserve/update identity mismatch")
    work = doc["work"]
    st = os.lstat(work)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) \
            or st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeStoreError("bootstrap reserve work tree must be owner-owned mode 0700")
    doc["state"] = "ready"
    doc["history"].append("ready")
    _write_json_atomic(_bootstrap_reserve_path(private_dir, attempt_id), doc, 0o600)
    return doc


def accept_bootstrap_reserve(private_dir: str, attempt_id: str, *,
                             source_commit: str, source_tag: str,
                             owner_uid: int = OWNER_UID) -> dict:
    """Accept and delete exactly the record-authorized rollback reserve.

    The acceptance intent is durable before deletion. A crash after deletion is
    resumable: the next invocation observes ``acceptance_intent`` plus an absent
    exact work path and commits ``accepted``. No name/prefix-based deletion is
    permitted.
    """
    import shutil as _sh
    doc = read_bootstrap_reserve(private_dir, attempt_id, owner_uid)
    if doc["source_commit"] != source_commit or doc["source_tag"] != source_tag:
        raise RuntimeStoreError("bootstrap acceptance identity mismatch")
    if doc["state"] == "accepted":
        return doc
    if doc["state"] == "ready":
        doc["state"] = "acceptance_intent"
        doc["history"].append("acceptance_intent")
        _write_json_atomic(_bootstrap_reserve_path(private_dir, attempt_id), doc, 0o600)
    elif doc["state"] != "acceptance_intent":
        raise RuntimeStoreError("bootstrap reserve is not ready for acceptance")

    work = doc["work"]
    try:
        st = os.lstat(work)
    except FileNotFoundError:
        st = None
    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) \
                or st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o700:
            raise RuntimeStoreError("bootstrap reserve work tree changed during acceptance")
        current = os.lstat(work)
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
            raise RuntimeStoreError("bootstrap reserve work tree was replaced")
        _sh.rmtree(work)
        _fsync_dir(private_dir)
    doc["state"] = "accepted"
    doc["history"].append("accepted")
    _write_json_atomic(_bootstrap_reserve_path(private_dir, attempt_id), doc, 0o600)
    return doc


def discard_failed_bootstrap_reserve(private_dir: str, attempt_id: str, *,
                                      source_commit: str, source_tag: str,
                                      owner_uid: int = OWNER_UID) -> dict:
    """Delete one exact reserve after a bound PRE-DOWNTIME failed bootstrap.

    The failed update transaction is immutable evidence and remains in place.
    Only a ``diagnostic_failure`` transaction that never crossed
    ``downtime_started`` authorizes this operation. Intent is durable before
    deletion, making a crash after deletion safely resumable. Successful,
    rolled-back, post-downtime, foreign, or substituted reserves are refused.
    """
    import shutil as _sh

    doc = read_bootstrap_reserve(private_dir, attempt_id, owner_uid)
    if doc["source_commit"] != source_commit or doc["source_tag"] != source_tag:
        raise RuntimeStoreError("failed bootstrap discard identity mismatch")

    tx = read_update_attempt(private_dir, attempt_id, owner_uid)
    if tx["phase"] != "diagnostic_failure" \
            or "downtime_started" in tx["history"] \
            or tx["facts"].get("downtime_started") is not None:
        raise RuntimeStoreError(
            "bootstrap reserve discard requires a pre-downtime diagnostic failure")
    if tx["source_commit"] != doc["source_commit"] \
            or tx["source_tag"] != doc["source_tag"] \
            or tx["target_version"] != doc["target_version"] \
            or tx["facts"].get("previous_version") \
            != doc["expected_installed_version"]:
        raise RuntimeStoreError("failed bootstrap reserve/update identity mismatch")

    if doc["state"] == "failure_discarded":
        return doc
    if doc["state"] == "staged":
        doc["state"] = "failure_discard_intent"
        doc["history"].append("failure_discard_intent")
        _write_json_atomic(_bootstrap_reserve_path(private_dir, attempt_id), doc, 0o600)
    elif doc["state"] != "failure_discard_intent":
        raise RuntimeStoreError("bootstrap reserve is not a failed staged reserve")

    work = doc["work"]
    try:
        st = os.lstat(work)
    except FileNotFoundError:
        st = None
    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) \
                or st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o700:
            raise RuntimeStoreError(
                "failed bootstrap reserve work tree changed during discard")
        current = os.lstat(work)
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
            raise RuntimeStoreError("failed bootstrap reserve work tree was replaced")
        _sh.rmtree(work)
        _fsync_dir(private_dir)
    doc["state"] = "failure_discarded"
    doc["history"].append("failure_discarded")
    _write_json_atomic(_bootstrap_reserve_path(private_dir, attempt_id), doc, 0o600)
    return doc


def _publish_symlink(app_dir: str, link_text: str) -> None:
    """Atomically (re)point the selector: temp symlink + rename over."""
    selector = os.path.join(app_dir, SELECTOR_NAME)
    tmp = os.path.join(app_dir, f".{SELECTOR_NAME}.tmp.{os.getpid()}")
    try:
        os.symlink(link_text, tmp)
        os.rename(tmp, selector)           # atomic over an existing symlink
        _fsync_dir(app_dir)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
#  safe store opening (finding 2: one primitive, used everywhere)              #
# --------------------------------------------------------------------------- #

def assert_app_dir(app_dir: str, owner_uid: int = OWNER_UID) -> None:
    """APP_DIR must be a REAL, owner-owned, non-group/other-writable directory
    (lstat; a symlinked or service-writable app root is refused). This is the
    root of every store/selector operation and must never be followed through
    a symlink or trusted while service-writable."""
    st = os.lstat(app_dir)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeStoreError(f"app dir {app_dir!r} is not a real directory")
    if st.st_uid != owner_uid:
        raise RuntimeStoreError(
            f"app dir {app_dir!r} owner uid {st.st_uid}, expected {owner_uid} "
            f"(run the first-transition ownership change before any store op)")
    if st.st_mode & 0o022:
        raise RuntimeStoreError(f"app dir {app_dir!r} is group/other writable")


def open_store(app_dir: str, owner_uid: int = OWNER_UID, *, create: bool = False) -> str:
    """THE store-directory primitive. Validates APP_DIR, then lstat-validates
    `.venvs`: a symlink or foreign object is refused (never followed); when
    absent and create=True it is made root-owned 0755; an existing store must
    be a real, owner-owned, non-g/o-writable directory. Returns the store path.
    Used by stage/finalize/convert/validate/gc so no caller can create or
    traverse the store through a substituted parent."""
    assert_app_dir(app_dir, owner_uid)
    store = os.path.join(app_dir, STORE_NAME)
    try:
        st = os.lstat(store)
    except FileNotFoundError:
        if not create:
            raise RuntimeStoreError(f"store {store!r} does not exist")
        os.mkdir(store, 0o755)
        if os.geteuid() == 0:
            os.chown(store, 0, 0)
        os.chmod(store, 0o755)
        _fsync_dir(app_dir)
        return store
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeStoreError(f"store {store!r} is not a real directory (refusing)")
    if st.st_uid != owner_uid:
        raise RuntimeStoreError(f"store {store!r} owner uid {st.st_uid}, expected {owner_uid}")
    if st.st_mode & 0o022:
        raise RuntimeStoreError(f"store {store!r} is group/other writable")
    return store


# --------------------------------------------------------------------------- #
#  deterministic candidate identity                                            #
# --------------------------------------------------------------------------- #

def compute_candidate_id(*, app_version: str, commit: str, arch: str,
                         python_version: str, abi: str,
                         input_digest_kind: str, input_digest: str) -> str:
    """FULL lowercase 64-hex SHA-256 over the canonical JSON of the exact bound
    inputs. No truncation; `kind:"built"` is recorded in the manifest."""
    import hashlib
    doc = {"abi": abi, "app_version": app_version, "arch": arch,
           "commit": commit, "input_digest": input_digest,
           "input_digest_kind": input_digest_kind,
           "python_version": python_version, "schema": 1}
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
#  manifests                                                                   #
# --------------------------------------------------------------------------- #

def manifest_path(app_dir: str, runtime_id: str) -> str:
    return os.path.join(app_dir, STORE_NAME, f"{runtime_id}.manifest.json")


def write_manifest(app_dir: str, runtime_id: str, *, kind: str,
                   input_digest: str, state: str = "validated",
                   extra: "dict | None" = None) -> None:
    if not _ID_RE.match(runtime_id):
        raise RuntimeStoreError(f"invalid runtime id: {runtime_id!r}")
    doc = {"schema": 1, "runtime_id": runtime_id, "kind": kind,
           "state": state, "input_digest": input_digest}
    if extra:
        doc.update(extra)
    _write_json_atomic(manifest_path(app_dir, runtime_id), doc)


def read_manifest(app_dir: str, runtime_id: str) -> "dict | None":
    if not _ID_RE.match(runtime_id):
        raise RuntimeStoreError(f"invalid runtime id: {runtime_id!r}")
    doc = _read_json(manifest_path(app_dir, runtime_id))
    if doc is not None and doc.get("runtime_id") != runtime_id:
        raise RuntimeStoreError(f"manifest/id mismatch for {runtime_id!r}")
    return doc


# --------------------------------------------------------------------------- #
#  selector gate (B1)                                                          #
# --------------------------------------------------------------------------- #

def _allowed_runtime_symlink(root_real: str, path: str) -> bool:
    target = os.path.realpath(path)
    inside = target == root_real or target.startswith(root_real + os.sep)
    rel = os.path.relpath(path, root_real)
    is_interp = (
        os.path.dirname(rel) == "bin"
        and re.fullmatch(r"python(?:3(?:\.\d+)?)?", os.path.basename(path))
        and target == os.path.realpath(_sys_python())
    )
    return inside or (
        is_interp and os.path.isfile(target) and os.access(target, os.X_OK)
    )


def _validate_tree_shape(root: str) -> None:
    """Non-mutating hardlink/type/symlink boundary for an untrusted tree."""
    rst = os.lstat(root)
    if stat.S_ISLNK(rst.st_mode) or not stat.S_ISDIR(rst.st_mode):
        raise RuntimeStoreError(f"runtime root {root!r} is not a real directory")
    root_real = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                if not _allowed_runtime_symlink(root_real, path):
                    raise RuntimeStoreError(
                        f"disallowed symlink in runtime tree: {path!r} -> "
                        f"{os.path.realpath(path)!r}")
                continue
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise RuntimeStoreError(f"non-regular object in runtime tree: {path!r}")
            if stat.S_ISREG(st.st_mode) and st.st_nlink != 1:
                raise RuntimeStoreError(f"hardlinked file in runtime tree: {path!r}")


def _tighten_base_venv_permissions(root: str,
                                   owner_uid: int = OWNER_UID) -> None:
    """Remove g/o-write inherited from base-venv templates, before install.

    Some Python distributions preserve the mode of their bundled activation
    templates after applying the child umask.  This narrowly normalizes only
    the freshly created, trusted base venv; arbitrary installer output is
    never permission-laundered.  Shape/ownership/set-id checks for the complete
    tree precede every mutation, and fchmod operates on an O_NOFOLLOW
    descriptor whose identity is matched back to the read-only preflight.
    """
    _validate_tree_shape(root)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if (not nofollow or not directory or not nonblock
            or not hasattr(os, "fchmod")):
        raise RuntimeStoreError("secure base-venv permission tightening unavailable")

    paths = [root]
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        paths.extend(os.path.join(dirpath, name)
                     for name in dirnames + filenames)

    preflight = []
    for path in paths:
        try:
            before = os.lstat(path)
        except OSError as exc:
            raise RuntimeStoreError(
                f"base venv entry {path!r} changed during preflight: {exc}") from exc
        if stat.S_ISLNK(before.st_mode):
            continue  # allowed interpreter links were checked by the shape gate
        if before.st_uid != owner_uid:
            raise RuntimeStoreError(
                f"base venv entry {path!r} owner uid {before.st_uid}, "
                f"expected {owner_uid}")
        if before.st_mode & 0o6000:
            raise RuntimeStoreError(f"base venv entry {path!r} is setuid/setgid")
        preflight.append((path, before.st_dev, before.st_ino,
                          stat.S_ISDIR(before.st_mode)))

    for path, expected_dev, expected_ino, is_dir in preflight:
        flags = (os.O_RDONLY | nofollow | nonblock
                 | getattr(os, "O_CLOEXEC", 0))
        if is_dir:
            flags |= directory
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise RuntimeStoreError(
                f"cannot securely open base venv entry {path!r}: {exc}") from exc
        try:
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (expected_dev, expected_ino):
                raise RuntimeStoreError(
                    f"base venv entry {path!r} changed during permission tightening")
            current_is_dir = stat.S_ISDIR(current.st_mode)
            if current_is_dir != is_dir or (not is_dir
                                             and not stat.S_ISREG(current.st_mode)):
                raise RuntimeStoreError(
                    f"base venv entry {path!r} changed type during tightening")
            if stat.S_ISREG(current.st_mode) and current.st_nlink != 1:
                raise RuntimeStoreError(
                    f"base venv entry {path!r} became hardlinked")
            if current.st_uid != owner_uid:
                raise RuntimeStoreError(
                    f"base venv entry {path!r} owner changed during tightening")
            if current.st_mode & 0o6000:
                raise RuntimeStoreError(
                    f"base venv entry {path!r} became setuid/setgid")
            mode = stat.S_IMODE(current.st_mode)
            if mode & 0o022:
                os.fchmod(fd, mode & ~0o022)
        finally:
            os.close(fd)

    _validate_tree_path(root, owner_uid)


def _validate_tree_path(root: str, owner_uid: int = OWNER_UID) -> None:
    """Recursively validate a runtime tree without following any symlink.

    This function is deliberately read-only.  It must run before ordinary
    path-based chmod/chown normalization, which can follow directory symlinks.
    The base-venv exception above first shape-gates the complete tree, then uses
    identity-matched O_NOFOLLOW descriptors, and finally calls this full gate.
    """
    _validate_tree_shape(root)
    rst = os.lstat(root)
    if rst.st_uid != owner_uid:
        raise RuntimeStoreError(
            f"runtime root {root!r} owner uid {rst.st_uid}, expected {owner_uid}")
    if rst.st_mode & 0o022:
        raise RuntimeStoreError(f"runtime root {root!r} is group/other writable")
    if rst.st_mode & 0o6000:
        raise RuntimeStoreError(f"runtime root {root!r} is setuid/setgid")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            q = os.path.join(dirpath, name)
            st = os.lstat(q)
            if stat.S_ISLNK(st.st_mode):
                continue  # already validated by the non-mutating shape gate
            if st.st_uid != owner_uid:
                raise RuntimeStoreError(f"tree entry {q!r} owner uid {st.st_uid}, expected {owner_uid}")
            if st.st_mode & 0o022:
                raise RuntimeStoreError(f"tree entry {q!r} is group/other writable")
            if st.st_mode & 0o6000:
                raise RuntimeStoreError(f"tree entry {q!r} is setuid/setgid")


def validate_legacy_shape(app_dir: str, owner_uid: int = OWNER_UID) -> None:
    """Validate a pre-conversion venv before recursive ownership mutation."""
    assert_app_dir(app_dir, owner_uid)
    root = os.path.join(app_dir, SELECTOR_NAME)
    if os.path.realpath(os.path.dirname(root)) != os.path.realpath(app_dir):
        raise RuntimeStoreError("legacy runtime path escapes the app root")
    _validate_tree_shape(root)


def validate_legacy_runtime(app_dir: str, owner_uid: int = OWNER_UID) -> None:
    """Full post-transition gate for the real-directory legacy venv."""
    validate_legacy_shape(app_dir, owner_uid)
    root = os.path.join(app_dir, SELECTOR_NAME)
    _validate_tree_path(root, owner_uid)
    if not os.path.isfile(os.path.join(root, "pyvenv.cfg")):
        raise RuntimeStoreError("legacy runtime has no pyvenv.cfg")
    py = os.path.join(root, "bin", "python3")
    if not (os.path.isfile(py) or os.path.islink(py)) or not os.access(py, os.X_OK):
        raise RuntimeStoreError("legacy runtime has no executable bin/python3")


def validate_runtime_tree(app_dir: str, runtime_id: str,
                          owner_uid: int = OWNER_UID) -> None:
    """RECURSIVE, non-following ownership/type/mode validation of the COMPLETE
    runtime tree (finding 6). Every entry under .venvs/<id> must be owner-owned,
    not group/other-writable, and contain no symlinks except the venv's exact
    standard interpreter links to its creating base Python, and no setuid/setgid.
    Run BEFORE executing the runtime's interpreter, so a substituted or
    service-writable package below site-packages cannot execute first."""
    _validate_tree_path(os.path.join(app_dir, STORE_NAME, runtime_id), owner_uid)


def validate_target(app_dir: str, runtime_id: str,
                    owner_uid: int = OWNER_UID) -> None:
    """Validate a runtime AS AN ACTIVATION TARGET without touching or requiring
    the selector: real-directory termination, containment, ownership, no g/o
    write, pyvenv.cfg, executable interpreter, and a VALIDATED manifest bound
    to the id. Used PRE-FLIP by activate()/rollback_activation() and by the
    selector gate itself."""
    if not _ID_RE.match(runtime_id):
        raise RuntimeStoreError(f"invalid runtime id: {runtime_id!r}")
    assert_app_dir(app_dir, owner_uid)     # finding 2
    store = os.path.join(app_dir, STORE_NAME)
    try:
        sst = os.lstat(store)
    except OSError as exc:
        raise RuntimeStoreError(f"store {store!r} unavailable: {exc}") from exc
    if stat.S_ISLNK(sst.st_mode) or not stat.S_ISDIR(sst.st_mode):
        raise RuntimeStoreError(f"store {store!r} is not a real directory")
    target = os.path.join(store, runtime_id)
    try:
        tst = os.lstat(target)
    except OSError as exc:
        raise RuntimeStoreError(f"target {target!r} missing: {exc}") from exc
    if stat.S_ISLNK(tst.st_mode):
        raise RuntimeStoreError(f"selector target {target!r} is a second symlink hop (refused)")
    if not stat.S_ISDIR(tst.st_mode):
        raise RuntimeStoreError(f"selector target {target!r} is not a real directory")
    if os.path.realpath(os.path.dirname(os.path.realpath(target))) != os.path.realpath(store):
        raise RuntimeStoreError(f"selector target {target!r} escapes the store")
    for p, pst in ((target, tst), (os.path.join(target, "bin"), None)):
        cst = pst or os.lstat(p)
        if cst.st_uid != owner_uid:
            raise RuntimeStoreError(f"{p!r} owner uid {cst.st_uid}, expected {owner_uid}")
        if cst.st_mode & 0o022:
            raise RuntimeStoreError(f"{p!r} is group/other writable")
    if not os.path.isfile(os.path.join(target, "pyvenv.cfg")):
        raise RuntimeStoreError(f"{target!r} has no pyvenv.cfg (not a venv)")
    py = os.path.join(target, "bin", "python3")
    if not (os.path.isfile(py) or os.path.islink(py)) or not os.access(py, os.X_OK):
        raise RuntimeStoreError(f"{py!r} missing or not executable")
    man = read_manifest(app_dir, runtime_id)
    if man is None:
        raise RuntimeStoreError(f"runtime {runtime_id!r} has no manifest (unvalidated)")
    if man.get("state") != "validated":
        raise RuntimeStoreError(
            f"runtime {runtime_id!r} manifest state {man.get('state')!r} != 'validated'")


def validate_selector(app_dir: str, owner_uid: int = OWNER_UID) -> str:
    """Full selector gate. Returns the active runtime id or raises."""
    selector = os.path.join(app_dir, SELECTOR_NAME)
    st = os.lstat(selector)
    if not stat.S_ISLNK(st.st_mode):
        raise RuntimeStoreError(f"selector {selector!r} is not a symlink")
    text = os.readlink(selector)
    parts = text.split("/")
    if len(parts) != 2 or parts[0] != STORE_NAME or text.startswith("/") or ".." in parts:
        raise RuntimeStoreError(
            f"selector link text {text!r} is not exactly '{STORE_NAME}/<runtime-id>'")
    runtime_id = parts[1]
    validate_target(app_dir, runtime_id, owner_uid)
    return runtime_id


# --------------------------------------------------------------------------- #
#  candidate construction + live semantic revalidation                         #
# --------------------------------------------------------------------------- #

def _clean_env() -> dict:
    """Environment for runtime validation subprocesses: interpreter-altering
    variables are STRIPPED so an injected PYTHONPATH/PYTHONHOME/PIP_* can never
    change what a runtime appears to contain (validation must observe the
    runtime itself, nothing else)."""
    # An allowlist is easier to audit than trying to enumerate every loader or
    # interpreter injection variable (LD_PRELOAD, LD_AUDIT, DYLD_*, VIRTUAL_ENV,
    # PYTHON*, ...).  Validation never needs network credentials or caller state.
    allowed = {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "COMSPEC",
    }
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def _run_import_smoke(python_path: str,
                      smoke_imports: "tuple[str, ...] | None" = None) -> None:
    """Load the exact committed runtime probes with the candidate interpreter."""
    import subprocess
    probes = (SMOKE_PROBES if smoke_imports is None
              else tuple((mod, f"import {mod}") for mod in smoke_imports))
    for label, code in probes:
        r = subprocess.run([python_path, "-I", "-c", code],
                           capture_output=True, text=True, env=_clean_env())
        if r.returncode != 0:
            raise RuntimeStoreError(
                f"runtime smoke failed for {label!r}: {r.stderr.strip()[:200]}")


def _dist_record(python_path: str) -> str:
    """Canonical installed-distribution record of a runtime, computed with THAT
    runtime's own interpreter (live evidence, not a manifest claim)."""
    import subprocess
    r = subprocess.run([python_path, "-I", "-m", "pip", "freeze", "--all"],
                       capture_output=True, text=True, env=_clean_env())
    if r.returncode != 0:
        raise RuntimeStoreError(f"pip freeze failed in {python_path!r}: {r.stderr.strip()[:200]}")
    lines = sorted(ln.strip() for ln in r.stdout.splitlines() if ln.strip())
    import hashlib
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def revalidate_runtime(app_dir: str, runtime_id: str,
                       owner_uid: int = OWNER_UID,
                       smoke_imports: "tuple[str, ...] | None" = None) -> None:
    """LIVE semantic validation of an EXISTING runtime against its manifest:
    structure/ownership (validate_target), recomputed dist record equality,
    `pip check`, import smoke, and interpreter/architecture binding. Required
    for candidate-ID collisions -- a manifest comparison alone never accepts a
    pre-existing runtime."""
    import subprocess
    validate_target(app_dir, runtime_id, owner_uid)
    # RECURSIVE trust-closure check BEFORE executing this runtime's interpreter.
    validate_runtime_tree(app_dir, runtime_id, owner_uid)
    man = read_manifest(app_dir, runtime_id)
    py = os.path.join(app_dir, STORE_NAME, runtime_id, "bin", "python3")
    rec = _dist_record(py)
    if man.get("dist_record") != rec:
        raise RuntimeStoreError(
            f"runtime {runtime_id!r} installed-distribution record differs from its "
            f"manifest (live={rec[:12]}.., manifest={str(man.get('dist_record'))[:12]}..)")
    chk = subprocess.run([py, "-I", "-m", "pip", "check"],
                         capture_output=True, text=True, env=_clean_env())
    if chk.returncode != 0:
        raise RuntimeStoreError(f"pip check failed: {chk.stdout.strip()[:200]}")
    probe = ("import platform,sysconfig,sys;"
             "print(platform.machine());"
             "print(sysconfig.get_config_var('SOABI') or '');"
             "print('%d.%d' % sys.version_info[:2])")
    r = subprocess.run([py, "-I", "-c", probe], capture_output=True, text=True,
                       env=_clean_env())
    if r.returncode != 0:
        raise RuntimeStoreError(f"interpreter probe failed in {runtime_id!r}")
    arch, abi, pyver = (r.stdout.splitlines() + ["", "", ""])[:3]
    for field, live in (("arch", arch), ("abi", abi), ("python_version", pyver)):
        want = man.get(field)
        if want is not None and want != live:
            raise RuntimeStoreError(
                f"runtime {runtime_id!r} {field} mismatch: manifest={want!r} live={live!r}")
    _run_import_smoke(py, smoke_imports)


def stage_candidate(app_dir: str, runtime_id: str, attempt_id: str,
                    owner_uid: int = OWNER_UID) -> str:
    """Create the candidate's staging venv (attempt-owned, inside the store).
    Returns the staging python path. Fails closed on any leftover staging."""
    import subprocess
    if not _ID_RE.match(runtime_id) or runtime_id == LEGACY_ID:
        raise RuntimeStoreError(f"invalid candidate id: {runtime_id!r}")
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise RuntimeStoreError(f"invalid candidate attempt id: {attempt_id!r}")
    store = open_store(app_dir, owner_uid, create=True)
    if os.path.lexists(os.path.join(store, runtime_id)):
        raise RuntimeStoreError(
            f"runtime {runtime_id!r} already exists (use live revalidation, not staging)")
    if os.path.lexists(manifest_path(app_dir, runtime_id)):
        raise RuntimeStoreError(
            f"runtime {runtime_id!r} has an orphan manifest (reconcile before staging)")
    staging = os.path.join(store, f".staging-{attempt_id}")
    if os.path.lexists(staging):
        raise RuntimeStoreError(f"staging {staging!r} already exists (diagnose/clean first)")
    # Popen's POSIX-only ``umask`` parameter changes only the venv child.  The
    # staged runtime must not depend on a caller/runner's ambient umask (for
    # example 0002 would otherwise create bin/activate as group-writable), and
    # changing the parent process umask would be unsafe for library callers.
    r = subprocess.run([_sys_python(), "-m", "venv", staging],
                       capture_output=True, text=True, env=_clean_env(),
                       umask=0o022)
    if r.returncode != 0:
        raise RuntimeStoreError(f"venv creation failed: {r.stderr.strip()[:200]}")
    _tighten_base_venv_permissions(staging, owner_uid)
    return os.path.join(staging, "bin", "python3")


def finalize_candidate(app_dir: str, runtime_id: str, attempt_id: str, *,
                       manifest_fields: dict, owner_uid: int = OWNER_UID,
                       smoke_imports: "tuple[str, ...] | None" = None) -> str:
    """Validate the STAGED candidate and publish it atomically.

    The validated manifest is durably published *before* the directory rename.
    Therefore a crash can leave either an attempt-bound orphan manifest plus
    staging, or a complete final runtime plus validated manifest -- never a
    visible final runtime with an intermediate ``building`` manifest.
    """
    import subprocess
    import shutil as _sh
    if not _ID_RE.fullmatch(runtime_id) or runtime_id == LEGACY_ID:
        raise RuntimeStoreError(f"invalid candidate id: {runtime_id!r}")
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise RuntimeStoreError(f"invalid candidate attempt id: {attempt_id!r}")
    store = open_store(app_dir, owner_uid)  # RE-CHECK store identity before publish
    staging = os.path.join(store, f".staging-{attempt_id}")
    final = os.path.join(store, runtime_id)
    py = os.path.join(staging, "bin", "python3")
    manifest_written = False
    try:
        if not os.path.isdir(staging) or os.path.islink(staging):
            raise RuntimeStoreError(f"staging {staging!r} missing or not a real directory")
        # RECURSIVE trust-closure check of the STAGING tree BEFORE executing
        # its interpreter (pip check / smoke). The staging dir sits inside the
        # store, so validate against it directly.
        _validate_tree_path(staging, owner_uid)
        chk = subprocess.run([py, "-I", "-m", "pip", "check"],
                             capture_output=True, text=True, env=_clean_env())
        if chk.returncode != 0:
            raise RuntimeStoreError(f"candidate pip check failed: {chk.stdout.strip()[:200]}")
        _run_import_smoke(py, smoke_imports)
        fields = dict(manifest_fields)
        forbidden = {"schema", "runtime_id", "kind", "state"} & set(fields)
        if forbidden:
            raise RuntimeStoreError(
                f"candidate manifest fields override reserved keys: {sorted(forbidden)!r}")
        fields["dist_record"] = _dist_record(py)
        fields["publication_attempt"] = attempt_id
        _fsync_tree(staging)
        if os.path.lexists(final) or os.path.lexists(manifest_path(app_dir, runtime_id)):
            raise RuntimeStoreError(
                f"candidate publication collision for runtime {runtime_id!r}")
        write_manifest(app_dir, runtime_id, kind="built",
                       input_digest=fields.get("input_digest", ""),
                       state="validated", extra=fields)
        manifest_written = True
        os.rename(staging, final)            # ATOMIC publication
        _fsync_dir(store)
        validate_target(app_dir, runtime_id, owner_uid)
        return runtime_id
    except BaseException:
        if os.path.isdir(staging) and not os.path.islink(staging):
            _sh.rmtree(staging, ignore_errors=True)   # attempt-owned staging only
        if manifest_written and not os.path.lexists(final):
            try:
                os.unlink(manifest_path(app_dir, runtime_id))
            except OSError:
                pass
        raise


def build_candidate(app_dir: str, private_dir: str, runtime_id: str, *,
                    attempt_id: str, install_cmd, manifest_fields: dict,
                    owner_uid: int = OWNER_UID,
                    smoke_imports: "tuple[str, ...] | None" = None) -> str:
    """One-call candidate lifecycle (python/test convenience): stage -> caller's
    per-arch install -> finalize. Collisions: live semantic revalidation only."""
    if not _ID_RE.match(runtime_id) or runtime_id == LEGACY_ID:
        raise RuntimeStoreError(f"invalid candidate id: {runtime_id!r}")
    store = open_store(app_dir, owner_uid, create=True)
    if os.path.lexists(os.path.join(store, runtime_id)):
        revalidate_runtime(app_dir, runtime_id, owner_uid, smoke_imports)
        return runtime_id                    # identical, live-verified: reuse
    staging = os.path.join(store, f".staging-{attempt_id}")
    if os.path.lexists(staging):
        raise RuntimeStoreError(f"staging {staging!r} already exists (diagnose/clean first)")
    py = stage_candidate(app_dir, runtime_id, attempt_id, owner_uid)
    try:
        install_cmd(py)
    except BaseException:
        import shutil as _sh
        _sh.rmtree(os.path.dirname(os.path.dirname(py)), ignore_errors=True)
        raise
    return finalize_candidate(app_dir, runtime_id, attempt_id,
                              manifest_fields=manifest_fields,
                              owner_uid=owner_uid, smoke_imports=smoke_imports)


def _sys_python() -> str:
    import sys as _s
    return _s.executable or "/usr/bin/python3"


def discard_candidate_staging(app_dir: str, attempt_id: str,
                              owner_uid: int = OWNER_UID) -> bool:
    """Delete only the exact attempt-owned candidate staging directory.

    Used when a pre-downtime update was interrupted. The store and app-root
    boundaries are validated first; final runtimes and foreign entries are
    never candidates for this operation. Returns whether a staging directory
    was removed.
    """
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise RuntimeStoreError(f"invalid candidate attempt id: {attempt_id!r}")
    assert_app_dir(app_dir, owner_uid)
    store_path = os.path.join(app_dir, STORE_NAME)
    if not os.path.lexists(store_path):
        return False
    store = open_store(app_dir, owner_uid)
    staging = os.path.join(store, f".staging-{attempt_id}")
    try:
        st = os.lstat(staging)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeStoreError(f"candidate staging {staging!r} is not a real directory")
    if st.st_uid != owner_uid:
        raise RuntimeStoreError(f"candidate staging {staging!r} has wrong owner")
    import shutil as _sh
    _sh.rmtree(staging)
    _fsync_dir(store)
    return True


def _recovery_manifest(app_dir: str, runtime_id: str, attempt_id: str,
                       owner_uid: int) -> dict:
    """Read an exact candidate manifest before recovery may remove it.

    Removal authority comes from the durable transaction's candidate id *and*
    the manifest's publication-attempt binding.  A foreign, linked, shared, or
    differently-owned object is preserved and fails closed.
    """
    path = manifest_path(app_dir, runtime_id)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise RuntimeStoreError(f"candidate manifest {path!r} is not a regular file")
    if st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
        raise RuntimeStoreError(
            f"candidate manifest {path!r} must be owner-owned, unshared mode 0600")
    doc = _read_json(path)
    if doc is None or doc.get("runtime_id") != runtime_id:
        raise RuntimeStoreError("candidate recovery manifest/id mismatch")
    if doc.get("publication_attempt") != attempt_id:
        raise RuntimeStoreError(
            "candidate recovery manifest is not bound to this update attempt")
    return doc


def _selector_resolves_to(app_dir: str, target: str) -> bool:
    selector = os.path.join(app_dir, SELECTOR_NAME)
    try:
        st = os.lstat(selector)
    except FileNotFoundError:
        return False
    if not stat.S_ISLNK(st.st_mode):
        return False
    return os.path.realpath(selector) == os.path.realpath(target)


def reconcile_candidate_attempt(app_dir: str, runtime_id: str, attempt_id: str,
                                owner_uid: int = OWNER_UID,
                                smoke_imports: "tuple[str, ...] | None" = None) -> str:
    """Reconcile every crash boundary of pre-downtime candidate publication.

    A valid final candidate is retained for deterministic reuse.  Attempt-owned
    staging and orphan manifests are removed.  An invalid final candidate is
    removable only when its strict manifest binds it to this exact attempt and
    the selector does not resolve to it.  Foreign or ambiguous objects are
    preserved for diagnosis.
    """
    if not _ID_RE.fullmatch(runtime_id) or runtime_id == LEGACY_ID:
        raise RuntimeStoreError(f"invalid candidate id: {runtime_id!r}")
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise RuntimeStoreError(f"invalid candidate attempt id: {attempt_id!r}")
    assert_app_dir(app_dir, owner_uid)
    store_path = os.path.join(app_dir, STORE_NAME)
    if not os.path.lexists(store_path):
        return "absent"
    store = open_store(app_dir, owner_uid)
    final = os.path.join(store, runtime_id)
    manifest = manifest_path(app_dir, runtime_id)
    removed_staging = discard_candidate_staging(app_dir, attempt_id, owner_uid)

    if not os.path.lexists(final):
        if not os.path.lexists(manifest):
            return "discarded-staging" if removed_staging else "absent"
        _recovery_manifest(app_dir, runtime_id, attempt_id, owner_uid)
        os.unlink(manifest)
        _fsync_dir(store)
        return "discarded-partial"

    try:
        revalidate_runtime(app_dir, runtime_id, owner_uid, smoke_imports)
    except (RuntimeStoreError, OSError):
        # Only this exact attempt's never-activated output may be removed.
        _recovery_manifest(app_dir, runtime_id, attempt_id, owner_uid)
        st = os.lstat(final)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise RuntimeStoreError(
                f"candidate runtime {final!r} is not a real directory")
        if st.st_uid != owner_uid:
            raise RuntimeStoreError(f"candidate runtime {final!r} has wrong owner")
        if _selector_resolves_to(app_dir, final):
            raise RuntimeStoreError("refusing to remove a selector-referenced runtime")
        import shutil as _sh
        _sh.rmtree(final)
        os.unlink(manifest)
        _fsync_dir(store)
        return "discarded-invalid"
    return "validated"


# --------------------------------------------------------------------------- #
#  legacy conversion (B3) -- write-ahead, idempotent, resumable                #
# --------------------------------------------------------------------------- #

def _transition_path(private_dir: str) -> str:
    return os.path.join(private_dir, TRANSITION_RECORD)


def convert_legacy(app_dir: str, private_dir: str, owner_uid: int = OWNER_UID) -> str:
    """One-time conversion of the real-directory selector into the store.

    States (classifiable at every interruption):
      unconverted : venv real dir, no record          -> full run
      recorded    : record present                     -> resume from its phase
      converted   : venv symlink validates, no record  -> no-op
    Returns the active runtime id."""
    assert_app_dir(app_dir, owner_uid)     # finding 2: real root-owned app dir first
    selector = os.path.join(app_dir, SELECTOR_NAME)
    store = os.path.join(app_dir, STORE_NAME)
    target = os.path.join(store, LEGACY_ID)
    rec_path = _transition_path(private_dir)
    rec = _read_json(rec_path)

    # Already-converted fast path (idempotent).
    if rec is None and os.path.islink(selector):
        return validate_selector(app_dir, owner_uid)

    if rec is None:
        # Fresh conversion: validate the legacy object BEFORE any mutation.
        st = os.lstat(selector)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise RuntimeStoreError(f"legacy {selector!r} is not a real directory")
        if os.path.realpath(os.path.dirname(os.path.realpath(selector))) \
                != os.path.realpath(app_dir):
            raise RuntimeStoreError(f"legacy {selector!r} escapes the app root")
        if st.st_uid != owner_uid:
            raise RuntimeStoreError(
                f"legacy {selector!r} owner uid {st.st_uid}, expected {owner_uid} "
                f"(run the ownership transition first)")
        if os.path.lexists(target):
            man = read_manifest(app_dir, LEGACY_ID)
            if man is None:
                raise RuntimeStoreError(
                    f"{target!r} exists without a manifest (foreign; refusing)")
        # WRITE-AHEAD record, then mutate. open_store validates APP_DIR is a
        # real, root-owned, non-writable dir and lstat-gates `.venvs` (a
        # symlinked store is refused, never followed) before creating it.
        open_store(app_dir, owner_uid, create=True)
        rec = {"schema": 1, "op": "convert-legacy", "phase": "recorded",
               "selector": selector, "target": target, "runtime_id": LEGACY_ID}
        _write_json_atomic(rec_path, rec)

    # Resume-capable ordered steps; each re-checks on-disk state first.
    sel_is_dir = os.path.isdir(selector) and not os.path.islink(selector)
    if sel_is_dir and not os.path.lexists(target):
        os.rename(selector, target)                   # same-fs move
        rec["phase"] = "moved"
        _write_json_atomic(rec_path, rec)
    if read_manifest(app_dir, LEGACY_ID) is None:
        write_manifest(app_dir, LEGACY_ID, kind="legacy",
                       input_digest="legacy-preexisting")
    if not os.path.islink(selector):
        if os.path.lexists(selector):
            raise RuntimeStoreError(
                f"{selector!r} still exists as a non-symlink after move (diagnose)")
        _publish_symlink(app_dir, f"{STORE_NAME}/{LEGACY_ID}")
        rec["phase"] = "published"
        _write_json_atomic(rec_path, rec)
    runtime_id = validate_selector(app_dir, owner_uid)
    os.unlink(rec_path)                               # transition complete
    return runtime_id


def rollback_conversion(app_dir: str, private_dir: str,
                        owner_uid: int = OWNER_UID) -> None:
    """Restore the pre-conversion state (real directory at the selector path).
    Only meaningful while a transition record exists or right after conversion."""
    selector = os.path.join(app_dir, SELECTOR_NAME)
    target = os.path.join(app_dir, STORE_NAME, LEGACY_ID)
    rec_path = _transition_path(private_dir)
    if os.path.islink(selector):
        os.unlink(selector)
    if os.path.isdir(target) and not os.path.lexists(selector):
        os.rename(target, selector)
    try:
        os.unlink(rec_path)
    except FileNotFoundError:
        pass
    st = os.lstat(selector)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeStoreError("conversion rollback did not restore a real directory")


# --------------------------------------------------------------------------- #
#  activation / rollback / retention (B4)                                      #
# --------------------------------------------------------------------------- #

def activate(app_dir: str, private_dir: str, runtime_id: str,
             owner_uid: int = OWNER_UID) -> str:
    """Atomic selector flip to a runtime that has ALREADY passed the full
    target gate. Order: validate the CANDIDATE (no selector mutation) ->
    record previous -> atomic flip -> re-validate; an unexpected post-flip
    failure RESTORES the previous selector before raising, so an invalid
    target can never remain the visible selector. Returns the previous id."""
    validate_target(app_dir, runtime_id, owner_uid)   # PRE-FLIP, no mutation
    previous = validate_selector(app_dir, owner_uid)
    if previous == runtime_id:
        return previous                               # no-op
    _write_json_atomic(os.path.join(private_dir, "selector-previous.json"),
                       {"schema": 1, "previous": previous, "current": runtime_id})
    _publish_symlink(app_dir, f"{STORE_NAME}/{runtime_id}")
    try:
        validate_selector(app_dir, owner_uid)
    except BaseException:
        _publish_symlink(app_dir, f"{STORE_NAME}/{previous}")   # restore, then raise
        raise
    return previous


def activate_initial(app_dir: str, runtime_id: str,
                     owner_uid: int = OWNER_UID) -> str:
    """Publish the first validated runtime selector for a fresh installation.

    The selector must be absent; a legacy real-directory runtime or an existing
    selector is never overwritten by the fresh-install path. Target validation
    happens before publication, and an unexpected post-publish failure removes
    only the selector created by this call.
    """
    validate_target(app_dir, runtime_id, owner_uid)
    selector = os.path.join(app_dir, SELECTOR_NAME)
    if os.path.lexists(selector):
        raise RuntimeStoreError("initial activation requires an absent selector")
    _publish_symlink(app_dir, f"{STORE_NAME}/{runtime_id}")
    try:
        return validate_selector(app_dir, owner_uid)
    except BaseException:
        try:
            os.unlink(selector)
            _fsync_dir(app_dir)
        except FileNotFoundError:
            pass
        raise


def rollback_activation(app_dir: str, private_dir: str,
                        owner_uid: int = OWNER_UID) -> str:
    """Flip the selector back to the recorded previous runtime (local, offline).
    The previous runtime is validated as a TARGET before any flip; a post-flip
    failure restores the pre-rollback selector before raising."""
    rec = _read_json(os.path.join(private_dir, "selector-previous.json"))
    if rec is None or not isinstance(rec.get("previous"), str):
        raise RuntimeStoreError("no recorded previous runtime (nothing to roll back to)")
    prev = rec["previous"]
    validate_target(app_dir, prev, owner_uid)         # PRE-FLIP, no mutation
    current = validate_selector(app_dir, owner_uid)
    _publish_symlink(app_dir, f"{STORE_NAME}/{prev}")
    try:
        return validate_selector(app_dir, owner_uid)
    except BaseException:
        _publish_symlink(app_dir, f"{STORE_NAME}/{current}")
        raise


def gc(app_dir: str, private_dir: str, owner_uid: int = OWNER_UID) -> list:
    """Remove manifest-recorded runtimes that are neither current nor the
    recorded previous. Foreign/unrecorded objects ALWAYS survive. Returns the
    removed ids."""
    import shutil as _sh
    current = validate_selector(app_dir, owner_uid)
    prev_rec = _read_json(os.path.join(private_dir, "selector-previous.json")) or {}
    keep = {current, prev_rec.get("previous")}
    store = open_store(app_dir, owner_uid)
    removed = []
    for name in sorted(os.listdir(store)):
        if name.endswith(".manifest.json"):
            continue
        if name in keep:
            continue
        man = read_manifest(app_dir, name) if _ID_RE.match(name) else None
        if man is None:
            continue                                  # unrecorded/foreign: survive
        path = os.path.join(store, name)
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            continue                                  # substituted object: survive
        _sh.rmtree(path)
        os.unlink(manifest_path(app_dir, name))
        removed.append(name)
    return removed
