"""
tests/unit/test_api_update.py
-----------------------------
Unit tests for the one-click update API (``backend/api/update.py``):
GET /api/update/check, POST /api/update/install, GET /api/update/status.

Design:
  * The async route handlers are called DIRECTLY (the ``_user`` / ``_csrf``
    Depends parameters are passed as plain args, so FastAPI's dependency
    injection / auth is bypassed -- we are unit-testing handler logic, not the
    auth layer, which has its own tests).
  * ALL network access is stubbed: ``_fetch_latest`` and ``_gh_download`` are
    monkeypatched; no socket is ever opened.
  * The privileged helper is stubbed: ``_invoke_helper`` is monkeypatched, so
    ``ccc-update-apply`` / sudo / systemd are never executed.
  * State files (cache, status) are redirected to a tmp dir.

Regression focus (per Batch 2 scope): a STALE ``in_progress`` status whose
worker is gone (dead pid, or a live pid that is NOT the update worker) must be
reconciled to ``unknown`` -- the API must not report a misleading active update,
and a dead-worker status must not block a fresh install.

ADR-0001: these tests assert the *Policy* layer only resolves the latest STABLE
release server-side and hands a tarball to the trusted helper as data; they never
assert the API drives privileged control flow itself.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time

import pytest

# os.fork / os.kill / /proc/<pid>/cmdline reconciliation is POSIX/Linux-only.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="pid reconciliation uses os.fork/os.kill and /proc",
)

fastapi = pytest.importorskip("fastapi", reason="backend deps (fastapi) required")
from fastapi import HTTPException  # noqa: E402

from backend._version import APP_VERSION  # noqa: E402
from backend.api import update as upd  # noqa: E402

# A version strictly greater than the installed APP_VERSION, for upgrade paths.
_NEWER = "99.0.0"
_OLDER = "0.0.1"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect all state-file constants to a tmp dir."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(upd, "_STATE_DIR", str(state))
    monkeypatch.setattr(upd, "_CHECK_CACHE", str(state / "update-check.json"))
    monkeypatch.setattr(upd, "_STATUS_PATH", str(state / "update-status.json"))
    monkeypatch.setattr(upd, "_INSTALLED_CORE", str(tmp_path / "core-version"))
    return state


def _write_cache(env, **over):
    doc = {
        "checked_at_epoch": time.time(),
        "checked_at": "2026-06-30T00:00:00Z",
        "latest": _NEWER,
        "tag": f"v{_NEWER}",
        "tarball_url": "https://codeload.github.com/x/y/tar.gz",
        "html_url": "https://github.com/x/y/releases/tag/v" + _NEWER,
        "published_at": "2026-06-30T00:00:00Z",
        "notes_preview": ["note one", "note two"],
        "recommended_core": None,
    }
    doc.update(over)
    pathlib.Path(env / "update-check.json").write_text(json.dumps(doc))
    return doc


def _write_status(env, **fields):
    doc = {"schema": 1, "updated_at": "2026-06-30T00:00:00Z"}
    doc.update(fields)
    pathlib.Path(env / "update-status.json").write_text(json.dumps(doc))


# --------------------------------------------------------------------------- #
#  Pure helpers                                                                #
# --------------------------------------------------------------------------- #
def test_semver_parsing():
    assert upd._semver("v1.2.3") == (1, 2, 3)
    assert upd._semver("1.2.3") == (1, 2, 3)
    assert upd._semver("1.2") is None
    assert upd._semver(None) is None


def test_sanitize_notes_strips_html_and_markers():
    notes = upd._sanitize_notes("# Heading\n- bullet <b>x</b>\n\n* `code` item")
    assert notes == ["Heading", "bullet x", "code item"]
    assert all("<" not in n for n in notes)


def test_present_core_warning_and_update_available():
    doc = {"latest": _NEWER, "recommended_core": "2.0.0", "notes_preview": []}
    out = upd._present(doc, installed_core="1.0.0", reachable=True)
    assert out["update_available"] is True
    assert out["core_warning"] is True
    assert out["current"] == APP_VERSION
    assert out["reachable"] is True


# --------------------------------------------------------------------------- #
#  Worker liveness reconciliation (regression core)                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pid", [0, -1, True, "x", None, 1.5])
def test_worker_alive_rejects_non_positive_or_nonint(pid):
    assert upd._update_worker_alive(pid) is False


def test_worker_alive_false_for_dead_pid():
    pid = os.fork()
    if pid == 0:  # child
        os._exit(0)
    os.waitpid(pid, 0)  # reap -> pid is now dead
    assert upd._update_worker_alive(pid) is False


def test_worker_alive_false_for_live_nonworker_pid():
    # Our own process is alive but its cmdline is NOT ccc-update-apply, so it
    # must NOT be mistaken for a running update worker.
    assert upd._update_worker_alive(os.getpid()) is False


# --------------------------------------------------------------------------- #
#  _read_status                                                                #
# --------------------------------------------------------------------------- #
def test_status_idle_when_no_file(env):
    out = upd._read_status()
    assert out["state"] == "idle"


def test_status_malformed_is_unknown(env):
    pathlib.Path(env / "update-status.json").write_text("{not json")
    assert upd._read_status()["state"] == "unknown"


def test_status_terminal_passthrough(env):
    _write_status(env, state="success", id="abc", from_version="0.3.8",
                  to_version=_NEWER, message="done")
    out = upd._read_status()
    assert out["state"] == "success"
    assert out["id"] == "abc"


def test_status_in_progress_preserved_when_worker_alive(env, monkeypatch):
    _write_status(env, state="in_progress", id="x", pid=4242)
    monkeypatch.setattr(upd, "_update_worker_alive", lambda pid: True)
    assert upd._read_status()["state"] == "in_progress"


def test_status_stale_in_progress_reconciled_to_unknown(env, monkeypatch):
    # REGRESSION: in_progress + dead worker -> unknown, not a misleading active.
    _write_status(env, state="in_progress", id="x", pid=999999)
    monkeypatch.setattr(upd, "_update_worker_alive", lambda pid: False)
    out = upd._read_status()
    assert out["state"] == "unknown"
    assert "did not complete" in out["message"]


def test_status_stale_in_progress_via_real_dead_pid(env):
    # End-to-end through the real liveness check (no monkeypatch).
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    _write_status(env, state="in_progress", id="x", pid=pid)
    assert upd._read_status()["state"] == "unknown"


# --------------------------------------------------------------------------- #
#  GET /check                                                                  #
# --------------------------------------------------------------------------- #
def test_check_uses_fresh_cache_without_network(env, monkeypatch):
    _write_cache(env)
    # If _fetch_latest is called the test fails: fresh cache must short-circuit.
    monkeypatch.setattr(upd, "_fetch_latest",
                        lambda: (_ for _ in ()).throw(AssertionError("network used")))
    out = _run(upd.check(force=False, _user=None))
    assert out["latest"] == _NEWER
    assert out["update_available"] is True
    assert out["reachable"] is True


def test_check_force_refetches(env, monkeypatch):
    _write_cache(env, latest=_OLDER)  # stale-ish content, but force ignores it
    fetched = {
        "checked_at_epoch": time.time(), "checked_at": "2026-06-30T01:00:00Z",
        "latest": _NEWER, "tag": f"v{_NEWER}",
        "tarball_url": "https://codeload.github.com/x/y/tar.gz",
        "html_url": "h", "published_at": "p", "notes_preview": [], "recommended_core": None,
    }
    monkeypatch.setattr(upd, "_fetch_latest", lambda: fetched)
    out = _run(upd.check(force=True, _user=None))
    assert out["latest"] == _NEWER
    assert out["reachable"] is True


def test_check_falls_back_to_cache_when_unreachable(env, monkeypatch):
    _write_cache(env, checked_at_epoch=0)  # stale -> triggers fetch
    monkeypatch.setattr(upd, "_fetch_latest",
                        lambda: (_ for _ in ()).throw(RuntimeError("github down")))
    out = _run(upd.check(force=False, _user=None))
    assert out["latest"] == _NEWER
    assert out["reachable"] is False  # served from cache, network failed


def test_check_no_cache_and_unreachable(env, monkeypatch):
    monkeypatch.setattr(upd, "_fetch_latest",
                        lambda: (_ for _ in ()).throw(RuntimeError("github down")))
    out = _run(upd.check(force=False, _user=None))
    assert out["latest"] is None
    assert out["reachable"] is False
    assert out["update_available"] is False


# --------------------------------------------------------------------------- #
#  POST /install                                                               #
# --------------------------------------------------------------------------- #
def _install(version):
    return upd.install(upd.InstallRequest(version=version), _user=None, _csrf=None)


def test_install_409_when_no_cache(env):
    with pytest.raises(HTTPException) as exc:
        _run(_install(_NEWER))
    assert exc.value.status_code == 409


def test_install_409_on_version_mismatch(env):
    _write_cache(env, latest=_NEWER)
    with pytest.raises(HTTPException) as exc:
        _run(_install("1.2.3"))  # != cached latest
    assert exc.value.status_code == 409


def test_install_409_when_not_newer(env):
    _write_cache(env, latest=APP_VERSION)  # latest == installed
    with pytest.raises(HTTPException) as exc:
        _run(_install(APP_VERSION))
    assert exc.value.status_code == 409


def test_install_409_when_update_in_progress(env, monkeypatch):
    _write_cache(env, latest=_NEWER)
    _write_status(env, state="in_progress", id="x", pid=4242)
    monkeypatch.setattr(upd, "_update_worker_alive", lambda pid: True)
    with pytest.raises(HTTPException) as exc:
        _run(_install(_NEWER))
    assert exc.value.status_code == 409
    assert "in progress" in exc.value.detail


def test_install_502_when_download_fails(env, monkeypatch):
    _write_cache(env, latest=_NEWER)
    monkeypatch.setattr(upd, "_gh_download",
                        lambda url: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(HTTPException) as exc:
        _run(_install(_NEWER))
    assert exc.value.status_code == 502


def test_install_no_server_gzip_precheck_forwards_to_helper(env, monkeypatch):
    # ADR-0003 trust boundary: the server performs NO structural/gzip pre-check on
    # the untrusted downloaded artifact. A non-gzip artifact is framed and handed to
    # the privileged helper, which verifies the signature and rejects it (-> 409).
    # (Replaces the removed server-side gzip-502 pre-check; without mocking the
    # helper the old test reached real sudo.)
    _write_cache(env, latest=_NEWER)
    monkeypatch.setattr(upd, "_gh_download", lambda url: b"PK\x03\x04not-gzip")
    seen = {}

    def _fake_helper(payload):
        seen["payload"] = payload
        return ("exit", upd._EXIT_VALIDATION)
    monkeypatch.setattr(upd, "_invoke_helper", _fake_helper)
    with pytest.raises(HTTPException) as exc:
        _run(_install(_NEWER))
    assert exc.value.status_code == 409          # helper rejected; NOT a server-side 502
    assert seen["payload"].startswith(upd._FRAME_MAGIC)  # forwarded as a framed payload


def test_install_happy_path_returns_accepted(env, monkeypatch):
    _write_cache(env, latest=_NEWER)
    monkeypatch.setattr(upd, "_gh_download", lambda url: b"\x1f\x8b" + b"\x00" * 32)
    monkeypatch.setattr(upd, "_invoke_helper", lambda tb: ("ack", "accepted deadbeef00"))
    out = _run(_install(_NEWER))
    assert out["status"] == "accepted"
    assert out["id"] == "deadbeef00"
    assert out["from_version"] == APP_VERSION
    assert out["to_version"] == _NEWER


def test_install_not_blocked_by_dead_worker_status(env, monkeypatch):
    # REGRESSION: a stale in_progress whose worker is dead must NOT block a new
    # install (the status is reconciled to unknown before the in_progress gate).
    _write_cache(env, latest=_NEWER)
    _write_status(env, state="in_progress", id="old", pid=999999)
    monkeypatch.setattr(upd, "_update_worker_alive", lambda pid: False)
    monkeypatch.setattr(upd, "_gh_download", lambda url: b"\x1f\x8b" + b"\x00" * 32)
    monkeypatch.setattr(upd, "_invoke_helper", lambda tb: ("ack", "accepted feed0000beef"))
    out = _run(_install(_NEWER))
    assert out["status"] == "accepted"


@pytest.mark.parametrize("kind,info,code", [
    ("exit", 3, 503),          # _EXIT_FS         -> not available on this server
    ("exit", 2, 409),          # _EXIT_VALIDATION -> already running / invalid
    ("timeout", None, 500),
    ("mismatch", "garbage", 500),
])
def test_install_helper_failures_map_to_http(env, monkeypatch, kind, info, code):
    assert (upd._EXIT_FS, upd._EXIT_VALIDATION) == (3, 2)  # guard the mapping
    _write_cache(env, latest=_NEWER)
    monkeypatch.setattr(upd, "_gh_download", lambda url: b"\x1f\x8b" + b"\x00" * 32)
    monkeypatch.setattr(upd, "_invoke_helper", lambda tb: (kind, info))
    with pytest.raises(HTTPException) as exc:
        _run(_install(_NEWER))
    assert exc.value.status_code == code


# --------------------------------------------------------------------------- #
#  GET /status                                                                 #
# --------------------------------------------------------------------------- #
def test_get_status_route_reconciles_stale(env):
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    _write_status(env, state="in_progress", id="x", pid=pid)
    out = _run(upd.get_status(_user=None))
    assert out["state"] == "unknown"
