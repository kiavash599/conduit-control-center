"""tests/unit/test_update_lifecycle_contract.py -- R2 shell lifecycle contracts.

Text contracts over update.sh/install.sh proving the accepted architecture is
actually wired: no pip against active/previous runtime, no pip self-upgrade,
.env excluded in BOTH tar directions, state-aware rollback branching, exact
helper/sudoers rollback, selector-based rollback (never reinstall), and the
candidate build-before-downtime ordering.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UPDATE = (ROOT / "update.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")


def test_no_pip_self_upgrade_anywhere():
    for s in (UPDATE, INSTALL):
        assert "--upgrade pip" not in s
        assert "install --quiet --upgrade pip" not in s


def test_lifecycle_engines_pin_deterministic_umask():
    for script in (UPDATE, INSTALL):
        assert script.count("umask 022") == 1
        assert script.index("set -euo pipefail") < script.index("umask 022")
        assert script.index("umask 022") < script.index("readonly APP_USER=")


def test_pip_policy_env_hardening_present():
    for s in (UPDATE, INSTALL):
        assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in s
        assert "PIP_NO_INPUT=1" in s


def test_candidate_build_before_downtime():
    # candidate staged+finalized in phase2 (service running) ...
    assert "Building candidate runtime in attempt-owned staging (service running)" in UPDATE
    assert "stage-candidate" in UPDATE and "finalize-candidate" in UPDATE
    # ... activated only in the downtime deploy phase
    assert "Activating candidate runtime (atomic selector flip)" in UPDATE
    # ordering: phase2_preinstall before phase3_deploy
    order = UPDATE[UPDATE.index("phase0_preflight\n"):]
    assert order.index("phase2_preinstall") < order.index("phase3_deploy")


def test_candidate_crash_recovery_is_wired_through_both_runtime_clis():
    installed = (ROOT / "deployment" / "bin" / "ccc-runtime").read_text()
    bootstrap = (ROOT / "deployment" / "bootstrap" / "ccc-bootstrap-runtime").read_text()
    for cli in (installed, bootstrap):
        assert 'argv[0] == "reconcile-candidate"' in cli
        assert "reconcile_candidate_attempt" in cli
    recovery = _recovery_function()
    assert '_rt reconcile-candidate "${_old_candidate}" "${_old_id}"' in recovery


def test_pre_downtime_failure_reconciles_candidate_before_terminal_record():
    on_exit = UPDATE[UPDATE.index("_on_exit() {"):UPDATE.index("_print_manual_recovery() {")]
    assert '"${_durable_phase:-}" == "candidate_intent"' in on_exit
    assert '"${_durable_phase:-}" == "candidate_ready"' in on_exit
    reconcile = on_exit.index('_rt reconcile-candidate "${_failed_candidate}"')
    terminal = on_exit.index("_tx_mark diagnostic_failure", reconcile)
    assert reconcile < terminal
    assert "transaction remains nonterminal for recovery" in on_exit


def test_candidate_bytes_and_validated_manifest_are_durable_before_publication():
    runtime = (ROOT / "backend" / "runtime_store.py").read_text()
    start = runtime.index("def finalize_candidate(")
    end = runtime.index("\ndef build_candidate(", start)
    finalize = runtime[start:end]
    assert 'state="building"' not in finalize
    assert finalize.index("_fsync_tree(staging)") < finalize.index("write_manifest(")
    assert finalize.index('state="validated"') < finalize.index("os.rename(staging, final)")


def test_legacy_service_owned_app_root_is_tightened_before_store_creation():
    assert "_secure_legacy_app_root()" in UPDATE
    execution = UPDATE[UPDATE.index("phase0_preflight\n"):]
    assert execution.index("phase1_backup") < execution.index("phase2_preinstall")
    phase1 = UPDATE[UPDATE.index("phase1_backup() {"):UPDATE.index("_rotate_backups() {")]
    assert phase1.index("_tx_mark ownership_intent") < phase1.index("_secure_legacy_app_root")
    assert phase1.index("_secure_legacy_app_root") < phase1.index("_tx_mark ownership_complete")


def test_legacy_runtime_is_shape_gated_and_secured_before_root_executes_it():
    phase1 = UPDATE[UPDATE.index("phase1_backup() {"):UPDATE.index("_rotate_backups() {")]
    assert "_secure_legacy_venv" not in phase1
    assert "pip freeze" not in phase1
    assert 'venv/bin/python3"' not in phase1
    secure = UPDATE[UPDATE.index("_secure_legacy_venv() {"):UPDATE.index("_provision_priv_state_dirs() {")]
    shape_gate = secure.index("_rt validate-legacy-shape")
    mutation = secure.index("chown -hR")
    full_gate = secure.index("_rt validate-legacy \\")
    assert shape_gate < mutation < full_gate
    verifier = UPDATE[UPDATE.index("_verify_venv_ownership() {"):
                      UPDATE.index("_secure_legacy_venv() {")]
    assert "validate-legacy" in verifier
    assert 'find "${APP_DIR}/venv"' not in verifier
    phase2 = UPDATE[UPDATE.index("phase2_preinstall() {"):UPDATE.index("#  Phase 2b - Conduit")]
    assert "_secure_legacy_venv" not in phase2
    assert "_verify_runtime_pre_downtime" in phase2
    conversion = UPDATE[UPDATE.index('if [[ ! -L "${APP_DIR}/venv" ]]'):
                        UPDATE.index("step \"3a1t - Trust-anchor transaction")]
    assert conversion.index("_tx_mark conversion_intent") < conversion.index("_secure_legacy_venv")
    assert conversion.index("_secure_legacy_venv") < conversion.index("_rt convert-legacy")


def test_legacy_validation_surface_exists_in_both_runtime_clis():
    for rel in (
        "deployment/bin/ccc-runtime",
        "deployment/bootstrap/ccc-bootstrap-runtime",
    ):
        cli = (ROOT / rel).read_text()
        assert "validate_legacy_shape" in cli
        assert "validate_legacy_runtime" in cli
    assert UPDATE.index("_tx_mark ownership_complete") < UPDATE.index("stage-candidate")
    secure = UPDATE[UPDATE.index("_secure_legacy_app_root() {"):
                    UPDATE.index("_provision_priv_state_dirs() {")]
    assert 'chown root:root "${APP_DIR}"' in secure
    assert 'chmod 0755 "${APP_DIR}"' in secure
    assert "must be a real directory for ownership transition" in secure


def test_preflight_env_read_uses_only_canonical_nonsecret_cli():
    assert 'CCC_ENV_TOOL="/opt/conduit-cc/bin/ccc-env"' in UPDATE
    env_reader = UPDATE[UPDATE.index("_env_val() {"):UPDATE.index("_read_version() {")]
    assert 'get-key "${CONF_DIR}/.env"' in env_reader
    assert "grep" not in env_reader
    assert "cut" not in env_reader
    preflight = UPDATE[UPDATE.index("phase0_preflight() {"):
                       UPDATE.index("phase1_backup() {")]
    assert preflight.index("_validate_env_tool") < preflight.index("_env_val CF_RECORD_NAME")


def test_candidate_version_is_parsed_as_data_never_executed():
    for script in (UPDATE, INSTALL):
        assert "exec(open(" not in script
        assert "_version.py" in script
        assert "count != 1" in script
    assert "grep -oP 'APP_VERSION" not in UPDATE
    assert "grep -oP 'APP_VERSION" not in INSTALL


def test_m2_is_verify_only_not_a_second_privileged_artifact_writer():
    m2 = UPDATE[UPDATE.index("phase_m2_config_write_artifacts() {"):
                UPDATE.index("phase_bs1_reduced_guard() {")]
    assert "single Phase-3 writer" in m2
    assert ">> \"${_sudoers}\"" not in m2
    assert 'install -o root -g root -m 0755 "${_helper_src}"' not in m2
    assert "install -d" not in m2
    assert "cp \"${_unit_src}\"" not in m2
    assert "systemctl daemon-reload" not in m2
    assert 'grep -Fxq "${_app_user} ALL=(root)' in m2
    assert 'visudo -cf "${_sudoers}"' in m2
    assert 'cmp -s <(sed' in m2


def test_systemd_units_have_one_transactional_writer_and_exact_rollback():
    phase2b = UPDATE[UPDATE.index("phase2b_conduit_update() {"):
                     UPDATE.index("_conduit_rollback() {")]
    assert "/etc/systemd/system/conduit.service" not in phase2b

    phase3 = UPDATE[UPDATE.index("phase3_deploy() {"):
                    UPDATE.index("#  Phase 4 - Health verification")]
    assert '_tx_mark deploy_intent' in phase3
    assert '_install_unit_atomic "${APP_DIR}/deployment/conduit-cc.service"' in phase3
    assert '_install_unit_atomic "${APP_DIR}/deployment/conduit.service"' in phase3
    assert phase3.index("_tx_mark deploy_intent") < phase3.index("_install_unit_atomic")
    assert phase3.index("phase_m2_config_write_artifacts") < phase3.index("_tx_mark deployed")

    backup = UPDATE[UPDATE.index("phase1_backup() {"):UPDATE.index("_rotate_backups() {")]
    assert "conduit-service-present" in backup
    assert "conduit-dropin-dir-present" in backup
    rollback = UPDATE[UPDATE.index("5e - Restoring exact systemd unit state"):
                      UPDATE.index("5f - Re-applying nginx configuration")]
    assert '_install_unit_atomic "${BACKUP_DIR}/conduit-cc.service"' in rollback
    assert '_install_unit_atomic "${BACKUP_DIR}/conduit.service"' in rollback
    assert "restored to recorded absence" in rollback
    assert "new conduit drop-in directory is not empty; refusing removal" in rollback


def test_no_pip_against_active_or_previous_runtime():
    # dependency install targets the CANDIDATE python (_cand_py), never APP_DIR/venv
    assert 'install_python_deps "${_cand_py}"' in UPDATE
    # rollback restores the runtime via the SELECTOR, never a force-reinstall
    assert "force-reinstall" not in UPDATE
    assert "selector-based rollback" in UPDATE


def test_env_excluded_in_both_tar_directions():
    assert "tar --exclude='etc/conduit-cc/.env' -czf" in UPDATE   # creation
    assert "tar --exclude='etc/conduit-cc/.env' -xzf" in UPDATE   # extraction
    # manual recovery text matches
    assert "--exclude='etc/conduit-cc/.env' -xzf" in UPDATE


def test_transaction_aware_rollback_uses_durable_facts_and_disk_state():
    assert "_get_attempt_state" not in UPDATE
    assert "_ATTEMPT_STATE_FILE" not in UPDATE
    assert "5a1 - Runtime selector rollback (transaction-aware)" in UPDATE
    for fact in (
        "candidate_id", "previous_runtime", "activation_done",
        "converted_by_attempt",
    ):
        assert f"_tx_fact {fact}" in UPDATE
    assert '"${_current_runtime}" == "${_candidate_id}"' in UPDATE
    assert '"${_current_runtime}" == "${_previous_runtime}"' in UPDATE
    assert '-d "${APP_DIR}/venv" && ! -L "${APP_DIR}/venv"' in UPDATE
    assert "recorded activation has an unclassifiable selector state" in UPDATE
    # selector rollback happens BEFORE code restore (5a1 before 5b/5c)
    assert UPDATE.index("5a1 - Runtime selector rollback") < UPDATE.index("5b  Restore")


def test_shared_mutations_are_bracketed_by_transaction_checkpoints():
    required = (
        "ownership_intent", "ownership_complete", "backup_intent", "backup_complete",
        "candidate_intent", "candidate_ready",
        "downtime_intent", "downtime_started", "conversion_intent",
        "conversion_complete", "trust_intent", "trust_complete",
        "activation_intent", "activated", "deploy_intent", "deployed",
        "service_start_intent", "service_started", "health_verified", "success",
        "rollback_started", "runtime_restored", "files_restored",
        "service_restore_intent", "rolled_back", "diagnostic_failure",
    )
    for phase in required:
        assert f"_tx_mark {phase}" in UPDATE


def test_durable_success_wins_over_the_process_local_trap_flag():
    trap = UPDATE[UPDATE.index("_on_exit() {"):UPDATE.index("trap '_on_exit' EXIT")]
    durable = trap.index('_durable_phase="$(_tx_phase')
    success = trap.index('== "success"')
    rollback = trap.index('phase5_rollback')
    assert durable < success < rollback


def test_backup_creation_and_retention_are_attempt_record_authorized():
    phase1 = UPDATE[UPDATE.index("phase1_backup() {"):UPDATE.index("_rotate_backups() {")]
    assert '${_ts}-${CCC_UPDATE_ATTEMPT_ID}' in phase1
    assert "backup path collision before intent" in phase1
    assert phase1.index("_tx_mark backup_intent") < phase1.index('mkdir -m 0700 "${BACKUP_DIR}"')
    assert 'mkdir -p "${BACKUP_DIR}"' not in phase1
    rotate = UPDATE[UPDATE.index("_rotate_backups() {"):
                    UPDATE.index("# --------------------------------------------------------------------------- #\n#  Phase 2")]
    assert "attempt-backups" in rotate
    assert "_validate_recorded_backup_dir" in rotate
    assert "rm -rf --one-file-system" in rotate
    assert "find \"${BACKUP_ROOT}\"" not in rotate
    assert "xargs rm" not in rotate
    trap = UPDATE[UPDATE.index("_on_exit() {"):UPDATE.index("trap '_on_exit' EXIT")]
    assert '== "backup_intent"' in trap
    assert "_validate_recorded_backup_dir" in trap
    assert "transaction remains nonterminal for recovery" in trap


def test_backup_retention_mutates_only_after_durable_success():
    backup = UPDATE[UPDATE.index("phase1_backup() {"):UPDATE.index("_rotate_backups() {")]
    summary = UPDATE[UPDATE.index("phase6_summary() {"):]
    assert "_rotate_backups" not in backup
    assert summary.index("_rotate_backups") < summary.index("Post-update review")
    assert UPDATE.index("_tx_mark success") < UPDATE.index("phase6_summary")


def test_candidate_identity_is_out_of_band_and_never_payload_self_asserted():
    assert "release/candidate-identity.env" not in UPDATE
    assert "unknown-source" not in UPDATE
    assert "CCC_AUTHORIZED_SOURCE_COMMIT" in UPDATE
    assert '[[ "${_tag}" == "v${NEW_VERSION_ID}" ]]' in UPDATE


def test_fresh_install_identity_comes_from_verified_release_manifest_inputs():
    assert "--authorized-identity-file" in INSTALL
    assert "--authorized-source-commit" in INSTALL
    assert "--authorized-source-tag" in INSTALL
    assert "INSTALL_SOURCE_COMMIT" in INSTALL
    assert "INSTALL_SOURCE_TAG" in INSTALL
    assert "git rev-parse" not in INSTALL
    assert 'getattr(os, "O_NOFOLLOW", 0)' in INSTALL
    assert "identity schema mismatch" in INSTALL
    assert "never both" in INSTALL


def test_exact_helper_and_sudoers_rollback():
    assert "Backing up installed privileged helpers + sudoers (exact bytes)" in UPDATE
    assert "bin-manifest.nul" in UPDATE                  # unambiguous NUL inventory
    assert "rm -rf /opt/conduit-cc/bin" in UPDATE        # exact fixed path, incl dotfiles/nesting
    assert "rebuilt from the exact previous byte set" in UPDATE
    assert "helper directory was absent before update" in UPDATE
    assert "invalid helper-directory presence record" in UPDATE
    assert "same-name nested objects" in UPDATE
    assert "sudoers-present" in UPDATE                   # absent/present recorded
    assert "visudo -cf" in UPDATE                        # sudoers validated on restore
    assert "sudoers was absent before update - new file removed" in UPDATE
    assert "invalid or incomplete sudoers presence record" in UPDATE


def test_trust_anchor_preserved_across_rollback():
    # the authorized anchor is a deliberate security transition, never restored
    # from backup
    assert "trust anchor provisioned during this attempt" in UPDATE
    assert "PRESERVED (never restored from any backup)" in UPDATE


def test_fresh_install_builds_candidate_before_initial_selector_publication():
    assert "python3 -m venv \"${APP_DIR}/venv\"" not in INSTALL
    assert "ccc-runtime convert-legacy" not in INSTALL
    stage = INSTALL.index("stage-candidate")
    deps = INSTALL.index('install_python_deps "${_candidate_py}"')
    finalize = INSTALL.index("finalize-candidate")
    activate = INSTALL.index("activate-initial")
    assert stage < deps < finalize < activate
    assert "no runtime selector was published" in INSTALL
    reconcile = INSTALL.index("reconcile-candidate", stage - 1000)
    assert reconcile < stage
    assert "cannot reconcile prior initial-runtime candidate publication" in INSTALL


def test_interpreter_bound_execution():
    assert "python3 -m uvicorn" in (ROOT / "deployment" / "conduit-cc.service").read_text()
    # every pip call is -m pip
    assert "/venv/bin/pip " not in UPDATE and "/venv/bin/pip " not in INSTALL


def _recovery_function() -> str:
    start = UPDATE.index("_recover_incomplete_transaction() {")
    marker = "\n# --------------------------------------------------------------------------- #\n#  Phase 0"
    return UPDATE[start:UPDATE.index(marker, start)].rstrip()


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bash") is None,
                    reason="executes the production Bash recovery function")
@pytest.mark.parametrize("previous_version", ("0.3.14", "0.3.15", "0.3.18"))
@pytest.mark.parametrize(
    "phase,service_active,expected",
    [
        ("begun", True, ["mark:diagnostic_failure"]),
        ("ownership_intent", True, ["mark:diagnostic_failure"]),
        ("candidate_intent", True, [
            "reconcile:" + "e" * 64 + ":123456789abc",
            "mark:diagnostic_failure",
        ]),
        ("candidate_ready", True, [
            "reconcile:" + "e" * 64 + ":123456789abc",
            "mark:diagnostic_failure",
        ]),
        ("downtime_intent", True, [
            "reconcile:" + "e" * 64 + ":123456789abc",
            "mark:diagnostic_failure",
        ]),
        ("downtime_started", False, ["rollback"]),
        # Crash window: stop completed after downtime_intent, before the
        # downtime_started checkpoint. Disk service state forces rollback.
        ("downtime_intent", False, ["rollback"]),
    ],
)
def test_production_recovery_function_classifies_interrupted_state(
        tmp_path, phase, service_active, expected, previous_version):
    facts = {
        "backup_dir": "/var/backups/conduit-cc/20260721-120000-123456789abc",
        "previous_version": previous_version,
    }
    if phase in {"candidate_intent", "candidate_ready", "downtime_intent",
                 "downtime_started"}:
        facts["candidate_id"] = "e" * 64
    tx = [{
        "attempt_id": "123456789abc",
        "phase": phase,
        "facts": facts,
    }]
    log = tmp_path / "calls.log"
    harness = f"""#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME=conduit-cc
APP_DIR=/opt/conduit-cc
BACKUP_ROOT=/var/backups/conduit-cc
CCC_UPDATE_ATTEMPT_ID=
_TRANSACTION_BEGUN=false
_DOWNTIME_STARTED=false
_ROLLBACK_ACTIVE=false
_ROLLBACK_ATTEMPTED=false
BACKUP_DIR=
CURRENT_VERSION=
TX_JSON='{json.dumps(tx, separators=(",", ":"))}'
CALL_LOG='{log}'
SERVICE_ACTIVE={'true' if service_active else 'false'}
warn() {{ :; }}
info() {{ :; }}
die() {{ printf 'die:%s\n' "$*" >>"$CALL_LOG"; exit 99; }}
_rt() {{
  case "$1" in
    attempt-incomplete) printf 'UPDATE_INCOMPLETE=%s\n' "$TX_JSON" ;;
    discard-staging) printf 'discard:%s\n' "$2" >>"$CALL_LOG" ;;
    reconcile-candidate) printf 'reconcile:%s:%s\n' "$2" "$3" >>"$CALL_LOG" ;;
    *) return 98 ;;
  esac
}}
_tx_mark() {{ printf 'mark:%s\n' "$1" >>"$CALL_LOG"; }}
systemctl() {{ "$SERVICE_ACTIVE"; }}
phase5_rollback() {{ printf 'rollback\n' >>"$CALL_LOG"; return 0; }}
_validate_recorded_backup_dir() {{ return 0; }}
_read_version() {{ printf '{previous_version}\n'; }}
{_recovery_function()}
_recover_incomplete_transaction
"""
    script = tmp_path / "recover.sh"
    script.write_text(harness)
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == expected
