"""tests/unit/test_epic1_ownership_contract.py -- static ownership-closure gates.

Text-contract regressions that fail closed if the Epic-1 boundary is reopened:
  * no broad `chown -R <service> ... APP_DIR` reappears in install/update/rollback;
  * deploy rsync normalizes ownership explicitly (root, not source-preserved);
  * the privileged status path is the root-published public path, not the
    service-writable StateDirectory;
  * root helpers do not read/execute service-writable interpreter/module paths
    (the transitive trust closure).
These run on every platform (pure text); no root, no devices.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_no_recursive_service_chown_of_appdir():
    """The exact F1/F6 defect must never return in any form."""
    pat = re.compile(r"chown\s+-R\s+[\"']?\$\{APP_USER\}")
    for f in ("install.sh", "update.sh"):
        assert not pat.search(_read(f)), f"{f} reintroduced chown -R APP_USER"


def test_deploy_rsync_normalizes_root_ownership():
    for f in ("install.sh", "update.sh"):
        s = _read(f)
        assert "--chown=root:root" in s, f"{f} missing rsync --chown=root:root"
        assert "--chmod=D0755,F0644" in s, f"{f} missing rsync --chmod normalization"


def test_appdir_ownership_verification_present():
    for f in ("install.sh", "update.sh"):
        assert "_verify_app_dir_ownership" in _read(f)
        assert "_secure_legacy_venv" in _read(f)


def test_status_uses_public_root_owned_path_not_statedir():
    """F2: the service reads status from the root-published public dir; it must
    not read/write the old service-writable StateDirectory status path."""
    upd = _read("backend/api/update.py")
    assert "/var/lib/ccc-status/update-status.json" in upd
    assert "/var/lib/conduit-cc/update-status.json" not in upd
    bak = _read("backend/api/backup.py")
    assert "/var/lib/ccc-status/restore-status.json" in bak
    assert "/var/lib/conduit-cc/restore-status.json" not in bak


def test_update_helper_uses_private_state_dir():
    h = _read("deployment/bin/ccc-update-apply")
    assert 'PRIVATE_DIR = "/var/lib/ccc-update"' in h
    assert 'PUBLIC_STATUS_DIR = "/var/lib/ccc-status"' in h
    # the fixed-name plain-open temp status writer is gone
    assert 'f"{STATUS_PATH}.tmp"' not in h
    assert "priv_state" in h                    # publishes via the safe writer


def test_restore_helper_uses_private_state_and_safe_publisher():
    h = _read("deployment/bin/ccc-restore-apply")
    assert 'PRIVATE_DIR = "/var/lib/ccc-update"' in h
    assert "publish_status" in h
    assert 'tempfile.mkstemp(prefix=".restore-status-"' not in h   # replaced


def test_update_restore_use_one_mutex_and_separate_fixed_units():
    update = _read("deployment/bin/ccc-update-apply")
    restore = _read("deployment/bin/ccc-restore-apply")
    lock = 'LOCK_PATH = f"{PRIVATE_DIR}/lifecycle.lock"'
    assert lock in update
    assert lock in restore
    for helper in (update, restore):
        assert 'UPDATE_UNIT = "ccc-update.service"' in helper
        assert 'RESTORE_UNIT = "ccc-restore.service"' in helper
    assert 'SYSTEMD_RUN = "/usr/bin/systemd-run"' in restore
    assert '"__run-worker"' in restore


def test_update_source_identity_is_authorized_before_acceptance_ack():
    update = _read("deployment/bin/ccc-update-apply")
    body = update[update.index("def apply_cmd()"):
                  update.index("def main()")]
    commit_gate = body.index("verified manifest source commit is unavailable")
    tag_gate = body.index("verified manifest source tag/version binding is unavailable")
    ack = body.index('sys.stdout.write(f"accepted {update_id}\\n")')
    launch = body.index("rc = _launch_update_unit")
    assert commit_gate < ack
    assert tag_gate < ack
    assert ack < launch


def test_restore_secret_handoff_is_fifo_only_and_service_stop_is_complete():
    restore = _read("deployment/bin/ccc-restore-apply")
    unit = _read("deployment/conduit-cc.service")
    assert "os.mkfifo(payload_fifo" in restore
    assert "os.mkfifo(ack_fifo" in restore
    assert "os.fork(" not in restore
    assert "os.setsid(" not in restore
    launch = restore[restore.index("def _launch_restore_unit"):
                      restore.index("def _validate_fifo")]
    assert "blob" not in launch
    assert "passphrase" not in launch
    assert "KillMode=control-group" in unit
    assert "KillMode=process" not in unit


def test_root_helpers_do_not_depend_on_service_writable_paths():
    """Transitive closure: the interpreter/modules/scripts a root helper uses
    live under root-owned /opt/conduit-cc. The unit must not have re-widened
    APP_DIR ownership (checked above); here we assert the helpers reference the
    trusted installed paths, never a service-owned temp/interpreter."""
    for h in ("deployment/bin/ccc-update-apply", "deployment/bin/ccc-restore-apply"):
        s = _read(h)
        assert "/opt/conduit-cc" in s
        assert "/tmp/" not in s          # no execution/import from a world-writable tmp


def test_old_service_runtime_is_never_executed_by_root_during_update():
    """The v0.3.18 venv is untrusted until the stopped-service conversion.

    Backup and candidate staging may inspect its shape but must not invoke its
    interpreter. The prior diagnostic pip-freeze was not a rollback input.
    """
    update = _read("update.sh")
    phase1 = update[update.index("phase1_backup() {"):update.index("_rotate_backups() {")]
    phase2 = update[update.index("phase2_preinstall() {"):
                    update.index("#  Phase 2b - Conduit")]
    assert '"${APP_DIR}/venv/bin/python3"' not in phase1 + phase2
    assert "pip freeze" not in phase1 + phase2
    assert "validate-legacy-shape" in update


def test_unit_grants_state_dirs_but_ownership_is_the_boundary():
    u = _read("deployment/conduit-cc.service")
    assert "ReadWritePaths=/var/lib/ccc-update" in u
    assert "ReadWritePaths=/var/lib/ccc-status" in u
    assert "ProtectSystem=strict" in u


def test_trust_anchor_dir_provisioned_and_ceremony_installed():
    ins = _read("install.sh")
    assert '"${APP_DIR}/trust"' in ins
    assert "ccc-provision-trust-anchor" in ins
    assert "ccc-provision-trust-anchor" in _read("update.sh")


def test_env_contract_is_0600_everywhere():
    assert '".env": 0o600' in _read("backend/backup/restore.py")
    assert '".env": 0o640' not in _read("backend/backup/restore.py")
    assert "set_env_key" in _read("backend/api/settings.py")
