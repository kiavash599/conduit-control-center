# SPDX-License-Identifier: MIT
"""Option 1 (bytecode-cache purge) — deployment contract.

Text/grep checks that update.sh and install.sh contain the venv-pruned
__pycache__/*.pyc purge (scoped to ${APP_DIR}), with the required step markers,
and that rsync exclude semantics were NOT changed.
"""
from __future__ import annotations

import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PRUNE = '-path "${APP_DIR}/venv" -o -path "${APP_DIR}/venv/*"'


def _read(rel):
    with open(os.path.join(_ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def test_update_sh_defines_and_calls_scoped_purge():
    s = _read("update.sh")
    assert "_purge_bytecode()" in s                         # helper defined
    assert _PRUNE in s                                       # venv AND children pruned
    assert "-type d -name __pycache__" in s
    assert "-name '*.pyc'" in s
    assert '"${APP_DIR}"' in s                               # strictly scoped
    # explicit worker-log step markers, in the deploy and rollback paths
    assert "3b1 - Purging stale Python bytecode" in s
    assert "5c1 - Purging stale Python bytecode (post-restore)" in s
    # helper defined once + called in deploy + called in rollback
    assert s.count("_purge_bytecode") >= 3


def test_install_sh_contains_scoped_purge():
    s = _read("install.sh")
    assert "2b1" in s and "Purging stale Python bytecode" in s
    assert _PRUNE in s
    assert "-type d -name __pycache__" in s
    assert "-name '*.pyc'" in s
    assert '"${APP_DIR}"' in s


def test_rsync_exclude_semantics_unchanged():
    # constraint: do NOT change rsync exclude semantics — the deploy + rollback
    # rsyncs still exclude __pycache__ (the purge is a separate, explicit step).
    up = _read("update.sh")
    assert "--exclude '__pycache__/'" in up
    ins = _read("install.sh")
    assert "--exclude '__pycache__/'" in ins
