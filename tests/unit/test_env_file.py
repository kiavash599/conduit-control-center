"""tests/unit/test_env_file.py -- canonical .env write contract (Epic 1, F7)."""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="POSIX mode/symlink semantics required")

from backend import env_file as E  # noqa: E402


def test_write_sets_0600_regular_file(tmp_path):
    p = tmp_path / ".env"
    E.write_env_text(str(p), "A=1\n")
    assert p.read_text() == "A=1\n"
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


def test_write_refuses_symlink(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("secret")
    link = tmp_path / ".env"
    link.symlink_to(victim)
    with pytest.raises(E.EnvFileError, match="not a single regular file"):
        E.write_env_text(str(link), "A=2\n")
    assert victim.read_text() == "secret"      # symlink never followed


def test_write_is_atomic_and_preserves_previous_on_failure(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    E.write_env_text(str(p), "ORIG=1\n")

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        E.write_env_text(str(p), "NEW=2\n")
    monkeypatch.setattr(os, "replace", real_replace)
    assert p.read_text() == "ORIG=1\n"                       # previous intact
    assert [f for f in os.listdir(tmp_path) if f.startswith(".env-")] == []  # no litter


def test_set_env_key_replaces_only_target_line(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nADMIN_PASSWORD_HASH='old'\nB=2\n")
    p.chmod(0o600)
    E.set_env_key(str(p), "ADMIN_PASSWORD_HASH=", "ADMIN_PASSWORD_HASH='new'\n")
    assert p.read_text() == "A=1\nADMIN_PASSWORD_HASH='new'\nB=2\n"
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


def test_set_env_key_appends_when_absent(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n")
    p.chmod(0o600)
    E.set_env_key(str(p), "ADMIN_PASSWORD_HASH=", "ADMIN_PASSWORD_HASH='h'\n")
    assert p.read_text() == "A=1\nADMIN_PASSWORD_HASH='h'\n"


def test_set_env_key_rejects_mismatched_value_line(tmp_path):
    p = tmp_path / ".env"
    p.write_text("")
    p.chmod(0o600)
    with pytest.raises(E.EnvFileError):
        E.set_env_key(str(p), "K=", "OTHER=1\n")


def test_read_rejects_noncanonical_mode_and_hardlink(tmp_path):
    p = tmp_path / ".env"
    p.write_text("CF_RECORD_NAME=example.test\n")
    p.chmod(0o644)
    with pytest.raises(E.EnvFileError, match="mode is not exact 0600"):
        E.read_env_text(str(p))
    p.chmod(0o600)
    alias = tmp_path / "alias"
    os.link(p, alias)
    with pytest.raises(E.EnvFileError, match="not a regular file"):
        E.read_env_text(str(p))


def test_write_refuses_hardlinked_destination(tmp_path):
    p = tmp_path / ".env"
    p.write_text("ORIGINAL=1\n")
    p.chmod(0o600)
    alias = tmp_path / "alias"
    os.link(p, alias)
    with pytest.raises(E.EnvFileError, match="not a single regular file"):
        E.write_env_text(str(p), "REPLACED=1\n")
    assert p.read_text() == "ORIGINAL=1\n"
    assert alias.read_text() == "ORIGINAL=1\n"
