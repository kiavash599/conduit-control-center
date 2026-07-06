# SPDX-License-Identifier: MIT
"""Option 1 (bytecode-cache purge) — behavioral proof.

Reproduces the deterministic-artifact + timestamp-`.pyc` collision — a same-size
source change with a matching source mtime makes CPython load the STALE cached
bytecode (the defect that made a deployed 0.3.14 report 0.3.13) — then proves a
venv-pruned `__pycache__`/`*.pyc` purge resolves it while leaving the venv
bytecode untouched. Stdlib-only / cross-platform; a fresh interpreter subprocess
is used so a cold import reads the on-disk `.pyc`.
"""
from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys

_MTIME = 100_000_000   # fixed source mtime shared by compile + "redeploy" (the collision condition)


def _purge_scoped(app: str) -> None:
    """Mirror of the shell `_purge_bytecode`: remove `__pycache__` dirs and
    `*.pyc` under `app`, pruning the venv subtree AND its children."""
    venv = os.path.join(app, "venv")

    def _under_venv(p: str) -> bool:
        return p == venv or (p + os.sep).startswith(venv + os.sep)

    for root, dirs, _ in os.walk(app, topdown=True):
        dirs[:] = [d for d in dirs if not _under_venv(os.path.join(root, d))]
        if os.path.basename(root) == "__pycache__":
            shutil.rmtree(root, ignore_errors=True)
    for root, dirs, files in os.walk(app, topdown=True):
        dirs[:] = [d for d in dirs if not _under_venv(os.path.join(root, d))]
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass


def _import_ver(app, cwd) -> str:
    env = dict(os.environ, PYTHONPATH=str(app))
    env.pop("SOURCE_DATE_EPOCH", None)   # keep default timestamp .pyc behaviour
    r = subprocess.run([sys.executable, "-c", "import mymod; print(mymod.VER)"],
                       capture_output=True, text=True, env=env, cwd=str(cwd))
    return r.stdout.strip()


def test_stale_pyc_collision_and_purge_resolves_it(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    cwd = tmp_path / "cwd"; cwd.mkdir()          # neutral cwd (no mymod.py here)
    mod = app / "mymod.py"

    # venv bytecode that MUST survive the purge (venv + children pruned)
    vpc = app / "venv" / "lib" / "pkg" / "__pycache__"; vpc.mkdir(parents=True)
    (vpc / "keep.cpython-x.pyc").write_bytes(b"\x00keep")
    (app / "venv" / "loose.pyc").write_bytes(b"\x00loose")

    # 1) 0.3.13 source at a fixed mtime, compiled -> pyc records (mtime, size)
    mod.write_text('VER = "0.3.13"\n')
    os.utime(mod, (_MTIME, _MTIME))
    py_compile.compile(str(mod), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    assert list((app / "__pycache__").glob("mymod.*.pyc")), "pyc not created"

    # 2) "deploy" 0.3.14 — SAME size, SAME mtime (the exact collision condition)
    mod.write_text('VER = "0.3.14"\n')            # identical length to 0.3.13
    os.utime(mod, (_MTIME, _MTIME))

    # 3) collision: a cold import loads the STALE 0.3.13 bytecode
    assert _import_ver(app, cwd) == "0.3.13"

    # 4) purge (venv pruned), then re-import -> fresh 0.3.14
    _purge_scoped(str(app))
    assert not list((app / "__pycache__").glob("mymod.*.pyc"))
    assert _import_ver(app, cwd) == "0.3.14"

    # 5) venv bytecode untouched (prune verified)
    assert (vpc / "keep.cpython-x.pyc").exists()
    assert (app / "venv" / "loose.pyc").exists()
