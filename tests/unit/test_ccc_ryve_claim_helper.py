# SPDX-License-Identifier: MIT
"""Epic #3 R1: static guards for the ccc-ryve-claim helper + install/update wiring.

Pure file-content assertions (no execution; the `ryve-claim` binary is not
present in CI). Enforces the R1 binding constraints: umask 077 before the temp
PNG, a unique /tmp target, `--output` only, immediate unlink, PNG-magic
validation, no base64, no data-dir PNG path, no logging of Conduit output, and
append-if-missing sudoers wiring in install.sh / update.sh.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
HELPER = ROOT / "deployment" / "bin" / "ccc-ryve-claim"
INSTALL = ROOT / "install.sh"
UPDATE = ROOT / "update.sh"

HELPER_DST = "/opt/conduit-cc/bin/ccc-ryve-claim"
GRANT = "(conduit) NOPASSWD: /opt/conduit-cc/bin/ccc-ryve-claim"


def _helper() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_helper_sets_umask_077_before_temp():
    src = _helper()
    assert "os.umask(0o077)" in src
    assert src.index("os.umask(0o077)") < src.index("mkstemp")


def test_helper_uses_unique_tmp_target():
    src = _helper()
    assert "mkstemp" in src
    assert 'dir="/tmp"' in src


def test_helper_runs_ryve_claim_with_output_only():
    src = _helper()
    assert '"ryve-claim"' in src
    assert '"--output", tmp_path' in src
    # --output is the ONLY ryve-claim flag the helper passes.
    assert '"--name"' not in src


def test_helper_unlinks_temp_immediately():
    assert "os.unlink(tmp_path)" in _helper()


def test_helper_validates_png_magic():
    src = _helper()
    assert "PNG_MAGIC" in src
    assert "startswith(PNG_MAGIC)" in src


def test_helper_no_base64():
    assert "base64" not in _helper()


def test_helper_no_datadir_png_path():
    src = _helper()
    assert "ryve-claim-qr.png" not in src
    assert "/var/lib/conduit/data/ryve" not in src


def test_helper_no_logging_of_conduit_output():
    src = _helper()
    # No logging facility, and the captured Conduit output is never written out.
    assert "import logging" not in src
    assert "logging." not in src
    assert "logger" not in src
    assert "write(proc.stdout" not in src
    assert "write(proc.stderr" not in src


def test_helper_pipe_captures_conduit_output():
    # Conduit stdout/stderr must be captured (never inherited to journald).
    src = _helper()
    assert "stdout=subprocess.PIPE" in src
    assert "stderr=subprocess.PIPE" in src


def test_install_wires_helper_and_sudoers_grant():
    txt = INSTALL.read_text(encoding="utf-8")
    assert HELPER_DST in txt          # helper is installed
    assert GRANT in txt               # (conduit) sudoers grant present


def test_update_appends_helper_grant_if_missing():
    txt = UPDATE.read_text(encoding="utf-8")
    assert "_rv_helper_dst" in txt
    assert 'grep -qF "(conduit) NOPASSWD: ${_rv_helper_dst}"' in txt
    assert HELPER_DST in txt
