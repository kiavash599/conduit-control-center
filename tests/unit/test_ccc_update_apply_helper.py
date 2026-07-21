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

_SOURCE_COMMIT = "a" * 40
_SOURCE_TAG = "v0.3.9"


def _worker_args(work, *, frm="0.3.8", to="0.3.9", attempt_id=None,
                 commit=_SOURCE_COMMIT, tag=_SOURCE_TAG):
    return ["--work", str(work), "--from", frm, "--to", to,
            "--id", attempt_id or _WID,
            "--source-commit", commit, "--source-tag", tag]

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


def _stub_verify_boundary(mod, monkeypatch):
    """ADR-0003 payload framing + signature verification is covered by
    test_update_verify.py. Bypass ONLY that gate here (a plain tar arrives on
    stdin) so these tests exercise the helper's post-verify extraction,
    version-gate and launch logic. Production framing/verification is unchanged."""
    monkeypatch.setattr(mod, "_VERIFY_AVAILABLE", True)

    def _framed(work):
        data = sys.stdin.buffer.read()
        art = os.path.join(work, "payload.tar.gz")
        with open(art, "wb") as fh:
            fh.write(data)
        for _n in ("manifest.json", "manifest.json.sig"):
            open(os.path.join(work, _n), "wb").close()
        return (os.path.join(work, "manifest.json"),
                os.path.join(work, "manifest.json.sig"), art)

    monkeypatch.setattr(mod, "_read_framed_payload", _framed)
    monkeypatch.setattr(
        mod, "verify_release",
        lambda **k: types.SimpleNamespace(
             ok=True, reason="accepted",
             metadata={"version": "0.3.9", "signing_principal": "test-signer",
                       "product": "ccc",
                       "source": {"vcs": "git", "commit": _SOURCE_COMMIT,
                                  "tag": _SOURCE_TAG},
                      # the signed top-level allowlist the payloads use in these tests
                      "top_level": ["ccc-src-top", "ccc-top"]}))
    monkeypatch.setattr(mod, "product_scope_ok", lambda meta: True)
    monkeypatch.setattr(mod, "cross_check_version", lambda meta, vs: True)


_WID = "abcdefabcdef"        # canonical 12-hex attempt id used by worker tests


def _make_work(mod, version: str = "0.3.9", top: str = "ccc-src-top",
               valid_tree: bool = True, attempt_id: str = _WID,
               record: bool = True) -> tuple[str, str]:
    """Create a RECORDED PRIVATE_DIR/ccc-update-<id>/src/<top>/ tree, mimicking a
    post-ingest work dir (A2: worker deletion is record-authorized, so the
    fixture creates the real ownership record too). Returns (work_abs, tree_abs)."""
    from backend import priv_state as P
    work = os.path.join(mod.PRIVATE_DIR, f"ccc-update-{attempt_id}")
    if record:
        P.record_attempt(
            mod.ATTEMPTS_DIR, attempt_id, work, os.getuid(), kind="update")
    os.makedirs(work, mode=0o700)
    os.chmod(work, 0o700)
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
    """Load the helper and redirect all hardcoded constants to tmp paths.

    Epic-1: the state boundary is two directories -- a root-only PRIVATE dir
    (locks/work/log/records) and a service-readable PUBLIC status dir. Tests
    inject their own uid as the expected owner so the invariants run unprivileged
    while exercising the exact production checks."""
    m = _load()
    private = tmp_path / "priv"
    private.mkdir(mode=0o700)
    os.chmod(str(private), 0o700)
    attempts = private / "attempts"
    attempts.mkdir(mode=0o700)
    os.chmod(str(attempts), 0o700)
    public = tmp_path / "pub"
    public.mkdir(mode=0o755)
    os.chmod(str(public), 0o755)
    monkeypatch.setattr(m, "PRIVATE_DIR", str(private))
    monkeypatch.setattr(m, "ATTEMPTS_DIR", str(attempts))
    monkeypatch.setattr(m, "PUBLIC_STATUS_DIR", str(public))
    monkeypatch.setattr(m, "STATUS_PATH", str(public / "update-status.json"))
    monkeypatch.setattr(m, "LOCK_PATH", str(private / "update.lock"))
    monkeypatch.setattr(m, "WORKER_LOG", str(private / "update-worker.log"))
    monkeypatch.setattr(m, "_OWNER_UID", os.getuid())

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
    _stub_verify_boundary(mod, monkeypatch)
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
    _stub_verify_boundary(mod, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        mod._ingest_payload(work)
    assert exc.value.code == 3


# --------------------------------------------------------------------------- #
#  Stale work-dir sweep (safety)                                              #
# --------------------------------------------------------------------------- #
def test_sweep_removes_only_recorded_workdirs(mod):
    """Epic-1: sweep authority is the ownership RECORD, never a name prefix.
    A recorded orphan is removed; an unrecorded prefix-matching dir survives."""
    from backend import priv_state as P
    uid = os.getuid()
    priv, att = mod.PRIVATE_DIR, mod.ATTEMPTS_DIR
    # Recorded orphan (should be removed).
    recorded = os.path.join(priv, "ccc-update-aaaaaaaaaaaa")
    P.record_attempt(att, "aaaaaaaaaaaa", recorded, uid)
    os.makedirs(recorded)
    # UNRECORDED dir with the very same prefix (must survive -- no prefix authority).
    foreign = os.path.join(priv, "ccc-update-bbbbbbbbbbbb")
    os.makedirs(foreign)
    # A non-matching dir (untouched).
    os.makedirs(os.path.join(priv, "keep-me"))

    mod._sweep_stale_workdirs()

    assert not os.path.exists(recorded)                       # recorded -> removed
    assert os.path.isdir(foreign)                             # unrecorded -> preserved
    assert os.path.isdir(os.path.join(priv, "keep-me"))


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

    rc = mod._launch_update_unit("/var/lib/conduit-cc/ccc-update-x", (0, 3, 8),
                                 (0, 3, 9), "id123", _SOURCE_COMMIT, _SOURCE_TAG)
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
    assert cmd[cmd.index("--source-commit") + 1] == _SOURCE_COMMIT
    assert cmd[cmd.index("--source-tag") + 1] == _SOURCE_TAG


def test_launch_retries_after_reset_failed(mod, monkeypatch):
    calls = []

    def _fake_run(cmd, **k):
        calls.append(cmd)
        # systemd-run always fails; reset-failed succeeds.
        if cmd[0] == mod.SYSTEMD_RUN:
            return types.SimpleNamespace(returncode=1, stdout="unit exists")
        return types.SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rc = mod._launch_update_unit("/var/lib/conduit-cc/ccc-update-x", (0, 3, 8),
                                 (0, 3, 9), "id", _SOURCE_COMMIT, _SOURCE_TAG)
    assert rc != 0
    run_attempts = [c for c in calls if c[0] == mod.SYSTEMD_RUN]
    reset_calls = [c for c in calls if c[0] == mod.SYSTEMCTL and "reset-failed" in c]
    assert len(run_attempts) == 2          # initial + one retry
    assert len(reset_calls) == 1           # reset-failed between attempts


# --------------------------------------------------------------------------- #
#  Worker (__run-worker) defensive validation                                 #
# --------------------------------------------------------------------------- #
def test_worker_fails_closed_without_attempt_state_library(mod, monkeypatch):
    monkeypatch.setattr(mod, "_PSTATE_AVAILABLE", False)
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd([])
    assert exc.value.code == 3


def test_worker_rejects_workdir_outside_state(mod, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(outside))
    assert exc.value.code == 3
    assert not os.path.exists(mod.STATUS_PATH)


def test_worker_rejects_wrong_prefix(mod):
    bad = os.path.join(mod.PRIVATE_DIR, "notwork")
    os.makedirs(bad)
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(bad))
    assert exc.value.code == 3


def test_worker_rejects_missing_workdir(mod):
    ghost = os.path.join(mod.PRIVATE_DIR, "ccc-update-ghost")
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(ghost))
    assert exc.value.code == 3


def test_worker_refuses_unrecorded_work_before_update_execution(
        mod, monkeypatch):
    work, _ = _make_work(mod, record=False)
    called = []
    monkeypatch.setattr(
        mod, "_run_update", lambda *args, **kwargs: called.append(args) or 0)
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(work))
    assert exc.value.code == 3
    assert called == []
    assert os.path.isdir(work)  # no record -> no execution and no deletion authority


def test_update_worker_refuses_and_preserves_restore_attempt(mod):
    from backend import priv_state as P
    attempt_id = "123456abcdef"
    work = os.path.join(mod.PRIVATE_DIR, f"ccc-restore-{attempt_id}")
    P.record_attempt(
        mod.ATTEMPTS_DIR,
        attempt_id,
        work,
        os.getuid(),
        kind="restore",
    )
    os.mkdir(work, 0o700)
    os.chmod(work, 0o700)
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(work, attempt_id=attempt_id))
    assert exc.value.code == 3
    assert os.path.isdir(work)
    assert os.path.isfile(
        os.path.join(mod.ATTEMPTS_DIR, f"{attempt_id}.json"))


def test_worker_rejects_non_ccc_tree(mod):
    work, _ = _make_work(mod, valid_tree=False)
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(work))
    assert exc.value.code == 3


def test_worker_rejects_version_mismatch(mod):
    work, _ = _make_work(mod, version="0.3.9")
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(work, to="0.4.0", tag="v0.4.0"))
    assert exc.value.code == 3


@pytest.mark.parametrize(
    ("commit", "tag"),
    [("not-a-commit", _SOURCE_TAG), (_SOURCE_COMMIT, "v0.3.8")],
)
def test_worker_rejects_unbound_source_identity(mod, commit, tag):
    work, _ = _make_work(mod, version="0.3.9")
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(work, commit=commit, tag=tag))
    assert exc.value.code == 3


def test_worker_rejects_non_upgrade(mod):
    work, _ = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.9")  # installed == to -> not a strict increase
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(work))
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
        mod.run_worker_cmd(_worker_args(work))
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
    assert f"ccc-update-apply worker {_WID}" in logtext
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

    rc = mod._run_update(tree, (0, 3, 8), (0, 3, 9), "w2", work,
                         _SOURCE_COMMIT, _SOURCE_TAG)
    assert rc == 0
    assert json.loads(pathlib.Path(mod.STATUS_PATH).read_text())["state"] == "rolled_back"


def test_run_update_unexpected_state(mod, monkeypatch):
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")

    def _fake_run(cmd, **k):
        _set_installed(mod, "0.3.7")  # neither frm nor to
        return types.SimpleNamespace(returncode=1)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    rc = mod._run_update(tree, (0, 3, 8), (0, 3, 9), "w3", work,
                         _SOURCE_COMMIT, _SOURCE_TAG)
    assert rc == 0
    assert json.loads(pathlib.Path(mod.STATUS_PATH).read_text())["state"] == "failed"


def test_run_update_oserror_launching_updater(mod, monkeypatch):
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")

    def _boom(cmd, **k):
        raise OSError("bash not found")
    monkeypatch.setattr(mod.subprocess, "run", _boom)

    rc = mod._run_update(tree, (0, 3, 8), (0, 3, 9), _WID, work,
                         _SOURCE_COMMIT, _SOURCE_TAG)
    assert rc == 3
    assert json.loads(pathlib.Path(mod.STATUS_PATH).read_text())["state"] == "failed"
    assert not os.path.exists(work)


# --------------------------------------------------------------------------- #
#  _safe_extract negative-security matrix (Linux-only; finding #8)             #
# --------------------------------------------------------------------------- #
def _open_tar(path, entries):
    """entries: list of (name, kind, data). kind in file/fifo/chr/dir."""
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tar.addfile(info)
            elif kind == "chr":
                info.type = tarfile.CHRTYPE
                info.devmajor = 1
                info.devminor = 3
                tar.addfile(info)
    return tarfile.open(path, "r:gz")


def _extract(mod, tmp, entries, allowed=None):
    tarp = os.path.join(tmp, "a.tar.gz")
    dest = os.path.join(tmp, "dst")
    os.makedirs(dest, exist_ok=True)
    t = _open_tar(tarp, entries)
    if allowed is None:
        allowed = sorted({name.split("/", 1)[0] for name, _, _ in entries})
    mod._safe_extract(t, dest, allowed)   # allowed = the SIGNED top-level allowlist
    return dest


def test_extract_rejects_path_traversal(mod, tmp_path):
    with pytest.raises(SystemExit) as e:
        _extract(mod, str(tmp_path), [("../evil", "file", b"x")])
    assert e.value.code == 3


def test_extract_rejects_duplicate_members(mod, tmp_path):
    with pytest.raises(SystemExit) as e:
        _extract(mod, str(tmp_path), [("a/x", "file", b"1"), ("a/x", "file", b"2")])
    assert e.value.code == 3


def test_extract_rejects_fifo_and_device_members(mod, tmp_path):
    for kind in ("fifo", "chr"):
        with pytest.raises(SystemExit) as e:
            _extract(mod, str(tmp_path), [("special", kind, b"")])
        assert e.value.code == 3


def test_extract_rejects_unexpected_top_level(mod, tmp_path):
    # A member whose top-level is NOT in the signed allowlist is rejected -- an
    # arbitrary unexpected root cannot be smuggled in on EITHER platform.
    with pytest.raises(SystemExit) as e:
        _extract(mod, str(tmp_path), [("backend/x", "file", b"1"), ("surprise/y", "file", b"2")],
                 allowed=["backend"])
    assert e.value.code == 3


def test_extract_wheelhouse_only_when_in_allowlist(mod, tmp_path):
    # aarch64 isolation: the signed allowlist omits wheelhouse-armhf -> rejected.
    with pytest.raises(SystemExit) as e:
        _extract(mod, str(tmp_path), [("wheelhouse-armhf/x.whl", "file", b"PK")], allowed=["backend"])
    assert e.value.code == 3
    # armv7l: the signed allowlist includes it -> accepted.
    _extract(mod, str(tmp_path), [("wheelhouse-armhf/x.whl", "file", b"PK")],
             allowed=["wheelhouse-armhf"])


def test_extract_rejects_uncompressed_overflow(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "MAX_UNCOMPRESSED_BYTES", 5)
    with pytest.raises(SystemExit) as e:
        _extract(mod, str(tmp_path), [("big", "file", b"0123456789")])
    assert e.value.code == 3


def test_extract_insufficient_disk_fails_closed(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod.shutil, "disk_usage",
                        lambda d: types.SimpleNamespace(total=1, used=1, free=0))
    with pytest.raises(SystemExit) as e:
        _extract(mod, str(tmp_path), [("f", "file", b"data")])
    assert e.value.code == 3


def test_extract_disk_query_failure_fails_closed(mod, tmp_path, monkeypatch):
    # A disk_usage QUERY failure must FAIL CLOSED at the pre-extraction gate (exit 3),
    # not proceed.
    def _boom(d):
        raise OSError("statvfs unavailable")
    monkeypatch.setattr(mod.shutil, "disk_usage", _boom)
    with pytest.raises(SystemExit) as e:
        _extract(mod, str(tmp_path), [("ok/file", "file", b"data")])
    assert e.value.code == 3


# --------------------------------------------------------------------------- #
#  Privileged wheelhouse-path isolation (Question A)                           #
# --------------------------------------------------------------------------- #
def test_run_update_pins_wheelhouse_env_on_armv7(mod, monkeypatch):
    # An injected/inherited CCC_WHEELHOUSE_DIR must NOT redirect privileged pip;
    # the worker pins it to the VERIFIED tree on armv7l.
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")
    monkeypatch.setattr(mod.os, "uname", lambda: types.SimpleNamespace(machine="armv7l"))
    monkeypatch.setenv("CCC_WHEELHOUSE_DIR", "/attacker/unverified")
    seen = {}

    def _fake_run(cmd, **k):
        seen["env"] = k.get("env")
        _set_installed(mod, "0.3.9")
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    mod._run_update(tree, (0, 3, 8), (0, 3, 9), "wpin", work,
                    _SOURCE_COMMIT, _SOURCE_TAG)
    env = seen["env"]
    assert env is not None
    assert env["CCC_WHEELHOUSE_DIR"] == os.path.join(tree, "wheelhouse-armhf")
    assert env["CCC_WHEELHOUSE_DIR"] != "/attacker/unverified"


def test_run_update_strips_wheelhouse_env_on_aarch64(mod, monkeypatch):
    # aarch64 uses the index path; any inherited CCC_WHEELHOUSE_DIR is stripped.
    work, tree = _make_work(mod, version="0.3.9")
    _set_installed(mod, "0.3.8")
    monkeypatch.setattr(mod.os, "uname", lambda: types.SimpleNamespace(machine="aarch64"))
    monkeypatch.setenv("CCC_WHEELHOUSE_DIR", "/attacker/unverified")
    seen = {}

    def _fake_run(cmd, **k):
        seen["env"] = k.get("env")
        _set_installed(mod, "0.3.9")
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    mod._run_update(tree, (0, 3, 8), (0, 3, 9), "wstrip", work,
                    _SOURCE_COMMIT, _SOURCE_TAG)
    assert "CCC_WHEELHOUSE_DIR" not in seen["env"]


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
    _stub_verify_boundary(mod, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 2
    # no leftover work dirs
    assert not [n for n in os.listdir(mod.PRIVATE_DIR) if n.startswith("ccc-update-")]


def test_apply_happy_path_launches_unit(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "_unit_busy", lambda: False)
    monkeypatch.setattr(mod, "_ACTIVE_WAIT_S", 0)  # skip the post-launch poll
    _patch_exit(mod, monkeypatch)
    _set_installed(mod, "0.3.8")
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))
    _stub_verify_boundary(mod, monkeypatch)

    launched = {}

    def _fake_launch(work, frm, to, uid, source_commit, source_tag):
        launched["args"] = (work, frm, to, uid, source_commit, source_tag)
        return 0
    monkeypatch.setattr(mod, "_launch_update_unit", _fake_launch)

    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert out.startswith("accepted ")
    work, frm, to, uid, source_commit, source_tag = launched["args"]
    assert frm == (0, 3, 8) and to == (0, 3, 9)
    assert source_commit == _SOURCE_COMMIT and source_tag == _SOURCE_TAG
    assert os.path.basename(work).startswith("ccc-update-")
    assert os.path.isdir(work)  # not cleaned up on success (worker owns it)
    # apply itself writes no terminal status on success; the worker does.
    assert not os.path.exists(mod.STATUS_PATH)


def test_apply_marks_failed_when_launch_fails(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "_unit_busy", lambda: False)
    monkeypatch.setattr(mod, "_unit_state", lambda unit: "inactive")
    _patch_exit(mod, monkeypatch)
    _set_installed(mod, "0.3.8")
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))
    _stub_verify_boundary(mod, monkeypatch)
    monkeypatch.setattr(mod, "_launch_update_unit", lambda *a, **k: 3)

    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 0  # ack already sent

    assert capsys.readouterr().out.startswith("accepted ")
    doc = json.loads(pathlib.Path(mod.STATUS_PATH).read_text())
    assert doc["state"] == "failed"
    assert not [n for n in os.listdir(mod.PRIVATE_DIR) if n.startswith("ccc-update-")]


@pytest.mark.parametrize("unit_state", ["active", "activating", None])
def test_apply_preserves_attempt_when_failed_launch_state_is_ambiguous(
        mod, monkeypatch, capsys, unit_state):
    monkeypatch.setattr(mod, "_unit_busy", lambda: False)
    monkeypatch.setattr(mod, "_unit_state", lambda unit: unit_state)
    _patch_exit(mod, monkeypatch)
    _set_installed(mod, "0.3.8")
    _set_stdin(monkeypatch, _valid_payload("0.3.9"))
    _stub_verify_boundary(mod, monkeypatch)
    monkeypatch.setattr(mod, "_launch_update_unit", lambda *a, **k: 3)

    with pytest.raises(SystemExit) as exc:
        mod.apply_cmd()
    assert exc.value.code == 0  # public ack was already emitted
    assert capsys.readouterr().out.startswith("accepted ")
    assert not os.path.exists(mod.STATUS_PATH)  # no false terminal failure
    workdirs = [
        n for n in os.listdir(mod.PRIVATE_DIR) if n.startswith("ccc-update-")
    ]
    records = list(pathlib.Path(mod.ATTEMPTS_DIR).glob("*.json"))
    assert len(workdirs) == 1
    assert len(records) == 1


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


# --------------------------------------------------------------------------- #
#  A2 survival matrix: rejection must NEVER delete the rejected object         #
# --------------------------------------------------------------------------- #
def test_worker_rejection_preserves_outside_directory(mod, tmp_path):
    """The F-A2 exploit shape: an OUTSIDE path passed to __run-worker must be
    rejected AND survive (the old _fail deleted its raw --work argument)."""
    outside = tmp_path / "victim-outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    with pytest.raises(SystemExit) as exc:
        mod.run_worker_cmd(_worker_args(outside))
    assert exc.value.code == 3
    assert outside.is_dir() and (outside / "precious.txt").read_text() == "keep me"


def test_worker_rejection_preserves_unrecorded_workdir(mod):
    """A prefix-matching but UNRECORDED work dir survives its own rejection."""
    work, _ = _make_work(mod, valid_tree=False, attempt_id="eeeeeeeeeeee", record=False)
    with pytest.raises(SystemExit):
        mod.run_worker_cmd(_worker_args(work, attempt_id="eeeeeeeeeeee"))
    assert os.path.isdir(work)          # no record -> no deletion authority


def test_worker_rejection_preserves_argv_record_mismatch(mod):
    """A recorded attempt whose argv names a DIFFERENT path deletes nothing."""
    work, _ = _make_work(mod, valid_tree=True, attempt_id=_WID)   # recorded
    other, _ = _make_work(mod, valid_tree=False, attempt_id="ffffffffffff",
                          record=False)
    with pytest.raises(SystemExit):
        # id _WID but argv points at `other`: version-check fails -> _fail path;
        # cleanup must refuse (argv != recorded) and BOTH dirs survive.
        mod.run_worker_cmd(_worker_args(other))
    assert os.path.isdir(work) and os.path.isdir(other)


def test_cleanup_refuses_post_validation_swap(mod, monkeypatch):
    """Identity re-check: the recorded dir swapped between validation and
    removal is refused (dev/ino mismatch)."""
    from backend import priv_state as P
    work, _ = _make_work(mod, attempt_id=_WID)
    real_lstat = os.lstat
    seen = {"n": 0}

    def swapping_lstat(path):
        st = real_lstat(path)
        if path == work:
            seen["n"] += 1
            if seen["n"] == 2:            # second lstat = the pre-removal identity check
                class FakeSt:
                    st_mode = st.st_mode
                    st_dev, st_ino = st.st_dev, st.st_ino + 1   # swapped inode
                    st_uid, st_nlink = st.st_uid, st.st_nlink
                return FakeSt()
        return st

    monkeypatch.setattr(os, "lstat", swapping_lstat)
    with pytest.raises(P.PrivStateError, match="swapped"):
        P.cleanup_attempt(mod.PRIVATE_DIR, mod.ATTEMPTS_DIR, _WID, os.getuid(),
                          argv_work=work)
    monkeypatch.undo()
    assert os.path.isdir(work)
