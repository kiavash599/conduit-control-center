"""
backend/api/conduit.py
----------------------
Conduit node control endpoints.

Implemented in:
  Issue #17 -- Conduit adapter (systemctl wrapper)
  Issue #19 -- POST /api/conduit/start, stop, restart (this file)
  Issue #20 -- POST /api/conduit/pair (not implemented in this release; 501)

Routes
------
  POST /api/conduit/start    -- start the Conduit service
  POST /api/conduit/stop     -- stop the Conduit service
  POST /api/conduit/restart  -- restart the Conduit service
  POST /api/conduit/pair     -- not implemented in this release (returns 501)

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

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from backend.config import get_app_config
from backend.conduit.adapter import (
    ConduitAdapterError,
    ConduitPermissionError,
    ConduitStatus,
    apply_conduit_config,
    get_conduit_config_view,
    get_status,
    helper_is_safe,
    rollback_conduit_config,
    start,
    stop,
    restart,
    verify_conduit_config_health,
)
from backend.conduit.config_validation import (
    validate_bandwidth_mbps,
    validate_max_common_clients,
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
# Read-only configuration view (M1, §6.1)
# ---------------------------------------------------------------------------
# Reports the two operator-tunable knobs as configured (next-start) vs effective
# (running) values plus a drift flag. Read-only and aggregate-only: no write,
# restart, or privileged operation. Degrades to nulls; never 5xx on read miss.

class ConfigFieldOut(BaseModel):
    configured: int | None = None
    effective: int | None = None
    drift: bool | None = None
    unlimited_configured: bool = False
    unlimited_effective: bool = False


class ConduitConfigResponse(BaseModel):
    service_status: str
    drift: bool | None = None
    max_common_clients: ConfigFieldOut
    bandwidth_mbps: ConfigFieldOut


@router.get(
    "/config",
    response_model=ConduitConfigResponse,
    summary="Read-only Conduit configuration (configured vs effective)",
    responses={401: {"description": "Not authenticated"}},
)
async def get_conduit_config(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> ConduitConfigResponse:
    """Aggregate-only, read-only view. No write/restart/privileged operation."""
    view = await get_conduit_config_view()
    mcc, bw = view.max_common_clients, view.bandwidth_mbps
    return ConduitConfigResponse(
        service_status=view.service_status,
        drift=view.drift,
        max_common_clients=ConfigFieldOut(
            configured=mcc.configured, effective=mcc.effective, drift=mcc.drift,
        ),
        bandwidth_mbps=ConfigFieldOut(
            configured=bw.configured, effective=bw.effective, drift=bw.drift,
            unlimited_configured=bw.unlimited_configured,
            unlimited_effective=bw.unlimited_effective,
        ),
    )


# ---------------------------------------------------------------------------
# Configuration write (M2) -- validate + apply, with restart + rollback.
# ---------------------------------------------------------------------------
# Privilege boundary is the root helper (adapter.apply/rollback). This layer
# validates (independently of the helper), serializes via an apply-lock, does
# optimistic-concurrency + no-op checks, verifies health, and audits every
# outcome. is_live is advisory -- never a rollback gate.

class ConfigWriteRequest(BaseModel):
    max_common_clients: int
    bandwidth_mbps: int


class ExpectedEffective(BaseModel):
    max_common_clients: int | None = None
    bandwidth_mbps: int | None = None


class ConfigApplyRequest(ConfigWriteRequest):
    expected_effective: ExpectedEffective | None = None


def ensure_conduit_apply_lock(app) -> None:
    """Create the per-process apply-lock if absent (single-worker invariant)."""
    if not hasattr(app.state, "conduit_apply_lock"):
        app.state.conduit_apply_lock = asyncio.Lock()


def _validate_payload(mcc: object, bw: object) -> tuple[dict | None, list[dict]]:
    bw_max = getattr(get_app_config(), "conduit_bandwidth_max_mbps", 1000)
    errors: list[dict] = []
    nmcc, e1 = validate_max_common_clients(mcc)
    if e1:
        errors.append({"field": "max_common_clients", "message": e1})
    nbw, e2 = validate_bandwidth_mbps(bw, max_mbps=bw_max)
    if e2:
        errors.append({"field": "bandwidth_mbps", "message": e2})
    if errors:
        return None, errors
    return {"max_common_clients": nmcc, "bandwidth_mbps": nbw}, []


def _effective_dict(view) -> dict:
    bw = view.bandwidth_mbps
    return {
        "max_common_clients": view.max_common_clients.effective,
        "bandwidth_mbps": (-1 if bw.unlimited_effective else bw.effective),
    }


def _configured_dict(view) -> dict:
    bw = view.bandwidth_mbps
    return {
        "max_common_clients": view.max_common_clients.configured,
        "bandwidth_mbps": (-1 if bw.unlimited_configured else bw.configured),
    }


async def _write_config_audit(
    db: aiosqlite.Connection, result: str, username: str, *, old, requested, effective, reason=None
) -> int | None:
    """Audit a config write outcome on the request-scoped DB connection.

    Mirrors _write_action_audit: the connection comes from Depends(get_db) -- it
    is NOT acquired via ``async with get_db()`` (get_db is a dependency async
    generator, not an async context manager). Failures are logged with the
    actual exception type/message and swallowed: an audit-write failure must
    never change the operation's reported status or hide the real reason.
    """
    detail = (
        f"result={result} old={old} requested={requested} "
        f"effective={effective} reason={reason!r}"
    )
    try:
        cur = await db.execute(
            "INSERT INTO audit_log (timestamp, event_type, username, detail) "
            "VALUES (?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "CONDUIT_CONFIG",
                username,
                detail,
            ),
        )
        await db.commit()
        return cur.lastrowid
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to write config audit (result=%r): %s: %s -- continuing",
            result, type(exc).__name__, exc, exc_info=True,
        )
        return None


@router.post(
    "/config/validate",
    summary="Validate proposed Conduit configuration (no write)",
    responses={401: {"description": "Not authenticated"}, 403: {"description": "CSRF"},
               422: {"description": "Validation errors"}},
)
async def validate_config(
    payload: ConfigWriteRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
):
    normalized, errors = _validate_payload(payload.max_common_clients, payload.bandwidth_mbps)
    if errors:
        return JSONResponse(status_code=422, content={"valid": False, "errors": errors})
    view = await get_conduit_config_view()
    changed = _configured_dict(view) != normalized
    return {
        "valid": True,
        "errors": [],
        "normalized": normalized,
        "changed": changed,
        "restart_required": True,
    }


async def _service_healthy() -> bool:
    """Light post-action health check: Conduit active + metrics reachable.

    Used to decide rolled_back vs rollback_failed independent of the helper's
    restart exit code, which can transiently report non-zero on the Pi while
    Conduit recovers (Restart=on-failure).
    """
    view = await get_conduit_config_view()
    return view.service_status == "running" and view.max_common_clients.effective is not None


@router.post(
    "/config/apply",
    summary="Apply Conduit configuration (restart + verify + rollback)",
    responses={401: {"description": "Not authenticated"}, 403: {"description": "CSRF"},
               409: {"description": "Conflict"}, 422: {"description": "Validation errors"},
               500: {"description": "Rollback failed"}, 503: {"description": "Helper unavailable"}},
)
async def apply_config(
    request: Request,
    payload: ConfigApplyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
):
    username = user.user_id
    requested = {"max_common_clients": payload.max_common_clients,
                 "bandwidth_mbps": payload.bandwidth_mbps}

    normalized, errors = _validate_payload(payload.max_common_clients, payload.bandwidth_mbps)
    if errors:
        await _write_config_audit(db, "rejected", username, old=None, requested=requested,
                                  effective=None, reason="validation")
        return JSONResponse(status_code=422, content={"valid": False, "errors": errors})
    nmcc, nbw = normalized["max_common_clients"], normalized["bandwidth_mbps"]

    if not helper_is_safe():
        await _write_config_audit(db, "rejected", username, old=None, requested=normalized,
                                  effective=None, reason="helper_unsafe")
        return JSONResponse(status_code=503,
                            content={"status": "unavailable",
                                     "reason": "config helper missing or unsafe"})

    app = request.app
    ensure_conduit_apply_lock(app)
    lock = app.state.conduit_apply_lock
    if lock.locked():
        aid = await _write_config_audit(db, "conflict", username, old=None, requested=normalized,
                                        effective=None, reason="apply_in_progress")
        return JSONResponse(status_code=409,
                            content={"status": "conflict", "reason": "apply_in_progress",
                                     "audit_id": aid})

    async with lock:
        view = await get_conduit_config_view()
        old = _configured_dict(view)

        if payload.expected_effective is not None:
            cur_eff = _effective_dict(view)
            exp = payload.expected_effective
            if (exp.max_common_clients is not None
                    and exp.max_common_clients != cur_eff["max_common_clients"]) or \
               (exp.bandwidth_mbps is not None
                    and exp.bandwidth_mbps != cur_eff["bandwidth_mbps"]):
                aid = await _write_config_audit(db, "conflict", username, old=old,
                                                requested=normalized, effective=cur_eff,
                                                reason="drift")
                return JSONResponse(status_code=409,
                                    content={"status": "conflict", "reason": "drift",
                                             "audit_id": aid})

        if old == normalized:  # no-op: do not restart
            cur_eff = _effective_dict(view)
            aid = await _write_config_audit(db, "no-op", username, old=old, requested=normalized,
                                            effective=cur_eff)
            return JSONResponse(status_code=200,
                                content={"status": "applied", "effective": cur_eff,
                                         "audit_id": aid})

        # Apply, then use HEALTH VERIFICATION as the source of truth -- NOT the
        # helper's restart exit code, which can transiently report non-zero on
        # the Pi while Conduit recovers. If Conduit is healthy with the requested
        # values, the apply succeeded regardless of rc.
        rc, err = await apply_conduit_config(nmcc, nbw)
        ok, reason = await verify_conduit_config_health(nmcc, nbw)
        if ok:
            eff = _effective_dict(await get_conduit_config_view())
            aid = await _write_config_audit(
                db, "applied", username, old=old, requested=normalized, effective=eff,
                reason=(None if rc == 0 else f"apply_rc={rc}: {err}"))
            return JSONResponse(status_code=200,
                                content={"status": "applied", "effective": eff,
                                         "audit_id": aid})

        # Requested config did not verify -> roll back, then decide rolled_back
        # vs rollback_failed by ACTUAL post-rollback health (not the rollback rc).
        rb_rc, rb_err = await rollback_conduit_config()
        post_ok = await _service_healthy()
        eff = _effective_dict(await get_conduit_config_view())
        if post_ok:
            aid = await _write_config_audit(
                db, "rolled_back", username, old=old, requested=normalized, effective=eff,
                reason=f"verify failed: {reason}; apply_rc={rc}; rollback_rc={rb_rc}")
            return JSONResponse(status_code=200,
                                content={"status": "rolled_back", "reason": reason,
                                         "effective": eff, "audit_id": aid})
        aid = await _write_config_audit(
            db, "rollback_failed", username, old=old, requested=normalized, effective=eff,
            reason=(f"verify failed: {reason}; rollback_rc={rb_rc}: {rb_err}; "
                    "service unhealthy after rollback"))
        return JSONResponse(status_code=500,
                            content={"status": "rollback_failed", "audit_id": aid})


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


@router.post(
    "/pair",
    summary="Pair Conduit node (not implemented in this release)",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        422: {"description": "Invalid pairing link (empty, too long, control chars)"},
        501: {"description": "Pairing is not implemented in this release"},
    },
)
async def conduit_pair(
    body: PairRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    """
    Conduit pairing is not implemented in this release.

    Authentication, CSRF, and request-body validation (PairRequest) are still
    enforced: an unauthenticated request returns 401, a request with a missing
    or invalid CSRF token returns 403, and a malformed body returns 422. A
    well-formed, authenticated, CSRF-valid request returns 501 Not Implemented.
    Full pairing is planned for future Personal Mode work.

    SECURITY CONTRACT: the pairing link in the request body is never read,
    logged, stored, or passed to a subprocess. This handler returns 501 without
    touching ``body.pairing_link``, which goes out of scope when the request
    completes.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Conduit pairing is not available in this release. "
            "Full pairing support is planned for a future release."
        ),
    )
