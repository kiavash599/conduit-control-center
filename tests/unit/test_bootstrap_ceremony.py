"""tests/unit/test_bootstrap_ceremony.py -- v0.3.18->v0.3.19 bootstrap.

Text/behavioral contracts over the ceremony script, the staged runner and the
engine ordering. The runner's implementation/target separation and the
snapshot object contract are the decisive properties.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="POSIX semantics")

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOT = ROOT / "deployment" / "bootstrap" / "ccc-bootstrap.sh"
RUNNER = ROOT / "deployment" / "bootstrap" / "ccc-bootstrap-runtime"


def test_ceremony_never_delegates_to_installed_v0318_updater():
    s = BOOT.read_text(encoding="utf-8")
    # the STAGED engine is executed; the installed updater is never invoked
    assert 'bash "${SNAP}/update.sh"' in s
    assert "/opt/conduit-cc/update.sh" not in s
    # snapshot verified AFTER copy, before any engine handover
    assert "sha256sum -c" in s
    assert "exact-set check" in s


def test_ceremony_binds_owner_authorized_source_identity_outside_payload():
    s = BOOT.read_text(encoding="utf-8")
    assert "--source-commit" in s and "--source-tag" in s
    assert "source commit must be exactly 40 lowercase hex" in s
    assert "source tag does not match the verified snapshot APP_VERSION" in s
    assert '--authorized-source-commit "${SOURCE_COMMIT}"' in s
    assert '--authorized-source-tag "${SOURCE_TAG}"' in s


def test_ceremony_snapshot_rejects_nonregular_and_hardlinks():
    s = BOOT.read_text(encoding="utf-8")
    assert "! -type f ! -type d" in s          # symlink/special rejection
    assert "-links +1" in s                    # hardlink-dup rejection
    assert "--no-links --no-devices --no-specials" in s


def test_ceremony_write_ahead_records_and_retains_rollback_reserve():
    s = BOOT.read_text(encoding="utf-8")
    record = s.index('os.replace(tmp, path)')
    create = s.index('install -d -o root -g root -m 0700 "${STAGING}"')
    handoff = s.index('bash "${SNAP}/update.sh"')
    ready = s.index('ccc-runtime reserve-ready "${BOOT_ID}"')
    assert record < create < handoff < ready
    assert "bootstrap-reserves" in s
    assert '"work": work' in s
    assert '"state": "staged"' in s
    assert '"history": ["staged"]' in s
    # The ceremony never deletes its reserve. Only the separate, explicit
    # reserve-accept operation may do that after qualification.
    assert 'rm -rf "${STAGING}"' not in s


def test_runner_separates_implementation_source_from_fixed_target():
    s = RUNNER.read_text(encoding="utf-8")
    assert 'FIXED_TARGET = "/opt/conduit-cc"' in s
    # implementation is imported only from the staging snapshot, verified
    assert "resolved OUTSIDE the staging snapshot" in s
    assert 'SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_SELF_REAL)))' in s


def test_runner_rejects_foreign_target(tmp_path):
    # build a minimal staging snapshot with the runner + a stub runtime_store
    snap = tmp_path / "source"
    (snap / "deployment" / "bootstrap").mkdir(parents=True)
    (snap / "backend").mkdir()
    shutil.copyfile(RUNNER, snap / "deployment" / "bootstrap" / "ccc-bootstrap-runtime")
    (snap / "backend" / "__init__.py").write_text("")
    (snap / "backend" / "runtime_store.py").write_text(
        "class RuntimeStoreError(Exception): pass\n")
    r = subprocess.run(
        [sys.executable, "-I",
         str(snap / "deployment" / "bootstrap" / "ccc-bootstrap-runtime"),
         "--target", "/tmp/evil", "diagnose"],
        capture_output=True, text=True, cwd=str(tmp_path), env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 2
    assert "must be exactly" in r.stderr


def test_engine_requires_runtime_tool_or_fails_to_bootstrap():
    s = (ROOT / "update.sh").read_text(encoding="utf-8")
    assert "_validate_runtime_tool" in s
    # missing tool fails closed pointing at the bootstrap ceremony
    assert "bootstrap ceremony" in s
    # Runtime-tool path allowlist: installed OR the one exact attempt-bound
    # bootstrap runner location. The complete import closure is checked before
    # root executes Python, including hardlink and special-object refusal.
    assert "^/var/lib/ccc-update/bootstrap-([0-9a-f]{12,32})/source/" in s
    assert "deployment/bootstrap/ccc-bootstrap-runtime$" in s
    validator = s[s.index("_validate_runtime_tool() {"):s.index("_validate_env_tool() {")]
    assert "_verify_app_dir_ownership" in validator
    assert "_verify_bin_dir" in validator
    assert "-type f -links +1" in validator
    assert '"0:0:755:1"' in validator
    assert '"0:0:700:1"' in validator


def test_engine_backup_precedes_no_helper_mutation():
    """Option A: the engine's Phase-1 backup records helpers + sudoers BEFORE
    any helper/sudoers mutation; bootstrap performs none beforehand."""
    s = (ROOT / "update.sh").read_text(encoding="utf-8")
    assert "1e - Backing up installed privileged helpers" in s
    # helper re-provisioning / sudoers rewrite happens in the deploy phase (3b2),
    # which is AFTER phase1_backup in the call order at the bottom of the script.
    order = s[s.index("phase0_preflight\n"):]
    assert order.index("phase1_backup") < order.index("phase3_deploy")


def test_v0318_service_owned_app_root_is_transitioned_before_candidate_store():
    s = (ROOT / "update.sh").read_text(encoding="utf-8")
    assert s.index("_tx_mark ownership_intent") < s.index("_secure_legacy_app_root", s.index("phase1_backup()"))
    assert s.index("_secure_legacy_app_root", s.index("phase1_backup()")) < s.index("stage-candidate")
    assert "the staged bootstrap runner imports no old APP_DIR code" in s


def test_bootstrap_stages_canonical_env_reader_from_verified_snapshot():
    s = (ROOT / "deployment/bootstrap/ccc-bootstrap.sh").read_text(encoding="utf-8")
    verified = s.index("snapshot verified: exact set + per-file hashes")
    staged = s.index('ENV_ROOT="${STAGING}/env-tool"')
    handoff = s.index('bash "${SNAP}/update.sh"')
    assert verified < staged < handoff
    assert '"${SNAP}/deployment/bin/ccc-env" "${ENV_ROOT}/bin/ccc-env"' in s
    assert '"${SNAP}/backend/env_file.py" "${ENV_ROOT}/backend/env_file.py"' in s
    assert 'cmp -s "${SNAP}/deployment/bin/ccc-env"' in s
    assert '--env-tool "${ENV_RUNNER}"' in s
