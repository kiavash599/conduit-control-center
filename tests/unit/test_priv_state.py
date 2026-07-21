"""tests/unit/test_priv_state.py -- Epic-1 privileged state boundary (F2/F5).

Every test injects its OWN uid as the expected owner (owner_uid=os.getuid()),
so the CHECKS run unprivileged while exercising the exact production logic. The
symlink/attack matrix proves the safe publisher cannot be redirected and that
cleanup authority comes from ownership records, never name patterns.
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import sys

import pytest

if sys.platform != "linux":
    pytest.skip(
        "POSIX ownership/symlink semantics required", allow_module_level=True
    )

from backend import priv_state as P  # noqa: E402

UID = os.getuid()


# --- directory invariants ---------------------------------------------------- #

def _priv(tmp_path):
    d = tmp_path / "priv"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o700)
    return str(d)


def _pub(tmp_path):
    d = tmp_path / "pub"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    return str(d)


def test_private_dir_rejects_group_access(tmp_path):
    d = _priv(tmp_path)
    os.chmod(d, 0o770)
    with pytest.raises(P.PrivStateError, match="group/other access"):
        P.assert_private_dir(d, UID)


def test_private_dir_rejects_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(P.PrivStateError, match="must not be a symlink"):
        P.assert_private_dir(str(link), UID)


def test_public_dir_rejects_world_writable(tmp_path):
    d = _pub(tmp_path)
    os.chmod(d, 0o757)
    with pytest.raises(P.PrivStateError, match="group/other writable"):
        P.assert_public_dir(d, UID)


def test_dir_rejects_wrong_owner(tmp_path):
    d = _priv(tmp_path)
    with pytest.raises(P.PrivStateError, match="owner uid"):
        P.assert_private_dir(d, UID + 1)


# --- safe status publication (F2) -------------------------------------------- #

def test_publish_status_writes_atomic_bounded_json(tmp_path):
    pub = _pub(tmp_path)
    dest = os.path.join(pub, "update-status.json")
    P.publish_status(dest, {"state": "idle", "id": None}, owner_uid=UID)
    doc = json.loads(open(dest).read())
    assert doc["state"] == "idle"
    assert oct(os.stat(dest).st_mode & 0o777) == oct(P.PUBLIC_FILE_MODE)


def test_publish_status_rejects_symlinked_destination(tmp_path):
    """The F2 exploit: a pre-created symlink at the destination must NOT be
    followed (root would otherwise clobber the link target)."""
    pub = _pub(tmp_path)
    target = tmp_path / "victim.txt"
    target.write_text("precious")
    dest = os.path.join(pub, "update-status.json")
    os.symlink(str(target), dest)
    with pytest.raises(P.PrivStateError, match="not a regular file"):
        P.publish_status(dest, {"state": "x"}, owner_uid=UID)
    assert target.read_text() == "precious"      # victim untouched


def test_publish_status_rejects_service_writable_parent(tmp_path):
    pub = _pub(tmp_path)
    os.chmod(pub, 0o777)
    with pytest.raises(P.PrivStateError, match="group/other writable"):
        P.publish_status(os.path.join(pub, "s.json"), {"a": 1}, owner_uid=UID)


def test_publish_status_failure_preserves_previous(tmp_path):
    pub = _pub(tmp_path)
    dest = os.path.join(pub, "s.json")
    P.publish_status(dest, {"n": 1}, owner_uid=UID)
    # Oversized doc must fail without corrupting the previous valid file.
    with pytest.raises(P.PrivStateError, match="bounded size"):
        P.publish_status(dest, {"big": "x" * (P.MAX_STATUS_BYTES)}, owner_uid=UID)
    assert json.loads(open(dest).read())["n"] == 1
    # no temp litter left behind
    assert [f for f in os.listdir(pub) if f.startswith(".pub-")] == []


# --- private lock/log -------------------------------------------------------- #

def test_open_private_lock_rejects_symlink(tmp_path):
    priv = _priv(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("x")
    os.symlink(str(victim), os.path.join(priv, "update.lock"))
    with pytest.raises(OSError):           # O_NOFOLLOW -> ELOOP
        P.open_private_lock(os.path.join(priv, "update.lock"), UID)


def test_open_private_lock_wrong_parent_owner(tmp_path):
    priv = _priv(tmp_path)
    with pytest.raises(P.PrivStateError, match="owner uid"):
        P.open_private_lock(os.path.join(priv, "update.lock"), UID + 1)


def test_open_private_log_rejects_hardlink_before_truncation(tmp_path):
    priv = _priv(tmp_path)
    victim = tmp_path / "victim.log"
    victim.write_text("precious\n")
    os.link(victim, os.path.join(priv, "update-worker.log"))
    with pytest.raises(P.PrivStateError, match="single owner-owned"):
        P.open_private_log(os.path.join(priv, "update-worker.log"), UID)
    assert victim.read_text() == "precious\n"


def test_open_private_log_truncates_only_after_validation(tmp_path):
    priv = _priv(tmp_path)
    log = os.path.join(priv, "update-worker.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("old\n")
    fd = P.open_private_log(log, UID)
    os.write(fd, b"new\n")
    os.close(fd)
    assert pathlib.Path(log).read_text(encoding="utf-8") == "new\n"
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600


# --- attempt-ownership records + cleanup (F5) -------------------------------- #

def _attempts(priv):
    a = os.path.join(priv, "attempts")
    os.mkdir(a, 0o700)
    return a


def test_cleanup_only_removes_recorded_work(tmp_path):
    priv = _priv(tmp_path)
    att = _attempts(priv)
    work = os.path.join(priv, "ccc-update-aaaaaaaaaaaa")
    P.record_attempt(att, "aaaaaaaaaaaa", work, UID)
    os.mkdir(work, 0o700)
    assert P.cleanup_attempt(priv, att, "aaaaaaaaaaaa", UID) is True
    assert not os.path.exists(work)
    assert not os.path.exists(os.path.join(att, "aaaaaaaaaaaa.json"))


def test_restore_attempt_is_kind_bound_and_worker_validated(tmp_path):
    priv = _priv(tmp_path)
    att = _attempts(priv)
    attempt_id = "121212121212"
    work = os.path.join(priv, f"ccc-restore-{attempt_id}")
    P.record_attempt(att, attempt_id, work, UID, kind="restore")
    os.mkdir(work, 0o700)
    assert P.attempt_work(
        priv,
        att,
        attempt_id,
        kind="restore",
        owner_uid=UID,
        argv_work=work,
    ) == work
    with pytest.raises(P.PrivStateError, match="wrong kind"):
        P.attempt_work(
            priv,
            att,
            attempt_id,
            kind="update",
            owner_uid=UID,
            argv_work=work,
        )
    assert P.cleanup_attempt(priv, att, attempt_id, UID, argv_work=work)


def test_attempt_worker_rejects_path_alias_and_malformed_record(tmp_path):
    priv = _priv(tmp_path)
    att = _attempts(priv)
    attempt_id = "343434343434"
    work = os.path.join(priv, f"ccc-restore-{attempt_id}")
    P.record_attempt(att, attempt_id, work, UID, kind="restore")
    os.mkdir(work, 0o700)
    alias = os.path.join(priv, "work-alias")
    os.symlink(work, alias)
    with pytest.raises(P.PrivStateError, match="argv work path"):
        P.attempt_work(
            priv,
            att,
            attempt_id,
            kind="restore",
            owner_uid=UID,
            argv_work=alias,
        )
    record = os.path.join(att, f"{attempt_id}.json")
    pathlib.Path(record).write_text(
        json.dumps({
            "schema": 1,
            "attempt_id": attempt_id,
            "kind": "restore",
            "work": work,
            "unexpected": True,
        }),
        encoding="utf-8",
    )
    with pytest.raises(P.PrivStateError, match="malformed attempt record"):
        P.attempt_work(
            priv,
            att,
            attempt_id,
            kind="restore",
            owner_uid=UID,
            argv_work=work,
        )


def test_record_attempt_refuses_outside_existing_or_duplicate_path(tmp_path):
    priv = _priv(tmp_path)
    att = _attempts(priv)
    outside = tmp_path / "ccc-update-aaaaaaaaaaaa"
    with pytest.raises(P.PrivStateError, match="exact direct-child"):
        P.record_attempt(att, "aaaaaaaaaaaa", str(outside), UID)
    work = os.path.join(priv, "ccc-update-bbbbbbbbbbbb")
    os.mkdir(work)
    with pytest.raises(P.PrivStateError, match="already exists"):
        P.record_attempt(att, "bbbbbbbbbbbb", work, UID)
    os.rmdir(work)
    P.record_attempt(att, "bbbbbbbbbbbb", work, UID)
    with pytest.raises(P.PrivStateError, match="ownership record already exists"):
        P.record_attempt(att, "bbbbbbbbbbbb", work, UID)


def test_cleanup_is_idempotent(tmp_path):
    priv = _priv(tmp_path)
    att = _attempts(priv)
    assert P.cleanup_attempt(priv, att, "bbbbbbbbbbbb", UID) is True   # no record


def test_cleanup_refuses_symlinked_work(tmp_path):
    """Recorded path replaced by a symlink -> preserved and reported, never
    followed (cannot be used to delete an arbitrary directory)."""
    priv = _priv(tmp_path)
    att = _attempts(priv)
    victim = tmp_path / "victim"
    victim.mkdir()
    work = os.path.join(priv, "ccc-update-cccccccccccc")
    P.record_attempt(att, "cccccccccccc", work, UID)
    os.symlink(str(victim), work)
    with pytest.raises(P.PrivStateError, match="not a real directory"):
        P.cleanup_attempt(priv, att, "cccccccccccc", UID)
    assert victim.exists()


def test_sweep_preserves_foreign_unrecorded_objects(tmp_path):
    """A foreign directory with NO ownership record must survive the sweep."""
    priv = _priv(tmp_path)
    att = _attempts(priv)
    foreign = os.path.join(priv, "ccc-update-ffffffffffff")   # matches the old prefix!
    os.mkdir(foreign, 0o700)                                   # but has no record
    P.sweep_stale_attempts(priv, att, UID)
    assert os.path.isdir(foreign)      # prefix match alone never authorizes deletion


def test_sweep_skips_active_ids(tmp_path):
    priv = _priv(tmp_path)
    att = _attempts(priv)
    work = os.path.join(priv, "ccc-update-dddddddddddd")
    P.record_attempt(att, "dddddddddddd", work, UID)
    os.mkdir(work, 0o700)
    P.sweep_stale_attempts(priv, att, UID, active_ids={"dddddddddddd"})
    assert os.path.isdir(work)         # active attempt not swept
