# SPDX-License-Identifier: MIT
"""ADR-0003 Phase B (Option 2-refined) — audit deployment contract.

Text/grep contract test (no runtime): the systemd unit grants the narrow write
allowance for the audit directory, and both the installer and updater provision
that directory as root:conduit-cc 0750 under the root-owned parent /var/log,
before the service starts/restarts (so the unit's ReadWritePaths bind succeeds).
"""
from __future__ import annotations

import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AUDIT_DIR = "/var/log/conduit-cc-audit"


def _read(rel):
    with open(os.path.join(_ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def test_service_unit_grants_readwrite_for_audit_dir():
    unit = _read("deployment/conduit-cc.service")
    assert f"ReadWritePaths={AUDIT_DIR}" in unit
    # ProtectSystem=strict must remain (the reason the allowance is needed)
    assert "ProtectSystem=strict" in unit
    # the audit dir must NOT be the service-owned diagnostic log dir
    assert "ReadWritePaths=/var/log/conduit-cc\n" not in unit


def test_installer_provisions_audit_dir_root_conduit_cc_0750():
    sh = _read("install.sh")
    assert AUDIT_DIR in sh
    # root:conduit-cc 0750 (APP_USER is conduit-cc); created before service start
    assert 'install -d -o root -g "${APP_USER}" -m 0750 /var/log/conduit-cc-audit' in sh


def test_updater_provisions_audit_dir_before_restart():
    sh = _read("update.sh")
    assert "install -d -o root -g conduit-cc -m 0750 /var/log/conduit-cc-audit" in sh


def test_audit_module_path_matches_deployment():
    mod = _read("backend/update_audit.py")
    assert 'AUDIT_DIR = "/var/log/conduit-cc-audit"' in mod
