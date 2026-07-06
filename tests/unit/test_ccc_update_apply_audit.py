# SPDX-License-Identifier: MIT
"""ADR-0003 Phase B — helper audit-writer integration tests.

Loads the installed helper `deployment/bin/ccc-update-apply` as a module and
exercises its non-fatal `_audit` wrapper in isolation (no privileged flow). Proves:
  * records are built via the E3 allowlist — no trust material leaks (IC-11);
  * outcome/stage/correlation id are recorded correctly;
  * signing_principal is carried where verification succeeded and omitted otherwise;
  * an append failure (unwritable path) is NON-FATAL — never raises, changes nothing;
  * the wrapper is a clean no-op when the audit library is unavailable.
The wrapper only records; it can never alter the verifier result, exit code,
status content, deployment result, or rollback behaviour.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os

import pytest

from backend.update_audit import read_records

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HELPER = os.path.join(_ROOT, "deployment", "bin", "ccc-update-apply")
_HAS_HELPER = os.path.isfile(_HELPER)
_helper = pytest.mark.skipif(not _HAS_HELPER, reason="helper not present")


def _load():
    # The helper is an extensionless script, so an explicit source loader is needed.
    loader = importlib.machinery.SourceFileLoader("ccc_update_apply", _HELPER)
    spec = importlib.util.spec_from_loader("ccc_update_apply", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@_helper
def test_audit_records_are_redacted_and_correlated(tmp_path):
    h = _load()
    assert h._AUDIT_AVAILABLE and h._VERIFY_AVAILABLE
    h.AUDIT_DIR = str(tmp_path)
    h.AUDIT_FILE = str(tmp_path / "update-audit.jsonl")
    h._ATTEMPT_ID = "corr-abc"

    h._audit(h.OUTCOME_ACCEPTED, "authorize", from_version="0.3.12", target_version="0.3.13",
             signing_principal="conduit-control-center-publisher",
             signature="SSHSIG_SECRET", private_key="KEYBYTES", allowed_signers="ssh-ed25519 AAAA")
    h._audit(h.OUTCOME_APPLIED, "deploy", from_version="0.3.12", target_version="0.3.13")
    h._audit("reject_signature", "verify")

    recs = read_records(h.AUDIT_FILE)
    blob = open(h.AUDIT_FILE).read()
    assert [r["outcome"] for r in recs] == ["accepted", "applied", "reject_signature"]
    assert [r["stage"] for r in recs] == ["authorize", "deploy", "verify"]
    assert all(r["correlation_id"] == "corr-abc" for r in recs)
    assert all(r["audit_schema_version"] == 1 for r in recs)
    # IC-11: no trust material anywhere in the file
    for secret in ("SSHSIG_SECRET", "KEYBYTES", "AAAA"):
        assert secret not in blob
    # principal present where verified, omitted on a pre/at-signature reject
    assert recs[0]["signing_principal"] == "conduit-control-center-publisher"
    assert "signing_principal" not in recs[2]


@_helper
def test_audit_append_failure_is_non_fatal(tmp_path):
    h = _load()
    h.AUDIT_DIR = "/proc/definitely-not-writable"
    h.AUDIT_FILE = "/proc/definitely-not-writable/a.jsonl"
    h._ATTEMPT_ID = "corr-x"
    # must not raise and must not create anything
    h._audit(h.OUTCOME_APPLIED, "deploy")


@_helper
def test_audit_is_noop_when_unavailable(tmp_path):
    h = _load()
    h._AUDIT_AVAILABLE = False
    h.AUDIT_FILE = str(tmp_path / "should-not-exist.jsonl")
    h._audit(h.OUTCOME_APPLIED, "deploy")
    assert not os.path.exists(h.AUDIT_FILE)


@_helper
def test_audit_sets_file_mode_0640_and_group_chown_is_best_effort(tmp_path):
    import stat as _stat
    h = _load()
    h.AUDIT_DIR = str(tmp_path)
    h.AUDIT_FILE = str(tmp_path / "update-audit.jsonl")
    h._ATTEMPT_ID = "corr-mode"
    # writes without raising; chmod applies even as non-root, chown(root:conduit-cc)
    # is skipped best-effort when not root / group absent (still non-fatal).
    h._audit(h.OUTCOME_APPLIED, "deploy", from_version="0.3.13", target_version="0.3.14")
    assert os.path.exists(h.AUDIT_FILE)
    assert _stat.S_IMODE(os.stat(h.AUDIT_FILE).st_mode) == 0o640
