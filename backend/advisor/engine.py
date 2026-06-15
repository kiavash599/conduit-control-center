# SPDX-License-Identifier: MIT
"""
backend/advisor/engine.py
-------------------------
Pure Contribution Advisor engine (A1.2).

``evaluate(inp, *, now, state, policy)`` is a pure function: deterministic from
its inputs, no I/O, no clock reads (``now`` is injected), no storage. Cooldown
state is passed in and a new copy returned; the caller (A1.3) owns persistence.

Three domains (each emits at most one item, except health which also returns
the always-present summary):
  1. Capacity / client-limit  (back-off Warning  XOR  growth Suggestion/Strong)
  2. Reduced-mode quiet window (from hourly history)
  3. Contribution health      (broker / new / idle / declining)

Adjustments locked at A1.2 sign-off:
  - Engine is pure; ``now`` injected; state in -> state out; no storage/timers.
  - Missing temperature disables thermal Warning AND skips the thermal growth
    gate, but does NOT block growth (CPU/RAM/demand still decide).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from backend.advisor.models import (
    AdvisorInput,
    AdvisorItem,
    AdvisorPolicy,
    AdvisorResult,
    AdvisorState,
    ContributionHealthSummary,
    Domain,
    Severity,
)

_COOLDOWN_GROWTH = "capacity_growth"
_DOMAIN_PRIORITY = {Domain.HEALTH: 0, Domain.CAPACITY: 1, Domain.REDUCED_MODE: 2}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def evaluate(
    inp: AdvisorInput,
    *,
    now: datetime,
    state: AdvisorState | None = None,
    policy: AdvisorPolicy = AdvisorPolicy(),
) -> AdvisorResult:
    """Pure: produce advisory items + health summary + updated cooldown state."""
    if state is None:
        state = AdvisorState()

    items: list[AdvisorItem] = []

    cap_item, state = _capacity(inp, now, state, policy)
    if cap_item is not None:
        items.append(cap_item)

    rm_item = _reduced_mode(inp, policy)
    if rm_item is not None:
        items.append(rm_item)

    items.extend(_health(inp, policy))

    items = sorted(items, key=lambda it: (int(it.severity), _DOMAIN_PRIORITY[it.domain]))
    return AdvisorResult(items=items, summary=_summary(inp), state=state)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _round25(value: float) -> int:
    """Round half-up to the nearest 25 (avoids banker's-rounding surprises)."""
    return int((value + 12.5) // 25) * 25


def _growth_target(current: int, policy: AdvisorPolicy) -> int:
    step = _round25(current * policy.growth_pct)
    step = max(policy.growth_step_min, min(step, policy.growth_step_max))
    return min(current + step, policy.max_limit_ceiling)


def _track_max(state: AdvisorState, current: int | None) -> AdvisorState:
    """Reset the growth cooldown when the operator changes the limit (hysteresis)."""
    if current is None or current == state.last_max_common_clients:
        return state
    emitted = {k: v for k, v in state.last_emitted_at.items() if k != _COOLDOWN_GROWTH}
    return AdvisorState(last_emitted_at=emitted, last_max_common_clients=current)


# ---------------------------------------------------------------------------
# Domain 1 — Capacity / client limit  (one item: back-off XOR growth)
# ---------------------------------------------------------------------------
def _capacity(
    inp: AdvisorInput, now: datetime, state: AdvisorState, policy: AdvisorPolicy
) -> tuple[AdvisorItem | None, AdvisorState]:
    sys = inp.system
    if sys is None:
        return None, state

    node = inp.node
    cur = node.max_common_clients if node else None
    state = _track_max(state, cur)

    cpu, ram, temp = sys.cpu_percent, sys.ram_percent, sys.cpu_temperature_celsius

    # Back-off (WARNING): any present-and-exceeded metric. Temp skipped if None.
    pressures: list[str] = []
    if cpu is not None and cpu > policy.cpu_warn:
        pressures.append(f"CPU {cpu:.0f}%")
    if ram is not None and ram > policy.ram_warn:
        pressures.append(f"RAM {ram:.0f}%")
    if temp is not None and temp >= policy.temp_warn:
        pressures.append(f"{temp:.0f}°C")
    if pressures:
        return (
            AdvisorItem(
                Severity.WARNING,
                Domain.CAPACITY,
                "Reduce client limit",
                "Your station is under resource pressure — consider lowering the client limit.",
                "Resource pressure: " + ", ".join(pressures) + ".",
                "Lower max-common-clients in the Conduit service config and restart Conduit.",
            ),
            state,
        )

    # Growth is skipped entirely when the caller (A1.3 API, Option Z) has not yet
    # confirmed sustained headroom -> policy.growth_enabled is False. Back-off above
    # and _track_max (cooldown reset on config change) still run; crucially, no
    # growth cooldown is stamped here, so nothing needs to be rolled back.
    if not policy.growth_enabled:
        return None, state

    # Growth (SUGGESTION/STRONG): live + demand + headroom + temp gate (skip if None) + cooldown.
    if node is None or cur is None or node.connected_clients is None or cur <= 0:
        return None, state
    if (inp.conduit.is_live if inp.conduit else None) is not True:
        return None, state
    if cpu is None or ram is None:
        return None, state

    clients = node.connected_clients
    if clients < policy.demand_fraction * cur:
        return None, state
    if not (cpu < policy.cpu_grow_suggest and ram < policy.ram_grow_suggest):
        return None, state
    if temp is not None and temp >= policy.temp_grow_gate:  # adjustment: skip gate when None
        return None, state

    last = state.last_emitted_at.get(_COOLDOWN_GROWTH)
    if last is not None and (now - last) < timedelta(hours=policy.growth_cooldown_hours):
        return None, state

    new_limit = _growth_target(cur, policy)
    if new_limit <= cur:
        return None, state

    strong = cpu < policy.cpu_grow_strong and ram < policy.ram_grow_strong
    severity = Severity.STRONG_SUGGESTION if strong else Severity.SUGGESTION
    temp_txt = f", {temp:.0f}°C" if temp is not None else ""
    emitted = dict(state.last_emitted_at)
    emitted[_COOLDOWN_GROWTH] = now
    state = AdvisorState(last_emitted_at=emitted, last_max_common_clients=state.last_max_common_clients)

    return (
        AdvisorItem(
            severity,
            Domain.CAPACITY,
            "Raise client limit",
            f"Demand is near your limit with capacity to spare — consider raising the client limit to {new_limit}.",
            f"{clients}/{cur} clients with spare capacity (CPU {cpu:.0f}%, RAM {ram:.0f}%{temp_txt}).",
            f"Raise max-common-clients to {new_limit} in the Conduit service config and restart Conduit; "
            "connected clients briefly disconnect.",
        ),
        state,
    )


# ---------------------------------------------------------------------------
# Domain 2 — Reduced-mode quiet window
# ---------------------------------------------------------------------------
def _hour_of(bucket_utc: str) -> int | None:
    if "T" not in bucket_utc:
        return None
    try:
        return int(bucket_utc.split("T", 1)[1][:2])
    except (ValueError, IndexError):
        return None


def _longest_circular_run(hours: set[int]) -> tuple[int, int] | None:
    """Longest contiguous run of quiet hours on the 24h circle -> (start, length)."""
    if not hours:
        return None
    present = [h in hours for h in range(24)]
    if all(present):
        return (0, 24)
    best_start, best_len = None, 0
    cur_start, cur_len = None, 0
    for i in range(48):  # double the circle to catch midnight wrap
        h = i % 24
        if present[h]:
            if cur_len == 0:
                cur_start = h
            cur_len += 1
            if cur_len > best_len and cur_len <= 24:
                best_len, best_start = cur_len, cur_start
        else:
            cur_len = 0
    return (best_start, best_len) if best_start is not None else None


def _reduced_mode(inp: AdvisorInput, policy: AdvisorPolicy) -> AdvisorItem | None:
    t = inp.traffic
    if t is None or t.series_hourly is None:
        return None
    if t.history_days < policy.reduced_min_history_days:
        return None

    by_hour: dict[int, list[int]] = defaultdict(list)
    for b in t.series_hourly:
        h = _hour_of(b.bucket_utc)
        if h is not None:
            by_hour[h].append(b.total)
    if len(by_hour) < 24:  # need full daily coverage for a reliable peak
        return None

    med = {h: statistics.median(v) for h, v in by_hour.items()}
    peak = max(med.values())
    if peak <= 0:
        return None
    threshold = policy.reduced_quiet_fraction * peak

    quiet: set[int] = set()
    consistency: dict[int, float] = {}
    for h in range(24):
        vals = by_hour[h]
        if not vals:
            continue
        frac = sum(1 for x in vals if x <= threshold) / len(vals)
        consistency[h] = frac
        if med[h] <= threshold and frac >= policy.reduced_day_consistency:
            quiet.add(h)

    run = _longest_circular_run(quiet)
    if run is None:
        return None
    start, length = run
    if not (policy.reduced_min_run_hours <= length <= policy.reduced_max_run_hours):
        return None

    end = (start + length) % 24
    window = f"{start:02d}:00–{end:02d}:00 UTC"
    min_consistency = min(consistency[(start + i) % 24] for i in range(length))
    strong = length >= policy.reduced_strong_run_hours and min_consistency >= policy.reduced_strong_consistency
    severity = Severity.STRONG_SUGGESTION if strong else Severity.SUGGESTION

    return AdvisorItem(
        severity,
        Domain.REDUCED_MODE,
        "Schedule a reduced-mode quiet window",
        f"Traffic is consistently low {window} — consider a reduced-mode window then to save resources.",
        f"Lowest-traffic window {window} across the last {t.history_days} days.",
        f"Set a reduced-mode window for {window} via the Conduit service config (InproxyReduced*) and restart. "
        "Reduced mode is bounded by Conduit's 100 GB / 7-day minimum.",
    )


# ---------------------------------------------------------------------------
# Domain 3 — Contribution health (0-1 actionable item; summary is separate)
# ---------------------------------------------------------------------------
def _daily_totals(series: tuple) -> list[tuple[str, int]]:
    by_day: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for b in series:
        if "T" not in b.bucket_utc:
            continue
        day = b.bucket_utc.split("T", 1)[0]
        by_day[day][0] += b.total
        by_day[day][1] += 1
    complete = [(d, v[0]) for d, v in by_day.items() if v[1] >= 24]
    complete.sort()
    return complete


def _declining(inp: AdvisorInput, policy: AdvisorPolicy) -> AdvisorItem | None:
    conduit, t = inp.conduit, inp.traffic
    if (conduit.is_live if conduit else None) is not True:
        return None
    if t is None or t.series_hourly is None or t.history_days < policy.reduced_min_history_days:
        return None
    days = _daily_totals(t.series_hourly)
    if len(days) < policy.decline_min_days + 1:
        return None
    recent = [tot for _, tot in days[-policy.decline_min_days:]]
    baseline = [tot for _, tot in days[: -policy.decline_min_days]][-7:]
    if not baseline:
        return None
    mean = sum(baseline) / len(baseline)
    if mean <= 0:
        return None
    if all(r < policy.decline_fraction * mean for r in recent):
        return AdvisorItem(
            Severity.WARNING,
            Domain.HEALTH,
            "Contribution dropping",
            "Your recent contribution is well below your weekly average — "
            "check broker connectivity and your network.",
            f"Last {policy.decline_min_days} days each below "
            f"{int(policy.decline_fraction * 100)}% of the prior daily average.",
            None,
        )
    return None


def _health(inp: AdvisorInput, policy: AdvisorPolicy) -> list[AdvisorItem]:
    conduit, node, t = inp.conduit, inp.node, inp.traffic
    is_live = conduit.is_live if conduit else None
    uptime = conduit.uptime_seconds if conduit else None
    clients = node.connected_clients if node else None
    idle = node.idle_seconds if node else None
    lifetime_total = t.lifetime.total if (t and t.lifetime) else None

    if is_live is False:
        return [
            AdvisorItem(
                Severity.WARNING,
                Domain.HEALTH,
                "Broker disconnected",
                "Conduit is running but not connected to the Psiphon broker — "
                "no clients can be served until it reconnects.",
                "Broker connection is down.",
                None,
            )
        ]

    decl = _declining(inp, policy)
    if decl is not None:
        return [decl]

    new_window = uptime is not None and uptime < policy.new_station_uptime_hours * 3600
    no_traffic_yet = lifetime_total is None or lifetime_total < policy.new_station_traffic_floor_bytes
    if new_window and clients == 0 and no_traffic_yet:
        return [
            AdvisorItem(
                Severity.INFO,
                Domain.HEALTH,
                "New station",
                "No traffic yet — new stations can take 24–48 hours to start "
                "receiving clients while building reputation with the network.",
                "Uptime under 48 hours with no clients or recorded traffic.",
                None,
            )
        ]

    established = uptime is not None and uptime >= policy.new_station_uptime_hours * 3600
    if idle is not None and idle > policy.idle_warn_hours * 3600 and clients == 0 and established:
        return [
            AdvisorItem(
                Severity.SUGGESTION,
                Domain.HEALTH,
                "No recent traffic",
                "No clients for over 12 hours — check your connectivity and public IP.",
                "Idle for more than 12 hours with no connected clients.",
                None,
            )
        ]

    return []


# ---------------------------------------------------------------------------
# Always-present health summary
# ---------------------------------------------------------------------------
def _summary(inp: AdvisorInput) -> ContributionHealthSummary:
    conduit, node, t = inp.conduit, inp.node, inp.traffic
    is_live = conduit.is_live if conduit else None
    clients = node.connected_clients if node else None
    lifetime = t.lifetime if (t and t.lifetime) else None

    if node is None and is_live is None:
        status, headline = "offline", "Conduit metrics unavailable — is it running?"
    elif is_live is True:
        status = "live"
        headline = "Healthy — broker live" + (
            f", serving {clients} clients" if clients is not None else ""
        )
    elif is_live is False:
        status, headline = "disconnected", "Conduit running but not connected to the broker"
    else:
        status, headline = "unknown", "Status unknown"

    return ContributionHealthSummary(
        status=status,
        headline=headline,
        is_live=is_live,
        connected_clients=clients,
        lifetime_up=lifetime.bytes_up if lifetime else None,
        lifetime_down=lifetime.bytes_down if lifetime else None,
        recording_since=t.recording_since if t else None,
    )
