# SPDX-License-Identifier: MIT
"""M2 adapter tests: Environment parsing, reader migration (env-first with
ExecStart fallback), and the pure health-decision logic."""
from __future__ import annotations

import backend.conduit.adapter as adp
from backend.conduit.models import ConduitConfigView, ConfigField


# --------------------------- Environment parsing ---------------------------
def test_parse_environment():
    env = adp._parse_environment(
        "Environment=CCC_MAX_COMMON_CLIENTS=50 CCC_BANDWIDTH_MBPS=40"
    )
    assert env["CCC_MAX_COMMON_CLIENTS"] == "50"
    assert env["CCC_BANDWIDTH_MBPS"] == "40"
    assert adp._env_int(env, "CCC_MAX_COMMON_CLIENTS") == 50
    assert adp._env_int(env, "MISSING") is None


def test_parse_environment_empty():
    assert adp._parse_environment(None) == {}
    assert adp._parse_environment("Environment=") == {}


# --------------------------- health decision ---------------------------
def _view(status, mcc_e, bw_field):
    return ConduitConfigView(status, ConfigField(0, mcc_e), bw_field)


def test_health_reason():
    ok = _view("running", 50, ConfigField(40, 40))
    assert adp._health_reason(ok, 50, 40) is None
    assert adp._health_reason(_view("stopped", 50, ConfigField(40, 40)), 50, 40)
    assert adp._health_reason(_view("running", None, ConfigField(40, 40)), 50, 40)
    assert adp._health_reason(_view("running", 40, ConfigField(40, 40)), 50, 40)  # mcc mismatch
    unl = _view("running", 50, ConfigField(-1, 0, unlimited_configured=True, unlimited_effective=True))
    assert adp._health_reason(unl, 50, -1) is None
    assert adp._health_reason(ok, 50, 99)  # bw mismatch


# --------------------------- reader migration ---------------------------
async def test_reader_prefers_environment(monkeypatch):
    monkeypatch.setattr(
        adp, "_fetch_metrics_text",
        lambda _u: "conduit_max_common_clients 50\nconduit_bandwidth_limit_bytes_per_second 5000000\n",
    )

    async def _env():
        return "Environment=CCC_MAX_COMMON_CLIENTS=200 CCC_BANDWIDTH_MBPS=80"

    async def _exec():
        raise AssertionError("ExecStart fallback must not be used when env present")

    async def _status():
        return "running"

    monkeypatch.setattr(adp, "_read_configured_environment", _env)
    monkeypatch.setattr(adp, "_read_configured_execstart", _exec)
    monkeypatch.setattr(adp, "get_status", _status)

    v = await adp.get_conduit_config_view()
    assert v.max_common_clients.configured == 200
    assert v.bandwidth_mbps.configured == 80


async def test_reader_falls_back_to_execstart(monkeypatch):
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: "conduit_max_common_clients 50\n")

    async def _env():
        return "Environment="  # no CCC_ vars -> fallback

    async def _exec():
        return "argv[]=/x start --max-common-clients 50 --bandwidth 40 ;"

    async def _status():
        return "running"

    monkeypatch.setattr(adp, "_read_configured_environment", _env)
    monkeypatch.setattr(adp, "_read_configured_execstart", _exec)
    monkeypatch.setattr(adp, "get_status", _status)

    v = await adp.get_conduit_config_view()
    assert v.max_common_clients.configured == 50
    assert v.bandwidth_mbps.configured == 40


async def test_reader_no_execstart_fallback_for_missing_personal(monkeypatch):
    # Env has MCC/BW but NOT CCC_MAX_PERSONAL_CLIENTS (e.g. a pre-C2 M2 unit):
    # ExecStart must NOT be read (env-preferred invariant), and personal defaults
    # to 0 (Personal Mode off). Regression for the C6b reader change.
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: "conduit_max_common_clients 50\n")

    async def _env():
        return "Environment=CCC_MAX_COMMON_CLIENTS=200 CCC_BANDWIDTH_MBPS=80"

    async def _exec():
        raise AssertionError("ExecStart fallback must not be used for a missing personal knob")

    async def _status():
        return "running"

    monkeypatch.setattr(adp, "_read_configured_environment", _env)
    monkeypatch.setattr(adp, "_read_configured_execstart", _exec)
    monkeypatch.setattr(adp, "get_status", _status)

    v = await adp.get_conduit_config_view()
    assert v.max_personal_clients.configured == 0
    assert v.max_common_clients.configured == 200
    assert v.bandwidth_mbps.configured == 80


# --------------------------- reduced apply (BS1) ---------------------------
async def test_apply_passes_reduced_args(monkeypatch):
    captured = {}

    async def _stub(*args):
        captured["args"] = args
        return (0, "")

    monkeypatch.setattr(adp, "_run_helper", _stub)
    await adp.apply_conduit_config(
        50, 40, reduced_start_min=120, reduced_end_min=360,
        reduced_max_common=10, reduced_bandwidth_mbps=15,
    )
    a = list(captured["args"])
    assert a[0] == "apply"
    assert a[a.index("--reduced-start-min") + 1] == "120"
    assert a[a.index("--reduced-end-min") + 1] == "360"
    assert a[a.index("--reduced-max-common") + 1] == "10"
    assert a[a.index("--reduced-bandwidth-mbps") + 1] == "15"


async def test_apply_defaults_reduced_disabled(monkeypatch):
    captured = {}

    async def _stub(*args):
        captured["args"] = args
        return (0, "")

    monkeypatch.setattr(adp, "_run_helper", _stub)
    await adp.apply_conduit_config(50, 40)   # original two-knob call
    a = list(captured["args"])
    assert a[a.index("--reduced-start-min") + 1] == "-1"
    assert a[a.index("--reduced-end-min") + 1] == "-1"
    assert a[a.index("--reduced-max-common") + 1] == "0"
    assert a[a.index("--reduced-bandwidth-mbps") + 1] == "0"


# --------------------------- reduced read-back (BS1) ---------------------------
async def test_view_reduced_enabled(monkeypatch):
    monkeypatch.setattr(
        adp, "_fetch_metrics_text",
        lambda _u: "conduit_max_common_clients 50\nconduit_bandwidth_limit_bytes_per_second 5000000\n",
    )

    async def _env():
        return ("Environment=CCC_MAX_COMMON_CLIENTS=50 CCC_BANDWIDTH_MBPS=40 "
                "CCC_REDUCED_START=02:00 CCC_REDUCED_END=06:00 CCC_REDUCED_MAXCOMMON=10 "
                "CCC_REDUCED_UP=1875000 CCC_REDUCED_DOWN=1875000")

    async def _status():
        return "running"

    monkeypatch.setattr(adp, "_read_configured_environment", _env)
    monkeypatch.setattr(adp, "get_status", _status)

    v = await adp.get_conduit_config_view()
    assert v.reduced.enabled is True
    assert (v.reduced.start, v.reduced.end) == ("02:00", "06:00")
    assert v.reduced.max_common_clients == 10
    assert v.reduced.bandwidth_mbps == 15   # 1_875_000 / 125_000


async def test_view_reduced_disabled_empty_values(monkeypatch):
    # The disabled render writes empty CCC_REDUCED_START/END + zeros; the env
    # parser must yield "" (not a parse error), and the window reports disabled
    # while the normal knobs are still read.
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: "conduit_max_common_clients 50\n")

    async def _env():
        return ("Environment=CCC_MAX_COMMON_CLIENTS=50 CCC_BANDWIDTH_MBPS=40 "
                "CCC_REDUCED_START= CCC_REDUCED_END= CCC_REDUCED_MAXCOMMON=0 "
                "CCC_REDUCED_UP=0 CCC_REDUCED_DOWN=0")

    async def _status():
        return "running"

    monkeypatch.setattr(adp, "_read_configured_environment", _env)
    monkeypatch.setattr(adp, "get_status", _status)

    v = await adp.get_conduit_config_view()
    assert v.reduced.enabled is False
    assert v.reduced.start is None and v.reduced.max_common_clients is None
    assert v.max_common_clients.configured == 50
    assert v.bandwidth_mbps.configured == 40
