# SPDX-License-Identifier: MIT
"""
Unit tests for Live Operations adapter logic (Commit 1):
  * broker_state() -- the pure four-state machine (+ unknown degradation).
  * get_live_status() -- the forgiving, aggregate-only gauge reader.
No I/O for broker_state; get_live_status uses a stubbed _fetch_metrics_text.
"""
from __future__ import annotations

import backend.conduit.adapter as adp
from backend.conduit.models import LiveStatus


# --------------------------- broker_state (pure) ---------------------------
def test_broker_not_running_for_any_non_running_service():
    for status in ("stopped", "error", "starting", "stopping", "unknown"):
        assert adp.broker_state(status, 1, True) == "not_running", status


def test_broker_live_dominates():
    assert adp.broker_state("running", 1, True) == "live"
    assert adp.broker_state("running", 0, True) == "live"
    assert adp.broker_state("running", None, True) == "live"  # is_live wins, announcing absent


def test_broker_starting():
    assert adp.broker_state("running", 1, False) == "starting"
    assert adp.broker_state("running", 5, False) == "starting"


def test_broker_disconnected():
    assert adp.broker_state("running", 0, False) == "disconnected"
    assert adp.broker_state("running", None, False) == "disconnected"  # announcing absent -> 0


def test_broker_unknown_when_is_live_missing():
    assert adp.broker_state("running", None, None) == "unknown"
    assert adp.broker_state("running", 1, None) == "unknown"


# --------------------------- get_live_status (forgiving) ---------------------------
_FULL = (
    "conduit_is_live 1\n"
    "conduit_announcing 2\n"
    "conduit_connecting_clients 3\n"
    "conduit_idle_seconds 0\n"
    'conduit_build_info{build_repo="x",build_rev="8531118",go_version="go1.24"} 1\n'
)


async def test_live_status_full(monkeypatch):
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: _FULL)
    s = await adp.get_live_status()
    assert isinstance(s, LiveStatus)
    assert s.is_live is True
    assert s.announcing == 2
    assert s.connecting_clients == 3
    assert s.idle_seconds == 0
    assert s.build_rev == "8531118"


async def test_live_status_partial_fields_are_none(monkeypatch):
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: "conduit_is_live 0\n")
    s = await adp.get_live_status()
    assert s.is_live is False
    assert s.announcing is None
    assert s.connecting_clients is None
    assert s.idle_seconds is None
    assert s.build_rev is None


async def test_live_status_unreachable_returns_none(monkeypatch):
    def _boom(_u):
        raise OSError("metrics down")
    monkeypatch.setattr(adp, "_fetch_metrics_text", _boom)
    assert await adp.get_live_status() is None


async def test_live_status_aggregate_only_ignores_labelled(monkeypatch):
    # The unlabelled scalar must be read, never the labelled per-scope series.
    text = (
        'conduit_connecting_clients{scope="common"} 99\n'
        "conduit_connecting_clients 4\n"
        "conduit_is_live 1\n"
    )
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: text)
    s = await adp.get_live_status()
    assert s.connecting_clients == 4
    assert s.is_live is True
