# SPDX-License-Identifier: MIT
"""
backend/api/traffic.py
----------------------
Read-only Traffic Read API for the dashboard "Lifetime & history" surface.

Endpoints (registered under /api/traffic in main.py):
  GET  /api/traffic/summary           -> status, enabled, recording_since, lifetime,
                                         windows
  GET  /api/traffic/series?range=...  -> dense time buckets for the trend chart
  POST /api/traffic/recording         -> opt-in toggle: persist enabled state and
                                         start/stop the collector live (auth + CSRF)

All endpoints:
  - require an authenticated session (get_current_user -> 401);
  - are read-only and aggregate-only (no per-user / holder_id / last_error);
  - return HTTP 200 with empty/zero data when the collector is disabled or has
    not recorded yet (the dashboard renders a "not recording" state);
  - reject an unknown range with HTTP 422 (enum-validated query parameter).

Thin layer: all data access lives in backend/traffic/reads.py. No caching
(deferred per the approved plan — direct read-only SQLite access; observe
performance first).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from backend.database import get_db
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_csrf_token,
)
from backend.display_prefs import effective_timezone, effective_timezone_name
from backend.traffic import reads
from backend.traffic.prefs import effective_collector_enabled, set_collector_enabled

router = APIRouter(tags=["traffic"])


# ---------------------------------------------------------------------------
# Query / response models
# ---------------------------------------------------------------------------
class TrafficRange(str, Enum):
    h24 = "24h"
    d7 = "7d"
    d30 = "30d"


class BytesPair(BaseModel):
    bytes_up: int
    bytes_down: int


class TrafficWindows(BaseModel):
    last_24h: BytesPair
    last_7d: BytesPair


class TrafficSummary(BaseModel):
    status: str
    enabled: bool
    recording_since: Optional[str] = None
    last_ok_ts_utc: Optional[str] = None
    lifetime: Optional[BytesPair] = None
    windows: TrafficWindows


class RecordingToggle(BaseModel):
    """Body for POST /api/traffic/recording -- the operator's opt-in choice."""

    enabled: bool


class RecordingState(BaseModel):
    """Effective recording state after a toggle (and its persisted status)."""

    enabled: bool
    status: str


class SeriesBucket(BaseModel):
    bucket_utc: str
    bytes_up: int
    bytes_down: int
    # Set only when a local-day boundary bisected a UTC hour whose deltas had
    # already aged out, so the hour was attributed to the day it starts in.
    approximate: bool = False


class TrafficSeries(BaseModel):
    range: str
    granularity: str
    timezone: str
    buckets: list[SeriesBucket]


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get(
    "/summary",
    response_model=TrafficSummary,
    summary="Persistent traffic summary (lifetime + recent windows)",
    responses={401: {"description": "Not authenticated"}},
)
async def traffic_summary(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> TrafficSummary:
    async with get_db() as db:
        data = await reads.get_summary(db, now_ts=_now_utc())
    # Effective opt-in state (app_settings override, else config default) so the
    # dashboard can distinguish "recording is off" (show a turn-on CTA) from
    # "recording on, no data yet".
    data["enabled"] = await effective_collector_enabled()
    return TrafficSummary(**data)


@router.post(
    "/recording",
    response_model=RecordingState,
    summary="Enable or disable persistent traffic recording (opt-in)",
    responses={401: {"description": "Not authenticated"}},
)
async def set_recording(
    body: RecordingToggle,
    request: Request,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
) -> RecordingState:
    """Persist the operator's opt-in choice and apply it live (no restart).

    Writes the non-secret ``app_settings`` override, then reconciles the running
    collector to match. Idempotent: re-sending the same value is a safe no-op.
    """
    # Deferred import avoids a circular import (backend.main imports this router).
    from backend import main as app_main

    await set_collector_enabled(body.enabled)
    await app_main.reconcile_traffic_collector(request.app)
    enabled = await effective_collector_enabled()
    return RecordingState(enabled=enabled, status="running" if enabled else "disabled")


@router.get(
    "/series",
    response_model=TrafficSeries,
    summary="Persistent traffic time series (hourly/daily buckets)",
    responses={
        401: {"description": "Not authenticated"},
        422: {"description": "Invalid range"},
    },
)
async def traffic_series(
    range_: TrafficRange = Query(default=TrafficRange.h24, alias="range"),
    _user: AuthenticatedUser = Depends(get_current_user),
) -> TrafficSeries:
    # Daily ranges are re-aggregated into LOCAL calendar days when a display
    # timezone is set (storage stays UTC); hourly buckets are instants.
    tz = await effective_timezone()
    async with get_db() as db:
        data = await reads.get_series(
            db, range_key=range_.value, now_ts=_now_utc(), tz=tz
        )
    data["timezone"] = await effective_timezone_name()
    return TrafficSeries(**data)
