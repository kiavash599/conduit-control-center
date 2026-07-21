"""tests/unit/test_env_checkpoint.py -- A-.env checkpoint/restore canonicalization.

The inner restore engine's checkpoint/rollback for `.env` must go entirely
through the canonical Python implementation: an in-memory snapshot (never a
pathname copy of a possibly symlinked file) and the canonical atomic writer
(exact 0600, byte-preserving including trailing newlines).
"""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="POSIX semantics")

# backend.backup.restore transitively needs compiled `cryptography`; where that
# is unavailable (sandbox), skip the module -- this is WSL/Linux-authoritative.
try:
    from backend.backup import restore as R  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    pytest.skip(f"backend.backup.restore unavailable: {_exc}", allow_module_level=True)


def test_checkpoint_snapshots_env_in_memory_not_by_path(tmp_path):
    ccc = tmp_path / "conf"
    ccc.mkdir()
    (ccc / ".env").write_text("A=1\nADMIN_PASSWORD_HASH='x'\n")   # trailing newline
    os.chmod(ccc / ".env", 0o600)
    ckpt, captured = R._make_checkpoint(str(ccc))
    # .env is captured as an in-memory tuple, NOT a filesystem path
    assert captured[".env"][0] == "__env_text__"
    assert captured[".env"][1] == "A=1\nADMIN_PASSWORD_HASH='x'\n"
    # no .env file copy exists in the checkpoint dir
    assert not (pathlib_join(ckpt, ".env"))


def pathlib_join(d, n):
    return os.path.exists(os.path.join(d, n))


def test_checkpoint_skips_symlinked_env(tmp_path):
    """A symlinked .env is never followed into the checkpoint (arbitrary-read
    class); it is skipped exactly like an absent file."""
    ccc = tmp_path / "conf"
    ccc.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("SECRET-HOST-FILE")
    os.symlink(str(victim), str(ccc / ".env"))
    ckpt, captured = R._make_checkpoint(str(ccc))
    assert ".env" not in captured                     # not followed
    assert victim.read_text() == "SECRET-HOST-FILE"   # untouched


def test_rollback_restores_env_via_canonical_writer(tmp_path):
    ccc = tmp_path / "conf"
    ccc.mkdir()
    (ccc / ".env").write_text("ORIG=1\n")
    os.chmod(ccc / ".env", 0o600)
    _ckpt, captured = R._make_checkpoint(str(ccc))
    # mutate the live file, then roll back
    (ccc / ".env").write_text("MUTATED=2\n")
    R._rollback(str(ccc), captured)
    assert (ccc / ".env").read_text() == "ORIG=1\n"           # exact bytes restored
    assert oct(os.stat(ccc / ".env").st_mode & 0o777) == "0o600"
