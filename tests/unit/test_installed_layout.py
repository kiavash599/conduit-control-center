"""tests/unit/test_installed_layout.py -- A5 installed-layout import proof.

Executes the EXACT shipped helper bytes under `python -I` (real isolated mode:
no cwd, no PYTHONPATH, no user site) from a fake installed root, with a foreign
cwd and a clean environment. No helper constants are rewritten and no test
backdoor exists: the ONLY way `backend.priv_state` can import is through the
helper's own self-location bootstrap (<app-root>/bin/<helper> -> <app-root>).

Success is proven by an OBSERVABLE marker, never inferred from an exit code:
the helper emits MARKER=PRIV_STATE_IMPORT_OK before it evaluates state dirs,
and MARKER=PRIV_STATE_IMPORT_FAILED when the bootstrap/import is broken --
so "import worked but state dirs are invalid" (expected here, since an
unprivileged fake root cannot satisfy the production root-owner check) is
cleanly distinguishable from "import never happened".
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="POSIX helpers")

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _fake_root(tmp_path):
    """<fakeroot>/opt-x/{bin/ccc-restore-apply, backend/{__init__,priv_state}.py}
    -- exact shipped bytes, no rewriting."""
    app = tmp_path / "opt-x"
    (app / "bin").mkdir(parents=True)
    (app / "backend").mkdir()
    shutil.copyfile(ROOT / "deployment" / "bin" / "ccc-restore-apply",
                    app / "bin" / "ccc-restore-apply")
    (app / "backend" / "__init__.py").write_text("")
    shutil.copyfile(ROOT / "backend" / "priv_state.py",
                    app / "backend" / "priv_state.py")
    return app


def _run(helper: pathlib.Path, tmp_path):
    """python -I, foreign cwd, clean env, empty stdin."""
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-I", str(helper), "apply"],
        input=b"", capture_output=True,
        cwd=str(foreign_cwd), env={"PATH": "/usr/bin:/bin"}, timeout=60)


def test_installed_helper_imports_backend_through_derived_root(tmp_path):
    app = _fake_root(tmp_path)
    r = _run(app / "bin" / "ccc-restore-apply", tmp_path)
    err = r.stderr.decode()
    # decisive, observable proof: the import SUCCEEDED via the derived root...
    assert "MARKER=PRIV_STATE_IMPORT_OK" in err, err
    assert "MARKER=PRIV_STATE_IMPORT_FAILED" not in err
    # ...and the subsequent failure is the EXPECTED unprivileged-state one
    # (production root-owner check cannot pass in an unprivileged fake root;
    # this is the documented split between import proof and ownership proof).
    assert "MARKER=STATE_DIRS_INVALID" in err, err
    assert r.returncode != 0                       # still fail-closed overall


def test_helper_outside_bin_layout_fails_closed(tmp_path):
    """The shipped bytes placed in a NON-bin layout must refuse (no fallback
    to a hardcoded absolute root)."""
    app = tmp_path / "opt-x"
    (app / "notbin").mkdir(parents=True)
    shutil.copyfile(ROOT / "deployment" / "bin" / "ccc-restore-apply",
                    app / "notbin" / "ccc-restore-apply")
    r = _run(app / "notbin" / "ccc-restore-apply", tmp_path)
    err = r.stderr.decode()
    assert "MARKER=BAD_LAYOUT" in err, err
    assert "MARKER=PRIV_STATE_IMPORT_OK" not in err
    assert r.returncode != 0


def test_update_helper_shipped_bytes_use_derived_root(tmp_path):
    """Same proof for ccc-update-apply: with backend present in the fake root,
    the fail-closed priv_state import must succeed (the state-boundary check is
    then the distinguishable next failure)."""
    app = tmp_path / "opt-x"
    (app / "bin").mkdir(parents=True)
    (app / "backend").mkdir()
    shutil.copyfile(ROOT / "deployment" / "bin" / "ccc-update-apply",
                    app / "bin" / "ccc-update-apply")
    (app / "backend" / "__init__.py").write_text("")
    for m in ("priv_state.py", "update_verify.py", "update_audit.py"):
        src = ROOT / "backend" / m
        if src.exists():
            shutil.copyfile(src, app / "backend" / m)
    r = _run(app / "bin" / "ccc-update-apply", tmp_path)
    err = r.stderr.decode()
    # POSITIVE import proof: the helper emits the success marker itself; an
    # ambiguous exit can never be mistaken for import success.
    assert "MARKER=PRIV_STATE_IMPORT_OK" in err, err
    assert "MARKER=PRIV_STATE_IMPORT_FAILED" not in err
    assert r.returncode != 0
