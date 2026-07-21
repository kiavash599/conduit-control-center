"""tests/unit/test_lifecycle_filter_contract.py -- A4/B2 shared filter contract.

Two layers:
  1. Text contract: both scripts define the IDENTICAL `CCC_LIFECYCLE_EXCLUDES`
     array, and every lifecycle rsync call site consumes the array (no inline
     divergent exclusion copies).
  2. Behavioral: the EXACT filter values extracted from the scripts are run
     through REAL `rsync --delete` fixtures proving: the selector (real dir OR
     symlink), the runtime store, the trust anchor and the helpers dir all
     survive; stale source code is still deleted; the anchor stays
     byte-identical; and the anchor never lands in a backup.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("rsync") is None,
    reason="POSIX + rsync required")

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _extract_array(script: str) -> list[str]:
    s = (ROOT / script).read_text(encoding="utf-8")
    m = re.search(r"readonly -a CCC_LIFECYCLE_EXCLUDES=\(\n(.*?)\n\)", s, re.S)
    assert m, f"{script}: shared filter contract not found"
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]


def test_contract_is_identical_in_both_scripts():
    a, b = _extract_array("install.sh"), _extract_array("update.sh")
    assert a == b
    assert a == ["--exclude=/venv", "--exclude=/.venvs",
                 "--exclude=/trust", "--exclude=/bin"]


def test_every_lifecycle_rsync_consumes_the_array():
    for script, expected in (("install.sh", 1), ("update.sh", 3)):
        s = (ROOT / script).read_text(encoding="utf-8")
        uses = s.count('"${CCC_LIFECYCLE_EXCLUDES[@]}"')
        assert uses >= expected, f"{script}: only {uses} rsync sites consume the contract"
        # no divergent inline copy of the venv exclusion remains at rsync sites
        assert "--exclude 'venv/'" not in s
        assert "--exclude '/bin/'" not in s


def _mk_app(tmp_path, selector_symlink: bool):
    """A fake APP_DIR with all four protected roots + stale code."""
    app = tmp_path / "app"
    (app / ".venvs" / "legacy-0" / "bin").mkdir(parents=True)
    (app / ".venvs" / "legacy-0" / "bin" / "python3").write_text("#!x\n")
    if selector_symlink:
        (app / "venv").symlink_to(".venvs/legacy-0")
    else:
        (app / "venv" / "bin").mkdir(parents=True)
        (app / "venv" / "bin" / "python3").write_text("#!x\n")
    (app / "trust").mkdir()
    (app / "trust" / "allowed_signers").write_text("principal ssh-ed25519 AAAA\n")
    (app / "bin").mkdir()
    (app / "bin" / "ccc-env").write_text("#!helper\n")
    (app / "backend").mkdir()
    (app / "backend" / "old_module.py").write_text("stale\n")
    return app


def _mk_source(tmp_path):
    src = tmp_path / "source"
    (src / "backend").mkdir(parents=True)
    (src / "backend" / "new_module.py").write_text("new\n")
    (src / "update.sh").write_text("#!new\n")
    return src


@pytest.mark.parametrize("selector_symlink", [False, True],
                         ids=["legacy-real-dir", "converted-symlink"])
def test_deploy_delete_preserves_all_protected_roots(tmp_path, selector_symlink):
    filters = _extract_array("update.sh")
    app = _mk_app(tmp_path, selector_symlink)
    src = _mk_source(tmp_path)
    anchor_before = (app / "trust" / "allowed_signers").read_bytes()
    r = subprocess.run(["rsync", "-a", "--checksum", "--delete", *filters,
                        f"{src}/", f"{app}/"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # protected roots survive
    assert (app / "trust" / "allowed_signers").read_bytes() == anchor_before
    assert (app / ".venvs" / "legacy-0" / "bin" / "python3").exists()
    assert (app / "bin" / "ccc-env").exists()
    if selector_symlink:
        assert (app / "venv").is_symlink()
    else:
        assert (app / "venv" / "bin" / "python3").exists()
    # stale source code is still deleted; new code arrives
    assert not (app / "backend" / "old_module.py").exists()
    assert (app / "backend" / "new_module.py").exists()


def test_backup_never_contains_the_trust_anchor(tmp_path):
    filters = _extract_array("update.sh")
    app = _mk_app(tmp_path, selector_symlink=False)
    backup = tmp_path / "backup" / "app"
    backup.mkdir(parents=True)
    r = subprocess.run(["rsync", "-a", *filters, f"{app}/", f"{backup}/"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not (backup / "trust").exists()          # never enters ordinary backups
    assert not (backup / "venv").exists()
    assert not (backup / ".venvs").exists()
    assert (backup / "backend" / "old_module.py").exists()   # code IS backed up


def test_rollback_from_trustless_backup_preserves_live_anchor(tmp_path):
    """The F-A4 exploit shape: backup has no trust/ (correct), so a rollback
    --delete WITHOUT the filter would remove the live anchor. With the
    contract, the live anchor survives byte-identical."""
    filters = _extract_array("update.sh")
    app = _mk_app(tmp_path, selector_symlink=True)
    anchor_before = (app / "trust" / "allowed_signers").read_bytes()
    backup = tmp_path / "backup" / "app"
    (backup / "backend").mkdir(parents=True)
    (backup / "backend" / "restored.py").write_text("old-code\n")
    r = subprocess.run(["rsync", "-a", "--checksum", "--delete", *filters,
                        f"{backup}/", f"{app}/"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (app / "trust" / "allowed_signers").read_bytes() == anchor_before
    assert (app / "venv").is_symlink()
    assert (app / ".venvs").is_dir()
    assert (app / "backend" / "restored.py").exists()


def test_trust_digest_assertions_wired_in_update():
    s = (ROOT / "update.sh").read_text(encoding="utf-8")
    assert "_capture_trust_digest" in s
    # Definition + post-deploy assertion. Rollback deliberately preserves the
    # provisioned trust transition rather than restoring pre-bootstrap bytes;
    # the behavioral rollback test above proves the live anchor survives.
    assert s.count("_assert_trust_anchor_unchanged") == 2
    rollback = s.split("phase5_rollback() {", 1)[1].split("Phase 6", 1)[0]
    assert "_assert_trust_anchor_unchanged" not in rollback
    assert "trust anchor provisioned during this attempt" in rollback
    assert "sha256sum" in s
