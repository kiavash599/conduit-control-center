# SPDX-License-Identifier: MIT
"""
Tests for the root helper deployment/bin/ccc-apply-conduit-config (M2).

Loads the extension-less script via importlib, redirects its hardcoded paths to
a temp dir, and stubs _systemctl + _assert_safe_dir so the filesystem behaviour
(Environment-only write, atomicity, .bak rollback, unlink-when-no-previous) and
the input validation can be exercised on Linux without root or systemd.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

# The production helper is Linux-only (fcntl + O_NOFOLLOW). Tests that exercise
# the real filesystem/lock path are skipped off Linux; the pure tests (render,
# validation, argparse) run on every platform (Windows + Linux CI).
_linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="POSIX fcntl/O_NOFOLLOW; helper is Linux-only"
)

_HELPER = (
    pathlib.Path(__file__).resolve().parents[2]
    / "deployment" / "bin" / "ccc-apply-conduit-config"
)


def _load():
    # The helper has no .py extension, so attach an explicit source loader.
    loader = SourceFileLoader("ccc_apply_helper", str(_HELPER))
    spec = importlib.util.spec_from_loader("ccc_apply_helper", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _setup(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "DROPIN_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "DROPIN_PATH", str(tmp_path / "ccc.conf"))
    monkeypatch.setattr(mod, "BAK_PATH", str(tmp_path / "ccc.conf.bak"))
    monkeypatch.setattr(mod, "LOCK_PATH", str(tmp_path / ".ccc.lock"))
    calls = []
    monkeypatch.setattr(mod, "_systemctl", lambda *a: calls.append(a))
    monkeypatch.setattr(mod, "_assert_safe_dir", lambda: None)
    return calls


def test_render_environment_only():
    mod = _load()
    out = mod._render(50, -1).decode()
    assert "Environment=CCC_MAX_COMMON_CLIENTS=50" in out
    assert "Environment=CCC_BANDWIDTH_MBPS=-1" in out
    assert "ExecStart" not in out
    assert out.startswith("[Service]")


@_linux_only
def test_apply_writes_only_environment_and_restarts(tmp_path, monkeypatch):
    mod = _load()
    calls = _setup(mod, tmp_path, monkeypatch)
    mod.cmd_apply(50, 40)
    content = (tmp_path / "ccc.conf").read_text()
    assert "Environment=CCC_MAX_COMMON_CLIENTS=50" in content
    assert "Environment=CCC_BANDWIDTH_MBPS=40" in content
    assert "ExecStart" not in content
    assert calls == [("daemon-reload",), ("restart", "conduit")]


@_linux_only
def test_rollback_restores_previous(tmp_path, monkeypatch):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    mod.cmd_apply(50, 40)        # baseline
    mod.cmd_apply(200, 300)      # .bak now holds 50/40
    mod.cmd_rollback()
    content = (tmp_path / "ccc.conf").read_text()
    assert "CCC_MAX_COMMON_CLIENTS=50" in content
    assert "CCC_BANDWIDTH_MBPS=40" in content


@_linux_only
def test_rollback_unlinks_when_no_previous(tmp_path, monkeypatch):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    mod.cmd_apply(50, 40)        # no prior drop-in -> .bak cleared
    assert (tmp_path / "ccc.conf").exists()
    mod.cmd_rollback()
    assert not (tmp_path / "ccc.conf").exists()


def test_validation_rejects_bad_values():
    mod = _load()
    for bad in (0, 1001, -5):
        with pytest.raises(SystemExit):
            mod._validate_mcc(bad)
    for bad in (0, -2, 1001):
        with pytest.raises(SystemExit):
            mod._validate_bw(bad)


def test_main_rejects_out_of_range():
    mod = _load()
    with pytest.raises(SystemExit):
        mod.main(["apply", "--max-common-clients", "0", "--bandwidth-mbps", "40"])


def test_main_rejects_non_integer_and_unknown():
    mod = _load()
    with pytest.raises(SystemExit):
        mod.main(["apply", "--max-common-clients", "abc", "--bandwidth-mbps", "40"])
    with pytest.raises(SystemExit):
        mod.main(["bogus-subcommand"])
    with pytest.raises(SystemExit):
        mod.main(["apply", "--max-common-clients", "50"])  # missing --bandwidth-mbps
    with pytest.raises(SystemExit):
        mod.main(["apply", "--unit", "evil", "--max-common-clients", "50",
                  "--bandwidth-mbps", "40"])  # no unit/path args accepted


def test_lock_path_outside_dropin_dir():
    # The serialisation lock must NOT live in the systemd drop-in dir (read-only
    # under ProtectSystem=strict in the conduit-cc namespace); it lives under the
    # already-writable /etc/conduit-cc.
    mod = _load()
    assert mod.DROPIN_DIR not in mod.LOCK_PATH
    assert mod.LOCK_PATH.startswith("/etc/conduit-cc/")
    assert mod.LOCK_PATH.endswith(".lock")
    # Drop-in writes still target the systemd drop-in dir.
    assert mod.DROPIN_PATH == mod.DROPIN_DIR + "/ccc.conf"
    assert mod.BAK_PATH == mod.DROPIN_DIR + "/ccc.conf.bak"


def test_unit_has_only_narrow_readwritepaths():
    # conduit-cc.service keeps ProtectSystem=strict and adds ONLY the narrow
    # drop-in dir to ReadWritePaths (never broad /etc/systemd).
    repo = pathlib.Path(__file__).resolve().parents[2]
    unit = (repo / "deployment" / "conduit-cc.service").read_text()
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/etc/systemd/system/conduit.service.d" in unit
    rwp = [ln.strip() for ln in unit.splitlines() if ln.strip().startswith("ReadWritePaths=")]
    assert rwp == [
        "ReadWritePaths=/etc/conduit-cc",
        "ReadWritePaths=/etc/systemd/system/conduit.service.d",
    ], rwp
