# SPDX-License-Identifier: MIT
"""
Unit tests for backend.conduit.adapter.get_node_runtime — the forgiving,
read-only aggregate runtime-gauge reader for the Contribution Advisor (A1.1).

Covers: all-present, per-gauge missing, all-missing, unparseable, float idle,
unreachable endpoint, OSError, unexpected error, and the aggregate-only guard
(labelled per-scope / per-region series must NOT be parsed).

The blocking fetch (adapter._fetch_metrics_text) is monkeypatched — no network.
"""
from __future__ import annotations

import urllib.error

import backend.conduit.adapter as adapter
from backend.traffic.models import NodeRuntime


def _patch_metrics(monkeypatch, text):
    monkeypatch.setattr(adapter, "_fetch_metrics_text", lambda url: text)


def _patch_raise(monkeypatch, exc):
    def _boom(url):
        raise exc
    monkeypatch.setattr(adapter, "_fetch_metrics_text", _boom)


ALL_PRESENT = (
    "# HELP conduit_connected_clients Currently connected clients\n"
    "# TYPE conduit_connected_clients gauge\n"
    "conduit_connected_clients 7\n"
    "conduit_idle_seconds 0\n"
    "conduit_max_common_clients 50\n"
)


async def test_all_present(monkeypatch):
    _patch_metrics(monkeypatch, ALL_PRESENT)
    rt = await adapter.get_node_runtime()
    assert rt == NodeRuntime(connected_clients=7, idle_seconds=0, max_common_clients=50)


async def test_one_gauge_missing(monkeypatch):
    text = "conduit_connected_clients 7\nconduit_max_common_clients 50\n"  # no idle
    _patch_metrics(monkeypatch, text)
    rt = await adapter.get_node_runtime()
    assert rt.connected_clients == 7
    assert rt.max_common_clients == 50
    assert rt.idle_seconds is None


async def test_all_gauges_missing(monkeypatch):
    _patch_metrics(monkeypatch, "# nothing relevant\nother_metric 1\n")
    rt = await adapter.get_node_runtime()
    assert rt == NodeRuntime(None, None, None)


async def test_unparseable_value(monkeypatch):
    text = (
        "conduit_connected_clients abc\n"
        "conduit_idle_seconds 0\n"
        "conduit_max_common_clients 50\n"
    )
    _patch_metrics(monkeypatch, text)
    rt = await adapter.get_node_runtime()
    assert rt.connected_clients is None
    assert rt.idle_seconds == 0
    assert rt.max_common_clients == 50


async def test_float_idle_truncates_to_int(monkeypatch):
    text = (
        "conduit_connected_clients 3\n"
        "conduit_idle_seconds 12.0\n"
        "conduit_max_common_clients 50\n"
    )
    _patch_metrics(monkeypatch, text)
    rt = await adapter.get_node_runtime()
    assert rt.idle_seconds == 12


async def test_labelled_metric_not_parsed(monkeypatch):
    # Aggregate-only guard: labelled per-scope and per-region series must be
    # ignored; only the unlabelled scalar is read.
    text = (
        'conduit_connected_clients{scope="common"} 5\n'
        'conduit_region_connected_clients{scope="common",region="US"} 3\n'
        "conduit_connected_clients 7\n"
        "conduit_idle_seconds 0\n"
        "conduit_max_common_clients 50\n"
    )
    _patch_metrics(monkeypatch, text)
    rt = await adapter.get_node_runtime()
    assert rt.connected_clients == 7  # not 5 (scope), not 3 (region)


async def test_only_labelled_yields_none(monkeypatch):
    # If ONLY the labelled series exists (no unlabelled aggregate),
    # connected_clients must be None — never a per-scope/region value.
    text = (
        'conduit_connected_clients{scope="common"} 5\n'
        'conduit_region_connected_clients{scope="common",region="US"} 3\n'
        "conduit_idle_seconds 0\n"
        "conduit_max_common_clients 50\n"
    )
    _patch_metrics(monkeypatch, text)
    rt = await adapter.get_node_runtime()
    assert rt.connected_clients is None
    assert rt.idle_seconds == 0
    assert rt.max_common_clients == 50


async def test_unreachable_returns_none(monkeypatch):
    _patch_raise(monkeypatch, urllib.error.URLError("connection refused"))
    assert await adapter.get_node_runtime() is None


async def test_oserror_returns_none(monkeypatch):
    _patch_raise(monkeypatch, OSError("socket timeout"))
    assert await adapter.get_node_runtime() is None


async def test_unexpected_error_returns_none(monkeypatch):
    _patch_raise(monkeypatch, RuntimeError("boom"))
    assert await adapter.get_node_runtime() is None


def test_node_runtime_is_aggregate_only_contract():
    # Contract: exactly the three aggregate scalar fields — nothing region/scope.
    assert set(NodeRuntime.__dataclass_fields__) == {
        "connected_clients",
        "idle_seconds",
        "max_common_clients",
    }
