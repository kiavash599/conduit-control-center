# SPDX-License-Identifier: MIT
"""
Unit tests for the pure Contribution Advisor engine (A1.2).

Covers the A1.2 validation matrix: healthy, new, idle, resource pressure,
thermal, near-limit growth (strong/shallow/no-demand), quiet-window, broker
disconnected, conduit offline, declining, missing-temp (growth still allowed),
missing-idle, conflict ordering, cooldown + config-change reset, and purity
(deterministic, now injected).

Pure engine -> no mocks, no I/O; inputs are constructed directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.advisor.engine import evaluate
from backend.advisor.models import (
    AdvisorInput,
    AdvisorPolicy,
    AdvisorState,
    BytesPair,
    ConduitState,
    Domain,
    SeriesBucket,
    Severity,
    SystemSnapshot,
    TrafficSnapshot,
)
from backend.traffic.models import NodeRuntime

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
DAY = 86400
HOUR = 3600


def _sys(cpu, ram, temp=None):
    return SystemSnapshot(cpu_percent=cpu, ram_percent=ram, cpu_temperature_celsius=temp)


def _node(clients, idle, maxc):
    return NodeRuntime(connected_clients=clients, idle_seconds=idle, max_common_clients=maxc)


def _flat_series(per_hour_by_day):
    """per_hour_by_day: list of per-hour value for each day (same value all hours)."""
    base = datetime(2026, 6, 7, 0, 0, 0, tzinfo=timezone.utc)
    out = []
    for d, val in enumerate(per_hour_by_day):
        for h in range(24):
            ts = (base + timedelta(days=d, hours=h)).strftime("%Y-%m-%dT%H:00:00Z")
            out.append(SeriesBucket(ts, bytes_up=val, bytes_down=0))
    return tuple(out)


def _quiet_series(days=7, low_hours=range(1, 8), low=5, mid=40, high=100, high_hours=range(17, 24)):
    base = datetime(2026, 6, 7, 0, 0, 0, tzinfo=timezone.utc)
    out = []
    for d in range(days):
        for h in range(24):
            val = low if h in low_hours else (high if h in high_hours else mid)
            ts = (base + timedelta(days=d, hours=h)).strftime("%Y-%m-%dT%H:00:00Z")
            out.append(SeriesBucket(ts, bytes_up=val, bytes_down=0))
    return tuple(out)


LIFETIME = TrafficSnapshot(lifetime=BytesPair(1_000_000_000, 0))


def _domains(items):
    return [(it.domain, it.severity) for it in items]


# 1 — healthy
def test_healthy_no_items():
    inp = AdvisorInput(_sys(35, 60, 58), _node(120, 0, 200), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert r.items == []
    assert r.summary.status == "live"
    assert "Healthy" in r.summary.headline


# 2 — new station
def test_new_station_info():
    inp = AdvisorInput(_sys(20, 30, 50), _node(0, 0, 50), ConduitState(True, 10 * HOUR), TrafficSnapshot())
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.HEALTH, Severity.INFO)]
    assert "New station" == r.items[0].title


# 3 — idle established
def test_idle_established_suggestion():
    inp = AdvisorInput(_sys(20, 30, 50), _node(0, 20 * HOUR, 50), ConduitState(True, 6 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.HEALTH, Severity.SUGGESTION)]


# 4 — resource pressure (RAM)
def test_ram_pressure_warning():
    inp = AdvisorInput(_sys(75, 90, 72), _node(180, 0, 200), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.WARNING)]
    assert r.items[0].title == "Reduce client limit"


# 5 — thermal warning
def test_thermal_warning():
    inp = AdvisorInput(_sys(60, 50, 82), _node(50, 0, 200), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.WARNING)]


# 6 — near limit, deep headroom -> STRONG growth 50->75
def test_growth_strong():
    inp = AdvisorInput(_sys(22, 50, 55), _node(42, 0, 50), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.STRONG_SUGGESTION)]
    assert "75" in r.items[0].message


# 6b — near limit, shallow headroom -> SUGGESTION 50->75
def test_growth_shallow_suggestion():
    inp = AdvisorInput(_sys(38, 66, 64), _node(45, 0, 50), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.SUGGESTION)]
    assert "75" in r.items[0].message


# 6c — spare capacity but no demand -> nothing
def test_no_demand_no_growth():
    inp = AdvisorInput(_sys(15, 40, 55), _node(7, 0, 50), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert r.items == []
    assert r.summary.status == "live"


# 7 — quiet-window opportunity -> STRONG reduced-mode 01:00-08:00
def test_quiet_window_strong():
    traffic = TrafficSnapshot(lifetime=BytesPair(1, 0), series_hourly=_quiet_series(7), history_days=7)
    inp = AdvisorInput(_sys(30, 50, 60), _node(10, 0, 50), ConduitState(True, 10 * DAY), traffic)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.REDUCED_MODE, Severity.STRONG_SUGGESTION)]
    assert "01:00–08:00 UTC" in r.items[0].message


# 8a — broker disconnected (is_live False) -> WARNING
def test_broker_disconnected_warning():
    inp = AdvisorInput(_sys(30, 50, 60), _node(0, 0, 50), ConduitState(False, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.HEALTH, Severity.WARNING)]
    assert r.items[0].title == "Broker disconnected"


# 8b — conduit offline / metrics unreachable -> offline summary, no warning item
def test_conduit_offline_summary():
    inp = AdvisorInput(system=_sys(30, 50, 60), node=None, conduit=ConduitState(None, None), traffic=None)
    r = evaluate(inp, now=NOW)
    assert r.items == []
    assert r.summary.status == "offline"


# 9 — declining contribution -> WARNING (last 2 days < 50% of prior mean)
def test_declining_warning():
    series = _flat_series([100, 100, 100, 100, 100, 10, 10])  # 5 high days, 2 low days
    traffic = TrafficSnapshot(lifetime=BytesPair(1, 0), series_hourly=series, history_days=7)
    inp = AdvisorInput(_sys(30, 50, 60), _node(5, 0, 50), ConduitState(True, 10 * DAY), traffic)
    r = evaluate(inp, now=NOW)
    assert (Domain.HEALTH, Severity.WARNING) in _domains(r.items)
    assert any(it.title == "Contribution dropping" for it in r.items)


# 10 — missing temperature -> growth STILL allowed, no thermal warning (adjustment 2)
def test_missing_temp_allows_growth():
    inp = AdvisorInput(_sys(20, 45, None), _node(42, 0, 50), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.STRONG_SUGGESTION)]
    assert "75" in r.items[0].message
    assert all(it.severity != Severity.WARNING for it in r.items)


# 11 — missing idle -> idle rule skipped, healthy
def test_missing_idle_skips_rule():
    inp = AdvisorInput(_sys(30, 50, 60), _node(10, None, 50), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW)
    assert r.items == []
    assert r.summary.status == "live"


# 12 — conflict: resource pressure + quiet window -> both, Warning first
def test_conflict_pressure_and_quiet_window():
    traffic = TrafficSnapshot(lifetime=BytesPair(1, 0), series_hourly=_quiet_series(7), history_days=7)
    inp = AdvisorInput(_sys(60, 90, 60), _node(10, 0, 50), ConduitState(True, 10 * DAY), traffic)
    r = evaluate(inp, now=NOW)
    assert _domains(r.items) == [
        (Domain.CAPACITY, Severity.WARNING),
        (Domain.REDUCED_MODE, Severity.STRONG_SUGGESTION),
    ]


# 13 — cooldown suppresses repeat growth; config change resets it
def _growth_input():
    return AdvisorInput(_sys(22, 50, 55), _node(42, 0, 50), ConduitState(True, 10 * DAY), LIFETIME)


def test_cooldown_suppresses_repeat_growth():
    state = AdvisorState(last_emitted_at={"capacity_growth": NOW - timedelta(hours=1)}, last_max_common_clients=50)
    r = evaluate(_growth_input(), now=NOW, state=state)
    assert r.items == []  # within 24h cooldown


def test_cooldown_expired_re_emits():
    state = AdvisorState(last_emitted_at={"capacity_growth": NOW - timedelta(hours=25)}, last_max_common_clients=50)
    r = evaluate(_growth_input(), now=NOW, state=state)
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.STRONG_SUGGESTION)]
    assert r.state.last_emitted_at["capacity_growth"] == NOW


def test_config_change_resets_cooldown():
    # last_max=40 but current=50 -> operator changed limit -> cooldown reset -> emits
    state = AdvisorState(last_emitted_at={"capacity_growth": NOW - timedelta(hours=1)}, last_max_common_clients=40)
    r = evaluate(_growth_input(), now=NOW, state=state)
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.STRONG_SUGGESTION)]


# Purity — deterministic; engine reads no clock (now injected)
def test_deterministic_same_inputs():
    inp = _growth_input()
    a = evaluate(inp, now=NOW)
    b = evaluate(inp, now=NOW)
    assert _domains(a.items) == _domains(b.items)
    assert a.summary == b.summary


# --- A1.3 Option Z: growth_enabled flag -------------------------------------
def test_growth_enabled_default_true_keeps_behavior():
    r = evaluate(_growth_input(), now=NOW, policy=AdvisorPolicy())
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.STRONG_SUGGESTION)]


def test_growth_disabled_suppresses_growth():
    r = evaluate(_growth_input(), now=NOW, policy=AdvisorPolicy(growth_enabled=False))
    assert r.items == []
    assert r.summary.status == "live"


def test_growth_disabled_backoff_still_works():
    inp = AdvisorInput(_sys(75, 90, 72), _node(180, 0, 200), ConduitState(True, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW, policy=AdvisorPolicy(growth_enabled=False))
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.WARNING)]


def test_growth_disabled_reduced_mode_still_works():
    traffic = TrafficSnapshot(lifetime=BytesPair(1, 0), series_hourly=_quiet_series(7), history_days=7)
    inp = AdvisorInput(_sys(30, 50, 60), _node(10, 0, 50), ConduitState(True, 10 * DAY), traffic)
    r = evaluate(inp, now=NOW, policy=AdvisorPolicy(growth_enabled=False))
    assert _domains(r.items) == [(Domain.REDUCED_MODE, Severity.STRONG_SUGGESTION)]


def test_growth_disabled_health_still_works():
    inp = AdvisorInput(_sys(30, 50, 60), _node(0, 0, 50), ConduitState(False, 10 * DAY), LIFETIME)
    r = evaluate(inp, now=NOW, policy=AdvisorPolicy(growth_enabled=False))
    assert _domains(r.items) == [(Domain.HEALTH, Severity.WARNING)]
    assert r.items[0].title == "Broker disconnected"


def test_growth_disabled_summary_present():
    r = evaluate(_growth_input(), now=NOW, policy=AdvisorPolicy(growth_enabled=False))
    assert r.summary.status == "live"
    assert "Healthy" in r.summary.headline


def test_no_cooldown_stamped_when_growth_disabled():
    # Growth-eligible inputs, but growth disabled -> no growth cooldown recorded,
    # while _track_max still tracks the current limit. (No engine-internal keys referenced.)
    r = evaluate(_growth_input(), now=NOW, state=AdvisorState(), policy=AdvisorPolicy(growth_enabled=False))
    assert r.items == []
    assert r.state.last_emitted_at == {}
    assert r.state.last_max_common_clients == 50


def test_cooldown_stamped_when_growth_enabled():
    r = evaluate(_growth_input(), now=NOW, state=AdvisorState(), policy=AdvisorPolicy(growth_enabled=True))
    assert _domains(r.items) == [(Domain.CAPACITY, Severity.STRONG_SUGGESTION)]
    assert r.state.last_emitted_at != {}
    assert r.state.last_max_common_clients == 50
