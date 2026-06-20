# SPDX-License-Identifier: MIT
"""Unit tests for deployment/bin/ccc-restore-apply (S4B-2.1).

Loads the extension-less helper via importlib, redirects its hardcoded path
constants to tmp dirs, and stubs the backend engine + systemd/health + detach so
frame parsing, pre-flight, lock, checkpoint, worker branching, and the outcome
file can be exercised on Linux without root, systemd, cryptography, or a real
fork. The real double-fork + service stop/start is Pi-only (S4B-2.5)."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

_linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="POSIX fcntl/flock; helper is Linux-only"
)

_HELPER = (
    pathlib.Path(__file__).resolve().parents[2] / "deployment" / "bin" / "ccc-restore-apply"
)


def _load():
    loader = SourceFileLoader("ccc_restore_apply", str(_HELPER))
    spec = importlib.util.spec_from_loader("ccc_restore_apply", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


# --- dummy engine + helpers -------------------------------------------------


class _CryptoErr(Exception):
    pass


class _ArchiveErr(Exception):
    pass


class _KeyErr(Exception):
    pass


class _Result:
    def __init__(self, status):
        self.status = status


class _Opened:
    pass


_RID = "11111111-2222-3333-4444-555555555555"


def _frame(restore_id=_RID, blob=b"CIPHERTEXT", passphrase=b"secretpass"):
    head = (
        b"CCC-RESTORE/1\n"
        b"restore_id: " + restore_id.encode() + b"\n"
        b"blob_len: " + str(len(blob)).encode() + b"\n"
        b"passphrase_len: " + str(len(passphrase)).encode() + b"\n\n"
    )
    return head + blob + passphrase


# --- parse_frame ------------------------------------------------------------


def test_parse_frame_ok(mod):
    rid, blob, pp = mod.parse_frame(io.BytesIO(_frame(blob=b"abc", passphrase=b"pw12")))
    assert rid == _RID and blob == b"abc" and pp == b"pw12"


def test_parse_frame_bad_magic(mod):
    with pytest.raises(mod.FrameError):
        mod.parse_frame(io.BytesIO(b"NOPE/1\nrestore_id: x\n\n"))


def test_parse_frame_missing_fields(mod):
    with pytest.raises(mod.FrameError):
        mod.parse_frame(io.BytesIO(b"CCC-RESTORE/1\nblob_len: 3\n\nabc"))


def test_parse_frame_bad_restore_id(mod):
    raw = (b"CCC-RESTORE/1\nrestore_id: bad id!\nblob_len: 3\npassphrase_len: 2\n\nabcpp")
    with pytest.raises(mod.FrameError):
        mod.parse_frame(io.BytesIO(raw))


def test_parse_frame_oversize(mod, monkeypatch):
    monkeypatch.setattr(mod, "MAX_BLOB_BYTES", 4)
    with pytest.raises(mod.FrameError):
        mod.parse_frame(io.BytesIO(_frame(blob=b"toolong")))


def test_parse_frame_short_read(mod):
    raw = (
        b"CCC-RESTORE/1\nrestore_id: " + _RID.encode() +
        b"\nblob_len: 10\npassphrase_len: 2\n\nshort"
    )
    with pytest.raises(mod.FrameError):
        mod.parse_frame(io.BytesIO(raw))


# --- main: usage / state-dir / pre-flight / lock / backend ------------------


def _wire_main(mod, tmp_path, monkeypatch, open_backup):
    state = tmp_path / "state"
    state.mkdir()
    ccc = tmp_path / "ccc"
    ccc.mkdir()
    monkeypatch.setattr(mod, "STATE_DIR", str(state))
    monkeypatch.setattr(mod, "OUTCOME_PATH", str(state / "restore-status.json"))
    monkeypatch.setattr(mod, "LOCK_PATH", str(state / ".restore.lock"))
    monkeypatch.setattr(mod, "CCC_DIR", str(ccc))
    monkeypatch.setattr(
        mod, "_load_backend",
        lambda: (open_backup, lambda opened, ccc_dir: _Result("restored"),
                 (_CryptoErr, _ArchiveErr, _KeyErr)),
    )
    return state, ccc


def test_main_usage(mod):
    assert mod.main(argv=["bogus"], stdin=io.BytesIO(b"")) == mod.EXIT_USAGE


@_linux_only
def test_main_state_dir_missing(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "STATE_DIR", str(tmp_path / "nope"))
    assert mod.main(argv=["apply"], stdin=io.BytesIO(_frame())) == mod.EXIT_FS


@_linux_only
def test_main_preflight_failure_no_detach_no_outcome(mod, tmp_path, monkeypatch):
    def bad(blob, pp):
        raise _CryptoErr("wrong passphrase")

    state, _ = _wire_main(mod, tmp_path, monkeypatch, bad)
    detached = []
    monkeypatch.setattr(mod, "_detach_and_run", lambda *a: detached.append(a))
    rc = mod.main(argv=["apply"], stdin=io.BytesIO(_frame()))
    assert rc == mod.EXIT_PREFLIGHT
    assert detached == []                                   # never detached
    assert not (state / "restore-status.json").exists()     # nothing changed


@_linux_only
def test_main_preflight_success_acks_and_detaches(mod, tmp_path, monkeypatch, capsys):
    sentinel = _Opened()
    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: sentinel)
    seen = []
    monkeypatch.setattr(mod, "_detach_and_run",
                        lambda opened, rb, rid, started, fd: seen.append((opened, rid)))
    rc = mod.main(argv=["apply"], stdin=io.BytesIO(_frame()))
    assert rc == mod.EXIT_OK
    assert seen and seen[0][0] is sentinel and seen[0][1] == _RID
    assert capsys.readouterr().out.startswith("accepted " + _RID)


@_linux_only
def test_main_busy_lock(mod, tmp_path, monkeypatch):
    import fcntl

    state, _ = _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    monkeypatch.setattr(mod, "_detach_and_run", lambda *a: None)
    held = os.open(str(state / ".restore.lock"), os.O_CREAT | os.O_WRONLY, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        assert mod.main(argv=["apply"], stdin=io.BytesIO(_frame())) == mod.EXIT_BUSY
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)


@_linux_only
def test_main_backend_unavailable(mod, tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(mod, "STATE_DIR", str(state))
    monkeypatch.setattr(mod, "LOCK_PATH", str(state / ".restore.lock"))

    def boom():
        raise ImportError("no backend")

    monkeypatch.setattr(mod, "_load_backend", boom)
    assert mod.main(argv=["apply"], stdin=io.BytesIO(_frame())) == mod.EXIT_INTERNAL


# --- run_worker branches ----------------------------------------------------


def _wire_worker(mod, tmp_path, monkeypatch, *, restart_results, raise_in=None):
    ccc = tmp_path / "ccc"
    ccc.mkdir()
    monkeypatch.setattr(mod, "CCC_DIR", str(ccc))
    outcomes = []
    monkeypatch.setattr(mod, "write_outcome", lambda *a: outcomes.append(a))

    def _stop():
        if raise_in == "stop":
            raise RuntimeError("stop failed")

    monkeypatch.setattr(mod, "stop_service", _stop)
    monkeypatch.setattr(mod, "make_checkpoint", lambda d: ("CKPT", {"ccc.db": "x"}))
    monkeypatch.setattr(mod, "_cleanup_checkpoint", lambda c: None)
    counters = {"restore_checkpoint": 0}
    monkeypatch.setattr(mod, "restore_checkpoint",
                        lambda d, cap: counters.__setitem__("restore_checkpoint",
                                                             counters["restore_checkpoint"] + 1))
    seq = iter(restart_results)
    monkeypatch.setattr(mod, "restart_healthy", lambda: next(seq))

    def _restore(opened, ccc_dir):
        if raise_in == "restore":
            raise RuntimeError("restore boom")
        return _Result(_restore.status)

    return outcomes, counters, _restore


def test_worker_restored_success(mod, tmp_path, monkeypatch):
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch, restart_results=[True])
    restore.status = "restored"
    assert mod.run_worker(_Opened(), restore, "rid", "t0", lockfd=None) == "restored"
    assert counters["restore_checkpoint"] == 0
    assert outcomes[-1][0] == "restored" and outcomes[-1][4] is True   # state, restart_ok


def test_worker_restored_but_unhealthy_rolls_back(mod, tmp_path, monkeypatch):
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch,
                                               restart_results=[False, True])
    restore.status = "restored"
    assert mod.run_worker(_Opened(), restore, "rid", "t0") == "rolled_back"
    assert counters["restore_checkpoint"] == 1
    assert outcomes[-1][0] == "rolled_back"


def test_worker_s3_rolled_back_passthrough(mod, tmp_path, monkeypatch):
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch, restart_results=[True])
    restore.status = "rolled_back"
    assert mod.run_worker(_Opened(), restore, "rid", "t0") == "rolled_back"
    assert counters["restore_checkpoint"] == 0       # S3 already handled it


def test_worker_exception_recovers(mod, tmp_path, monkeypatch):
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch,
                                               restart_results=[True], raise_in="restore")
    restore.status = "restored"
    assert mod.run_worker(_Opened(), restore, "rid", "t0") == "rolled_back"
    assert outcomes[-1][0] == "rolled_back"


def test_worker_exception_rollback_failed(mod, tmp_path, monkeypatch):
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch,
                                               restart_results=[False], raise_in="restore")
    restore.status = "restored"
    assert mod.run_worker(_Opened(), restore, "rid", "t0") == "rollback_failed"
    assert outcomes[-1][0] == "rollback_failed"


# --- outcome file -----------------------------------------------------------


@_linux_only
def test_write_outcome_schema_modes_and_no_secrets(mod, tmp_path, monkeypatch):
    out = tmp_path / "restore-status.json"
    monkeypatch.setattr(mod, "OUTCOME_PATH", str(out))
    mod.write_outcome("restored", "rid-1", "t0", "t1", True, "Restore complete.")
    data = json.loads(out.read_text())
    assert data == {
        "schema": 1, "restore_id": "rid-1", "state": "restored",
        "started_utc": "t0", "finished_utc": "t1", "restart_ok": True,
        "message": "Restore complete.",
    }
    raw = out.read_text()
    for secret in ("CIPHERTEXT", "secretpass", "SESSION_SECRET", "CF_API_TOKEN"):
        assert secret not in raw
    assert (out.stat().st_mode & 0o777) == 0o640
