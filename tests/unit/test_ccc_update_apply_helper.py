"""
tests/unit/test_ccc_update_apply_helper.py
------------------------------------------
Unit tests for the privileged updater helper ``deployment/bin/ccc-update-apply``
(the Trusted Update Engine, Option B1 execution model).

The helper has no ``.py`` extension, so it is loaded with ``importlib`` /
``SourceFileLoader`` -- the same convention as
``test_ccc_restore_apply_helper.py``. Every hardcoded path constant is redirected
to a per-test tmp directory, and every privileged side-effect (``systemd-run``,
``systemctl``, ``update.sh``, ``os._exit``) is stubbed, so the engine's control
flow is exercised WITHOUT root and WITHOUT touching the host.

These tests intentionally encode ADR-0001 invariants as executable assertions:
  * Payload-as-data: the worker runs the INSTALLED ``update.sh``, never a script
    from the payload; the transient unit's ExecStart is the trusted SELF_PATH
    ``__run-worker`` -- never anything derived from the downloaded tarball.
  * Engine owns the privileged workflow: launch/validate/rollback/status live in
    the helper; the payload only ever describes a version.
  * Fail closed: empty/oversize/non-CCC payloads, non-upgrades, and tampered
    work directories are refused.
  * Defense in depth: the worker re-validates the engine-generated work dir.

The tests do NOT exercise real systemd or a real update; that is covered by
on-Pi integration validation. Here we assert the helper's logic and contracts.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import sys
import tarfile
import types
from importlib.machinery import SourceFileLoader

import pytest

# fcntl/flock + O_NOFOLLOW semantics make the helper POSIX/Linux-only.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="helper uses fcntl/flock and Linux mount-namespace semantics",
)

_HELPER = (
    pathlib.Path(__file__).resolve().parents[2] / "deployment" / "bin" / "ccc-update-apply"
)


def _load():
    loader = SourceFileLoader("ccc_update_apply", str(_HELPER))
    spec = importlib.util.spec_from_loader("ccc_update_apply", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
#  Payload / work-dir builders                                                 #
# --------------------------------------------------------------------------- #
def _tar_bytes(files: dict[str, bytes], symlinks: dict[str, str] | None = None) -> bytes:
    """Build a .tar.gz in memory. ``files`` maps arcname -> content; ``symlinks``
    maps arcname -> linkname (used to prove _safe_extract rejects links)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, link in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = link
            tar.addfile(info)
    return buf.getvalue()


def _valid_payload(version: str = "0.3.9", top: str = "ccc-src-top") -> bytes:
    return _tar_bytes({
        f"{top}/update.sh": b"#!/usr/bin/env bash\necho payload-not-executed\n",
        f"{top}/backend/_version.py": f'APP_VERSION = "{version}"\n'.encode(),
        f"{top}/README.md": b"ccc\n",
    })


def _set_stdin(monkeypatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(data)))


def _make_work(mod, version: str = "0.3.9", top: str = "ccc-src-top",
               valid_tree: bool = True) -> tuple[str, str]:
    """Create a STATE_DIR/ccc-update-XXXX/src/<top>/ tree, mimicking a post-ingest
    work dir. Returns (work_abs, tree_abs)."""
    import tempfile
    work = tempfile.mkdtemp(prefix="ccc-update-", dir=mod.STATE_DIR)
    tree = os.path.join(work, "src", top)
    os.makedirs(os.path.join(tree, "backend"), exist_ok=True)
    if valid_tree:
        with open(os.path.join(tree, "update.sh"), "w") as fh:
            fh.write("#!/usr/bin/env bash\n")
        with open(os.path.join(tree, "backend", "_version.py"), "w") as fh:
            fh.write(f'APP_VERSION = "{version}"\n')
    return work, tree


# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def mod(tmp_path, monkeypatch):
    """Load the helper and redirect all hardcoded constants to tmp paths."""
    m = _load()
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(m, "STATE_DIR", str(state))
    monkeypatch.setattr(m, "STATUS_PATH", str(state / "update-status.json"))
    monkeypatch.setattr(m, "LOCK_PATH", str(state / ".update.lock"))
    monkeypatch.setattr(m, "WORKER_LOG", str(state / "update-worker.log"))

    installed = tmp_path / "installed_version.py"
    installed.write_text('APP_VERSION = "0.3.8"\n')
    monkeypatch.setattr(m, "INSTALLED_VERSION_PY", str(installed))
    monkeypatch.setattr(m, "INSTALLED_UPDATE_SH", str(tmp_path / "update.sh"))
    monkeypatch.setattr(m, "SELF_PATH", str(tmp_path / "ccc-update-apply"))
    # Keep the documented fixed unit name + binaries (asserted, never executed).
    monkeypatch.setattr(m, "SYSTEMD_RUN", "/usr/bin/systemd-run")
    monkeypatch.setattr(m, "SYSTEMCTL", "/usr/bin/systemctl")
    return m


def _set_installed(mod, version: str) -> None:
    with open(mod.INSTALLED_VERSION_PY, "w") as fh:
        fh.write(f'APP_VERSION = "{version}"\n')


# --------------------------------------------------------------------------- #
#  Version parsing / status schema                                            #
# --------------------------------------------------------------------------- #
def test_parse_version_and_arg_roundtrip(mod):
    assert mod._parse_version('APP_VERSION = "1.2.3"') == (1, 2, 3)
    assert mod._parse_version("nothing here") is None
    assert mod._parse_ver_arg("0.3.9") == (0, 3, 9)
    assert mod._version_str((0, 3, 9)) == "0.3.9"


def test_parse_ver_arg_rejects_garbage(mod):
    with pytest.raises(SystemExit) as exc:
        mod._parse_ver_arg("v0.3.9")
    assert exc.value.code == 2


def test_write_status_schema(mod):
    mod.write_status("in_progress", id="abc123", pid=4242,
                     from_version="0.3.8", to_version="0.3.9")
    doc = json.loads(pathlib.Path(mod.STATUS_PATH).read_text())
    assert doc["schema"] == 1
    assert doc["state"] == "in_progress"
    assert doc["id"] == "abc123"
    assert doc["pid"] == 4242
    assert doc["from_version"] == "0.3.8"
    assert doc["to_version"] == "0.3.9"
    assert "updated_at" in doc


# --------------------------------------------------------------------------- #
#  Payload ingest + safe extraction                                           #
# --------------------------------------------------------------------------- #
def test_ingest_valid_payload(mod, monkeypatch):
    work, _ = _make_work(mod, valid_tree=False)
    # reuse the work dir but clear its src so ingest extracts fresh
    import shutil
    shutil.rmtree(os.path.join(work, "src"))
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))
    tree, ver = mod._ingest_payload(work)
    assert ver == (0, 3, 9)
    assert os.path.isfile(os.path.join(tree, "update.sh"))
    assert os.path.isfile(os.path.join(tree, "backend", "_version.py"))


def test_ingest_empty_payload_fails_closed(mod, monkeypatch):
    work, _ = _make_work(mod, valid_tree=False)
    _set_stdin(monkeypatch, b"")
    with pytest.raises(SystemExit) as exc:
        mod._ingest_payload(work)
    assert exc.value.code == 2


def test_ingest_oversize_payload_fails_closed(mod, monkeypatch):
    work, _ = _make_work(mod, valid_tree=False)
    monkeypatch.setattr(mod, "MAX_PAYLOAD_BYTES", 8)
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))
    with pytest.raises(SystemExit) as exc:
        mod._ingest_payload(work)
    assert exc.value.code == 2


def test_ingest_non_ccc_tree_fails_closed(mod, monkeypatch):
    work, _ = _make_work(mod, valid_tree=False)
    import shutil
    shutil.rmtree(os.path.join(work, "src"))
    _set_stdin(monkeypatch, _tar_bytes({"ccc-top/README.md": b"not ccc\n"}))
    with pytest.raises(SystemExit) as exc:
        mod._ingest_payload(work)
    assert exc.value.code == 2


def test_ingest_rejects_symlink_member(mod, monkeypatch):
    # ADR / defense-in-depth: links in the payload are refused (exit 3).
    work, _ = _make_work(mod, valid_tree=False)
    import shutil
    shutil.rmtree(os.path.join(work, "src"))
    payload = _tar_bytes(
        {"ccc-top/update.sh": b"x\n", "ccc-top/backend/_version.py": b'APP_VERSION = "0.3.9"\n'},
        symlinks={"ccc-top/evil": "/etc/passwd"},
    )
    _set_stdin(monkeypatch, payload)
    with pytest.raises(SystemExit) as exc:
        mod._ingest_payload(work)
    assert exc.value.code == 3


# --------------------------------------------------------------------------- #
#  Stale work-dir sweep (safety)                                              #
# --------------------------------------------------------------------------- #
def test_sweep_removes_only_real_workdirs(mod):
    state = mod.STATE_DIR
    # Real orphan work dirs (should be removed).
    os.makedirs(os.path.join(state, "ccc-update-a"))
    os.makedirs(os.path.join(state, "ccc-update-b", "sub"))
    # A regular file with the prefix (not a dir -> must be skipped).
    open(os.path.join(state, "ccc-update-file"), "w").close()
    # A non-matching dir (must be untouched).
    os.makedirs(os.path.join(state, "keep-me"))
    # A symlink with the prefix pointing OUTSIDE state (must not be followed).
    outside = os.path.join(state, "outside_target")
    os.makedirs(outside)
    open(os.path.join(outside, "precious"), "w").close()
    os.symlink(outside, os.path.join(state, "ccc-update-link"))

    mod._sweep_stale_workdirs()

    assert not os.path.exists(os.path.join(state, "ccc-update-a"))
    assert not os.path.exists(os.path.join(state, "ccc-update-b"))
    assert os.path.exists(os.path.join(state, "ccc-update-file"))
    assert os.path.isdir(os.path.join(state, "keep-me"))
    assert os.path.islink(os.path.join(state, "ccc-update-link"))
    assert os.path.exists(os.path.join(outside, "precious"))  # not followed


# --------------------------------------------------------------------------- #
#  _unit_busy concurrency gate                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("out,expected", [
    ("active\n", True),
    ("activating\n", True),
    ("reloading\n", True),
    ("deactivating\n", True),
    ("inactive\n", False),
    ("failed\n", False),
])
def test_unit_busy_states(mod, monkeypatch, out, expected):
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=out, returncode=0))
    assert mod._unit_busy() is expected


def test_unit_busy_oserror_is_not_busy(mod, monkeypatch):
    def _boom(*a, **k):
        raise OSError("systemctl missing")
    monkeypatch.setattr(mod.subprocess, "run", _boom)
    assert mod._unit_busy() is False


# --------------------------------------------------------------------------- #
#  Transient-unit launch (ADR: ExecStart is the trusted helper, not payload)  #
# --------------------------------------------------------------------------- #
def test_launch_builds_trusted_execstart(mod, monkeypatch):
    captured = {}

    def _fake_run(cmd, **k):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rc = mod._launch_update_unit("/var/lib/conduit-cc/ccc-update-x", (0, 3, 8), (0, 3, 9), "id123")
    assert rc == 0
    cmd = captured["cmd"]
    assert cmd[0] == mod.SYSTEMD_RUN
    assert "--collect" in cmd and "--no-block" in cmd
    assert f"--unit={mod.UPDATE_UNIT}" in cmd
    # ExecStart MUST be the trusted installed helper running its worker subcommand,
    # never a path/script derived from the downloaded payload.
    self_idx = cmd.index(mod.SELF_PATH)
    assert cmd[self_idx + 1] == "__run-worker"
    assert cmd[cmd.index("--work") + 1] == "/var/lib/conduit-cc/ccc-update-x"
    assert cmd[cmd.index("--from") + 1] == "0.3.8"
    assert cmd[cmd.index("--to") + 1] == "0.3.9"
    assert cmd[cmd.index("--id") + 1] == "id123"


def test_launch_retries_after_reset_failed(mod, monkeypatch):
    calls = []

    def _fake_run(cmd, **k):
        calls.append(cmd)
        # systemd-run always fails; reset-failed succeeds.
        if cmd[0] == mod.SYSTEMD_RUN:
            return types.SimpleNamespace(returncode=1, stdout="unit exists")
        return types.SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rc = mod._launch_update_unit("/var/lib/conduit-cc/ccc-update-x", (0, 3, 8), (0, 3, 9), "id")
    assert rc != 0
    run_attempts = [c for c in calls if c[0] == mod.SYSTEMD_RUN]
    reset_calls = [c for c in calls if c[0] == mod.SYSTEMCTL and "reset-failed" in c]
    assert len(run_attempts) == 2          # initial + one retry
    assert len(reset_calls) == 1           # reset-failed between attempts


# --------------------------------------------------------------------------- #
#  Worker (__run-worker) defensive validation                                 #
# --------------------------------------------------------------------------- #
def test_worker_rejects_workdir_outside_state(mod, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(["--work", str(outside), "--from", "0.3.8",
                            "--to", "0.3.9", "--id", "w"])
    assert exc.value.code == 3
    assert json.loads(pathlib.Path(mod.STATUS_PATH).read_text())["state"] == "failed"


def test_worker_rejects_wrong_prefix(mod):
    bad = os.path.join(mod.STATE_DIR, "notwork")
    os.makedirs(bad)
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(["--work", bad, "--from", "0.3.8", "--to", "0.3.9", "--id", "w"])
    assert exc.value.code == 3


def test_worker_rejects_missing_workdir(mod):
    ghost = os.path.join(mod.STATE_DIR, "ccc-update-ghost")
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(["--work", ghost, "--from", "0.3.8", "--to", "0.3.9", "--id", "w"])
    assert exc.value.code == 3


def test_worker_rejects_non_ccc_tree(mod):
    work, _ = _make_work(mod, valid_tree=False)
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(["--work", work, "--from", "0.3.8", "--to", "0.3.9", "--id", "w"])
    assert exc.value.code == 3


def test_worker_rejects_version_mismatch(mod):
    work, _ = _make_work(mod, version="0.3.9")
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(["--work", work, "--from", "0.3.8", "--to", "0.4.0", "--id", "w"])
    assert exc.value.code == 3


def test_worker_rejects_non_upgrade(mod):
    work, _ = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.9")  # installed == to -> not a strict increase
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(["--work", work, "--from", "0.3.8", "--to", "0.3.9", "--id", "w"])
    assert exc.value.code == 2


def test_worker_happy_path_runs_installed_update_sh(mod, monkeypatch):
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")

    seen = {}

    def _fake_run(cmd, **k):
        seen["cmd"] = cmd
        # ADR: the worker runs the INSTALLED update.sh, treating the payload tree
        # only as a --source for rsync; it never executes a payload script.
        _set_installed(mod, "0.3.9")  # simulate a successful deploy
        if k.get("stdout"):
            k["stdout"].write("update.sh ran\n")
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(["--work", work, "--from", "0.3.8", "--to", "0.3.9", "--id", "w1"])
    assert exc.value.code == 0

    cmd = seen["cmd"]
    assert cmd[0] == "bash"
    assert cmd[1] == mod.INSTALLED_UPDATE_SH
    assert "--ccc-only" in cmd and "--non-interactive" in cmd
    assert cmd[cmd.index("--source") + 1] == tree

    doc = json.loads(pathlib.Path(mod.STATUS_PATH).read_text())
    assert doc["state"] == "success"
    assert not os.path.exists(work)  # work dir cleaned up
    logtext = pathlib.Path(mod.WORKER_LOG).read_text()
    assert "ccc-update-apply worker w1" in logtext
    assert "update.sh ran" in logtext


# --------------------------------------------------------------------------- #
#  _run_update terminal-status matrix                                          #
# --------------------------------------------------------------------------- #
def test_run_update_rolled_back(mod, monkeypatch):
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")

    def _fake_run(cmd, **k):
        # update.sh fails and rolls back -> installed stays at 0.3.8 (== frm)
        return types.SimpleNamespace(returncode=1)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rc = mod._run_update(tree, (0, 3, 8), (0, 3, 9), "w2", work)
    assert rc == 0
    assert json.loads(pathlib.Path(mod.STATUS_PATH).read_text())["state"] == "rolled_back"


def test_run_update_unexpected_state(mod, monkeypatch):
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")

    def _fake_run(cmd, **k):
        _set_installed(mod, "0.3.7")  # neither frm nor to
        return types.SimpleNamespace(returncode=1)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rc = mod._run_update(tree, (0, 3, 8), (0, 3, 9), "w3", work)
    assert rc == 0
    assert json.loads(pathlib.Path(mod.STATUS_PATH).read_text())["state"] == "failed"


def test_run_update_oserror_launching_updater(mod, monkeypatch):
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")

    def _boom(cmd, **k):
        raise OSError("bash not found")
    monkeypatch.setattr(mod.subprocess, "run", _boom)

    rc = mod._run_update(tree, (0, 3, 8), (0, 3, 9), "w4", work)
    assert rc == 3
    assert json.loads(pathlib.Path(mod.STATUS_PATH).read_text())["state"] == "failed"
    assert not os.path.exists(work)


# --------------------------------------------------------------------------- #
#  apply (ingest-then-launch) control flow                                     #
# --------------------------------------------------------------------------- #
def _patch_exit(mod, monkeypatch):
    def _fake_exit(code):
        raise SystemExit(code)
    monkeypatch.setattr(mod.os, "_exit", _fake_exit)


def test_apply_refuses_when_unit_busy(mod, monkeypatch):
    monkeypatch.setattr(mod, "_unit_busy", lambda: True)
    # A sweep here would be a bug (the active run owns a work dir); trip it if called.
    monkeypatch.setattr(mod, "_sweep_stale_workdirs",
                        lambda: (_ for _ in ()).throw(AssertionError("must not sweep")))
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))
    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 2


def test_apply_refuses_non_upgrade(mod, monkeypatch):
    monkeypatch.setattr(mod, "_unit_busy", lambda: False)
    _set_installed(mod, "0.3.9")
    _set_stdin(monkeypatch, _valid_payload("0.3.7"))  # <= installed
    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 2
    # no leftover work dirs
    assert not [n for n in os.listdir(mod.STATE_DIR) if n.startswith("ccc-update-")]


def test_apply_happy_path_launches_unit(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "_unit_busy", lambda: False)
    monkeypatch.setattr(mod, "_ACTIVE_WAIT_S", 0)  # skip the post-launch poll
    _patch_exit(mod, monkeypatch)
    _set_installed(mod, "0.3.8")
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))

    launched = {}

    def _fake_launch(work, frm, to, uid):
        launched["args"] = (work, frm, to, uid)
        return 0
    monkeypatch.setattr(mod, "_launch_update_unit", _fake_launch)

    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert out.startswith("accepted ")
    work, frm, to, uid = launched["args"]
    assert frm == (0, 3, 8) and to == (0, 3, 9)
    assert os.path.basename(work).startswith("ccc-update-")
    assert os.path.isdir(work)  # not cleaned up on success (worker owns it)
    # apply itself writes no terminal status on success; the worker does.
    assert not os.path.exists(mod.STATUS_PATH)


def test_apply_marks_failed_when_launch_fails(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "_unit_busy", lambda: False)
    _patch_exit(mod, monkeypatch)
    _set_installed(mod, "0.3.8")
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))
    monkeypatch.setattr(mod, "_launch_update_unit", lambda *a, **k: 3)

    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 0  # ack already sent

    assert capsys.readouterr().out.startswith("accepted ")
    doc = json.loads(pathlib.Path(mod.STATUS_PATH).read_text())
    assert doc["state"] == "failed"
    assert not [n for n in os.listdir(mod.STATE_DIR) if n.startswith("ccc-update-")]


# --------------------------------------------------------------------------- #
#  argv dispatch                                                               #
# --------------------------------------------------------------------------- #
def test_main_dispatches_apply(mod, monkeypatch):
    called = {}
    monkeypatch.setattr(mod, "apply_cmd", lambda: called.setdefault("apply", True))
    monkeypatch.setattr(sys, "argv", ["ccc-update-apply", "apply"])
    mod.main()
    assert called.get("apply") is True


def test_main_dispatches_worker(mod, monkeypatch):
    seen = {}
    monkeypatch.setattr(mod, "run_worker_cmd", lambda argv: seen.setdefault("argv", argv))
    monkeypatch.setattr(sys, "argv", ["ccc-update-apply", "__run-worker", "--work", "x"])
    mod.main()
    assert seen["argv"] == ["--work", "x"]


@pytest.mark.parametrize("argv", [
    ["ccc-update-apply"],
    ["ccc-update-apply", "bogus"],
    ["ccc-update-apply", "apply", "extra"],
    ["ccc-update-apply", "apply", "0.3.9"],  # apply takes NO version argument
])
def test_main_rejects_bad_argv(mod, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 2
