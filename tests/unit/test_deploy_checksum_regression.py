# SPDX-License-Identifier: MIT
"""CP-001 — permanent regression for the deterministic-artifact rsync skip.

Reproduces the exact Stage-D failure mode (CCC-CAMP-0001, EV-007):
  * source and destination files with IDENTICAL size,
  * IDENTICAL deterministic mtime (epoch 0 / 1970),
  * DIFFERENT content ("0.3.13" -> "0.3.14", same byte length).
Proves that a plain `rsync -a --delete` SKIPS the changed file (the defect) and
that adding `--checksum` transfers it (the fix). Uses the real rsync binary in a
temp tree; skipped only if rsync is unavailable.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

_RSYNC = shutil.which("rsync")
pytestmark = pytest.mark.skipif(_RSYNC is None, reason="rsync binary not available")

_EPOCH = 0  # the deterministic-artifact timestamp (pack_tree sets member mtime=0)
_OLD = 'APP_VERSION = "0.3.13"\n'
_NEW = 'APP_VERSION = "0.3.14"\n'   # identical byte length to _OLD


def _tree(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (dst / "_version.py").write_text(_OLD)      # already-installed (stale) file
    (src / "_version.py").write_text(_NEW)      # new artifact content, same length
    for p in (src / "_version.py", dst / "_version.py"):
        os.utime(p, (_EPOCH, _EPOCH))           # deterministic mtime tie
    # precondition: the two files tie on size AND mtime
    assert (src / "_version.py").stat().st_size == (dst / "_version.py").stat().st_size
    assert int((src / "_version.py").stat().st_mtime) == int((dst / "_version.py").stat().st_mtime) == _EPOCH
    return src, dst


def _rsync(src, dst, *flags):
    subprocess.run([_RSYNC, "-a", *flags, "--delete", f"{src}/", f"{dst}/"],
                   check=True, capture_output=True)


def test_without_checksum_reproduces_the_skip(tmp_path):
    src, dst = _tree(tmp_path)
    _rsync(src, dst)                                   # no --checksum: quick-check tie -> skip
    assert (dst / "_version.py").read_text() == _OLD, \
        "expected the defect: same-size + mtime-0 file is SKIPPED, dest stays stale"


def test_with_checksum_transfers_the_change(tmp_path):
    src, dst = _tree(tmp_path)
    _rsync(src, dst, "--checksum")                     # CP-001 fix: compare by content
    assert (dst / "_version.py").read_text() == _NEW, \
        "with --checksum the changed file MUST be transferred"
