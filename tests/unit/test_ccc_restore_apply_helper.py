# SPDX-License-Identifier: MIT
"""Unit tests for deployment/bin/ccc-restore-apply (S4B-2.1).

Loads the extension-less helper via importlib, redirects its hardcoded path
constants to tmp dirs, and stubs the backend engine + systemd/health + transient
unit handoff so
frame parsing, pre-flight, lock, checkpoint, worker branching, and the outcome
file can be exercised without root, systemd, cryptography, or a live service.
Linux also exercises the real FIFO/fork handoff; real systemd remains
device-authoritative."""
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
def mod(monkeypatch):
    # The real helper exits after main(), so its restrictive process umask has
    # no caller to contaminate. These tests invoke main() in-process; restore
    # the pytest process's ambient umask after every case.
    original_umask = os.umask(0o077)
    os.umask(original_umask)
    m = _load()
    # Epic-1: run the root-owned-state invariants against the TEST user's uid.
    monkeypatch.setattr(m, "_OWNER_UID", getattr(os, "getuid", lambda: 0)())
    try:
        yield m
    finally:
        os.umask(original_umask)


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


# --- main: usage / state-dir / lock / transient-unit handoff ----------------


def _wire_main(mod, tmp_path, monkeypatch, open_backup):
    priv = tmp_path / "priv"
    priv.mkdir(mode=0o700)
    os.chmod(str(priv), 0o700)
    attempts = priv / "attempts"
    attempts.mkdir(mode=0o700)
    os.chmod(str(attempts), 0o700)
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    os.chmod(str(pub), 0o755)
    ccc = tmp_path / "ccc"
    ccc.mkdir()
    monkeypatch.setattr(mod, "PRIVATE_DIR", str(priv))
    monkeypatch.setattr(mod, "ATTEMPTS_DIR", str(attempts))
    monkeypatch.setattr(mod, "PUBLIC_STATUS_DIR", str(pub))
    monkeypatch.setattr(mod, "OUTCOME_PATH", str(pub / "restore-status.json"))
    monkeypatch.setattr(mod, "LOCK_PATH", str(priv / "lifecycle.lock"))
    monkeypatch.setattr(mod, "CCC_DIR", str(ccc))
    monkeypatch.setattr(mod, "_unit_busy", lambda unit: False)
    state = pub    # back-compat alias for callers that inspect the outcome dir
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
    monkeypatch.setattr(mod, "PRIVATE_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "PUBLIC_STATUS_DIR", str(tmp_path / "nope2"))
    assert mod.main(argv=["apply"], stdin=io.BytesIO(_frame())) == mod.EXIT_FS


@_linux_only
def test_main_worker_preflight_failure_no_ack_no_outcome(mod, tmp_path, monkeypatch):
    state, _ = _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())

    def handoff(frame, restore_id, attempt_id, work, payload, ack, lockfd):
        os.close(lockfd)
        return mod.EXIT_PREFLIGHT, f"error {mod.EXIT_PREFLIGHT}"

    monkeypatch.setattr(mod, "_handoff_and_wait", handoff)
    rc = mod.main(argv=["apply"], stdin=io.BytesIO(_frame()))
    assert rc == mod.EXIT_PREFLIGHT
    assert not (state / "restore-status.json").exists()     # nothing changed
    assert list(pathlib.Path(mod.ATTEMPTS_DIR).iterdir()) == []


@_linux_only
def test_main_success_returns_exact_worker_ack(mod, tmp_path, monkeypatch, capsys):
    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    seen = []

    def handoff(frame, restore_id, attempt_id, work, payload, ack, lockfd):
        seen.append((frame, restore_id, attempt_id, work, payload, ack))
        os.close(lockfd)
        return mod.EXIT_OK, f"accepted {restore_id}"

    monkeypatch.setattr(mod, "_handoff_and_wait", handoff)
    rc = mod.main(argv=["apply"], stdin=io.BytesIO(_frame()))
    assert rc == mod.EXIT_OK
    assert seen and seen[0][1] == _RID
    assert b"secretpass" in seen[0][0]  # payload is handed over in memory/FIFO only
    assert capsys.readouterr().out.startswith("accepted " + _RID)


@_linux_only
def test_main_busy_lock(mod, tmp_path, monkeypatch):
    import fcntl

    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    held = os.open(str(mod.LOCK_PATH), os.O_CREAT | os.O_WRONLY, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        assert mod.main(argv=["apply"], stdin=io.BytesIO(_frame())) == mod.EXIT_BUSY
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)


@_linux_only
def test_public_main_never_imports_restore_backend(mod, tmp_path, monkeypatch):
    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    def boom():
        raise AssertionError("public handoff path must not import the restore backend")

    monkeypatch.setattr(mod, "_load_backend", boom)

    def handoff(frame, restore_id, attempt_id, work, payload, ack, lockfd):
        os.close(lockfd)
        return mod.EXIT_OK, f"accepted {restore_id}"

    monkeypatch.setattr(mod, "_handoff_and_wait", handoff)
    assert mod.main(argv=["apply"], stdin=io.BytesIO(_frame())) == mod.EXIT_OK


# --- real Linux FIFO/fork handoff ------------------------------------------


@_linux_only
def test_real_fifo_fork_handoff_keeps_secrets_out_of_argv_and_env(
        mod, tmp_path, monkeypatch):
    blob = b"real-fifo-ciphertext"
    passphrase = b"real-fifo-passphrase"
    worker_entered = tmp_path / "worker-entered"
    release_worker = tmp_path / "release-worker"
    child = {}

    def open_backup(got_blob, got_passphrase):
        assert got_blob == blob
        assert got_passphrase == passphrase
        return _Opened()

    _wire_main(mod, tmp_path, monkeypatch, open_backup)

    def fake_run_worker(opened, restore_backup, restore_id, started_utc,
                        lockfd=None):
        assert isinstance(opened, _Opened)
        worker_entered.write_text("entered", encoding="ascii")
        deadline = mod.time.monotonic() + 5
        while not release_worker.exists() and mod.time.monotonic() < deadline:
            mod.time.sleep(0.01)
        return "restored"

    monkeypatch.setattr(mod, "run_worker", fake_run_worker)
    attempt_id = "a1b2c3d4e5f6"
    work, payload_fifo, ack_fifo = mod._prepare_handoff(attempt_id)
    lockfd = mod.acquire_lock()

    def launch(got_attempt, got_restore, got_work):
        assert (got_attempt, got_restore, got_work) == (
            attempt_id, _RID, work)
        pid = os.fork()
        if pid == 0:
            # systemd starts the real worker across an exec boundary, so the
            # O_CLOEXEC launch lock is not inherited.  Mirror that boundary in
            # this fork-only harness; otherwise the child waits on its own copy
            # of the parent's flock forever.
            os.close(lockfd)
            os._exit(mod._run_worker_entry(attempt_id, _RID, work))
        child["pid"] = pid
        return 0

    monkeypatch.setattr(mod, "_launch_restore_unit", launch)
    frame = mod._frame_bytes(_RID, blob, passphrase)
    try:
        rc, ack = mod._handoff_and_wait(
            frame, _RID, attempt_id, work, payload_fifo, ack_fifo, lockfd)
        assert rc == mod.EXIT_OK
        assert ack == f"accepted {_RID}"
        deadline = mod.time.monotonic() + 2
        while not worker_entered.exists() and mod.time.monotonic() < deadline:
            mod.time.sleep(0.01)
        assert worker_entered.exists()
        proc = pathlib.Path(f"/proc/{child['pid']}")
        exposed = (
            (proc / "cmdline").read_bytes()
            + (proc / "environ").read_bytes()
        )
        assert blob not in exposed
        assert passphrase not in exposed
        assert sorted(os.listdir(work)) == ["ack.fifo", "payload.fifo"]
        assert all(
            pathlib.Path(work, name).is_fifo()
            for name in ("ack.fifo", "payload.fifo")
        )
    finally:
        release_worker.touch()
        if "pid" in child:
            _, status = os.waitpid(child["pid"], 0)
            assert os.waitstatus_to_exitcode(status) == mod.EXIT_OK
    assert not os.path.lexists(work)


@_linux_only
def test_real_fifo_malformed_ack_stops_transient_unit(
        mod, tmp_path, monkeypatch):
    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    attempt_id = "b1c2d3e4f5a6"
    work, payload_fifo, ack_fifo = mod._prepare_handoff(attempt_id)
    lockfd = mod.acquire_lock()
    stopped = []
    child = {}

    def launch(*_args):
        pid = os.fork()
        if pid == 0:
            with open(payload_fifo, "rb", buffering=0) as stream:
                while stream.read(4096):
                    pass
            with open(ack_fifo, "wb", buffering=0) as stream:
                stream.write(b"malformed acknowledgement\n")
            os._exit(0)
        child["pid"] = pid
        return 0

    monkeypatch.setattr(mod, "_launch_restore_unit", launch)
    monkeypatch.setattr(mod, "_stop_restore_unit", lambda: stopped.append(True))
    try:
        rc, ack = mod._handoff_and_wait(
            mod._frame_bytes(_RID, b"blob", b"pw"),
            _RID, attempt_id, work, payload_fifo, ack_fifo, lockfd,
        )
        assert rc == mod.EXIT_INTERNAL
        assert ack == "malformed acknowledgement"
        assert stopped == [True]
    finally:
        if "pid" in child:
            os.waitpid(child["pid"], 0)
        mod._cleanup_attempt(attempt_id, work)


@_linux_only
def test_real_fifo_timeout_stops_transient_unit(
        mod, tmp_path, monkeypatch):
    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    monkeypatch.setattr(mod, "HANDOFF_TIMEOUT_S", 0.05)
    attempt_id = "c1d2e3f4a5b6"
    work, payload_fifo, ack_fifo = mod._prepare_handoff(attempt_id)
    lockfd = mod.acquire_lock()
    stopped = []
    monkeypatch.setattr(mod, "_launch_restore_unit", lambda *_args: 0)
    monkeypatch.setattr(mod, "_stop_restore_unit", lambda: stopped.append(True))
    try:
        with pytest.raises(TimeoutError):
            mod._handoff_and_wait(
                mod._frame_bytes(_RID, b"blob", b"pw"),
                _RID, attempt_id, work, payload_fifo, ack_fifo, lockfd,
            )
        assert stopped == [True]
    finally:
        mod._cleanup_attempt(attempt_id, work)


@_linux_only
def test_real_fifo_ack_wait_timeout_stops_transient_unit(
        mod, tmp_path, monkeypatch):
    """Reach the post-payload ack deadline, not the payload-open deadline."""
    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    monkeypatch.setattr(mod, "HANDOFF_TIMEOUT_S", 0.08)
    attempt_id = "d1e2f3a4b5c6"
    work, payload_fifo, ack_fifo = mod._prepare_handoff(attempt_id)
    lockfd = mod.acquire_lock()
    stopped = []
    child = {}
    frame = mod._frame_bytes(_RID, b"blob", b"pw")
    ready_r, ready_w = os.pipe()

    def launch(*_args):
        pid = os.fork()
        if pid == 0:
            os.close(ready_r)
            payloadfd = os.open(payload_fifo, os.O_RDONLY | os.O_NONBLOCK)
            try:
                os.write(ready_w, b"1")
                os.close(ready_w)
                received = bytearray()
                deadline = mod.time.monotonic() + 2
                while len(received) < len(frame) and mod.time.monotonic() < deadline:
                    try:
                        chunk = os.read(payloadfd, len(frame) - len(received))
                    except BlockingIOError:
                        chunk = b""
                    if chunk:
                        received.extend(chunk)
                    else:
                        mod.time.sleep(0.005)
                if bytes(received) != frame:
                    os._exit(2)
                # The worker consumed the complete payload but deliberately
                # never opens/writes ack.fifo.  Parent must hit the ack deadline.
                mod.time.sleep(0.20)
                os._exit(0)
            finally:
                os.close(payloadfd)
        os.close(ready_w)
        child["pid"] = pid
        assert os.read(ready_r, 1) == b"1"
        os.close(ready_r)
        return 0

    monkeypatch.setattr(mod, "_launch_restore_unit", launch)
    monkeypatch.setattr(mod, "_stop_restore_unit", lambda: stopped.append(True))
    try:
        rc, ack = mod._handoff_and_wait(
            frame, _RID, attempt_id, work, payload_fifo, ack_fifo, lockfd)
        assert rc == mod.EXIT_INTERNAL
        assert ack == ""
        assert stopped == [True]
    finally:
        if "pid" in child:
            _, status = os.waitpid(child["pid"], 0)
            assert os.waitstatus_to_exitcode(status) == 0
        else:
            os.close(ready_r)
            os.close(ready_w)
        mod._cleanup_attempt(attempt_id, work)


@_linux_only
def test_main_preserves_attempt_while_restore_unit_owns_it(
        mod, tmp_path, monkeypatch):
    _wire_main(mod, tmp_path, monkeypatch, lambda blob, pp: _Opened())
    handed_off = {"value": False}

    def busy(unit):
        return handed_off["value"] and unit == mod.RESTORE_UNIT

    def handoff(frame, restore_id, attempt_id, work, payload, ack, lockfd):
        os.close(lockfd)
        handed_off["value"] = True
        return mod.EXIT_INTERNAL, "malformed acknowledgement"

    monkeypatch.setattr(mod, "_unit_busy", busy)
    monkeypatch.setattr(mod, "_handoff_and_wait", handoff)
    assert mod.main(argv=["apply"], stdin=io.BytesIO(_frame())) == mod.EXIT_INTERNAL
    records = list(pathlib.Path(mod.ATTEMPTS_DIR).glob("*.json"))
    workdirs = list(pathlib.Path(mod.PRIVATE_DIR).glob("ccc-restore-*"))
    assert len(records) == 1
    assert len(workdirs) == 1


@_linux_only
def test_open_fifo_validation_rejects_path_object_swap(mod, tmp_path):
    fifo = tmp_path / "handoff.fifo"
    os.mkfifo(fifo, 0o600)
    os.chmod(fifo, 0o600)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        fifo.unlink()
        os.mkfifo(fifo, 0o600)
        os.chmod(fifo, 0o600)
        with pytest.raises(OSError, match="opened restore FIFO"):
            mod._validate_open_fifo(str(fifo), fd)
    finally:
        os.close(fd)


# --- run_worker branches ----------------------------------------------------


def _wire_worker(mod, tmp_path, monkeypatch, *, restart_results, raise_in=None):
    ccc = tmp_path / "ccc"
    ccc.mkdir()
    monkeypatch.setattr(mod, "CCC_DIR", str(ccc))
    outcomes = []
    # S4B-2.6: _finish passes conduit_settings_state as a keyword; capture it as
    # the trailing tuple element so existing positional indices stay valid.
    monkeypatch.setattr(
        mod, "write_outcome",
        lambda *a, **k: outcomes.append(a + (k.get("conduit_settings_state"),)))
    # Isolate worker-branch tests from the real apply path (overridden per-test).
    monkeypatch.setattr(mod, "_apply_conduit_settings", lambda opened: ("skipped", ""))

    def _stop():
        if raise_in == "stop":
            raise RuntimeError("stop failed")

    monkeypatch.setattr(mod, "stop_service", _stop)
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)   # skip grace delay (S4B-2.5c)
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
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    os.chmod(str(pub), 0o755)
    out = pub / "restore-status.json"
    monkeypatch.setattr(mod, "OUTCOME_PATH", str(out))
    mod.write_outcome("restored", "rid-1", "t0", "t1", True, "Restore complete.")
    data = json.loads(out.read_text())
    assert data == {
        "schema": 1, "restore_id": "rid-1", "state": "restored",
        "started_utc": "t0", "finished_utc": "t1", "restart_ok": True,
        "message": "Restore complete.",
        "pid": None,                          # terminal state -> pid null (S4B-2.5c)
        "conduit_settings_state": None,       # S4B-2.6 additive, default null
    }
    raw = out.read_text()
    for secret in ("CIPHERTEXT", "secretpass", "SESSION_SECRET", "CF_API_TOKEN"):
        assert secret not in raw
    assert (out.stat().st_mode & 0o777) == 0o640


# --- S4B-2.5c: grace delay + pid in in_progress outcome ---------------------


@_linux_only
def test_write_outcome_in_progress_has_pid_terminal_null(mod, tmp_path, monkeypatch):
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    os.chmod(str(pub), 0o755)
    out = pub / "restore-status.json"
    monkeypatch.setattr(mod, "OUTCOME_PATH", str(out))
    mod.write_outcome("in_progress", "rid-1", "t0", None, None, "Restore in progress.")
    assert json.loads(out.read_text())["pid"] == os.getpid()   # schema 1, additive
    mod.write_outcome("rolled_back", "rid-1", "t0", "t1", False, "reverted")
    assert json.loads(out.read_text())["pid"] is None


def test_worker_grace_delay_before_stop(mod, tmp_path, monkeypatch):
    order = []
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch, restart_results=[True])
    restore.status = "restored"
    # Re-stub time.sleep and stop_service to record call ordering.
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: order.append("sleep"))
    monkeypatch.setattr(mod, "stop_service", lambda: order.append("stop"))
    mod.run_worker(_Opened(), restore, "rid", "t0")
    assert "sleep" in order and "stop" in order
    assert order.index("sleep") < order.index("stop")   # grace BEFORE stop


def test_service_unit_kills_the_complete_service_control_group():
    unit = (
        pathlib.Path(__file__).resolve().parents[2]
        / "deployment" / "conduit-cc.service"
    ).read_text()
    assert "KillMode=control-group" in unit
    assert "KillMode=process" not in unit


def test_restore_uses_fixed_transient_unit_and_fifo_only_secret_handoff():
    helper = _HELPER.read_text(encoding="utf-8")
    assert 'RESTORE_UNIT = "ccc-restore.service"' in helper
    assert 'SYSTEMD_RUN = "/usr/bin/systemd-run"' in helper
    assert '"__run-worker"' in helper
    assert "os.mkfifo(payload_fifo" in helper
    assert "os.mkfifo(ack_fifo" in helper
    assert "os.fork(" not in helper
    assert "os.setsid(" not in helper
    launch = helper[helper.index("def _launch_restore_unit"):
                    helper.index("def _validate_fifo")]
    assert "blob" not in launch
    assert "passphrase" not in launch
    handoff = helper[helper.index("def _handoff_and_wait"):
                     helper.index("def _worker_ack")]
    assert "except BaseException:" in handoff
    assert "_stop_restore_unit()" in handoff


def test_update_and_restore_share_one_lifecycle_mutex():
    update = (
        pathlib.Path(__file__).resolve().parents[2]
        / "deployment" / "bin" / "ccc-update-apply"
    ).read_text(encoding="utf-8")
    restore = _HELPER.read_text(encoding="utf-8")
    expected = 'LOCK_PATH = f"{PRIVATE_DIR}/lifecycle.lock"'
    assert expected in update
    assert expected in restore


# ===========================================================================
# S4B-2.6: re-apply Conduit settings on a committed restore
# ===========================================================================


# --- _hhmm_to_min -----------------------------------------------------------


def test_hhmm_to_min_ok(mod):
    assert mod._hhmm_to_min("00:00") == 0
    assert mod._hhmm_to_min("23:59") == 1439
    assert mod._hhmm_to_min("06:30") == 390


@pytest.mark.parametrize("bad", ["24:00", "12:60", "noon", "1230", "", ":", "12:", "-1:00"])
def test_hhmm_to_min_rejects_malformed(mod, bad):
    with pytest.raises(Exception):
        mod._hhmm_to_min(bad)


# --- _conduit_apply_argv (representation conversion only) --------------------


def _cfg(**over):
    base = {
        "schema": 1, "configured": True,
        "max_common_clients": 50, "bandwidth_mbps": 100, "max_personal_clients": 2,
        "reduced": {"enabled": False},
    }
    base.update(over)
    return base


def test_conduit_apply_argv_no_reduced(mod):
    assert mod._conduit_apply_argv(_cfg()) == [
        "--max-common-clients", "50",
        "--bandwidth-mbps", "100",
        "--max-personal-clients", "2",
    ]


def test_conduit_apply_argv_with_reduced(mod):
    cfg = _cfg(reduced={"enabled": True, "start": "23:00", "end": "06:00",
                        "max_common": 10, "bandwidth_mbps": 20})
    assert mod._conduit_apply_argv(cfg) == [
        "--max-common-clients", "50",
        "--bandwidth-mbps", "100",
        "--max-personal-clients", "2",
        "--reduced-start-min", "1380",      # 23*60
        "--reduced-end-min", "360",         # 6*60
        "--reduced-max-common", "10",
        "--reduced-bandwidth-mbps", "20",
    ]


# --- _apply_conduit_settings branches ---------------------------------------


class _Item:
    def __init__(self, name, data):
        self.name = name
        self.data = data


class _Staging:
    def __init__(self, items):
        self.items = items


class _OpenedCS:
    def __init__(self, items):
        self.staging = _Staging(items)


def _opened_with(payload: bytes):
    return _OpenedCS([_Item("ccc.db", b"x"),
                      _Item("conduit_settings.json", payload)])


def test_apply_cs_absent_item_skipped(mod):
    # Legacy (pre-2.6) backup: no conduit_settings.json item.
    opened = _OpenedCS([_Item("ccc.db", b"x")])
    assert mod._apply_conduit_settings(opened) == ("skipped", "")


def test_apply_cs_configured_false_skipped(mod, monkeypatch):
    called = {"v": False}
    monkeypatch.setattr(mod, "_run_apply_helper",
                        lambda argv: called.__setitem__("v", True) or 0)
    opened = _opened_with(b'{"schema": 1, "configured": false}')
    assert mod._apply_conduit_settings(opened) == ("skipped", "")
    assert called["v"] is False               # helper never invoked


def test_apply_cs_applied_on_helper_success(mod, monkeypatch):
    seen = {}
    monkeypatch.setattr(mod, "_run_apply_helper",
                        lambda argv: seen.__setitem__("argv", argv) or 0)
    payload = json.dumps(_cfg()).encode()
    state, note = mod._apply_conduit_settings(_opened_with(payload))
    assert state == "applied" and note.strip()
    assert seen["argv"][:2] == ["--max-common-clients", "50"]


def test_apply_cs_failed_on_helper_nonzero(mod, monkeypatch):
    monkeypatch.setattr(mod, "_run_apply_helper", lambda argv: 3)
    assert mod._apply_conduit_settings(_opened_with(json.dumps(_cfg()).encode()))[0] == "failed"


def test_apply_cs_failed_on_malformed_json(mod, monkeypatch):
    called = {"v": False}
    monkeypatch.setattr(mod, "_run_apply_helper",
                        lambda argv: called.__setitem__("v", True) or 0)
    assert mod._apply_conduit_settings(_opened_with(b"{ not json"))[0] == "failed"
    assert called["v"] is False               # never reaches the helper


def test_apply_cs_failed_on_unrecognised_schema(mod, monkeypatch):
    monkeypatch.setattr(mod, "_run_apply_helper", lambda argv: 0)
    opened = _opened_with(b'{"schema": 99, "configured": true}')
    assert mod._apply_conduit_settings(opened)[0] == "failed"


def test_apply_cs_failed_on_bad_reduced_time(mod, monkeypatch):
    called = {"v": False}
    monkeypatch.setattr(mod, "_run_apply_helper",
                        lambda argv: called.__setitem__("v", True) or 0)
    cfg = _cfg(reduced={"enabled": True, "start": "bad", "end": "06:00",
                        "max_common": 10, "bandwidth_mbps": 20})
    assert mod._apply_conduit_settings(_opened_with(json.dumps(cfg).encode()))[0] == "failed"
    assert called["v"] is False               # conversion failed before the helper


def test_apply_cs_failed_on_helper_exception(mod, monkeypatch):
    def boom(argv):
        raise OSError("helper missing")

    monkeypatch.setattr(mod, "_run_apply_helper", boom)
    assert mod._apply_conduit_settings(_opened_with(json.dumps(_cfg()).encode()))[0] == "failed"


# --- run_worker threads conduit_settings_state ------------------------------


def test_worker_restored_threads_conduit_settings_state(mod, tmp_path, monkeypatch):
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch, restart_results=[True])
    restore.status = "restored"
    monkeypatch.setattr(mod, "_apply_conduit_settings", lambda opened: ("applied", " note"))
    assert mod.run_worker(_Opened(), restore, "rid", "t0") == "restored"
    assert outcomes[-1][0] == "restored"
    assert outcomes[-1][-1] == "applied"          # threaded into write_outcome


def test_worker_non_restored_records_skipped(mod, tmp_path, monkeypatch):
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch, restart_results=[True])
    restore.status = "rolled_back"
    # Settings must NOT be applied on a non-committed restore.
    monkeypatch.setattr(mod, "_apply_conduit_settings",
                        lambda opened: pytest.fail("must not apply on non-restored"))
    assert mod.run_worker(_Opened(), restore, "rid", "t0") == "rolled_back"
    assert outcomes[-1][-1] == "skipped"


def test_worker_unhealthy_restore_records_skipped(mod, tmp_path, monkeypatch):
    # restored-but-unhealthy -> rollback; settings never applied.
    outcomes, counters, restore = _wire_worker(mod, tmp_path, monkeypatch,
                                               restart_results=[False, True])
    restore.status = "restored"
    monkeypatch.setattr(mod, "_apply_conduit_settings",
                        lambda opened: pytest.fail("must not apply when unhealthy"))
    assert mod.run_worker(_Opened(), restore, "rid", "t0") == "rolled_back"
    assert outcomes[-1][-1] == "skipped"


# --- write_outcome carries the field ----------------------------------------


@_linux_only
def test_write_outcome_carries_conduit_settings_state(mod, tmp_path, monkeypatch):
    pub = tmp_path / "pub"
    pub.mkdir(mode=0o755)
    os.chmod(str(pub), 0o755)
    out = pub / "restore-status.json"
    monkeypatch.setattr(mod, "OUTCOME_PATH", str(out))
    mod.write_outcome("restored", "rid", "t0", "t1", True, "done",
                      conduit_settings_state="applied")
    assert json.loads(out.read_text())["conduit_settings_state"] == "applied"
    mod.write_outcome("restored", "rid", "t0", "t1", True, "done")   # default null
    assert json.loads(out.read_text())["conduit_settings_state"] is None
