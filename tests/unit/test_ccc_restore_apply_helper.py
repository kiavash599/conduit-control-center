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
    out = tmp_path / "restore-status.json"
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
    out = tmp_path / "restore-status.json"
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


def test_service_unit_has_killmode_process():
    unit = (
        pathlib.Path(__file__).resolve().parents[2]
        / "deployment" / "conduit-cc.service"
    ).read_text()
    assert "KillMode=process" in unit


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
    out = tmp_path / "restore-status.json"
    monkeypatch.setattr(mod, "OUTCOME_PATH", str(out))
    mod.write_outcome("restored", "rid", "t0", "t1", True, "done",
                      conduit_settings_state="applied")
    assert json.loads(out.read_text())["conduit_settings_state"] == "applied"
    mod.write_outcome("restored", "rid", "t0", "t1", True, "done")   # default null
    assert json.loads(out.read_text())["conduit_settings_state"] is None
