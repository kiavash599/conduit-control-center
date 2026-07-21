"""tests/unit/test_runtime_store.py -- Epic-2 store/selector/conversion/rollback.

Real venvs (python3 -m venv) in tmp fixtures; owner_uid injected as the test
uid so the exact production checks run unprivileged. Includes the interruption
matrix for the legacy conversion and the B6 trust/.env preservation seam.
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="POSIX semantics")

from backend import runtime_store as RS  # noqa: E402

# Collection must remain safe on Windows even though the module is skipped there.
UID = os.getuid() if hasattr(os, "getuid") else 0


def _mk_app(tmp_path, *, real_venv=True):
    app = tmp_path / "app"
    app.mkdir()
    priv = tmp_path / "priv"
    priv.mkdir(mode=0o700)
    if real_venv:
        subprocess.run([sys.executable, "-m", "venv", "--without-pip",
                        str(app / "venv")], check=True)
    (app / "trust").mkdir(mode=0o700)
    (app / "trust" / "allowed_signers").write_text("p ssh-ed25519 AAAA\n")
    return app, priv


def _tighten(app):
    """Simulate the ownership-transition mode bits (no g/o write)."""
    for root, dirs, files in os.walk(app):
        for d in dirs:
            p = os.path.join(root, d)
            os.chmod(p, os.lstat(p).st_mode & ~0o022)
        for f in files:
            p = os.path.join(root, f)
            if not os.path.islink(p):
                os.chmod(p, os.lstat(p).st_mode & ~0o022)


# --- conversion (B3) --------------------------------------------------------- #

def test_convert_legacy_end_to_end(tmp_path):
    app, priv = _mk_app(tmp_path)
    _tighten(app)
    rid = RS.convert_legacy(str(app), str(priv), UID)
    assert rid == RS.LEGACY_ID
    assert (app / "venv").is_symlink()
    assert os.readlink(app / "venv") == f".venvs/{RS.LEGACY_ID}"
    assert (app / ".venvs" / RS.LEGACY_ID / "pyvenv.cfg").is_file()
    # the moved venv's interpreter still runs THROUGH the selector path
    r = subprocess.run([str(app / "venv" / "bin" / "python3"), "-c", "print('ok')"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "ok"
    # no transition record remains
    assert not (priv / RS.TRANSITION_RECORD).exists()


def test_convert_is_idempotent(tmp_path):
    app, priv = _mk_app(tmp_path)
    _tighten(app)
    RS.convert_legacy(str(app), str(priv), UID)
    assert RS.convert_legacy(str(app), str(priv), UID) == RS.LEGACY_ID   # no-op


def test_convert_refuses_symlink_legacy(tmp_path):
    app, priv = _mk_app(tmp_path, real_venv=False)
    victim = tmp_path / "victim"
    victim.mkdir()
    (app / "venv").symlink_to(victim)   # not a valid selector text either
    with pytest.raises(RS.RuntimeStoreError):
        RS.convert_legacy(str(app), str(priv), UID)
    assert victim.exists()


def test_convert_interrupted_after_move_resumes(tmp_path):
    """Kill-point: record written, venv moved, selector not yet published."""
    app, priv = _mk_app(tmp_path)
    _tighten(app)
    store = app / ".venvs"
    store.mkdir()
    os.rename(app / "venv", store / RS.LEGACY_ID)
    RS._write_json_atomic(str(priv / RS.TRANSITION_RECORD),
                          {"schema": 1, "op": "convert-legacy", "phase": "moved",
                           "selector": str(app / "venv"),
                           "target": str(store / RS.LEGACY_ID),
                           "runtime_id": RS.LEGACY_ID})
    rid = RS.convert_legacy(str(app), str(priv), UID)
    assert rid == RS.LEGACY_ID and (app / "venv").is_symlink()


def test_convert_interrupted_before_move_resumes(tmp_path):
    """Kill-point: record written, nothing moved yet."""
    app, priv = _mk_app(tmp_path)
    _tighten(app)
    RS._write_json_atomic(str(priv / RS.TRANSITION_RECORD),
                          {"schema": 1, "op": "convert-legacy", "phase": "recorded",
                           "selector": str(app / "venv"),
                           "target": str(app / ".venvs" / RS.LEGACY_ID),
                           "runtime_id": RS.LEGACY_ID})
    (app / ".venvs").mkdir()
    rid = RS.convert_legacy(str(app), str(priv), UID)
    assert rid == RS.LEGACY_ID and (app / "venv").is_symlink()


def test_conversion_rollback_restores_real_dir(tmp_path):
    app, priv = _mk_app(tmp_path)
    _tighten(app)
    RS.convert_legacy(str(app), str(priv), UID)
    RS.rollback_conversion(str(app), str(priv), UID)
    assert (app / "venv").is_dir() and not (app / "venv").is_symlink()
    assert (app / "venv" / "pyvenv.cfg").is_file()


# --- selector gate (B1) ------------------------------------------------------ #

def _converted(tmp_path):
    app, priv = _mk_app(tmp_path)
    _tighten(app)
    RS.convert_legacy(str(app), str(priv), UID)
    return app, priv


def test_selector_rejects_multi_component_text(tmp_path):
    app, priv = _converted(tmp_path)
    os.unlink(app / "venv")
    (app / "venv").symlink_to(f".venvs/../{RS.STORE_NAME}/{RS.LEGACY_ID}")
    with pytest.raises(RS.RuntimeStoreError, match="not exactly"):
        RS.validate_selector(str(app), UID)


def test_selector_rejects_absolute_target(tmp_path):
    app, priv = _converted(tmp_path)
    os.unlink(app / "venv")
    (app / "venv").symlink_to(str(app / ".venvs" / RS.LEGACY_ID))
    with pytest.raises(RS.RuntimeStoreError, match="not exactly"):
        RS.validate_selector(str(app), UID)


def test_selector_rejects_second_hop(tmp_path):
    app, priv = _converted(tmp_path)
    hop = app / ".venvs" / HOP_ID
    hop.symlink_to(RS.LEGACY_ID)
    os.unlink(app / "venv")
    (app / "venv").symlink_to(f".venvs/{HOP_ID}")
    with pytest.raises(RS.RuntimeStoreError, match="second symlink hop"):
        RS.validate_selector(str(app), UID)


def test_selector_rejects_unvalidated_manifest(tmp_path):
    app, priv = _converted(tmp_path)
    RS.write_manifest(str(app), RS.LEGACY_ID, kind="legacy",
                      input_digest="x", state="building")
    with pytest.raises(RS.RuntimeStoreError, match="!= 'validated'"):
        RS.validate_selector(str(app), UID)


def test_selector_rejects_missing_manifest(tmp_path):
    app, priv = _converted(tmp_path)
    os.unlink(RS.manifest_path(str(app), RS.LEGACY_ID))
    with pytest.raises(RS.RuntimeStoreError, match="no manifest"):
        RS.validate_selector(str(app), UID)


def test_selector_rejects_group_writable_target(tmp_path):
    app, priv = _converted(tmp_path)
    os.chmod(app / ".venvs" / RS.LEGACY_ID, 0o775)
    with pytest.raises(RS.RuntimeStoreError, match="writable"):
        RS.validate_selector(str(app), UID)


# --- activation / rollback / retention (B4) ---------------------------------- #

R2_ID = "b" * 64
R0_ID = "c" * 64
HOP_ID = "d" * 64


def _second_runtime(app, rid=R2_ID):
    subprocess.run([sys.executable, "-m", "venv", "--without-pip",
                    str(app / ".venvs" / rid)], check=True)
    for root, dirs, files in os.walk(app / ".venvs" / rid):
        for d in dirs:
            p = os.path.join(root, d)
            os.chmod(p, os.lstat(p).st_mode & ~0o022)
    os.chmod(app / ".venvs" / rid, 0o755)
    RS.write_manifest(str(app), rid, kind="built", input_digest="digest-r2")
    return rid


def test_activate_flip_and_rollback(tmp_path):
    app, priv = _converted(tmp_path)
    rid = _second_runtime(app)
    prev = RS.activate(str(app), str(priv), rid, UID)
    assert prev == RS.LEGACY_ID
    assert os.readlink(app / "venv") == f".venvs/{rid}"
    # local rollback: flip back, previous runtime untouched
    back = RS.rollback_activation(str(app), str(priv), UID)
    assert back == RS.LEGACY_ID
    assert (app / ".venvs" / rid).is_dir()          # candidate retained on disk


def test_activate_refuses_unvalidated(tmp_path):
    app, priv = _converted(tmp_path)
    rid = _second_runtime(app)
    RS.write_manifest(str(app), rid, kind="built", input_digest="d", state="building")
    with pytest.raises(RS.RuntimeStoreError, match="validated"):
        RS.activate(str(app), str(priv), rid, UID)
    assert os.readlink(app / "venv") == f".venvs/{RS.LEGACY_ID}"   # unchanged


def test_gc_keeps_current_previous_and_foreign(tmp_path):
    app, priv = _converted(tmp_path)
    rid = _second_runtime(app)
    RS.activate(str(app), str(priv), rid, UID)      # current=r2, previous=legacy
    _second_runtime(app, rid=R0_ID)              # recorded, not current/prev
    foreign = app / ".venvs" / "foreign-dir"        # NO manifest
    foreign.mkdir()
    removed = RS.gc(str(app), str(priv), UID)
    assert removed == [R0_ID]
    assert (app / ".venvs" / rid).is_dir()          # current survives
    assert (app / ".venvs" / RS.LEGACY_ID).is_dir()  # previous survives
    assert foreign.is_dir()                          # unrecorded survives


# --- B6 cross-seam preservation ---------------------------------------------- #

def test_trust_anchor_identical_across_convert_activate_rollback(tmp_path):
    app, priv = _mk_app(tmp_path)
    _tighten(app)
    anchor = app / "trust" / "allowed_signers"
    before = anchor.read_bytes()
    RS.convert_legacy(str(app), str(priv), UID)
    rid = _second_runtime(app)
    RS.activate(str(app), str(priv), rid, UID)
    RS.rollback_activation(str(app), str(priv), UID)
    RS.rollback_conversion(str(app), str(priv), UID)
    assert anchor.read_bytes() == before


# --- R1: pre-flip validation, post-flip restore, candidate lifecycle --------- #

def test_full_strength_candidate_id_is_64_hex_and_deterministic():
    a = RS.compute_candidate_id(app_version="0.3.19", commit="a" * 40, arch="armv7l",
                                python_version="3.10", abi="cpython-310-arm-linux-gnueabihf",
                                input_digest_kind="ltd", input_digest="e" * 64)
    b = RS.compute_candidate_id(app_version="0.3.19", commit="a" * 40, arch="armv7l",
                                python_version="3.10", abi="cpython-310-arm-linux-gnueabihf",
                                input_digest_kind="ltd", input_digest="e" * 64)
    assert a == b and len(a) == 64 and all(c in "0123456789abcdef" for c in a)
    c = RS.compute_candidate_id(app_version="0.3.19", commit="b" * 40, arch="armv7l",
                                python_version="3.10", abi="x", input_digest_kind="ltd",
                                input_digest="e" * 64)
    assert c != a


@pytest.mark.parametrize("sabotage", ["missing", "symlink", "writable", "no-pyvenv"])
def test_invalid_target_never_becomes_visible_selector(tmp_path, sabotage):
    """R4-class regression: activate() must validate the CANDIDATE before any
    flip; the visible selector must still point at the original runtime."""
    app, priv = _converted(tmp_path)
    rid = _second_runtime(app)
    target = app / ".venvs" / rid
    if sabotage == "missing":
        import shutil as sh
        sh.rmtree(target)
    elif sabotage == "symlink":
        import shutil as sh
        sh.rmtree(target)
        target.symlink_to(app / ".venvs" / RS.LEGACY_ID)
    elif sabotage == "writable":
        os.chmod(target, 0o777)
    elif sabotage == "no-pyvenv":
        os.unlink(target / "pyvenv.cfg")
    with pytest.raises(RS.RuntimeStoreError):
        RS.activate(str(app), str(priv), rid, UID)
    # the selector never moved
    assert os.readlink(app / "venv") == f".venvs/{RS.LEGACY_ID}"


def test_initial_activation_requires_absent_selector_and_valid_target(tmp_path):
    app, _priv = _mk_app(tmp_path)
    import shutil as sh
    sh.rmtree(app / "venv")
    _tighten(app)
    rid = _second_runtime(app)
    assert RS.activate_initial(str(app), rid, UID) == rid
    assert os.readlink(app / "venv") == f".venvs/{rid}"
    with pytest.raises(RS.RuntimeStoreError, match="absent selector"):
        RS.activate_initial(str(app), rid, UID)


def test_initial_activation_rejects_invalid_target_without_selector(tmp_path):
    app, _priv = _mk_app(tmp_path)
    import shutil as sh
    sh.rmtree(app / "venv")
    _tighten(app)
    with pytest.raises(RS.RuntimeStoreError):
        RS.activate_initial(str(app), "e" * 64, UID)
    assert not os.path.lexists(app / "venv")


def test_post_flip_failure_restores_previous_selector(tmp_path, monkeypatch):
    """If validate_selector unexpectedly fails AFTER the flip, the previous
    selector is restored before the exception propagates."""
    app, priv = _converted(tmp_path)
    rid = _second_runtime(app)
    real_validate = RS.validate_selector
    calls = {"n": 0}

    def failing_after_flip(app_dir, owner_uid=RS.OWNER_UID):
        calls["n"] += 1
        if calls["n"] == 2:      # 1st = pre-flip previous lookup, 2nd = post-flip
            raise RS.RuntimeStoreError("simulated post-flip corruption")
        return real_validate(app_dir, owner_uid)

    monkeypatch.setattr(RS, "validate_selector", failing_after_flip)
    with pytest.raises(RS.RuntimeStoreError, match="post-flip"):
        RS.activate(str(app), str(priv), rid, UID)
    monkeypatch.undo()
    assert os.readlink(app / "venv") == f".venvs/{RS.LEGACY_ID}"   # restored


def _install_nothing(py):
    return None


def test_build_candidate_full_lifecycle(tmp_path):
    app, priv = _converted(tmp_path)
    rid = "e" * 64
    got = RS.build_candidate(str(app), str(priv), rid, attempt_id="abcdefabcdef",
                             install_cmd=_install_nothing,
                             manifest_fields={"input_digest": "f" * 64,
                                              "app_version": "0.3.19"},
                             owner_uid=UID, smoke_imports=())
    assert got == rid
    man = RS.read_manifest(str(app), rid)
    assert man["state"] == "validated" and man["kind"] == "built"
    assert "dist_record" in man
    RS.validate_target(str(app), rid, UID)
    # no staging leftovers
    assert not [n for n in os.listdir(app / ".venvs") if n.startswith(".staging-")]


def test_build_candidate_failure_leaves_active_untouched_and_no_validated_manifest(tmp_path):
    app, priv = _converted(tmp_path)
    rid = "e" * 64

    def boom(py):
        raise RS.RuntimeStoreError("simulated dependency install failure")

    before = os.readlink(app / "venv")
    with pytest.raises(RS.RuntimeStoreError, match="simulated"):
        RS.build_candidate(str(app), str(priv), rid, attempt_id="abcdefabcdef",
                           install_cmd=boom, manifest_fields={}, owner_uid=UID,
                           smoke_imports=())
    assert os.readlink(app / "venv") == before          # active untouched
    assert RS.read_manifest(str(app), rid) is None       # no validated manifest
    assert not (app / ".venvs" / rid).exists()
    assert not [n for n in os.listdir(app / ".venvs") if n.startswith(".staging-")]


def test_build_candidate_collision_requires_live_revalidation(tmp_path):
    """An existing final runtime with the same id is reused ONLY after live
    semantic revalidation; a tampered dist record is rejected."""
    app, priv = _converted(tmp_path)
    rid = "e" * 64
    RS.build_candidate(str(app), str(priv), rid, attempt_id="abcdefabcdef",
                       install_cmd=_install_nothing,
                       manifest_fields={"input_digest": "f" * 64}, owner_uid=UID,
                       smoke_imports=())
    # identical rebuild -> live-verified reuse
    assert RS.build_candidate(str(app), str(priv), rid, attempt_id="bbbbbbbbbbbb",
                              install_cmd=_install_nothing,
                              manifest_fields={"input_digest": "f" * 64},
                              owner_uid=UID, smoke_imports=()) == rid
    # tamper the manifest's dist record -> live revalidation must reject
    man = RS.read_manifest(str(app), rid)
    man["dist_record"] = "0" * 64
    RS._write_json_atomic(RS.manifest_path(str(app), rid), man)
    with pytest.raises(RS.RuntimeStoreError, match="distribution record differs"):
        RS.build_candidate(str(app), str(priv), rid, attempt_id="cccccccccccc",
                           install_cmd=_install_nothing,
                           manifest_fields={"input_digest": "f" * 64}, owner_uid=UID,
                           smoke_imports=())


def test_leftover_staging_fails_closed(tmp_path):
    app, priv = _converted(tmp_path)
    (app / ".venvs" / ".staging-abcdefabcdef").mkdir()
    with pytest.raises(RS.RuntimeStoreError, match="already exists"):
        RS.build_candidate(str(app), str(priv), "e" * 64, attempt_id="abcdefabcdef",
                           install_cmd=_install_nothing, manifest_fields={},
                           owner_uid=UID, smoke_imports=())
    assert (app / ".venvs" / ".staging-abcdefabcdef").is_dir()   # preserved


def test_discard_candidate_staging_is_exact_and_attempt_owned(tmp_path):
    app, _priv = _converted(tmp_path)
    owned = app / ".venvs" / ".staging-abcdefabcdef"
    foreign = app / ".venvs" / ".staging-bbbbbbbbbbbb"
    final = app / ".venvs" / ("e" * 64)
    owned.mkdir()
    foreign.mkdir()
    final.mkdir()
    assert RS.discard_candidate_staging(
        str(app), "abcdefabcdef", owner_uid=UID) is True
    assert not owned.exists()
    assert foreign.is_dir() and final.is_dir()
    assert RS.discard_candidate_staging(
        str(app), "abcdefabcdef", owner_uid=UID) is False


def test_candidate_recovery_is_idempotent_before_store_exists(tmp_path):
    app, _priv = _mk_app(tmp_path, real_venv=False)
    _tighten(app)
    assert not (app / ".venvs").exists()
    assert RS.discard_candidate_staging(
        str(app), "abcdefabcdef", owner_uid=UID) is False
    assert RS.reconcile_candidate_attempt(
        str(app), "e" * 64, "abcdefabcdef", UID,
        smoke_imports=(),
    ) == "absent"
    assert not (app / ".venvs").exists()


def test_finalize_publishes_validated_manifest_before_final_directory(tmp_path,
                                                                      monkeypatch):
    """An interruption immediately after rename must never expose a final
    candidate with an intermediate manifest state."""
    app, _priv = _converted(tmp_path)
    rid = "e" * 64
    attempt = "abcdefabcdef"
    RS.stage_candidate(str(app), rid, attempt, UID)
    real_rename = RS.os.rename

    def rename_then_interrupt(src, dst):
        real_rename(src, dst)
        raise SystemExit("simulated interruption after publication")

    monkeypatch.setattr(RS.os, "rename", rename_then_interrupt)
    with pytest.raises(SystemExit, match="after publication"):
        RS.finalize_candidate(
            str(app), rid, attempt,
            manifest_fields={"input_digest": "f" * 64},
            owner_uid=UID, smoke_imports=(),
        )
    monkeypatch.undo()

    assert (app / ".venvs" / rid).is_dir()
    man = RS.read_manifest(str(app), rid)
    assert man["state"] == "validated"
    assert man["publication_attempt"] == attempt
    RS.revalidate_runtime(str(app), rid, UID, smoke_imports=())


def test_reconcile_removes_attempt_bound_orphan_manifest_and_staging(tmp_path):
    app, _priv = _converted(tmp_path)
    rid = "e" * 64
    attempt = "abcdefabcdef"
    RS.stage_candidate(str(app), rid, attempt, UID)
    RS.write_manifest(
        str(app), rid, kind="built", input_digest="f" * 64,
        extra={"publication_attempt": attempt},
    )

    assert RS.reconcile_candidate_attempt(
        str(app), rid, attempt, UID, smoke_imports=()) == "discarded-partial"
    assert not (app / ".venvs" / f".staging-{attempt}").exists()
    assert RS.read_manifest(str(app), rid) is None


def test_reconcile_retains_live_validated_candidate_for_reuse(tmp_path):
    app, priv = _converted(tmp_path)
    rid = "e" * 64
    attempt = "abcdefabcdef"
    RS.build_candidate(
        str(app), str(priv), rid, attempt_id=attempt,
        install_cmd=_install_nothing,
        manifest_fields={"input_digest": "f" * 64},
        owner_uid=UID, smoke_imports=(),
    )

    assert RS.reconcile_candidate_attempt(
        str(app), rid, attempt, UID, smoke_imports=()) == "validated"
    assert (app / ".venvs" / rid).is_dir()
    assert RS.read_manifest(str(app), rid)["state"] == "validated"


def test_reconcile_removes_only_invalid_candidate_bound_to_attempt(tmp_path):
    app, priv = _converted(tmp_path)
    rid = "e" * 64
    attempt = "abcdefabcdef"
    RS.build_candidate(
        str(app), str(priv), rid, attempt_id=attempt,
        install_cmd=_install_nothing,
        manifest_fields={"input_digest": "f" * 64},
        owner_uid=UID, smoke_imports=(),
    )
    man = RS.read_manifest(str(app), rid)
    man["dist_record"] = "0" * 64
    RS._write_json_atomic(RS.manifest_path(str(app), rid), man)

    assert RS.reconcile_candidate_attempt(
        str(app), rid, attempt, UID, smoke_imports=()) == "discarded-invalid"
    assert not (app / ".venvs" / rid).exists()
    assert RS.read_manifest(str(app), rid) is None


def test_reconcile_preserves_invalid_foreign_candidate_without_attempt_binding(tmp_path):
    app, _priv = _converted(tmp_path)
    rid = _second_runtime(app)
    before = RS.read_manifest(str(app), rid)

    with pytest.raises(RS.RuntimeStoreError, match="not bound to this update attempt"):
        RS.reconcile_candidate_attempt(
            str(app), rid, "abcdefabcdef", UID, smoke_imports=())
    assert (app / ".venvs" / rid).is_dir()
    assert RS.read_manifest(str(app), rid) == before


def test_stage_refuses_orphan_manifest_until_reconciled(tmp_path):
    app, _priv = _converted(tmp_path)
    rid = "e" * 64
    RS.write_manifest(
        str(app), rid, kind="built", input_digest="f" * 64,
        extra={"publication_attempt": "abcdefabcdef"},
    )
    with pytest.raises(RS.RuntimeStoreError, match="orphan manifest"):
        RS.stage_candidate(str(app), rid, "abcdefabcdef", UID)


@pytest.mark.parametrize("attempt", ["../outside", "abcdef/abcdef", "A" * 12, "short"])
def test_candidate_apis_reject_unbound_attempt_paths(tmp_path, attempt):
    app, _priv = _converted(tmp_path)
    rid = "e" * 64
    with pytest.raises(RS.RuntimeStoreError, match="invalid candidate attempt id"):
        RS.stage_candidate(str(app), rid, attempt, UID)
    with pytest.raises(RS.RuntimeStoreError, match="invalid candidate attempt id"):
        RS.finalize_candidate(
            str(app), rid, attempt,
            manifest_fields={"input_digest": "f" * 64},
            owner_uid=UID, smoke_imports=(),
        )
    assert not (tmp_path / "outside").exists()


# --- finding 2: symlinked store / service-writable app dir exploit ----------- #

def test_stage_candidate_rejects_symlinked_store(tmp_path):
    """The reproduced escape: a `.venvs` symlink must be REFUSED, never followed
    into an outside victim directory."""
    app, priv = _mk_app(tmp_path, real_venv=False)
    _tighten(app)
    victim = tmp_path / "victim"
    victim.mkdir()
    (app / ".venvs").symlink_to(victim)              # attacker-planted store symlink
    with pytest.raises(RS.RuntimeStoreError, match="not a real directory"):
        RS.stage_candidate(str(app), "e" * 64, "abcdefabcdef", UID)
    # nothing was created inside the victim
    assert list(victim.iterdir()) == []


def test_open_store_rejects_service_writable_app_dir(tmp_path):
    """First-transition exploit: exact v0.3.18 app dir is service-writable.
    open_store must refuse a group/other-writable app root."""
    app = tmp_path / "app"
    app.mkdir()
    os.chmod(app, 0o777)                             # service-writable
    with pytest.raises(RS.RuntimeStoreError, match="group/other writable"):
        RS.open_store(str(app), UID, create=True)


def test_open_store_rejects_symlinked_app_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "applink"
    link.symlink_to(real)
    with pytest.raises(RS.RuntimeStoreError, match="not a real directory"):
        RS.open_store(str(link), UID, create=True)


# --- finding 6: recursive trust-closure BEFORE interpreter execution --------- #

def test_revalidation_rejects_group_writable_nested_package(tmp_path):
    """A substituted/service-writable file deep under site-packages must be
    caught by the recursive tree check BEFORE the runtime's interpreter runs."""
    app, priv = _converted(tmp_path)
    rid = _second_runtime(app)                       # validated built runtime
    # plant a group-writable file inside the runtime tree (below bin)
    sp = app / ".venvs" / rid / "lib"
    sp.mkdir(exist_ok=True)
    evil = sp / "evil.py"
    evil.write_text("raise SystemExit('should never run')\n")
    os.chmod(evil, 0o666)                            # service-writable
    with pytest.raises(RS.RuntimeStoreError, match="group/other writable"):
        RS.validate_runtime_tree(str(app), rid, UID)


def test_revalidation_rejects_escaping_symlink_in_tree(tmp_path):
    app, priv = _converted(tmp_path)
    rid = _second_runtime(app)
    victim = tmp_path / "outside"
    victim.write_text("x")
    (app / ".venvs" / rid / "lib" ).mkdir(exist_ok=True)
    (app / ".venvs" / rid / "lib" / "escape").symlink_to(victim)
    with pytest.raises(RS.RuntimeStoreError, match="disallowed symlink"):
        RS.validate_runtime_tree(str(app), rid, UID)


def test_finalize_runs_recursive_check_before_interpreter(tmp_path):
    """finalize_candidate validates the STAGING tree before executing its
    interpreter. An ESCAPING symlink (which the mode-tightening loop does not
    neutralize) must be rejected and the runtime never published."""
    app, priv = _converted(tmp_path)
    rid = "e" * 64
    RS.stage_candidate(str(app), rid, "abcdefabcdef", UID)
    staging = app / ".venvs" / ".staging-abcdefabcdef"
    victim = tmp_path / "outside"
    victim.write_text("x")
    (staging / "lib").mkdir(exist_ok=True)
    (staging / "lib" / "escape").symlink_to(victim)
    with pytest.raises(RS.RuntimeStoreError, match="disallowed symlink"):
        RS.finalize_candidate(str(app), rid, "abcdefabcdef",
                              manifest_fields={"input_digest": "f" * 64},
                              owner_uid=UID, smoke_imports=())
    assert not (app / ".venvs" / rid).exists()       # never published


def test_finalize_never_chmods_external_directory_symlink_target(tmp_path):
    """Regression/non-vacuity for the pre-validation chmod escape.

    A directory symlink appears in os.walk(...).dirnames.  finalize must reject
    it without chmod/chown or any other mutation of the external directory.
    """
    app, priv = _converted(tmp_path)
    rid = "e" * 64
    RS.stage_candidate(str(app), rid, "abcdefabcdef", UID)
    staging = app / ".venvs" / ".staging-abcdefabcdef"
    victim = tmp_path / "outside-dir"
    victim.mkdir(mode=0o777)
    os.chmod(victim, 0o777)
    before = os.lstat(victim).st_mode & 0o7777
    (staging / "escape-dir").symlink_to(victim, target_is_directory=True)

    with pytest.raises(RS.RuntimeStoreError, match="disallowed symlink"):
        RS.finalize_candidate(str(app), rid, "abcdefabcdef",
                              manifest_fields={"input_digest": "f" * 64},
                              owner_uid=UID, smoke_imports=())

    assert (os.lstat(victim).st_mode & 0o7777) == before
    assert not (app / ".venvs" / rid).exists()


def test_committed_smoke_set_covers_native_platform_contract():
    required = {
        "pydantic_core", "cryptography.hazmat.bindings._rust", "bcrypt._bcrypt",
        "_cffi_backend", "httptools", "markupsafe._speedups", "psutil",
        "yaml._yaml", "uvloop", "watchfiles", "websockets",
    }
    assert required <= set(RS.SMOKE_IMPORTS)


def test_runtime_tree_rejects_hardlinked_file(tmp_path):
    app, _priv = _converted(tmp_path)
    rid = _second_runtime(app)
    victim = tmp_path / "outside-hardlink"
    victim.write_text("shared")
    alias = app / ".venvs" / rid / "shared-hardlink"
    os.link(victim, alias)
    with pytest.raises(RS.RuntimeStoreError, match="hardlinked file"):
        RS.validate_runtime_tree(str(app), rid, UID)
    assert victim.read_text() == "shared"


def test_legacy_shape_gate_precedes_and_blocks_recursive_mutation(tmp_path):
    app, _priv = _mk_app(tmp_path)
    _tighten(app)
    victim = tmp_path / "legacy-hardlink-victim"
    victim.write_text("preserve")
    os.link(victim, app / "venv" / "shared-hardlink")
    with pytest.raises(RS.RuntimeStoreError, match="hardlinked file"):
        RS.validate_legacy_shape(str(app), UID)
    assert victim.read_text() == "preserve"


def test_legacy_runtime_full_gate_accepts_real_tightened_venv(tmp_path):
    app, _priv = _mk_app(tmp_path)
    _tighten(app)
    RS.validate_legacy_shape(str(app), UID)
    RS.validate_legacy_runtime(str(app), UID)


def test_clean_env_drops_interpreter_and_dynamic_loader_injection(monkeypatch):
    hostile = {
        "PYTHONPATH": "/attacker/python",
        "PYTHONHOME": "/attacker/home",
        "PIP_CONFIG_FILE": "/attacker/pip.conf",
        "LD_PRELOAD": "/attacker/preload.so",
        "LD_LIBRARY_PATH": "/attacker/lib",
        "LD_AUDIT": "/attacker/audit.so",
        "VIRTUAL_ENV": "/attacker/venv",
    }
    for key, value in hostile.items():
        monkeypatch.setenv(key, value)
    env = RS._clean_env()
    assert not (set(hostile) & set(env))
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PIP_NO_INPUT"] == "1"


# --- per-attempt update transaction ----------------------------------------- #

def _private_state(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    os.chmod(private, 0o700)
    return private


def _success_steps(aid):
    return (
    ("ownership_intent", {}),
    ("ownership_complete", {}),
    ("backup_intent", {
        "backup_dir": f"/var/backups/conduit-cc/20260721-120000-{aid}"}),
    ("backup_complete", {"previous_version": "0.3.18"}),
    ("candidate_intent", {"candidate_id": "e" * 64}),
    ("candidate_ready", {}),
    ("downtime_intent", {}),
    ("downtime_started", {"downtime_started": True}),
    ("conversion_intent", {"converted_by_attempt": True}),
    ("conversion_complete", {}),
    ("trust_intent", {}),
    ("trust_complete", {"trust_done": True}),
    ("activation_intent", {"previous_runtime": RS.LEGACY_ID}),
    ("activated", {"activation_done": True}),
    ("deploy_intent", {}),
    ("deployed", {}),
    ("service_start_intent", {}),
    ("service_started", {}),
    ("health_verified", {}),
    ("success", {}),
    )


def _advance_success(private, aid):
    doc = None
    for phase, facts in _success_steps(aid):
        doc = RS.mark_update_attempt(
            str(private), aid, phase, facts=facts, owner_uid=UID)
    return doc


def test_update_attempt_is_write_ahead_atomic_and_identity_bound(tmp_path):
    private = _private_state(tmp_path)
    aid = "123456789abc"
    commit = "a" * 40
    first = RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit=commit, source_tag="v0.3.19", owner_uid=UID)
    assert first["phase"] == "begun" and first["history"] == ["begun"]
    path = pathlib.Path(RS.update_attempt_path(str(private), aid))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    RS.mark_update_attempt(str(private), aid, "ownership_intent", owner_uid=UID)
    RS.mark_update_attempt(str(private), aid, "ownership_complete", owner_uid=UID)
    backup = f"/var/backups/conduit-cc/20260721-120000-{aid}"
    RS.mark_update_attempt(
        str(private), aid, "backup_intent", facts={"backup_dir": backup}, owner_uid=UID)
    RS.mark_update_attempt(
        str(private), aid, "backup_complete",
        facts={"previous_version": "0.3.18"}, owner_uid=UID)
    doc = RS.mark_update_attempt(
        str(private), aid, "candidate_intent",
        facts={"candidate_id": "e" * 64}, owner_uid=UID)
    assert doc["history"] == [
        "begun", "ownership_intent", "ownership_complete", "backup_intent",
        "backup_complete", "candidate_intent",
    ]
    assert doc["facts"]["candidate_id"] == "e" * 64
    assert RS.read_update_attempt(str(private), aid, UID) == doc

    with pytest.raises(RS.RuntimeStoreError, match="collision"):
        RS.begin_update_attempt(
            str(private), aid, target_version="0.3.19",
            source_commit="b" * 40, source_tag="v0.3.19", owner_uid=UID)


def test_update_attempt_terminal_state_is_immutable(tmp_path):
    private = _private_state(tmp_path)
    aid = "123456789abc"
    RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    terminal = _advance_success(private, aid)
    assert terminal["phase"] == "success"
    assert RS.mark_update_attempt(
        str(private), aid, "success", owner_uid=UID) == terminal
    with pytest.raises(RS.RuntimeStoreError, match="terminal"):
        RS.mark_update_attempt(str(private), aid, "rollback_started", owner_uid=UID)
    assert RS.completed_update_backups(str(private), UID) == [{
        "attempt_id": aid,
        "backup_dir": f"/var/backups/conduit-cc/20260721-120000-{aid}",
    }]


def test_backup_authority_requires_exact_attempt_binding_and_terminal_state(tmp_path):
    private = _private_state(tmp_path)
    aid = "123456789abc"
    RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    RS.mark_update_attempt(str(private), aid, "ownership_intent", owner_uid=UID)
    RS.mark_update_attempt(str(private), aid, "ownership_complete", owner_uid=UID)
    with pytest.raises(RS.RuntimeStoreError, match="bound to its update attempt"):
        RS.mark_update_attempt(
            str(private), aid, "backup_intent",
            facts={"backup_dir": "/var/backups/conduit-cc/foreign"}, owner_uid=UID)
    exact = f"/var/backups/conduit-cc/20260721-120000-{aid}"
    RS.mark_update_attempt(
        str(private), aid, "backup_intent", facts={"backup_dir": exact}, owner_uid=UID)
    RS.mark_update_attempt(
        str(private), aid, "backup_complete",
        facts={"previous_version": "0.3.18"}, owner_uid=UID)
    assert RS.completed_update_backups(str(private), UID) == []
    RS.mark_update_attempt(
        str(private), aid, "diagnostic_failure", owner_uid=UID)
    assert RS.completed_update_backups(str(private), UID) == [
        {"attempt_id": aid, "backup_dir": exact}
    ]


def test_update_attempt_rejects_phase_jumps_and_misplaced_facts(tmp_path):
    private = _private_state(tmp_path)
    aid = "123456789abc"
    RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    with pytest.raises(RS.RuntimeStoreError, match="invalid update phase transition"):
        RS.mark_update_attempt(str(private), aid, "candidate_ready", owner_uid=UID)
    with pytest.raises(RS.RuntimeStoreError, match="not valid at this transaction phase"):
        RS.mark_update_attempt(
            str(private), aid, "ownership_intent",
            facts={"candidate_id": "e" * 64}, owner_uid=UID)
    RS.mark_update_attempt(str(private), aid, "ownership_intent", owner_uid=UID)
    RS.mark_update_attempt(str(private), aid, "ownership_complete", owner_uid=UID)
    RS.mark_update_attempt(
        str(private), aid, "backup_intent",
        facts={"backup_dir": f"/var/backups/conduit-cc/20260721-120000-{aid}"},
        owner_uid=UID,
    )
    with pytest.raises(RS.RuntimeStoreError, match="missing required"):
        RS.mark_update_attempt(str(private), aid, "backup_complete", owner_uid=UID)


def test_every_normal_interruption_is_terminalizable(tmp_path):
    """Crash matrix for every forward phase: before downtime the attempt is
    diagnostically terminal; after downtime it can complete the ordered,
    resumable rollback checkpoints. No forward phase is unclassifiable."""
    for index in range(len(_success_steps("0" * 12))):
        case_root = tmp_path / str(index)
        case_root.mkdir()
        private = _private_state(case_root)
        aid = f"{index + 1:012x}"
        steps = _success_steps(aid)
        stop_phase = ("begun", {}) if index == 0 else steps[index - 1]
        stop_phase = stop_phase[0]
        RS.begin_update_attempt(
            str(private), aid, target_version="0.3.19",
            source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
        for phase, facts in steps:
            if stop_phase == "begun":
                break
            RS.mark_update_attempt(
                str(private), aid, phase, facts=facts, owner_uid=UID)
            if phase == stop_phase:
                break
        if stop_phase in {
            "begun", "ownership_intent", "ownership_complete", "backup_intent",
            "backup_complete",
            "candidate_intent", "candidate_ready",
            "downtime_intent",
        }:
            final = RS.mark_update_attempt(
                str(private), aid, "diagnostic_failure", owner_uid=UID)
        else:
            final = None
            for phase in (
                "rollback_started", "runtime_restored", "files_restored",
                "service_restore_intent", "rolled_back",
            ):
                final = RS.mark_update_attempt(
                    str(private), aid, phase, owner_uid=UID)
        assert final["phase"] in RS.UPDATE_TERMINAL_PHASES
        assert RS.incomplete_update_attempts(str(private), UID) == []


def test_v0318_layout_model_rehearsal_rolls_back_code_and_runtime(tmp_path):
    """Executable v0.3.18-layout model over the production primitives.

    This is deliberately not called the exact-device rehearsal: it models the
    relevant v0.3.18 layout (real-directory legacy venv plus old root-owned
    code/helper bytes) while exercising the real runtime/transaction module.
    The exact tagged v0.3.18 installation and systemd behavior remain a device
    gate. It builds the candidate before downtime, converts, activates and
    deploys, then injects the post-activation health failure and completes the
    durable rollback sequence.
    """
    app, private = _mk_app(tmp_path)
    version_file = app / "backend" / "_version.py"
    version_file.parent.mkdir()
    version_file.write_text('APP_VERSION = "0.3.18"\n')
    helper = app / "bin" / "ccc-update-apply"
    helper.parent.mkdir()
    helper.write_bytes(b"v0.3.18-helper\n")
    _tighten(app)
    old_cfg = (app / "venv" / "pyvenv.cfg").read_bytes()
    old_helper = helper.read_bytes()

    # Candidate exists and validates while the v0.3.18 real-dir runtime remains
    # active: the expensive dependency phase precedes downtime.
    RS.open_store(str(app), UID, create=True)
    candidate = _second_runtime(app, rid="e" * 64)
    aid = "123456789abc"
    RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    for phase, facts in _success_steps(aid):
        if phase == "candidate_intent":
            facts = {"candidate_id": candidate}
        if phase == "conversion_intent":
            facts = {"converted_by_attempt": True}
            RS.mark_update_attempt(
                str(private), aid, phase, facts=facts, owner_uid=UID)
            RS.convert_legacy(str(app), str(private), UID)
            continue
        if phase == "activation_intent":
            facts = {"previous_runtime": RS.LEGACY_ID}
            RS.mark_update_attempt(
                str(private), aid, phase, facts=facts, owner_uid=UID)
            assert RS.activate(str(app), str(private), candidate, UID) == RS.LEGACY_ID
            continue
        if phase == "activated":
            RS.mark_update_attempt(
                str(private), aid, phase, facts=facts, owner_uid=UID)
            version_file.write_text('APP_VERSION = "0.3.19"\n')
            helper.write_bytes(b"v0.3.19-helper\n")
            continue
        if phase == "health_verified":
            break  # injected health failure after start/activation/deploy
        RS.mark_update_attempt(
            str(private), aid, phase, facts=facts, owner_uid=UID)

    RS.mark_update_attempt(str(private), aid, "rollback_started", owner_uid=UID)
    assert RS.rollback_activation(str(app), str(private), UID) == RS.LEGACY_ID
    RS.rollback_conversion(str(app), str(private), UID)
    RS.mark_update_attempt(str(private), aid, "runtime_restored", owner_uid=UID)
    version_file.write_text('APP_VERSION = "0.3.18"\n')
    helper.write_bytes(old_helper)
    RS.mark_update_attempt(str(private), aid, "files_restored", owner_uid=UID)
    RS.mark_update_attempt(
        str(private), aid, "service_restore_intent", owner_uid=UID)
    final = RS.mark_update_attempt(
        str(private), aid, "rolled_back", owner_uid=UID)

    assert final["phase"] == "rolled_back"
    assert (app / "venv").is_dir() and not (app / "venv").is_symlink()
    assert (app / "venv" / "pyvenv.cfg").read_bytes() == old_cfg
    assert version_file.read_text() == 'APP_VERSION = "0.3.18"\n'
    assert helper.read_bytes() == old_helper
    assert (app / ".venvs" / candidate).is_dir()  # immutable rollback reserve


def test_first_transition_runtime_rollback_is_replay_safe_after_conversion_undo(tmp_path):
    """Disk state after conversion undo is a valid resume boundary.

    The shell transaction can crash after rollback_conversion returns but
    before it writes runtime_restored. Replaying rollback_conversion must be a
    no-op success, while replaying rollback_activation would be incorrect
    because the selector is once again a real directory.
    """
    app, private = _mk_app(tmp_path)
    RS.open_store(str(app), UID, create=True)
    candidate = _second_runtime(app, rid="e" * 64)
    assert RS.convert_legacy(str(app), str(private), UID) == RS.LEGACY_ID
    assert RS.activate(str(app), str(private), candidate, UID) == RS.LEGACY_ID
    assert RS.rollback_activation(str(app), str(private), UID) == RS.LEGACY_ID
    RS.rollback_conversion(str(app), str(private), UID)

    assert (app / "venv").is_dir() and not (app / "venv").is_symlink()
    RS.rollback_conversion(str(app), str(private), UID)  # resumed replay
    assert (app / "venv").is_dir() and not (app / "venv").is_symlink()
    with pytest.raises(RS.RuntimeStoreError):
        RS.rollback_activation(str(app), str(private), UID)


def test_runtime_tree_rejects_broad_python_prefix_symlink_escape(tmp_path):
    app, _private = _mk_app(tmp_path)
    RS.open_store(str(app), UID, create=True)
    rid = _second_runtime(app, rid="e" * 64)
    external = tmp_path / "external-python-payload"
    external.write_text("payload")
    external.chmod(0o700)
    (app / RS.STORE_NAME / rid / "bin" / "python-attacker").symlink_to(external)

    with pytest.raises(RS.RuntimeStoreError, match="disallowed symlink"):
        RS.validate_runtime_tree(str(app), rid, UID)


def test_incomplete_attempt_discovery_rejects_foreign_objects(tmp_path):
    private = _private_state(tmp_path)
    RS.begin_update_attempt(
        str(private), "123456789abc", target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    RS.begin_update_attempt(
        str(private), "abcdefabcdef", target_version="0.3.19",
        source_commit="b" * 40, source_tag="v0.3.19", owner_uid=UID)
    RS.mark_update_attempt(
        str(private), "abcdefabcdef", "diagnostic_failure", owner_uid=UID)
    pending = RS.incomplete_update_attempts(str(private), UID)
    assert [doc["attempt_id"] for doc in pending] == ["123456789abc"]

    (private / "transactions" / "foreign").write_text("x")
    with pytest.raises(RS.RuntimeStoreError, match="foreign object"):
        RS.incomplete_update_attempts(str(private), UID)


# --- bootstrap rollback-reserve acceptance --------------------------------- #

def _bootstrap_reserve(private, aid, *, commit="a" * 40, tag="v0.3.19"):
    records = private / RS.BOOTSTRAP_RESERVE_DIR
    records.mkdir(mode=0o700)
    os.chmod(records, 0o700)
    work = private / f"bootstrap-{aid}"
    work.mkdir(mode=0o700)
    os.chmod(work, 0o700)
    doc = {
        "schema": 1,
        "attempt_id": aid,
        "work": str(work.resolve()),
        "source_commit": commit,
        "source_tag": tag,
        "target_version": tag[1:],
        "state": "staged",
        "history": ["staged"],
    }
    RS._write_json_atomic(str(records / f"{aid}.json"), doc, 0o600)
    return work


def test_bootstrap_reserve_requires_success_then_accepts_exact_tree(tmp_path):
    private = _private_state(tmp_path)
    aid = "123456789abc"
    work = _bootstrap_reserve(private, aid)
    RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    with pytest.raises(RS.RuntimeStoreError, match="successful update"):
        RS.mark_bootstrap_reserve_ready(str(private), aid, UID)
    _advance_success(private, aid)
    ready = RS.mark_bootstrap_reserve_ready(str(private), aid, UID)
    assert ready["state"] == "ready" and work.is_dir()

    accepted = RS.accept_bootstrap_reserve(
        str(private), aid, source_commit="a" * 40,
        source_tag="v0.3.19", owner_uid=UID)
    assert accepted["state"] == "accepted"
    assert accepted["history"][-2:] == ["acceptance_intent", "accepted"]
    assert not work.exists()
    # Exact-identity acceptance is idempotent after completion.
    assert RS.accept_bootstrap_reserve(
        str(private), aid, source_commit="a" * 40,
        source_tag="v0.3.19", owner_uid=UID) == accepted


def test_bootstrap_reserve_acceptance_resumes_after_delete_crash(tmp_path):
    private = _private_state(tmp_path)
    aid = "123456789abc"
    work = _bootstrap_reserve(private, aid)
    RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    _advance_success(private, aid)
    doc = RS.mark_bootstrap_reserve_ready(str(private), aid, UID)
    doc["state"] = "acceptance_intent"
    doc["history"].append("acceptance_intent")
    RS._write_json_atomic(
        RS._bootstrap_reserve_path(str(private), aid), doc, 0o600)
    work.rmdir()  # simulate crash after deletion but before accepted checkpoint

    resumed = RS.accept_bootstrap_reserve(
        str(private), aid, source_commit="a" * 40,
        source_tag="v0.3.19", owner_uid=UID)
    assert resumed["state"] == "accepted"


def test_bootstrap_reserve_refuses_identity_mismatch_and_substitution(tmp_path):
    private = _private_state(tmp_path)
    aid = "123456789abc"
    work = _bootstrap_reserve(private, aid)
    RS.begin_update_attempt(
        str(private), aid, target_version="0.3.19",
        source_commit="a" * 40, source_tag="v0.3.19", owner_uid=UID)
    _advance_success(private, aid)
    RS.mark_bootstrap_reserve_ready(str(private), aid, UID)
    with pytest.raises(RS.RuntimeStoreError, match="identity mismatch"):
        RS.accept_bootstrap_reserve(
            str(private), aid, source_commit="b" * 40,
            source_tag="v0.3.19", owner_uid=UID)

    work.rmdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    work.symlink_to(victim, target_is_directory=True)
    with pytest.raises(RS.RuntimeStoreError, match="changed during acceptance"):
        RS.accept_bootstrap_reserve(
            str(private), aid, source_commit="a" * 40,
            source_tag="v0.3.19", owner_uid=UID)
    assert victim.is_dir()
