# SPDX-License-Identifier: MIT
"""
backend/api/traffic.py
----------------------
Read-only Traffic Read API for the dashboard "Lifetime & history" surface.

Endpoints (registered under /api/traffic in main.py):
  GET /api/traffic/summary           -> status, recording_since, lifetime, windows
  GET /api/traffic/series?range=...  -> dense time buckets for the trend chart

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

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.database import get_db
from backend.dependencies import AuthenticatedUser, get_current_user
from backend.traffic import reads

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
    recording_since: Optional[str] = None
    last_ok_ts_utc: Optional[str] = None
    lifetime: Optional[BytesPair] = None
    windows: TrafficWindows


class SeriesBucket(BaseModel):
    bucket_utc: str
    bytes_up: int
    bytes_down: int


class TrafficSeries(BaseModel):
    range: str
    granularity: str
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
    return TrafficSummary(**data)


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
    async with get_db() as db:
        data = await reads.get_series(db, range_key=range_.value, now_ts=_now_utc())
    return TrafficSeries(**data)
