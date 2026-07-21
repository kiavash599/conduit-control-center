"""backend/priv_state.py -- privileged updater/restore state boundary (Epic 1).

THE private/public state contract
=================================
Root-owned PRIVATE state (locks, work trees, worker logs, attempt-ownership
records) lives under ``PRIVATE_DIR`` (root:root 0700). The service account can
neither read nor write it: it cannot create, replace, rename, symlink, lock,
truncate, or delete anything inside the privileged transaction boundary.

The service-visible PUBLIC status documents live under ``PUBLIC_STATUS_DIR``
(root:root 0755): the parent is root-owned and NOT service-writable, the files
are root:conduit-cc 0640. The service may READ them; only root can publish.

Why this module exists (F2): the previous updater wrote root status through a
FIXED temp name with a plain ``open(.., "w")`` inside the service-writable
StateDirectory -- a service-account symlink at that fixed name would have let
root clobber an arbitrary host file. Every publisher here is symlink-safe:
unpredictable ``mkstemp`` creation, parent/ownership invariants checked first,
``fsync`` + atomic ``os.replace`` + parent-directory ``fsync``, and temp
cleanup on every failure path.

Deletion authority (F5-class): cleanup NEVER derives authority from a name
prefix. Every attempt records an ownership document under ``ATTEMPTS_DIR``;
only recorded, validated, contained paths are ever removed.

``OWNER_UID`` is 0 in production. Tests inject their own uid -- the CHECKS are
identical; only the expected owner differs.

stdlib-only: imported by the privileged helpers (ccc-update-apply,
ccc-restore-apply), which must not pull heavy dependencies.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile

# --- Canonical locations (Epic-1 state boundary) ---------------------------- #
PRIVATE_DIR = "/var/lib/ccc-update"            # root:root 0700 -- privileged state
ATTEMPTS_DIR = f"{PRIVATE_DIR}/attempts"       # per-attempt ownership records
PUBLIC_STATUS_DIR = "/var/lib/ccc-status"      # root:root 0755 -- published status
UPDATE_STATUS_PATH = f"{PUBLIC_STATUS_DIR}/update-status.json"
RESTORE_STATUS_PATH = f"{PUBLIC_STATUS_DIR}/restore-status.json"
LIFECYCLE_LOCK_PATH = f"{PRIVATE_DIR}/lifecycle.lock"
WORKER_LOG_PATH = f"{PRIVATE_DIR}/update-worker.log"

OWNER_UID = 0                                  # production owner of all state
PUBLIC_FILE_MODE = 0o640                       # published status: root:conduit-cc
MAX_STATUS_BYTES = 64 * 1024                   # bounded, canonical JSON only
_ATTEMPT_ID_RE = re.compile(r"^[0-9a-f]{12}$")


class PrivStateError(Exception):
    """Fail-closed privileged-state violation (specific, testable)."""


# --- Directory invariants ---------------------------------------------------- #

def _lstat_dir(path: str) -> os.stat_result:
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise PrivStateError(f"required directory missing: {path!r}: {exc}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise PrivStateError(f"directory must not be a symlink: {path!r}")
    if not stat.S_ISDIR(st.st_mode):
        raise PrivStateError(f"not a directory: {path!r}")
    return st


def assert_private_dir(path: str, owner_uid: int = OWNER_UID) -> None:
    """PRIVATE state parent: real dir, exact owner, no group/other access."""
    st = _lstat_dir(path)
    if st.st_uid != owner_uid:
        raise PrivStateError(
            f"private dir {path!r} owner uid {st.st_uid}, expected {owner_uid}")
    if st.st_mode & 0o077:
        raise PrivStateError(
            f"private dir {path!r} mode {oct(st.st_mode & 0o777)} grants group/other access")


def assert_public_dir(path: str, owner_uid: int = OWNER_UID) -> None:
    """PUBLIC status parent: real dir, exact owner, NOT group/other writable."""
    st = _lstat_dir(path)
    if st.st_uid != owner_uid:
        raise PrivStateError(
            f"public status dir {path!r} owner uid {st.st_uid}, expected {owner_uid}")
    if st.st_mode & 0o022:
        raise PrivStateError(
            f"public status dir {path!r} mode {oct(st.st_mode & 0o777)} is group/other writable")


def _fsync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# --- Safe public status publication (F2 fix) -------------------------------- #

def publish_status(dest_path: str, doc: dict, *, owner_uid: int = OWNER_UID,
                   group_gid: "int | None" = None) -> None:
    """Symlink-safe, atomic publication of a bounded canonical JSON status file.

    Invariants enforced BEFORE any write: the parent is a real, owner-owned,
    non-service-writable directory; the destination is absent or a regular file
    (an attacker-placed symlink or foreign object fails closed and the previous
    valid status file is left untouched). The temp name is unpredictable
    (mkstemp), the payload is bounded canonical JSON, and publication is
    flush + fsync + chmod/chown + os.replace + parent fsync. The temp file is
    removed on every failure path.
    """
    parent = os.path.dirname(dest_path) or "."
    assert_public_dir(parent, owner_uid)
    try:
        dst = os.lstat(dest_path)
    except FileNotFoundError:
        dst = None
    except OSError as exc:
        raise PrivStateError(f"cannot stat status destination: {exc}") from exc
    if dst is not None and not stat.S_ISREG(dst.st_mode):
        raise PrivStateError(
            f"status destination {dest_path!r} is not a regular file (refusing)")

    payload = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_STATUS_BYTES:
        raise PrivStateError("status document exceeds bounded size")

    fd, tmp = tempfile.mkstemp(prefix=".pub-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), PUBLIC_FILE_MODE)
            if os.geteuid() == 0:
                # root MUST set exact ownership; a failure here is a real error.
                os.fchown(fh.fileno(), owner_uid,
                          group_gid if group_gid is not None else 0)
        os.replace(tmp, dest_path)
        _fsync_dir(parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- Private lock / log opening --------------------------------------------- #

def open_private_lock(lock_path: str, owner_uid: int = OWNER_UID) -> int:
    """Open (create) a lock file inside the validated PRIVATE dir.

    O_NOFOLLOW + post-open identity check: the opened inode must be a regular,
    owner-owned, single-link file that is STILL what the path names (no swap
    between open and use). Returns the open fd (caller flocks/closes)."""
    assert_private_dir(os.path.dirname(lock_path), owner_uid)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise PrivStateError(f"lock {lock_path!r} is not a regular file")
        if fst.st_uid != owner_uid:
            raise PrivStateError(f"lock {lock_path!r} owner uid {fst.st_uid}, expected {owner_uid}")
        if fst.st_nlink != 1:
            raise PrivStateError(f"lock {lock_path!r} has {fst.st_nlink} links")
        cur = os.lstat(lock_path)
        if (cur.st_dev, cur.st_ino) != (fst.st_dev, fst.st_ino):
            raise PrivStateError(f"lock {lock_path!r} was replaced during open")
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_private_log(log_path: str, owner_uid: int = OWNER_UID) -> int:
    """Safely open/truncate the worker log inside the PRIVATE directory.

    Validation happens before truncation, so a hardlinked/foreign inode cannot
    use the root worker as a clobber primitive even if private state was damaged.
    """
    assert_private_dir(os.path.dirname(log_path), owner_uid)
    fd = os.open(
        log_path,
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode) or fst.st_uid != owner_uid or fst.st_nlink != 1:
            raise PrivStateError("worker log is not a single owner-owned regular file")
        cur = os.lstat(log_path)
        if (cur.st_dev, cur.st_ino) != (fst.st_dev, fst.st_ino):
            raise PrivStateError("worker log was replaced during open")
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


# --- Attempt ownership records (exact deletion authority; F5-class) ---------- #

_ATTEMPT_WORK_PREFIX = {
    "update": "ccc-update",
    "restore": "ccc-restore",
}

def _record_path(attempts_dir: str, attempt_id: str) -> str:
    if not _ATTEMPT_ID_RE.match(attempt_id or ""):
        raise PrivStateError(f"invalid attempt id: {attempt_id!r}")
    return os.path.join(attempts_dir, f"{attempt_id}.json")


def record_attempt(attempts_dir: str, attempt_id: str, work_path: str,
                   owner_uid: int = OWNER_UID, *, kind: str = "update") -> None:
    """Durably record ownership of `work_path` BEFORE it is created, so an
    interrupted attempt is always either absent or recorded (never orphaned)."""
    path = _record_path(attempts_dir, attempt_id)
    assert_private_dir(attempts_dir, owner_uid)
    if kind not in _ATTEMPT_WORK_PREFIX:
        raise PrivStateError(f"invalid attempt kind: {kind!r}")
    private_dir = os.path.realpath(os.path.dirname(attempts_dir))
    work = os.path.realpath(work_path)
    expected = os.path.join(
        private_dir, f"{_ATTEMPT_WORK_PREFIX[kind]}-{attempt_id}")
    if work != expected or os.path.realpath(os.path.dirname(work)) != private_dir:
        raise PrivStateError("attempt work path is not the exact direct-child path")
    if os.path.lexists(work):
        raise PrivStateError("attempt work path already exists before ownership record")
    if os.path.lexists(path):
        raise PrivStateError("attempt ownership record already exists")
    rec = {"schema": 1, "attempt_id": attempt_id, "kind": kind, "work": work}
    fd, tmp = tempfile.mkstemp(prefix=".rec-", dir=attempts_dir)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(rec, sort_keys=True) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), 0o600)
        os.replace(tmp, path)
        _fsync_dir(attempts_dir)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_record(attempts_dir: str, attempt_id: str) -> "dict | None":
    path = _record_path(attempts_dir, attempt_id)
    try:
        with open(path, "rb") as fh:
            rec = json.loads(fh.read().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise PrivStateError(f"unreadable attempt record {path!r}: {exc}") from exc
    if not isinstance(rec, dict) or set(rec) != {
            "schema", "attempt_id", "kind", "work"} \
            or rec.get("schema") != 1 or rec.get("attempt_id") != attempt_id \
            or rec.get("kind") not in _ATTEMPT_WORK_PREFIX \
            or not isinstance(rec.get("work"), str):
        raise PrivStateError(f"malformed attempt record: {path!r}")
    private_dir = os.path.realpath(os.path.dirname(attempts_dir))
    expected = os.path.join(
        private_dir,
        f"{_ATTEMPT_WORK_PREFIX[rec['kind']]}-{attempt_id}",
    )
    if rec["work"] != expected:
        raise PrivStateError(f"attempt record has a noncanonical work path: {path!r}")
    return rec


def attempt_work(private_dir: str, attempts_dir: str, attempt_id: str, *,
                 kind: str, owner_uid: int = OWNER_UID,
                 argv_work: "str | None" = None) -> str:
    """Return the strictly recorded work path for an exact operation kind.

    This is the non-mutating worker-side counterpart to ``cleanup_attempt``:
    internal transient-unit argv can identify an attempt, but never authorizes
    a filesystem path on its own.
    """
    assert_private_dir(private_dir, owner_uid)
    assert_private_dir(attempts_dir, owner_uid)
    rec = _load_record(attempts_dir, attempt_id)
    if rec is None or rec["kind"] != kind:
        raise PrivStateError("attempt record is absent or has the wrong kind")
    work = rec["work"]
    if argv_work is not None and (
            argv_work != work or os.path.realpath(argv_work) != work):
        raise PrivStateError("argv work path does not match the attempt record")
    st = os.lstat(work)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) \
            or st.st_uid != owner_uid or stat.S_IMODE(st.st_mode) != 0o700:
        raise PrivStateError("attempt work path must be owner-owned mode 0700 real directory")
    return work


def cleanup_attempt(private_dir: str, attempts_dir: str, attempt_id: str,
                    owner_uid: int = OWNER_UID, argv_work: "str | None" = None) -> bool:
    """Remove ONLY the work tree this attempt recorded. Idempotent.

    THE single deletion authority (A2): every removal -- worker success/failure,
    launch failure, rejection, stale sweep -- goes through here. Validated
    before any removal:
      * attempt id format and record existence/schema (_load_record);
      * record/argv canonical-path equality when the caller supplies the path
        it was HANDED (argv_work) -- a mismatched argv never deletes anything;
      * direct containment of the recorded path inside PRIVATE_DIR;
      * real-directory object type (lstat; symlinks refused, never followed);
      * CURRENT object identity: st_dev/st_ino re-checked immediately before
        rmtree so a post-validation swap is refused.
    Invalid, outside, symlinked, substituted, unrecorded, malformed and
    ID-mismatched objects survive for diagnosis. Prefix checks may reject but
    never authorize. Returns True when nothing remains for this attempt."""
    assert_private_dir(private_dir, owner_uid)
    assert_private_dir(attempts_dir, owner_uid)
    rec = _load_record(attempts_dir, attempt_id)
    if rec is None:
        if argv_work is not None and os.path.lexists(argv_work):
            raise PrivStateError(
                f"no ownership record for attempt {attempt_id!r}; refusing to touch "
                f"{argv_work!r} (preserved for diagnosis)")
        return True                       # already fully cleaned (idempotent)
    work = rec["work"]
    if argv_work is not None and (
            argv_work != work or os.path.realpath(argv_work) != work):
        raise PrivStateError(
            f"argv work path {argv_work!r} does not match recorded {work!r} (refusing)")
    root = os.path.realpath(private_dir)
    if not (os.path.realpath(os.path.dirname(work)) == root):
        raise PrivStateError(
            f"recorded work path {work!r} is not directly inside {root!r} (refusing)")
    try:
        st = os.lstat(work)
    except FileNotFoundError:
        st = None
    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise PrivStateError(
                f"recorded work path {work!r} is not a real directory (preserved for diagnosis)")
        cur = os.lstat(work)              # CURRENT identity, immediately pre-removal
        if (cur.st_dev, cur.st_ino) != (st.st_dev, st.st_ino):
            raise PrivStateError(f"work path {work!r} was swapped during validation (refusing)")
        shutil.rmtree(work)
        _fsync_dir(private_dir)
    os.unlink(_record_path(attempts_dir, attempt_id))
    _fsync_dir(attempts_dir)
    return True


def sweep_stale_attempts(private_dir: str, attempts_dir: str,
                         owner_uid: int = OWNER_UID,
                         active_ids: "frozenset[str] | set[str]" = frozenset()) -> list:
    """Clean every RECORDED, non-active attempt. Unrecorded objects inside the
    private dir are NEVER touched (foreign/diagnosable). Returns the list of
    attempt ids that could not be cleaned (preserved, with reasons logged by
    the caller)."""
    assert_private_dir(attempts_dir, owner_uid)
    failed = []
    for name in sorted(os.listdir(attempts_dir)):
        if not name.endswith(".json"):
            continue
        attempt_id = name[:-5]
        if not _ATTEMPT_ID_RE.match(attempt_id) or attempt_id in active_ids:
            continue
        try:
            cleanup_attempt(private_dir, attempts_dir, attempt_id, owner_uid)
        except PrivStateError:
            failed.append(attempt_id)
    return failed
