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
