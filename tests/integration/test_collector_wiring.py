# SPDX-License-Identifier: MIT
"""
Startup/shutdown wiring tests for the traffic collector (P0 Step 3c).

These exercise the lifespan helpers in backend.main with a spy collector, so no
database, flock, or real Conduit is required (they run on any platform):
  - disabled by default -> collector is never constructed or started
  - enabled -> collector constructed with config values and run() scheduled
  - graceful stop -> request_stop() then the task completes
  - stubborn collector -> graceful wait times out, task is cancelled
  - stop with no task -> safe no-op
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import backend.main as main


class _App:
    """Minimal stand-in for the FastAPI app (only .state is used here)."""

    def __init__(self):
        self.state = SimpleNamespace()


class _SpyCollector:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.run_started = asyncio.Event()
        self.stop_requested = False
        self._release = asyncio.Event()
        self.holder_id = "spy-holder"

    async def run(self):
        self.run_started.set()
        await self._release.wait()

    def request_stop(self):
        self.stop_requested = True
        self._release.set()


class _StubbornCollector(_SpyCollector):
    def request_stop(self):
        # Records the request but never releases -> graceful wait must time out.
        self.stop_requested = True


def _cfg(enabled: bool):
    return SimpleNamespace(
        traffic_collector_enabled=enabled,
        traffic_collect_interval_seconds=60.0,
        traffic_gap_threshold_seconds=90.0,
    )


async def test_disabled_does_not_construct_or_start(monkeypatch):
    monkeypatch.setattr(main, "get_app_config", lambda: _cfg(False))
    made = []
    monkeypatch.setattr(
        main, "TrafficCollector", lambda **kw: made.append(kw) or _SpyCollector(**kw)
    )
    app = _App()
    main._maybe_start_traffic_collector(app)
    assert app.state.traffic_collector is None
    assert app.state.traffic_collector_task is None
    assert made == []  # never constructed
    await main._stop_traffic_collector(app)  # safe no-op


async def test_enabled_starts_with_config_and_stops(monkeypatch):
    monkeypatch.setattr(main, "get_app_config", lambda: _cfg(True))
    monkeypatch.setattr(main, "TrafficCollector", _SpyCollector)
    app = _App()
    main._maybe_start_traffic_collector(app)

    col = app.state.traffic_collector
    task = app.state.traffic_collector_task
    assert isinstance(col, _SpyCollector)
    assert col.kwargs["interval_seconds"] == 60.0
    assert col.kwargs["gap_threshold_seconds"] == 90.0
    await asyncio.wait_for(col.run_started.wait(), timeout=1.0)  # task actually ran

    await main._stop_traffic_collector(app)
    assert col.stop_requested is True
    assert task.done()


async def test_stop_cancels_when_graceful_times_out(monkeypatch):
    monkeypatch.setattr(main, "get_app_config", lambda: _cfg(True))
    monkeypatch.setattr(main, "TrafficCollector", _StubbornCollector)
    monkeypatch.setattr(main, "_COLLECTOR_SHUTDOWN_TIMEOUT_S", 0.05)
    app = _App()
    main._maybe_start_traffic_collector(app)
    col = app.state.traffic_collector
    task = app.state.traffic_collector_task
    await asyncio.wait_for(col.run_started.wait(), timeout=1.0)

    await main._stop_traffic_collector(app)
    assert col.stop_requested is True
    assert task.cancelled() or task.done()


async def test_stop_with_no_task_is_safe():
    app = _App()
    app.state.traffic_collector = None
    app.state.traffic_collector_task = None
    await main._stop_traffic_collector(app)  # must not raise
