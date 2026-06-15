# SPDX-License-Identifier: MIT
"""
Unit tests for the read-only Conduit configuration view (M1, §6.1).

Covers the pure helpers (bytes<->Mbps, argv parsing, drift) and the forgiving
get_conduit_config_view() across in-sync / drift / metrics-down / ExecStart-
unavailable / unlimited cases. No privileged or write operation is exercised.
"""
from __future__ import annotations

import backend.conduit.adapter as adp
from backend.conduit.models import ConduitConfigView, ConfigField

_RAISE = object()

EXECSTART = (
    "{ path=/opt/conduit/conduit ; argv[]=/opt/conduit/conduit start "
    "--data-dir /var/lib/conduit/data --metrics-addr 127.0.0.1:9090 "
    "--max-common-clients 50 --bandwidth 40 ; ignore_errors=no ; "
    "start_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
)
METRICS = (
    "conduit_max_common_clients 50\n"
    "conduit_bandwidth_limit_bytes_per_second 5000000\n"
)


# --------------------------- pure helpers ---------------------------
def test_bps_to_mbps():
    assert adp._bps_to_mbps(None) is None
    assert adp._bps_to_mbps(0) == 0
    assert adp._bps_to_mbps(125_000) == 1
    assert adp._bps_to_mbps(5_000_000) == 40


def test_argv_parse_both_flags():
    argv = adp._argv_from_execstart(EXECSTART)
    assert adp._flag_int(argv, "--max-common-clients") == 50
    assert adp._flag_int(argv, "--bandwidth") == 40


def test_argv_parse_equals_form():
    argv = adp._argv_from_execstart("argv[]=/x start --bandwidth=-1 ;")
    assert adp._flag_int(argv, "--bandwidth") == -1


def test_argv_parse_missing_and_garbled():
    assert adp._argv_from_execstart(None) == []
    assert adp._flag_int(adp._argv_from_execstart("argv[]=/x start ;"), "--bandwidth") is None
    bad = adp._argv_from_execstart("argv[]=/x --bandwidth notanint ;")
    assert adp._flag_int(bad, "--bandwidth") is None


def test_argv_uses_last_execstart():
    blob = "argv[]=/x start --bandwidth 10 ; } { argv[]=/x start --bandwidth 99 ;"
    assert adp._flag_int(adp._argv_from_execstart(blob), "--bandwidth") == 99


# --------------------------- drift ---------------------------
def test_field_drift():
    assert ConfigField(50, 50).drift is False
    assert ConfigField(50, 40).drift is True
    assert ConfigField(50, None).drift is None
    assert ConfigField(None, 50).drift is None
    assert ConfigField(-1, 0, unlimited_configured=True, unlimited_effective=True).drift is False
    assert ConfigField(40, 0, unlimited_effective=True).drift is True


def test_view_drift_aggregation():
    sync = ConfigField(50, 50)
    assert ConduitConfigView("running", sync, sync).drift is False
    assert ConduitConfigView("running", ConfigField(50, 40), sync).drift is True
    assert ConduitConfigView("running", ConfigField(50, None), sync).drift is None


# --------------------------- get_conduit_config_view ---------------------------
def _patch(monkeypatch, *, metrics, execstart, status="running"):
    if metrics is _RAISE:
        def _boom(_url):
            raise OSError("down")
        monkeypatch.setattr(adp, "_fetch_metrics_text", _boom)
    else:
        monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _url: metrics)

    async def _run_stub(_args):
        return (0, execstart, "") if execstart is not None else (1, "", "boom")
    monkeypatch.setattr(adp, "_run", _run_stub)

    async def _status():
        return status
    monkeypatch.setattr(adp, "get_status", _status)


async def test_view_in_sync(monkeypatch):
    _patch(monkeypatch, metrics=METRICS, execstart=EXECSTART)
    v = await adp.get_conduit_config_view()
    assert v.service_status == "running"
    assert (v.max_common_clients.configured, v.max_common_clients.effective) == (50, 50)
    assert v.max_common_clients.drift is False
    assert (v.bandwidth_mbps.configured, v.bandwidth_mbps.effective) == (40, 40)
    assert v.drift is False


async def test_view_drift(monkeypatch):
    m = "conduit_max_common_clients 40\nconduit_bandwidth_limit_bytes_per_second 5000000\n"
    _patch(monkeypatch, metrics=m, execstart=EXECSTART)
    v = await adp.get_conduit_config_view()
    assert v.max_common_clients.drift is True
    assert v.drift is True


async def test_view_metrics_down(monkeypatch):
    _patch(monkeypatch, metrics=_RAISE, execstart=EXECSTART, status="stopped")
    v = await adp.get_conduit_config_view()
    assert v.max_common_clients.effective is None
    assert v.max_common_clients.configured == 50
    assert v.max_common_clients.drift is None
    assert v.service_status == "stopped"


async def test_view_execstart_unavailable(monkeypatch):
    _patch(monkeypatch, metrics=METRICS, execstart=None)
    v = await adp.get_conduit_config_view()
    assert v.max_common_clients.configured is None
    assert v.max_common_clients.effective == 50
    assert v.max_common_clients.drift is None


async def test_view_bandwidth_unlimited(monkeypatch):
    m = "conduit_max_common_clients 50\nconduit_bandwidth_limit_bytes_per_second 0\n"
    es = "argv[]=/x start --max-common-clients 50 --bandwidth -1 ;"
    _patch(monkeypatch, metrics=m, execstart=es)
    v = await adp.get_conduit_config_view()
    assert v.bandwidth_mbps.unlimited_configured is True
    assert v.bandwidth_mbps.unlimited_effective is True
    assert v.bandwidth_mbps.drift is False
