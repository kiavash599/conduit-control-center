# SPDX-License-Identifier: MIT
"""
Unit tests for backend.api.advisor C1 helpers (A1.3c step C1).

Pure helpers (no I/O): _history_days, _append_sample/_prune, _window_average,
_growth_allowed (G1-G4 incl. temp skip-if-missing). I/O helpers (_gather_*) are
exercised with monkeypatched readers/psutil. The endpoint/serialization/router
are NOT in this step.
"""
from __future__ import annotations

import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone

import backend.api.advisor as adv
from backend.advisor.models import AdvisorPolicy, SystemSnapshot
from backend.traffic.models import CounterReading, NodeRuntime

UTC = timezone.utc
POLICY = AdvisorPolicy()


class _Cfg:
    """Minimal AppConfig stand-in for the warm-up/sampling knobs."""

    advisor_sample_window_seconds = 900
    advisor_sample_throttle_seconds = 45
    advisor_growth_min_samples = 10
    advisor_growth_min_span_seconds = 600
    advisor_growth_sample_pass_fraction = 0.80
    advisor_hourly_history_hours = 168
    traffic_hourly_retention_days = 180


CFG = _Cfg()


def _buf(n, span_s, cpu=20, ram=50, temp=55):
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    step = span_s / (n - 1) if n > 1 else 0
    return deque(
        (base + timedelta(seconds=step * i), SystemSnapshot(cpu, ram, temp)) for i in range(n)
    )


# --------------------------- _history_days ---------------------------
def test_history_days_valid():
    now = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    assert adv._history_days("2026-06-06T00:00:00Z", now, 180) == 8  # 8.5d -> 8


def test_history_days_none():
    assert adv._history_days(None, datetime(2026, 6, 14, tzinfo=UTC), 180) == 0


def test_history_days_malformed():
    assert adv._history_days("not-a-date", datetime(2026, 6, 14, tzinfo=UTC), 180) == 0


def test_history_days_capped():
    assert adv._history_days("2020-01-01T00:00:00Z", datetime(2026, 6, 14, tzinfo=UTC), 180) == 180


def test_history_days_future_is_zero():
    assert adv._history_days("2026-06-20T00:00:00Z", datetime(2026, 6, 14, tzinfo=UTC), 180) == 0


# --------------------------- buffer append/prune ---------------------------
def test_append_respects_throttle():
    buf = deque()
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    s = SystemSnapshot(10, 20, 50)
    adv._append_sample(buf, base, s, throttle_seconds=45, window_seconds=900)
    adv._append_sample(buf, base + timedelta(seconds=30), s, throttle_seconds=45, window_seconds=900)
    assert len(buf) == 1  # within throttle -> skipped
    adv._append_sample(buf, base + timedelta(seconds=60), s, throttle_seconds=45, window_seconds=900)
    assert len(buf) == 2  # past throttle -> appended


def test_prune_drops_old_samples():
    buf = deque()
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    s = SystemSnapshot(10, 20, 50)
    buf.append((base, s))
    adv._append_sample(buf, base + timedelta(seconds=1000), s, throttle_seconds=45, window_seconds=900)
    assert len(buf) == 1 and buf[0][0] == base + timedelta(seconds=1000)


def test_none_sample_not_appended_but_prunes():
    buf = deque()
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    buf.append((base, SystemSnapshot(10, 20, 50)))
    adv._append_sample(buf, base + timedelta(seconds=1000), None, throttle_seconds=45, window_seconds=900)
    assert len(buf) == 0


# --------------------------- _window_average ---------------------------
def test_window_average():
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    buf = deque([(base, SystemSnapshot(10, 20, 50)), (base, SystemSnapshot(30, 40, 60))])
    a = adv._window_average(buf)
    assert (a.cpu_percent, a.ram_percent, a.cpu_temperature_celsius) == (20.0, 30.0, 55.0)


def test_window_average_temp_none_when_absent():
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    a = adv._window_average(deque([(base, SystemSnapshot(10, 20, None))]))
    assert a.cpu_temperature_celsius is None and a.cpu_percent == 10.0


def test_window_average_empty():
    assert adv._window_average(deque()) is None


# --------------------------- _growth_allowed (G1-G4) ---------------------------
def test_growth_allowed_true():
    assert adv._growth_allowed(_buf(10, 600), POLICY, CFG) is True


def test_growth_g1_too_few_samples():
    assert adv._growth_allowed(_buf(9, 600), POLICY, CFG) is False


def test_growth_g2_span_too_short():
    assert adv._growth_allowed(_buf(10, 300), POLICY, CFG) is False


def test_growth_g3_individual_fraction_below():
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    step = 600 / 9
    samples = [
        (base + timedelta(seconds=step * i), SystemSnapshot(95 if i < 3 else 20, 50, 55))
        for i in range(10)
    ]  # 3/10 fail -> 70% pass < 80%
    assert adv._growth_allowed(deque(samples), POLICY, CFG) is False


def test_growth_g4_average_fails_even_if_g3_passes():
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    step = 600 / 9
    # 8 samples cpu=39 (pass), 2 samples cpu=45 (fail) -> G3 = 0.8 passes;
    # avg cpu = (8*39 + 2*45)/10 = 40.2 >= 40 -> G4 fails.
    samples = [
        (base + timedelta(seconds=step * i), SystemSnapshot(39 if i < 8 else 45, 50, 55))
        for i in range(10)
    ]
    assert adv._growth_allowed(deque(samples), POLICY, CFG) is False


def test_growth_temp_missing_still_allows():
    assert adv._growth_allowed(_buf(10, 600, temp=None), POLICY, CFG) is True


def test_growth_temp_present_high_blocks():
    assert adv._growth_allowed(_buf(10, 600, temp=85), POLICY, CFG) is False


# --------------------------- _gather_system (sync, monkeypatched) ---------------------------
def test_gather_system_ok(monkeypatch):
    monkeypatch.setattr(adv.psutil, "cpu_percent", lambda interval=None: 12.3)
    monkeypatch.setattr(adv.psutil, "virtual_memory", lambda: type("VM", (), {"percent": 45.6})())
    monkeypatch.setattr(adv, "_cpu_temperature", lambda: 55.0)
    s = adv._gather_system()
    assert (s.cpu_percent, s.ram_percent, s.cpu_temperature_celsius) == (12.3, 45.6, 55.0)


def test_gather_system_degrades(monkeypatch):
    def boom(*a, **k):
        raise adv.psutil.Error("x")
    monkeypatch.setattr(adv.psutil, "cpu_percent", boom)
    assert adv._gather_system() is None


# --------------------------- _gather_conduit / _gather_node (async, monkeypatched) ---------------------------
async def test_gather_conduit_ok(monkeypatch):
    async def fake():
        return CounterReading(bytes_up=1, bytes_down=2, uptime_seconds=100.0, is_live=True)
    monkeypatch.setattr(adv, "read_counters", fake)
    c = await adv._gather_conduit()
    assert c.is_live is True and c.uptime_seconds == 100.0


async def test_gather_conduit_unreachable(monkeypatch):
    async def boom():
        raise adv.ConduitUnreachableError("down")
    monkeypatch.setattr(adv, "read_counters", boom)
    c = await adv._gather_conduit()
    assert c.is_live is None and c.uptime_seconds is None


async def test_gather_node_passthrough(monkeypatch):
    async def fake():
        return NodeRuntime(connected_clients=7, idle_seconds=0, max_common_clients=50)
    monkeypatch.setattr(adv, "get_node_runtime", fake)
    n = await adv._gather_node()
    assert n.connected_clients == 7


# --------------------------- _gather_traffic (async, monkeypatched) ---------------------------
async def test_gather_traffic_with_history(monkeypatch):
    async def fake_summary(db, *, now_ts):
        return {
            "recording_since": "2026-06-01T00:00:00Z",
            "lifetime": {"bytes_up": 10, "bytes_down": 20},
            "windows": {"last_24h": {"bytes_up": 1, "bytes_down": 2},
                        "last_7d": {"bytes_up": 3, "bytes_down": 4}},
        }
    async def fake_series(db, *, hours, now_ts):
        return [{"bucket_utc": "2026-06-14T11:00:00Z", "bytes_up": 5, "bytes_down": 6}]
    monkeypatch.setattr(adv.reads, "get_summary", fake_summary)
    monkeypatch.setattr(adv.reads, "get_hourly_series", fake_series)
    t = await adv._gather_traffic(None, now_ts="2026-06-14T12:00:00Z", cfg=CFG)
    assert t.history_days >= 7
    assert t.series_hourly is not None and t.series_hourly[0].bytes_up == 5
    assert t.lifetime.bytes_up == 10 and t.last_24h.bytes_up == 1 and t.last_7d.bytes_down == 4


async def test_gather_traffic_short_history_skips_series(monkeypatch):
    async def fake_summary(db, *, now_ts):
        return {"recording_since": "2026-06-13T00:00:00Z", "lifetime": None, "windows": {}}
    called = {"series": False}
    async def fake_series(db, *, hours, now_ts):
        called["series"] = True
        return []
    monkeypatch.setattr(adv.reads, "get_summary", fake_summary)
    monkeypatch.setattr(adv.reads, "get_hourly_series", fake_series)
    t = await adv._gather_traffic(None, now_ts="2026-06-14T12:00:00Z", cfg=CFG)
    assert t.series_hourly is None and t.history_days < 7
    assert called["series"] is False


async def test_gather_traffic_degrades(monkeypatch):
    async def boom(db, *, now_ts):
        raise sqlite3.OperationalError("db gone")
    monkeypatch.setattr(adv.reads, "get_summary", boom)
    assert await adv._gather_traffic(None, now_ts="2026-06-14T12:00:00Z", cfg=CFG) is None
