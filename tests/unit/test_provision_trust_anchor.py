"""tests/unit/test_provision_trust_anchor.py -- Epic-1 F12 ceremony.

Loads the extensionless helper via SourceFileLoader (same convention as the
other privileged-helper tests), injects tmp trust/state dirs and the current
uid as the expected owner, and generates a real Ed25519 key per test so the
suite is hermetic. The decisive regression is circular-trust prevention: an
anchor located inside the application/release tree cannot authorize itself.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("ssh-keygen") is None,
    reason="POSIX + ssh-keygen required")

_HELPER = (pathlib.Path(__file__).resolve().parents[2]
           / "deployment" / "bin" / "ccc-provision-trust-anchor")
PRINCIPAL = "conduit-control-center-publisher"


def _load():
    loader = SourceFileLoader("ccc_provision_trust", str(_HELPER))
    spec = importlib.util.spec_from_loader("ccc_provision_trust", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _make_key(tmp_path):
    kp = tmp_path / "pubkey"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(kp), "-q"], check=True)
    b64 = (tmp_path / "pubkey.pub").read_text().split()[1]
    blob = base64.b64decode(b64)
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return b64, fp


@pytest.fixture
def mod(tmp_path, monkeypatch):
    m = _load()
    trust_dir = tmp_path / "opt" / "trust"
    trust_dir.mkdir(parents=True, mode=0o700)
    os.chmod(trust_dir, 0o700)
    app_dir = tmp_path / "opt"
    monkeypatch.setattr(m, "APP_DIR", str(app_dir))
    monkeypatch.setattr(m, "TRUST_DIR", str(trust_dir))
    monkeypatch.setattr(m, "TRUST_PATH", str(trust_dir / "allowed_signers"))
    monkeypatch.setattr(m, "PRIVATE_STATE_DIR", str(tmp_path / "priv"))
    monkeypatch.setattr(m, "_OWNER_UID", os.getuid())
    return m


def _candidate(tmp_path, line: str, name="anchor.txt", mode=0o600):
    p = tmp_path / name
    p.write_text(line)
    os.chmod(p, mode)
    return str(p)


def _args(mod, frm, fp):
    import argparse
    return argparse.Namespace(frm=frm, fingerprint=fp)


def test_install_accepts_canonical_anchor(mod, tmp_path):
    b64, fp = _make_key(tmp_path)
    cand = _candidate(tmp_path, f"{PRINCIPAL} ssh-ed25519 {b64}\n")
    assert mod.cmd_install(_args(mod, cand, fp)) == 0
    installed = pathlib.Path(mod.TRUST_PATH)
    assert installed.read_text().split()[0] == PRINCIPAL
    assert oct(installed.stat().st_mode & 0o777) == "0o600"


def test_install_is_idempotent_for_identical_anchor(mod, tmp_path):
    b64, fp = _make_key(tmp_path)
    cand = _candidate(tmp_path, f"{PRINCIPAL} ssh-ed25519 {b64}\n")
    assert mod.cmd_install(_args(mod, cand, fp)) == 0
    assert mod.cmd_install(_args(mod, cand, fp)) == 0     # identical -> preserved


def test_install_refuses_conflicting_existing_anchor(mod, tmp_path):
    b64a, fpa = _make_key(tmp_path)
    (tmp_path / "second").mkdir()
    b64b, fpb = _make_key(tmp_path / "second")
    mod.cmd_install(_args(mod, _candidate(tmp_path, f"{PRINCIPAL} ssh-ed25519 {b64a}\n"), fpa))
    cand_b = _candidate(tmp_path, f"{PRINCIPAL} ssh-ed25519 {b64b}\n", name="b.txt")
    with pytest.raises(SystemExit) as e:
        mod.cmd_install(_args(mod, cand_b, fpb))
    assert e.value.code == 2       # no --force; conflicting anchor fails closed


def test_fingerprint_mismatch_rejected(mod, tmp_path):
    b64, _fp = _make_key(tmp_path)
    cand = _candidate(tmp_path, f"{PRINCIPAL} ssh-ed25519 {b64}\n")
    with pytest.raises(SystemExit) as e:
        mod.cmd_install(_args(mod, cand, "SHA256:AAAAwrongwrongwrongwrongwrongwrongwrongwrong"))
    assert e.value.code == 2


def test_wrong_principal_rejected(mod, tmp_path):
    b64, fp = _make_key(tmp_path)
    cand = _candidate(tmp_path, f"someone-else ssh-ed25519 {b64}\n")
    with pytest.raises(SystemExit) as e:
        mod.cmd_install(_args(mod, cand, fp))
    assert e.value.code == 2


def test_extra_entries_rejected(mod, tmp_path):
    b64, fp = _make_key(tmp_path)
    cand = _candidate(tmp_path,
                      f"{PRINCIPAL} ssh-ed25519 {b64}\n{PRINCIPAL} ssh-ed25519 {b64}\n")
    with pytest.raises(SystemExit) as e:
        mod.cmd_install(_args(mod, cand, fp))
    assert e.value.code == 2


@pytest.mark.parametrize("bad,needle", [
    (b"\xef\xbb\xbfx", "BOM"),
    (b"a\x00b", "NUL"),
    (b"a\r\nb", "CR"),
])
def test_byte_hygiene_rejected(mod, tmp_path, bad, needle):
    p = tmp_path / "raw"
    p.write_bytes(bad)
    os.chmod(p, 0o600)
    with pytest.raises(SystemExit) as e:
        mod._read_candidate(str(p))
    assert e.value.code == 2


def test_group_writable_candidate_rejected(mod, tmp_path):
    b64, fp = _make_key(tmp_path)
    cand = _candidate(tmp_path, f"{PRINCIPAL} ssh-ed25519 {b64}\n", mode=0o660)
    with pytest.raises(SystemExit) as e:
        mod._read_candidate(cand)
    assert e.value.code == 2


def test_symlink_candidate_rejected(mod, tmp_path):
    b64, _ = _make_key(tmp_path)
    real = _candidate(tmp_path, f"{PRINCIPAL} ssh-ed25519 {b64}\n")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    with pytest.raises(SystemExit) as e:
        mod._read_candidate(str(link))
    assert e.value.code == 2


def test_circular_trust_prevented_anchor_inside_app_tree(mod, tmp_path):
    """The decisive F12 regression: a candidate anchor that resolves INSIDE the
    application/release tree can never authorize that release."""
    b64, _ = _make_key(tmp_path)
    inside = pathlib.Path(mod.APP_DIR) / "release" / "allowed_signers"
    inside.parent.mkdir(parents=True)
    inside.write_text(f"{PRINCIPAL} ssh-ed25519 {b64}\n")
    os.chmod(inside, 0o600)
    with pytest.raises(SystemExit) as e:
        mod._read_candidate(str(inside))
    assert e.value.code == 2


def test_circular_trust_prevented_anchor_inside_updater_state(mod, tmp_path):
    b64, _ = _make_key(tmp_path)
    inside = pathlib.Path(mod.PRIVATE_STATE_DIR) / "allowed_signers"
    inside.parent.mkdir(parents=True)
    inside.write_text(f"{PRINCIPAL} ssh-ed25519 {b64}\n")
    os.chmod(inside, 0o600)
    with pytest.raises(SystemExit) as e:
        mod._read_candidate(str(inside))
    assert e.value.code == 2


# --- finding 1: EXACT staged-layout execution (no monkeypatch) --------------- #

def test_staged_provisioner_imports_verifier_from_snapshot(tmp_path):
    """Execute the EXACT shipped provisioner from the exact snapshot layout
    <snapshot>/deployment/bin/ccc-provision-trust-anchor with the real staged
    backend/update_verify.py. Monkeypatching module globals is insufficient; a
    real subprocess proves _VERIFY_AVAILABLE is True (implementation resolved
    from the snapshot, not the parent-of-bin)."""
    import subprocess
    snap = tmp_path / "snapshot"
    (snap / "deployment" / "bin").mkdir(parents=True)
    (snap / "backend").mkdir()
    shutil.copyfile(_HELPER, snap / "deployment" / "bin" / "ccc-provision-trust-anchor")
    (snap / "backend" / "__init__.py").write_text("")
    _repo_root = pathlib.Path(__file__).resolve().parents[2]
    shutil.copyfile(_repo_root / "backend" / "update_verify.py",
                    snap / "backend" / "update_verify.py")
    b64, _fp = _make_key(tmp_path)
    cand = tmp_path / "anchor.txt"
    cand.write_text(f"{PRINCIPAL} ssh-ed25519 {b64}\n")
    os.chmod(cand, 0o600)
    # Valid --target so execution reaches _validate, which checks the verifier
    # FIRST: with the fix the import succeeds and it fails later on the
    # deliberately wrong fingerprint; with the broken parent-of-bin import it
    # would fail with "backend.update_verify unavailable". The discriminator is
    # exactly which error surfaces.
    r = subprocess.run(
        [sys.executable, "-I",
         str(snap / "deployment" / "bin" / "ccc-provision-trust-anchor"),
         "install", "--from", str(cand),
         "--fingerprint", "SHA256:deliberatelyWrongFingerprintValue",
         "--target", "/opt/conduit-cc"],
        capture_output=True, text=True, cwd=str(tmp_path), env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 2
    assert "update_verify unavailable" not in r.stderr    # IMPORT SUCCEEDED (finding 1)
    assert "fingerprint mismatch" in r.stderr             # reached the verifier-backed check
