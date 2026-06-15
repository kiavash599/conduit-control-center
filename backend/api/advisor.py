# SPDX-License-Identifier: MIT
"""
backend/api/advisor.py
----------------------
Contribution Advisor API layer (impure shell around the pure A1.2 engine).

A1.3(c) C1: input-gathering + warm-up helpers only. The endpoint, serialization
models, router registration, and app.state lifecycle are added in C2/C3.

Responsibilities of this layer (NOT the engine):
  - gather live inputs (psutil, Conduit metrics, traffic reads), each degrading
    to None on failure (never raising out of the gather helpers);
  - maintain the rolling system-sample buffer (throttled append + window prune);
  - compute the growth warm-up gate (G1-G4) using AdvisorPolicy bands ONLY for
    the headroom thresholds (single source of truth; no duplicated constants).

The engine stays pure: it receives the already-smoothed inputs + injected now +
caller-owned state, and this layer never reaches into engine internals.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import psutil
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from backend.advisor.engine import evaluate
from backend.advisor.models import (
    AdvisorInput,
    AdvisorPolicy,
    AdvisorState,
    BytesPair,
    ConduitState,
    SeriesBucket,
    SystemSnapshot,
    TrafficSnapshot,
)
from backend.conduit.adapter import (
    ConduitUnreachableError,
    MetricsContractError,
    get_node_runtime,
    read_counters,
)
from backend.config import get_app_config
from backend.database import get_db
from backend.dependencies import AuthenticatedUser, get_current_user
from backend.traffic import reads

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

    from backend.config import AppConfig
    from backend.traffic.models import NodeRuntime

logger = logging.getLogger(__name__)

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# A sample is (timestamp, SystemSnapshot); the buffer (a deque of these) is
# created in app.state in C3, not here.


# ---------------------------------------------------------------------------
# Time helpers (now is injected into the engine; these never touch the engine)
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# System sampling (point-in-time)
# ---------------------------------------------------------------------------
def _cpu_temperature() -> float | None:
    """CPU temperature in degrees C, or None if unavailable. Never raises."""
    try:
        if not hasattr(psutil, "sensors_temperatures"):
            return None
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        for key in ("cpu_thermal", "coretemp", "k10temp", "acpitz"):
            entries = temps.get(key)
            if entries:
                return round(entries[0].current, 1)
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except (psutil.Error, OSError):
        logger.debug("advisor: CPU temperature unavailable", exc_info=True)
    return None


def _gather_system() -> SystemSnapshot | None:
    """Point-in-time host metrics; None if psutil fails (degrade-not-fail)."""
    try:
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
    except (psutil.Error, OSError):
        logger.warning("advisor: system metrics unavailable", exc_info=True)
        return None
    return SystemSnapshot(
        cpu_percent=round(cpu, 1),
        ram_percent=round(ram, 1),
        cpu_temperature_celsius=_cpu_temperature(),
    )


# ---------------------------------------------------------------------------
# Conduit-derived inputs (forgiving)
# ---------------------------------------------------------------------------
async def _gather_node() -> "NodeRuntime | None":
    """Aggregate runtime gauges; forgiving (None if endpoint unreachable)."""
    return await get_node_runtime()


async def _gather_conduit() -> ConduitState:
    """is_live + uptime via read_counters; None fields if unreachable/contract-miss."""
    try:
        reading = await read_counters()
        return ConduitState(is_live=reading.is_live, uptime_seconds=reading.uptime_seconds)
    except (ConduitUnreachableError, MetricsContractError):
        return ConduitState(is_live=None, uptime_seconds=None)


# ---------------------------------------------------------------------------
# Traffic inputs (read-only, aggregate-only; degrade-not-fail)
# ---------------------------------------------------------------------------
async def _gather_traffic(
    db: "aiosqlite.Connection", *, now_ts: str, cfg: "AppConfig"
) -> TrafficSnapshot | None:
    """
    Build the TrafficSnapshot from the read layer. Hourly series is fetched only
    when there is >= 7 whole days of history (the engine's reduced-mode/decline
    floor); otherwise series_hourly is None. Degrades to None on any read error.
    """
    try:
        summary = await reads.get_summary(db, now_ts=now_ts)
        recording_since = summary.get("recording_since")
        now_dt = _parse_ts(now_ts) or _now()
        history = _history_days(recording_since, now_dt, cfg.traffic_hourly_retention_days)

        series: tuple[SeriesBucket, ...] | None = None
        if history >= 7:
            rows = await reads.get_hourly_series(
                db, hours=cfg.advisor_hourly_history_hours, now_ts=now_ts
            )
            series = tuple(
                SeriesBucket(r["bucket_utc"], r["bytes_up"], r["bytes_down"]) for r in rows
            )

        lt = summary.get("lifetime")
        windows = summary.get("windows") or {}
        w24 = windows.get("last_24h")
        w7d = windows.get("last_7d")
        return TrafficSnapshot(
            lifetime=BytesPair(lt["bytes_up"], lt["bytes_down"]) if lt else None,
            last_24h=BytesPair(w24["bytes_up"], w24["bytes_down"]) if w24 else None,
            last_7d=BytesPair(w7d["bytes_up"], w7d["bytes_down"]) if w7d else None,
            series_hourly=series,
            recording_since=recording_since,
            history_days=history,
        )
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError):
        logger.warning("advisor: traffic snapshot unavailable", exc_info=True)
        return None


def _history_days(recording_since: str | None, now: datetime, cap_days: int) -> int:
    """Whole UTC days of recorded history, clamped to [0, cap_days]."""
    start = _parse_ts(recording_since) if recording_since else None
    if start is None:
        return 0
    days = int((now - start).total_seconds() // 86400)
    return max(0, min(days, cap_days))


# ---------------------------------------------------------------------------
# Rolling sample buffer (throttled append + window prune)
# ---------------------------------------------------------------------------
def _prune(buffer: "deque", now: datetime, window_seconds: int) -> None:
    while buffer and (now - buffer[0][0]).total_seconds() > window_seconds:
        buffer.popleft()


def _append_sample(
    buffer: "deque",
    now: datetime,
    sample: SystemSnapshot | None,
    *,
    throttle_seconds: int,
    window_seconds: int,
) -> None:
    """Append at most one sample per throttle interval, then prune to the window."""
    if sample is None:
        _prune(buffer, now, window_seconds)
        return
    if buffer and (now - buffer[-1][0]).total_seconds() < throttle_seconds:
        _prune(buffer, now, window_seconds)
        return
    buffer.append((now, sample))
    _prune(buffer, now, window_seconds)


def _window_average(buffer) -> SystemSnapshot | None:
    """Mean CPU/RAM/temp over the buffered samples (None per field if no data)."""
    if not buffer:
        return None
    cpus = [s.cpu_percent for _, s in buffer if s and s.cpu_percent is not None]
    rams = [s.ram_percent for _, s in buffer if s and s.ram_percent is not None]
    temps = [
        s.cpu_temperature_celsius
        for _, s in buffer
        if s and s.cpu_temperature_celsius is not None
    ]
    return SystemSnapshot(
        cpu_percent=round(sum(cpus) / len(cpus), 1) if cpus else None,
        ram_percent=round(sum(rams) / len(rams), 1) if rams else None,
        cpu_temperature_celsius=round(sum(temps) / len(temps), 1) if temps else None,
    )


# ---------------------------------------------------------------------------
# Growth warm-up gate (G1-G4). Headroom bands come from AdvisorPolicy ONLY.
# ---------------------------------------------------------------------------
def _sample_passes_headroom(s: SystemSnapshot | None, policy: AdvisorPolicy) -> bool:
    if s is None or s.cpu_percent is None or s.ram_percent is None:
        return False
    if not (s.cpu_percent < policy.cpu_grow_suggest and s.ram_percent < policy.ram_grow_suggest):
        return False
    # temp: skipped if missing; if present must pass the gate
    if s.cpu_temperature_celsius is not None and s.cpu_temperature_celsius >= policy.temp_grow_gate:
        return False
    return True


def _growth_allowed(buffer, policy: AdvisorPolicy, cfg: "AppConfig") -> bool:
    """
    G1 min sample count, G2 min time span, G3 >= pass-fraction of samples
    individually pass headroom, G4 window-average passes headroom. All four must
    hold. Headroom thresholds are read from AdvisorPolicy (single source).
    """
    n = len(buffer)
    if n < cfg.advisor_growth_min_samples:  # G1
        return False
    span = (buffer[-1][0] - buffer[0][0]).total_seconds()
    if span < cfg.advisor_growth_min_span_seconds:  # G2
        return False
    passed = sum(1 for _, s in buffer if _sample_passes_headroom(s, policy))
    if (passed / n) < cfg.advisor_growth_sample_pass_fraction:  # G3
        return False
    avg = _window_average(buffer)  # G4
    if avg is None or avg.cpu_percent is None or avg.ram_percent is None:
        return False
    return avg.cpu_percent < policy.cpu_grow_suggest and avg.ram_percent < policy.ram_grow_suggest


# ---------------------------------------------------------------------------
# Response models (aggregate-only; never expose AdvisorState/buffer/region/scope)
# ---------------------------------------------------------------------------
class AdvisorItemOut(BaseModel):
    severity: str   # "warning" | "strong_suggestion" | "suggestion" | "info"
    domain: str     # "capacity" | "reduced_mode" | "health"
    title: str
    message: str
    rationale: str
    apply_hint: str | None = None


class AdvisorSummaryOut(BaseModel):
    status: str
    headline: str
    is_live: bool | None = None
    connected_clients: int | None = None
    lifetime_up: int | None = None
    lifetime_down: int | None = None
    recording_since: str | None = None


class AdvisorResponse(BaseModel):
    summary: AdvisorSummaryOut
    items: list[AdvisorItemOut]
    generated_at: str


def _serialize(result, now: datetime) -> AdvisorResponse:
    s = result.summary
    return AdvisorResponse(
        summary=AdvisorSummaryOut(
            status=s.status,
            headline=s.headline,
            is_live=s.is_live,
            connected_clients=s.connected_clients,
            lifetime_up=s.lifetime_up,
            lifetime_down=s.lifetime_down,
            recording_since=s.recording_since,
        ),
        items=[
            AdvisorItemOut(
                severity=it.severity.name.lower(),
                domain=it.domain.value,
                title=it.title,
                message=it.message,
                rationale=it.rationale,
                apply_hint=it.apply_hint,
            )
            for it in result.items
        ],
        generated_at=_iso(now),
    )


# ---------------------------------------------------------------------------
# app.state lifecycle -- shared init used by BOTH the lifespan (eager) and the
# endpoint (defensive). Creating the asyncio.Lock here means it is bound to the
# running event loop. In-memory + per-process: valid only under --workers 1
# (the traffic collector shares this single-worker invariant).
# ---------------------------------------------------------------------------
def ensure_advisor_state(app) -> None:
    st = app.state
    if not hasattr(st, "advisor_state") or st.advisor_state is None:
        st.advisor_state = AdvisorState()
    if not hasattr(st, "advisor_samples"):
        st.advisor_samples = deque()
    if not hasattr(st, "advisor_lock"):
        st.advisor_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
router = APIRouter(tags=["advisor"])


@router.get(
    "",
    response_model=AdvisorResponse,
    summary="Contribution Advisor recommendations + health summary",
    responses={401: {"description": "Not authenticated"}},
)
async def get_advisor(
    request: Request,
    response: Response,
    _user: AuthenticatedUser = Depends(get_current_user),
) -> AdvisorResponse:
    """
    Read-only, aggregate-only advisory output. Degrades gracefully: any missing
    input yields fewer items + an honest summary; never 5xx on input issues.
    """
    response.headers["Cache-Control"] = "no-store"
    app = request.app
    ensure_advisor_state(app)
    cfg = get_app_config()
    now = _now()
    now_ts = _iso(now)

    # --- gather inputs OUTSIDE the lock (I/O); each degrades to None ---
    sys_now = _gather_system()
    node = await _gather_node()
    conduit = await _gather_conduit()
    traffic = None
    try:
        async with get_db() as db:
            traffic = await _gather_traffic(db, now_ts=now_ts, cfg=cfg)
    except (sqlite3.Error, OSError):
        logger.warning("advisor: database unavailable", exc_info=True)

    base_policy = AdvisorPolicy()

    # --- critical section (await-free): buffer + warm-up + evaluate + persist ---
    async with app.state.advisor_lock:
        buf = app.state.advisor_samples
        _append_sample(
            buf,
            now,
            sys_now,
            throttle_seconds=cfg.advisor_sample_throttle_seconds,
            window_seconds=cfg.advisor_sample_window_seconds,
        )
        sys_avg = _window_average(buf)
        growth_allowed = _growth_allowed(buf, base_policy, cfg)
        policy = replace(base_policy, growth_enabled=growth_allowed)
        inp = AdvisorInput(system=sys_avg, node=node, conduit=conduit, traffic=traffic)
        result = evaluate(inp, now=now, state=app.state.advisor_state, policy=policy)
        app.state.advisor_state = result.state

    return _serialize(result, now)
