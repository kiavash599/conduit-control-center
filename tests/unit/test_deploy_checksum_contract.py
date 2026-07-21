# SPDX-License-Identifier: MIT
"""CP-001 — content-based deploy transfer, deployment contract.

Text/grep checks that BOTH rsync invocations in update.sh (the phase-3 deploy and
the phase-5 rollback restore) carry `--checksum`, so a deterministic-artifact
same-length content change can no longer be skipped by rsync's size+mtime
quick-check. Also confirms the exclude semantics were NOT changed.
"""
from __future__ import annotations

import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read(rel):
    with open(os.path.join(_ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def test_both_rsync_invocations_use_checksum():
    s = _read("update.sh")
    # deploy (SOURCE_DIR -> APP_DIR) and rollback restore (BACKUP_DIR/app -> APP_DIR)
    assert "rsync -a --checksum --delete" in s
    assert s.count("rsync -a --checksum --delete") >= 2, \
        "expected --checksum on BOTH the deploy and the rollback-restore rsync"
    # no bare (non-checksum) `rsync -a --delete` deploy/restore remains
    assert not re.search(r"rsync -a --delete\b", s), \
        "a bare `rsync -a --delete` (no --checksum) still remains"


def test_exclude_semantics_unchanged():
    s = _read("update.sh")
    shared_match = re.search(
        r"readonly -a CCC_LIFECYCLE_EXCLUDES=\(\n(.*?)\n\)", s, re.S)
    assert shared_match, "shared lifecycle exclude contract vanished"
    shared = set(re.findall(r"--exclude=([^\s]+)", shared_match.group(1)))
    assert shared == {"/venv", "/.venvs", "/trust", "/bin"}
    deploy = s.split("phase3_deploy()", 1)[1]
    deploy = deploy.split("rsync -a --checksum --delete", 1)[1]
    deploy = deploy.split("${SOURCE_DIR}/", 1)[0]
    assert '"${CCC_LIFECYCLE_EXCLUDES[@]}"' in deploy
    for pat in ("--exclude 'ccc.db'", "--exclude '__pycache__/'",
                "--exclude '.git/'", "--exclude '.env'"):
        assert pat in s, f"deploy exclude vanished: {pat}"
    # rollback restore still purges bytecode after restore (unchanged behaviour)
    assert "5c1 - Purging stale Python bytecode (post-restore)" in s
