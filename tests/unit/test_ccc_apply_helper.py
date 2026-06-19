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


# --------------------------------------------------------------------------
# BS1 Commit 1 -- reduced-window (Bandwidth Scheduling) helper behaviour
# --------------------------------------------------------------------------

def test_render_reduced_off_by_default():
    # The original two-argument render must now emit the reduced knobs as OFF.
    mod = _load()
    out = mod._render(50, 40).decode()
    assert "Environment=CCC_REDUCED_START=\n" in out
    assert "Environment=CCC_REDUCED_END=\n" in out
    assert "Environment=CCC_REDUCED_MAXCOMMON=0" in out
    assert "Environment=CCC_REDUCED_UP=0" in out
    assert "Environment=CCC_REDUCED_DOWN=0" in out
    assert "ExecStart" not in out


def test_validate_reduced_disabled_is_default():
    mod = _load()
    assert mod._validate_reduced(-1, -1, 0, 0, 50) == ("", "", 0, 0, 0)


def test_validate_reduced_enabled_formats_and_converts():
    # 02:00-06:00, max 10, 15 Mbps -> 1_875_000 bytes/sec on both directions.
    mod = _load()
    assert mod._validate_reduced(120, 360, 10, 15, 50) == (
        "02:00", "06:00", 10, 1875000, 1875000,
    )


def test_validate_reduced_wraparound_allowed():
    mod = _load()
    assert mod._validate_reduced(1320, 360, 10, 15, 50) == (
        "22:00", "06:00", 10, 1875000, 1875000,
    )


def test_validate_reduced_rejects_invalid():
    mod = _load()
    bad = [
        (120, 120, 10, 15),    # start == end
        (-5, 360, 10, 15),     # start out of range (not the disabled sentinel)
        (120, 1440, 10, 15),   # end out of range
        (120, 360, 0, 15),     # reduced-max 0 while enabled
        (120, 360, 60, 15),    # reduced-max > max-common (50)
        (120, 360, 10, 0),     # reduced bandwidth 0 while enabled
        (120, 360, 10, 1001),  # reduced bandwidth out of range
        (120, -1, 10, 15),     # partial config (end disabled, start set)
    ]
    for s, e, rc, bw in bad:
        with pytest.raises(SystemExit):
            mod._validate_reduced(s, e, rc, bw, 50)


def test_render_reduced_output_is_directive_allowlisted():
    # Security: only [Service] + Environment= lines; the sole string values
    # (reduced times) are empty or a helper-built HH:MM -- never anything that
    # could inject a systemd directive.
    import re
    mod = _load()
    reduced = mod._validate_reduced(120, 360, 10, 15, 50)
    out = mod._render(50, 40, reduced).decode()
    lines = [ln for ln in out.split("\n") if ln]
    assert lines[0] == "[Service]"
    for ln in lines[1:]:
        assert ln.startswith("Environment="), ln
    assert "ExecStart" not in out
    for key in ("CCC_REDUCED_START", "CCC_REDUCED_END"):
        m = re.search(rf"Environment={key}=(.*)", out)
        assert m is not None
        assert m.group(1) == "" or re.fullmatch(r"\d\d:\d\d", m.group(1)), m.group(1)


def test_main_rejects_bad_reduced():
    mod = _load()
    base = ["apply", "--max-common-clients", "50", "--bandwidth-mbps", "40"]
    # start == end
    with pytest.raises(SystemExit):
        mod.main(base + ["--reduced-start-min", "120", "--reduced-end-min", "120",
                         "--reduced-max-common", "10", "--reduced-bandwidth-mbps", "15"])
    # reduced-max exceeds max-common
    with pytest.raises(SystemExit):
        mod.main(base + ["--reduced-start-min", "120", "--reduced-end-min", "360",
                         "--reduced-max-common", "60", "--reduced-bandwidth-mbps", "15"])
    # non-integer reduced arg rejected by argparse
    with pytest.raises(SystemExit):
        mod.main(base + ["--reduced-start-min", "abc"])


@_linux_only
def test_apply_with_reduced_window_writes_drop_in(tmp_path, monkeypatch):
    mod = _load()
    calls = _setup(mod, tmp_path, monkeypatch)
    mod.main(["apply", "--max-common-clients", "50", "--bandwidth-mbps", "40",
              "--reduced-start-min", "120", "--reduced-end-min", "360",
              "--reduced-max-common", "10", "--reduced-bandwidth-mbps", "15"])
    content = (tmp_path / "ccc.conf").read_text()
    assert "Environment=CCC_REDUCED_START=02:00" in content
    assert "Environment=CCC_REDUCED_END=06:00" in content
    assert "Environment=CCC_REDUCED_MAXCOMMON=10" in content
    assert "Environment=CCC_REDUCED_UP=1875000" in content
    assert "Environment=CCC_REDUCED_DOWN=1875000" in content
    assert "ExecStart" not in content
    assert calls == [("daemon-reload",), ("restart", "conduit")]


@_linux_only
def test_two_arg_apply_still_disables_reduced(tmp_path, monkeypatch):
    # Backward compatibility: the current M2 backend call (no reduced args)
    # must still succeed and render the window OFF.
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    mod.main(["apply", "--max-common-clients", "50", "--bandwidth-mbps", "40"])
    content = (tmp_path / "ccc.conf").read_text()
    assert "Environment=CCC_REDUCED_START=\n" in content
    assert "Environment=CCC_REDUCED_MAXCOMMON=0" in content


def test_conduit_unit_has_reduced_knobs():
    # The shipped unit must define the reduced defaults (disabled) and the five
    # static --set tokens that consume them.
    repo = pathlib.Path(__file__).resolve().parents[2]
    unit = (repo / "deployment" / "conduit.service").read_text()
    for env in ("CCC_REDUCED_START=", "CCC_REDUCED_END=",
                "CCC_REDUCED_MAXCOMMON=0", "CCC_REDUCED_UP=0", "CCC_REDUCED_DOWN=0"):
        assert f"Environment={env}" in unit, env
    for tok in (
        "--set InproxyReducedStartTime=${CCC_REDUCED_START}",
        "--set InproxyReducedEndTime=${CCC_REDUCED_END}",
        "--set InproxyReducedMaxCommonClients=${CCC_REDUCED_MAXCOMMON}",
        "--set InproxyReducedLimitUpstreamBytesPerSecond=${CCC_REDUCED_UP}",
        "--set InproxyReducedLimitDownstreamBytesPerSecond=${CCC_REDUCED_DOWN}",
    ):
        assert tok in unit, tok


# ---------------------------------------------------------------------------
# Personal-clients knob (C3): integer COUNT only, never a compartment ID.
# ---------------------------------------------------------------------------


def test_render_personal_explicit_value():
    mod = _load()
    out = mod._render(50, 40, mpc=5).decode()
    assert "Environment=CCC_MAX_PERSONAL_CLIENTS=5" in out
    assert "ExecStart" not in out


def test_render_personal_defaults_to_zero():
    mod = _load()
    out = mod._render(50, 40).decode()
    assert "Environment=CCC_MAX_PERSONAL_CLIENTS=0" in out


def test_validate_mpc_range():
    mod = _load()
    assert mod._validate_mpc(0) == 0          # off (inclusive min)
    assert mod._validate_mpc(1000) == 1000    # inclusive max
    for bad in (-1, 1001):
        with pytest.raises(SystemExit):
            mod._validate_mpc(bad)


def test_main_apply_threads_personal(monkeypatch):
    mod = _load()
    captured = {}
    monkeypatch.setattr(
        mod, "cmd_apply",
        lambda mcc, bw, reduced, mpc=0: captured.update(mcc=mcc, bw=bw, mpc=mpc),
    )
    mod.main(["apply", "--max-common-clients", "50", "--bandwidth-mbps", "40",
              "--max-personal-clients", "7"])
    assert captured["mpc"] == 7


def test_main_omitted_personal_defaults_zero(monkeypatch):
    mod = _load()
    captured = {}
    monkeypatch.setattr(
        mod, "cmd_apply",
        lambda mcc, bw, reduced, mpc=0: captured.update(mpc=mpc),
    )
    mod.main(["apply", "--max-common-clients", "50", "--bandwidth-mbps", "40"])
    assert captured["mpc"] == 0


def test_main_rejects_out_of_range_personal():
    mod = _load()
    for bad in ("-1", "1001"):
        with pytest.raises(SystemExit):
            mod.main(["apply", "--max-common-clients", "50",
                      "--bandwidth-mbps", "40", "--max-personal-clients", bad])


def test_helper_rejects_compartment_id_argument():
    # The helper must NEVER accept a compartment ID flag (argparse rejects it).
    mod = _load()
    with pytest.raises(SystemExit):
        mod.main(["apply", "--max-common-clients", "50", "--bandwidth-mbps", "40",
                  "--compartment-id", "AAAA"])


def test_render_has_no_compartment_identifiers():
    mod = _load()
    out = mod._render(50, 40, mpc=5).decode().lower()
    assert "compartment" not in out
    assert "--compartment-id" not in out


def test_render_personal_and_reduced_coexist():
    mod = _load()
    reduced = mod._validate_reduced(120, 360, 10, 15, 50)  # 02:00-06:00 UTC
    out = mod._render(50, 40, reduced=reduced, mpc=8).decode()
    assert "Environment=CCC_MAX_PERSONAL_CLIENTS=8" in out
    assert "Environment=CCC_REDUCED_START=02:00" in out
    assert "Environment=CCC_REDUCED_MAXCOMMON=10" in out
    assert "ExecStart" not in out


@_linux_only
def test_apply_logs_mpc_count_not_compartment(tmp_path, monkeypatch):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    logs = []

    class _Rec:
        def info(self, fmt, *a):
            logs.append(fmt % a)

        def error(self, *a, **k):
            pass

    monkeypatch.setattr(mod, "LOG", _Rec())
    mod.cmd_apply(50, 40, mpc=6)
    joined = " ".join(logs).lower()
    assert "mpc=6" in joined
    assert "compartment" not in joined


def test_conduit_unit_has_personal_clients_knob():
    # Personal Mode (C2): the shipped unit must define the =0 default and the
    # braced ExecStart token that consumes it, must NOT pass --compartment-id
    # (auto-loaded from disk), and must keep the existing reduced --set tokens
    # (no regression). The =0 default is mandatory: without it the braced
    # ${CCC_MAX_PERSONAL_CLIENTS} would expand empty and fail Conduit startup.
    repo = pathlib.Path(__file__).resolve().parents[2]
    unit = (repo / "deployment" / "conduit.service").read_text()

    # Default present and exactly 0 (whole-line match).
    assert any(
        ln.strip() == "Environment=CCC_MAX_PERSONAL_CLIENTS=0"
        for ln in unit.splitlines()
    ), "missing Environment=CCC_MAX_PERSONAL_CLIENTS=0"

    # ExecStart token present and BRACED (one argument, no shell split).
    assert "--max-personal-clients ${CCC_MAX_PERSONAL_CLIENTS}" in unit
    assert "--max-personal-clients $CCC_MAX_PERSONAL_CLIENTS " not in unit  # no unbraced form

    # Compartment ID must never be on ExecStart.
    assert "--compartment-id" not in unit

    # Regression guard: a couple of the reduced tokens remain present.
    assert "--set InproxyReducedStartTime=${CCC_REDUCED_START}" in unit
    assert "Environment=CCC_MAX_COMMON_CLIENTS=50" in unit


def test_unit_has_only_narrow_readwritepaths():
    # conduit-cc.service keeps ProtectSystem=strict and adds ONLY narrow paths to
    # ReadWritePaths: the drop-in dir (M2) and the Conduit data dir (C6e Personal
    # Mode helper) -- never broad /etc/systemd, never broad /var/lib/conduit.
    repo = pathlib.Path(__file__).resolve().parents[2]
    unit = (repo / "deployment" / "conduit-cc.service").read_text()
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/etc/systemd/system/conduit.service.d" in unit
    rwp = [ln.strip() for ln in unit.splitlines() if ln.strip().startswith("ReadWritePaths=")]
    assert rwp == [
        "ReadWritePaths=/etc/conduit-cc",
        "ReadWritePaths=/etc/systemd/system/conduit.service.d",
        "ReadWritePaths=/var/lib/conduit/data",
    ], rwp
    # The data-dir grant is the precise dir, not the broad parent.
    assert "ReadWritePaths=/var/lib/conduit\n" not in unit
    # Defense-in-depth: the private key is carved back to read-only.
    assert "ReadOnlyPaths=/var/lib/conduit/data/conduit_key.json" in unit
    # The data dir must exist at start, so ORDER after conduit.service -- but do
    # NOT pull it in (CCC must not auto-start the Conduit node): After= only.
    assert any(ln.startswith("After=") and "conduit.service" in ln
               for ln in unit.splitlines())
    assert not any(ln.startswith("Wants=") and "conduit.service" in ln
                   for ln in unit.splitlines())
