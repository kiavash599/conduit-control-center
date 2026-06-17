"""
backend/api/status.py
---------------------
GET /api/status -- Conduit node status endpoint.

Implemented in:
  Issue #17 -- Conduit adapter (systemctl wrapper)
  Issue #18 -- This file

Response schema
---------------
{
    "node_status":      "running" | "stopped" | "starting" | "stopping" | "error",
    "last_changed":     "2026-05-31T14:30:00Z" | null,
    "conduit_version":  "1.2.3" | null,
    "uptime_seconds":   3600.0  | null
}

node_status    -- current systemd service state; always present
last_changed   -- ISO 8601 UTC timestamp of the last active-enter event;
                  null if the service has never started or the timestamp
                  is not available
conduit_version -- detected Conduit version string; null if not determinable
                  (see adapter.get_version() for detection strategy and
                  validation requirements)
uptime_seconds -- seconds since last_changed when node_status is "running";
                  null otherwise or when last_changed is unavailable

Error responses
---------------
HTTP 401  -- no valid session (enforced by get_current_user dependency)
HTTP 503  -- Conduit service not found, or systemctl permission denied
             (get_status() raised ConduitAdapterError / ConduitPermissionError)

Notes
-----
- get_last_changed() and get_version() failures are non-fatal: the endpoint
  returns node_status with those fields set to null rather than failing.
- Both secondary calls run concurrently (asyncio.gather) to minimise latency
  on a Pi where subprocess calls take measurable time.
- Response caching (using metrics_cache_ttl_seconds from config.json) is a
  documented future improvement. Not implemented in v0.1.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.conduit.adapter import (
    ConduitAdapterError,
    ConduitPermissionError,
    broker_state,
    get_last_changed,
    get_live_status,
    get_status,
    get_version,
)
from backend.dependencies import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["status"])


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class LiveOut(BaseModel):
    """
    Live broker/activity sub-block (Live Operations, Option 1).

    Read-only and aggregate-only. ``broker_state`` is ALWAYS present and
    degrades to "unknown" (running but metrics unreadable) or "not_running";
    the other fields are null on a metrics miss. Deliberately does NOT duplicate
    connected_clients / bytes / uptime shown by the Advisor, Traffic, and
    Node Status cards. (``conduit_uptime_seconds`` is intentionally deferred —
    Node Status shows service uptime only, to avoid two uptime figures.)
    """

    broker_state: str  # not_running | starting | live | disconnected | unknown
    connecting_clients: int | None = None
    idle_seconds: int | None = None
    build_rev: str | None = None


class StatusResponse(BaseModel):
    """
    Response body for GET /api/status.

    All fields except node_status and live.broker_state may be null when the
    corresponding data is not available (service never started, version not
    detectable, metrics endpoint unreachable, etc.).
    """

    node_status: str
    last_changed: str | None
    conduit_version: str | None
    uptime_seconds: float | None
    live: LiveOut


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _compute_uptime(last_changed: str | None, node_status: str) -> float | None:
    """
    Return seconds since last_changed when the service is running.

    Returns None when:
    - node_status is not "running"
    - last_changed is None
    - last_changed cannot be parsed as ISO 8601

    Parameters
    ----------
    last_changed : str | None
        ISO 8601 UTC string produced by adapter.get_last_changed()
        (e.g. "2026-05-31T14:30:00Z")
    node_status : str
        Current ConduitStatus value.
    """
    if node_status != "running" or last_changed is None:
        return None
    try:
        dt = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
        delta = (datetime.now(timezone.utc) - dt).total_seconds()
        return round(max(0.0, delta), 1)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="Get Conduit node status",
    response_model=StatusResponse,
    responses={
        200: {"description": "Status returned successfully"},
        401: {"description": "Not authenticated"},
        503: {"description": "Conduit service unavailable or not configured"},
    },
)
async def get_conduit_status(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> StatusResponse:
    """
    Return the current Conduit node status.

    Calls get_status() first; on failure returns HTTP 503 immediately.
    Then calls get_last_changed() and get_version() concurrently; their
    failures are non-fatal and result in null fields rather than errors.

    Authentication is enforced via get_current_user (returns 401 if no
    valid session cookie is present).
    """
    # Primary call: get_status() failure is fatal for this endpoint.
    try:
        node_status = await get_status()
    except ConduitPermissionError as exc:
        logger.error("GET /api/status: permission error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except ConduitAdapterError as exc:
        logger.error("GET /api/status: adapter error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    # Secondary calls: ALL non-fatal. A failure of any of these (including the
    # live-status metrics scrape) must never change the HTTP status code nor
    # prevent node_status / conduit_version / uptime_seconds from being returned.
    results = await asyncio.gather(
        get_last_changed(),
        get_version(),
        get_live_status(),
        return_exceptions=True,
    )

    last_changed: str | None = None
    if isinstance(results[0], BaseException):
        logger.warning(
            "GET /api/status: get_last_changed() failed: %s -- "
            "last_changed will be null",
            results[0],
        )
    else:
        last_changed = results[0]

    conduit_version: str | None = None
    if isinstance(results[1], BaseException):
        logger.warning(
            "GET /api/status: get_version() failed: %s -- "
            "conduit_version will be null",
            results[1],
        )
    else:
        conduit_version = results[1]

    # Live broker/activity gauges. get_live_status() already returns None on an
    # unreachable endpoint; return_exceptions=True guards any unexpected error.
    # Either way, broker_state degrades (unknown/not_running) and the other live
    # fields go null -- the fields above are unaffected.
    live_status = None
    if isinstance(results[2], BaseException):
        logger.warning(
            "GET /api/status: get_live_status() failed: %s -- live fields degrade",
            results[2],
        )
    else:
        live_status = results[2]  # LiveStatus | None

    live = LiveOut(
        broker_state=broker_state(
            node_status,
            live_status.announcing if live_status else None,
            live_status.is_live if live_status else None,
        ),
        connecting_clients=live_status.connecting_clients if live_status else None,
        idle_seconds=live_status.idle_seconds if live_status else None,
        build_rev=live_status.build_rev if live_status else None,
    )

    return StatusResponse(
        node_status=node_status,
        last_changed=last_changed,
        conduit_version=conduit_version,
        uptime_seconds=_compute_uptime(last_changed, node_status),
        live=live,
    )
