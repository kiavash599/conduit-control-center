"""tests/unit/test_ownership_validators.py -- A6 validator behavior.

Runs the REAL validator functions extracted from update.sh in a bash harness
against seeded fixtures. Ownership checks that require uid 0 are exercised via
their mode/symlink halves (the -user/-group root predicates cannot be satisfied
unprivileged and are covered by the text contract + device qualification).
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason="POSIX + bash required")

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _extract(fn_name: str) -> str:
    s = (ROOT / "update.sh").read_text(encoding="utf-8")
    m = re.search(rf"{fn_name}\(\) \{{.*?\n\}}", s, re.S)
    assert m, f"{fn_name} not found"
    return m.group(0)


def _run_validator(tmp_path, app_dir: pathlib.Path, fn: str, drop_user_checks=True):
    body = _extract(fn)
    if drop_user_checks:
        # unprivileged harness: the uid/gid-root predicates cannot pass; strip
        # them so the MODE/SYMLINK halves are exercised for real.
        body = body.replace("-not -user root -o -not -group root -o ", "")
        body = body.replace("-not -user root -o ", "")
    script = f"""
set -u
APP_DIR="{app_dir}"
info() {{ :; }}
{body}
{fn}
echo VALIDATOR_PASSED
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_world_writable_file_rejected(tmp_path):
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    f = app / "backend" / "mod.py"
    f.write_text("x")
    f.chmod(0o666)                                     # root:root 0666 class
    r = _run_validator(tmp_path, app, "_verify_app_dir_ownership")
    assert "VALIDATOR_PASSED" not in r.stdout
    assert "invariant violated" in r.stderr


def test_world_writable_directory_rejected(tmp_path):
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    (app / "backend").chmod(0o777)
    r = _run_validator(tmp_path, app, "_verify_app_dir_ownership")
    assert "VALIDATOR_PASSED" not in r.stdout


def test_setuid_file_rejected(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    f = app / "tool"
    f.write_text("x")
    f.chmod(0o4755)
    r = _run_validator(tmp_path, app, "_verify_app_dir_ownership")
    assert "VALIDATOR_PASSED" not in r.stdout


def test_foreign_symlink_rejected_but_selector_pruned(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "backend").mkdir()
    (app / "backend" / "mod.py").write_text("x")
    (app / "backend" / "mod.py").chmod(0o644)
    (app / "backend").chmod(0o755)
    app.chmod(0o755)
    # the selector symlink at /venv is PRUNED (sanctioned)...
    (app / ".venvs").mkdir()
    (app / ".venvs").chmod(0o755)
    (app / "venv").symlink_to(".venvs/x")
    r = _run_validator(tmp_path, app, "_verify_app_dir_ownership")
    assert "VALIDATOR_PASSED" in r.stdout, r.stderr
    # ...but a symlink anywhere ELSE in the closure is rejected
    (app / "backend" / "evil-link").symlink_to("/etc/passwd")
    r = _run_validator(tmp_path, app, "_verify_app_dir_ownership")
    assert "VALIDATOR_PASSED" not in r.stdout


def test_clean_tree_passes(tmp_path):
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    (app / "backend" / "mod.py").write_text("x")
    (app / "backend" / "mod.py").chmod(0o644)
    (app / "backend").chmod(0o755)
    app.chmod(0o755)
    r = _run_validator(tmp_path, app, "_verify_app_dir_ownership")
    assert "VALIDATOR_PASSED" in r.stdout, r.stderr


def test_store_validator_delegates_and_is_not_bash_harness_testable(tmp_path):
    # _verify_store_ownership now delegates to `_rt validate-store-shape`
    # (backend/runtime_store.py::validate_store_shape) instead of a raw `find`
    # -- fixing the same interpreter-symlink false-positive class that
    # `_verify_venv_ownership` was already fixed for. Like that sibling, it is
    # deliberately EXCLUDED from this minimal shell-only `_run_validator`
    # harness: `_rt` is a python3 invocation of the installed CLI and is not
    # defined here, so extracting just the function body and running it
    # standalone would only prove "_rt: command not found" fails closed, not
    # exercise real ownership/symlink semantics. The real behavior -- accepts
    # a genuine multi-candidate store with real interpreter symlinks, rejects
    # group-writable manifests/candidates, hardlinks, and foreign objects -- is
    # covered against the real implementation, with real venvs, in
    # tests/unit/test_runtime_store.py's test_store_shape_* suite.
    app = tmp_path / "app"
    (app / ".venvs" / "r1").mkdir(parents=True)
    (app / ".venvs" / "r1").chmod(0o775)
    r = _run_validator(tmp_path, app, "_verify_store_ownership")
    assert "_rt: command not found" in r.stderr
    assert "VALIDATOR_PASSED" not in r.stdout


def test_validators_are_wired_into_lifecycle_sites():
    s = (ROOT / "update.sh").read_text(encoding="utf-8")
    deploy = s[s.index("phase3_deploy"):s.index("phase3b_start_service")]
    assert "_verify_app_dir_ownership" in deploy
    assert "_verify_store_ownership" in deploy
    assert "_verify_trust_dir" in deploy
    roll = s[s.index("phase5_rollback"):]
    assert "_verify_app_dir_ownership" in roll
    assert "_verify_trust_dir" in roll
    # the write-bit predicate is present in the committed validator (non-vacuity
    # partner: removing /022 makes the seeded 0666 test fail)
    assert "-perm /022" in s
