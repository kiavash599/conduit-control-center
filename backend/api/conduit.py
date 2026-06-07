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
HTTP 403  -- CSRF token missing or invalid (require_csrf_token dependency)
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
from pydantic import BaseModel, Field, field_validator

from backend.conduit.adapter import (
    ConduitAdapterError,
    ConduitPermissionError,
    ConduitStatus,
    get_status,
    restart,
    start,
    stop,
    pair as adapter_pair,
)
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    get_db,
    require_csrf_token,
)

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
        403: {"description": "CSRF token missing or invalid"},
        409: {"description": "Service already running or transitioning"},
        503: {"description": "Conduit service unavailable or sudoers not configured"},
    },
)
async def conduit_start(
    user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
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
        403: {"description": "CSRF token missing or invalid"},
        409: {"description": "Service already stopped or transitioning"},
        503: {"description": "Conduit service unavailable or sudoers not configured"},
    },
)
async def conduit_stop(
    user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
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
        403: {"description": "CSRF token missing or invalid"},
        409: {"description": "Service is currently starting or stopping"},
        503: {"description": "Conduit service unavailable or sudoers not configured"},
    },
)
async def conduit_restart(
    user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
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
# Route: pair (Issue #20)
# ---------------------------------------------------------------------------

# SECURITY NOTE: the pairing link is the most sensitive credential in the system.
# The design constraints for this endpoint are:
#   - The link is NEVER logged at any layer.
#   - The link is NEVER stored in the database, .env, or any file.
#   - The link is NEVER included in exception messages or response bodies.
#   - The link is NEVER passed as a command-line argument (process list exposure).
#   - The link reaches the Conduit CLI via stdin ONLY.
#   - All response strings are static; none are derived from the link value.


class PairRequest(BaseModel):
    """
    Body for POST /api/conduit/pair.

    repr=False prevents the pairing_link value from appearing in repr(body),
    which protects it from accidental exposure in exception tracebacks that
    are caught and logged by logger.exception().

    TODO: Add a format-specific validator once the Psiphon pairing link
    format is confirmed against Psiphon documentation.  The current
    validator enforces only structural safety constraints.
    """

    pairing_link: str = Field(
        min_length=1,
        max_length=4096,
        repr=False,
        description=(
            "Psiphon Conduit pairing link. "
            "Never stored, never logged, never passed as argv."
        ),
    )

    @field_validator("pairing_link")
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        """
        Reject strings containing C0 control characters (ord < 32).

        Rationale: control characters (null bytes, newlines, carriage
        returns, tabs, etc.) could corrupt a stdin-based protocol or
        interfere with CLI argument parsing.  The pairing link is expected
        to consist of printable characters only.

        TODO: Replace with a format-specific regex once the Psiphon
        pairing link format is confirmed.
        """
        if any(ord(c) < 32 for c in v):
            raise ValueError(
                "Pairing link must not contain control characters "
                "(null bytes, newlines, tabs, etc.)."
            )
        return v


class PairResponse(BaseModel):
    """Response body for POST /api/conduit/pair."""

    status: str   # "paired" | "failed"
    message: str  # static operator-facing string; never link-derived


@router.post(
    "/pair",
    summary="Pair Conduit node (pairing link never stored)",
    response_model=PairResponse,
    responses={
        200: {"description": "Pairing attempt completed; check 'status' field"},
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        422: {"description": "Invalid pairing link (empty, too long, control chars)"},
        503: {"description": "Conduit binary not found or unexpected adapter error"},
    },
)
async def conduit_pair(
    body: PairRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
    # NOTE: no db dependency -- this endpoint makes no database writes by design.
    # See Issue #20 design decision: pairing is a transient in-memory operation.
    # TODO (future): add CONDUIT_PAIR audit entry once CLI interface is confirmed.
    #   Event: CONDUIT_PAIR, detail: user={username}, result={paired|failed}
    #   Before adding: confirm link is fully out of scope at the write site.
) -> PairResponse:
    """
    Submit a Psiphon Conduit pairing link.

    The link is extracted from the request body, passed to the Conduit
    CLI via stdin, and then goes out of scope.  It is never written to
    any persistent storage, log, or response field.

    Returns 200 with status="paired" on success, or status="failed" on
    CLI failure.  Returns 503 if the Conduit binary is not found.

    Authentication is enforced via get_current_user (returns 401 if no
    valid session cookie is present).
    """
    # Extract the link to a local variable; let the Pydantic body go out
    # of scope as soon as possible.
    pairing_link: str = body.pairing_link

    try:
        result = await adapter_pair(pairing_link)
    except ConduitAdapterError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    finally:
        # Explicit deletion signals intent.  The local variable is the only
        # reference at this point; body.pairing_link also holds one, but
        # body goes out of scope immediately after this function returns.
        del pairing_link

    return PairResponse(status=result["status"], message=result["message"])
