"""
backend/api/conduit.py
----------------------
Conduit node control endpoints.

Implemented in:
  Issue #17 -- Conduit adapter (systemctl wrapper)
  Issue #19 -- POST /api/conduit/start, stop, restart (this file)
  Issue #20 -- POST /api/conduit/pair (transient pairing, no storage)

Routes
------
  POST /api/conduit/start    -- start the Conduit service
  POST /api/conduit/stop     -- stop the Conduit service
  POST /api/conduit/restart  -- restart the Conduit service
  POST /api/conduit/pair     -- pair Conduit node (Issue #20, still 501)

Pre-condition rules
-------------------
All three action endpoints check current service state before acting.
The check is a best-effort UX guard, not a hard mutex -- a small TOCTOU
window exists between the status read and the systemctl call, but this
is acceptable on a single-admin dashboard.

409 Conflict is returned when:
  - start is called and the service is already "running" or "starting"
  - stop  is called and the service is already "stopped" or "stopping"
  - any action is called while the service is "starting" or "stopping"

All other state/action combinations proceed:
  - restart is allowed from "running", "stopped", and "error" states
    (mirrors native systemctl restart behaviour -- starts the service
    if it is not already running)
  - start and stop are allowed from "error" state

Audit log
---------
Each action writes one CONDUIT_START / CONDUIT_STOP / CONDUIT_RESTART
entry to the audit_log table.  The detail field records the username,
pre-action status, and the final result.

Audit write failures are logged at ERROR and swallowed -- a DB hiccup
must never abort or misreport a service control action that already ran.

Error responses
---------------
HTTP 401  -- no valid session (get_current_user dependency)
HTTP 409  -- pre-condition not satisfied (see rules above)
HTTP 503  -- ConduitPermissionError (sudoers rule missing / misconfigured)
             or ConduitAdapterError (service not found, systemctl failure)
HTTP 500  -- unexpected error (caught by global handler in main.py)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.conduit.adapter import (
    ConduitAdapterError,
    ConduitPermissionError,
    ConduitStatus,
    get_status,
    restart,
    start,
    stop,
)
from backend.dependencies import AuthenticatedUser, get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conduit"])


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ActionResponse(BaseModel):
    """
    Response body for start / stop / restart endpoints.

    Fields
    ------
    action     : the requested action ("start" | "stop" | "restart")
    success    : True if the service reached the desired state within timeout
    new_status : final observed ConduitStatus after the action
    message    : operator-friendly description from the adapter
    """

    action: str
    success: bool
    new_status: str
    message: str


# ---------------------------------------------------------------------------
# 409 helper
# ---------------------------------------------------------------------------

# States from which each action must not proceed.
# "starting" and "stopping" are always blocked for all actions (mid-transition).
_BLOCKED: dict[str, set[ConduitStatus]] = {
    "start":   {"running", "starting", "stopping"},
    "stop":    {"stopped", "starting", "stopping"},
    "restart": {"starting", "stopping"},
}

_CONFLICT_MESSAGES: dict[str, dict[ConduitStatus, str]] = {
    "start": {
        "running":  "Conduit is already running.",
        "starting": "Conduit is already starting. Wait for it to finish.",
        "stopping": "Conduit is stopping. Wait for it to finish before starting.",
    },
    "stop": {
        "stopped":  "Conduit is already stopped.",
        "starting": "Conduit is starting. Wait for it to finish before stopping.",
        "stopping": "Conduit is already stopping.",
    },
    "restart": {
        "starting": "Conduit is starting. Wait for it to finish before restarting.",
        "stopping": "Conduit is stopping. Wait for it to finish before restarting.",
    },
}


def _check_precondition(action: str, current_status: ConduitStatus) -> None:
    """
    Raise HTTP 409 if the current status makes this action a no-op or unsafe.

    Parameters
    ----------
    action         : "start" | "stop" | "restart"
    current_status : ConduitStatus from get_status()

    Raises
    ------
    HTTPException(409)  if the action is blocked for this state
    """
    if current_status in _BLOCKED.get(action, set()):
        message = _CONFLICT_MESSAGES.get(action, {}).get(
            current_status,
            f"Cannot {action} while service is in '{current_status}' state.",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        )


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _write_action_audit(
    db: aiosqlite.Connection,
    event_type: str,
    username: str,
    pre_status: ConduitStatus,
    result: dict,
) -> None:
    """
    Write a CONDUIT_START / CONDUIT_STOP / CONDUIT_RESTART audit entry.

    Failures are logged at ERROR and silently suppressed.  A failed audit
    write must never abort or misreport a service action that already ran.
    """
    detail = (
        f"user={username!r}, "
        f"pre_status={pre_status!r}, "
        f"success={result['success']}, "
        f"new_status={result['status']!r}"
    )
    try:
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, event_type, username, detail)
            VALUES (?, ?, ?, ?)
            """,
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), event_type, username, detail),
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.error(
            "Failed to write audit log (event=%r, username=%r) -- continuing",
            event_type,
            username,
        )


# ---------------------------------------------------------------------------
# Shared action handler
# ---------------------------------------------------------------------------


async def _handle_action(
    action: str,
    event_type: str,
    adapter_fn,
    user: AuthenticatedUser,
    db: aiosqlite.Connection,
) -> ActionResponse:
    """
    Shared logic for start / stop / restart:
      1. Get current status (pre-condition check)
      2. Check pre-condition (raise 409 if blocked)
      3. Call adapter function
      4. Write audit entry
      5. Return ActionResponse

    Parameters
    ----------
    action      : "start" | "stop" | "restart"
    event_type  : audit log event type string
    adapter_fn  : adapter coroutine (start, stop, or restart)
    user        : authenticated user from get_current_user dependency
    db          : database connection from get_db dependency
    """
    # 1. Get current status for pre-condition check and audit.
    try:
        pre_status = await get_status()
    except ConduitPermissionError as exc:
        logger.error("%s: get_status permission error: %s", action, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except ConduitAdapterError as exc:
        logger.error("%s: get_status adapter error: %s", action, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    # 2. Pre-condition check -- raises 409 if blocked.
    _check_precondition(action, pre_status)

    # 3. Call the adapter.
    try:
        result = await adapter_fn()
    except ConduitPermissionError as exc:
        logger.error("%s: permission error: %s", action, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except ConduitAdapterError as exc:
        logger.error("%s: adapter error: %s", action, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    # 4. Audit log (non-fatal on failure).
    await _write_action_audit(db, event_type, user.user_id, pre_status, result)

    # 5. Return structured response.
    return ActionResponse(
        action=action,
        success=result["success"],
        new_status=result["status"],
        message=result["message"],
    )


# ---------------------------------------------------------------------------
# Routes: start / stop / restart
# ---------------------------------------------------------------------------


@router.post(
    "/start",
    summary="Start the Conduit service",
    response_model=ActionResponse,
    responses={
        200: {"description": "Action completed; check 'success' field for result"},
        401: {"description": "Not authenticated"},
        409: {"description": "Service already running or transitioning"},
        503: {"description": "Conduit service unavailable or sudoers not configured"},
    },
)
async def conduit_start(
    user: AuthenticatedUser = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> ActionResponse:
    """
    Start the Conduit service.

    Returns 409 if the service is already running or currently starting.
    Returns 200 with success=False if start was issued but the service
    did not reach "running" within conduit_action_timeout_seconds.
    """
    return await _handle_action("start", "CONDUIT_START", start, user, db)


@router.post(
    "/stop",
    summary="Stop the Conduit service",
    response_model=ActionResponse,
    responses={
        200: {"description": "Action completed; check 'success' field for result"},
        401: {"description": "Not authenticated"},
        409: {"description": "Service already stopped or transitioning"},
        503: {"description": "Conduit service unavailable or sudoers not configured"},
    },
)
async def conduit_stop(
    user: AuthenticatedUser = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> ActionResponse:
    """
    Stop the Conduit service.

    Returns 409 if the service is already stopped or currently stopping.
    Returns 200 with success=False if stop was issued but the service
    did not reach "stopped" within conduit_action_timeout_seconds.
    """
    return await _handle_action("stop", "CONDUIT_STOP", stop, user, db)


@router.post(
    "/restart",
    summary="Restart the Conduit service",
    response_model=ActionResponse,
    responses={
        200: {"description": "Action completed; check 'success' field for result"},
        401: {"description": "Not authenticated"},
        409: {"description": "Service is currently starting or stopping"},
        503: {"description": "Conduit service unavailable or sudoers not configured"},
    },
)
async def conduit_restart(
    user: AuthenticatedUser = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> ActionResponse:
    """
    Restart the Conduit service.

    Allowed from "running", "stopped", and "error" states -- mirrors
    native systemctl restart behaviour (starts the service if stopped).
    Returns 409 only if the service is currently mid-transition
    (starting or stopping).
    """
    return await _handle_action("restart", "CONDUIT_RESTART", restart, user, db)


# ---------------------------------------------------------------------------
# Route: pair (Issue #20 stub)
# ---------------------------------------------------------------------------

_NOT_IMPLEMENTED_20 = JSONResponse(
    status_code=501,
    content={"detail": "Not implemented. Tracked in Issue #20."},
)


@router.post("/pair", summary="Pair Conduit node (pairing link never stored)")
async def pair(_user: AuthenticatedUser = Depends(get_current_user)):
    return _NOT_IMPLEMENTED_20
