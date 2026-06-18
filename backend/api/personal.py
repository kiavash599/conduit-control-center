# SPDX-License-Identifier: MIT
"""
backend/api/personal.py
-----------------------
Personal Mode API -- phase C6a (read + non-restart create only).

Endpoints (mounted under /api/conduit):
  GET  /personal/status      -- compartment existence/validity + display name
  POST /personal/compartment -- create a personal compartment (NO restart)
  GET  /personal/token       -- re-display the pairing token (show-token model)

Scope (C6a): NO max-clients, NO active state, NO regenerate/restore, NO restart,
NO systemctl, NO UI. The created compartment is inert because
CCC_MAX_PERSONAL_CLIENTS = 0 until a later phase enables it.

Token-handling rules (do not weaken):
  The pairing token exists ONLY as: helper pipe -> C5 adapter return value ->
  this API response body. It is NEVER logged, NEVER persisted, NEVER placed in
  an exception message or a URL. Token-bearing responses send Cache-Control:
  no-store. The status endpoint never exposes a token or the raw compartment ID.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from backend.api.conduit import (
    _reduced_dict,
    _service_healthy,
    ensure_conduit_apply_lock,
)
from backend.conduit.adapter import (
    apply_conduit_config,
    get_conduit_config_view,
    helper_is_safe,
    rollback_conduit_config,
    verify_conduit_config_health,
)
from backend.conduit.config_validation import validate_max_personal_clients
from backend.conduit.errors import (
    ConduitAdapterError,
    ConduitPermissionError,
    PersonalDivergenceError,
    PersonalValidationError,
)
from backend.conduit.personal import (
    personal_create,
    personal_show_token,
    personal_status,
)
from backend.database import (
    PERSONAL_COMPARTMENT_NAME_KEY,
    get_setting,
    set_setting,
)
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_csrf_token,
)

router = APIRouter(tags=["personal"])

_NAME_MAX = 32


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PersonalStatusResponse(BaseModel):
    compartment_exists: bool
    valid: bool
    backup_exists: bool
    display_name: str | None
    max_personal_clients: int          # effective if available, else configured
    active: bool                       # compartment_exists AND max_personal > 0


class MaxClientsRequest(BaseModel):
    max_personal_clients: int


class CreateCompartmentRequest(BaseModel):
    display_name: str

    @field_validator("display_name")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("display_name must not be blank")
        if len(v) > _NAME_MAX:
            raise ValueError(f"display_name must be at most {_NAME_MAX} characters")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_store(payload: dict) -> JSONResponse:
    """JSON response for token-bearing bodies -- never cached."""
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


def _http_for_personal_error(exc: ConduitAdapterError) -> HTTPException:
    """Map an adapter exception to an HTTP error. Never includes a token/ID;
    the adapter's messages are already generic."""
    if isinstance(exc, PersonalValidationError):
        return HTTPException(status_code=422, detail="Invalid personal compartment input.")
    if isinstance(exc, ConduitPermissionError):
        return HTTPException(
            status_code=503,
            detail="Server is not permitted to manage the personal compartment (check sudoers).",
        )
    if isinstance(exc, PersonalDivergenceError):
        return HTTPException(
            status_code=503,
            detail="Personal pairing token format mismatch; an update is required.",
        )
    return HTTPException(status_code=503, detail="Personal compartment operation failed.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/personal/status",
    response_model=PersonalStatusResponse,
    summary="Personal compartment status (read-only; no token, no ID)",
    responses={401: {"description": "Not authenticated"}},
)
async def get_personal_status(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> PersonalStatusResponse:
    """Read-only: helper status + the stored display name. No lock, no restart."""
    try:
        st = await personal_status()
    except ConduitAdapterError as exc:
        raise _http_for_personal_error(exc) from exc
    name = await get_setting(PERSONAL_COMPARTMENT_NAME_KEY)
    view = await get_conduit_config_view()
    eff = view.max_personal_clients.effective
    mpc = eff if eff is not None else (view.max_personal_clients.configured or 0)
    return PersonalStatusResponse(
        compartment_exists=st.exists,
        valid=st.valid,
        backup_exists=st.backup,
        display_name=name,
        max_personal_clients=mpc,
        active=(st.exists and mpc > 0),
    )


@router.post(
    "/personal/compartment",
    summary="Create a personal compartment (no restart; inert until enabled)",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        409: {"description": "A personal compartment already exists"},
    },
)
async def create_personal_compartment(
    body: CreateCompartmentRequest,
    request: Request,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
) -> JSONResponse:
    """Create-only (non-destructive): 409 if a compartment already exists. Holds
    the shared apply-lock for the write; performs NO restart and NO systemctl --
    the compartment is inert while CCC_MAX_PERSONAL_CLIENTS = 0."""
    ensure_conduit_apply_lock(request.app)
    async with request.app.state.conduit_apply_lock:
        try:
            st = await personal_status()
        except ConduitAdapterError as exc:
            raise _http_for_personal_error(exc) from exc
        if st.exists:
            raise HTTPException(status_code=409, detail="A personal compartment already exists.")

        # Persist only the non-secret display name (C1).
        await set_setting(PERSONAL_COMPARTMENT_NAME_KEY, body.display_name)
        try:
            token = await personal_create(body.display_name)
        except ConduitAdapterError as exc:
            raise _http_for_personal_error(exc) from exc

    return _no_store({"token": token})


@router.get(
    "/personal/token",
    summary="Re-display the pairing token (show-token; no restart, no caching)",
    responses={
        401: {"description": "Not authenticated"},
        404: {"description": "No personal compartment configured"},
    },
)
async def get_personal_token(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> JSONResponse:
    """Rebuild the token from the persisted display name + the on-disk ID (via
    the helper). Read-only: no lock, no restart, no persistence, no caching."""
    name = await get_setting(PERSONAL_COMPARTMENT_NAME_KEY)
    if not name:
        raise HTTPException(status_code=404, detail="No personal compartment configured.")
    try:
        token = await personal_show_token(name)
    except ConduitAdapterError as exc:
        raise _http_for_personal_error(exc) from exc
    return _no_store({"token": token})


@router.put(
    "/personal/max-clients",
    summary="Enable/disable/adjust the personal-client limit (restart via M2)",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        409: {"description": "No valid compartment (when enabling) / apply in progress"},
        422: {"description": "max_personal_clients out of range"},
    },
)
async def set_personal_max_clients(
    body: MaxClientsRequest,
    request: Request,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
) -> JSONResponse:
    """Set CCC_MAX_PERSONAL_CLIENTS via the M2 apply path (restart + health +
    rollback). Full-set merge: preserves max_common/bandwidth/reduced; changes
    ONLY the personal knob. No token, no compartment ID. 0 disables; N>0
    enables/adjusts. A value equal to the current configured one is a no-op (no
    restart). Reuses the shared apply-lock; introduces no new restart mechanism."""
    n, err = validate_max_personal_clients(body.max_personal_clients)
    if err:
        raise HTTPException(status_code=422, detail=err)

    # Ordering guard: never write max>0 without a valid compartment (Conduit
    # would refuse to start). Checked BEFORE acquiring the apply-lock.
    if n > 0:
        try:
            st = await personal_status()
        except ConduitAdapterError as exc:
            raise _http_for_personal_error(exc) from exc
        if not (st.exists and st.valid):
            raise HTTPException(
                status_code=409,
                detail="Create a valid personal compartment before enabling Personal Mode.",
            )

    if not helper_is_safe():
        raise HTTPException(status_code=503, detail="Config helper missing or unsafe.")

    app = request.app
    ensure_conduit_apply_lock(app)
    lock = app.state.conduit_apply_lock
    if lock.locked():
        raise HTTPException(status_code=409, detail="A configuration apply is already in progress.")

    async with lock:
        view = await get_conduit_config_view()
        mcc = view.max_common_clients.configured
        bw = -1 if view.bandwidth_mbps.unlimited_configured else view.bandwidth_mbps.configured
        if mcc is None or bw is None:
            raise HTTPException(status_code=503, detail="Cannot read current Conduit configuration.")

        current = view.max_personal_clients.configured or 0
        if current == n:
            # No change -> no restart.
            return JSONResponse(
                status_code=200,
                content={"status": "no-op", "active": n > 0, "max_personal_clients": n},
            )

        # Full-set merge: preserve mcc/bw/reduced; change only the personal knob.
        red = _reduced_dict(view)
        await apply_conduit_config(
            mcc, bw,
            max_personal_clients=n,
            reduced_start_min=red["start_min"],
            reduced_end_min=red["end_min"],
            reduced_max_common=red["reduced_max_common_clients"],
            reduced_bandwidth_mbps=red["reduced_bandwidth_mbps"],
        )
        ok, _reason = await verify_conduit_config_health(mcc, bw)

        # C6b hardening: confirm the personal limit actually took effect. When the
        # conduit_max_personal_clients metric is PRESENT it must equal the request;
        # ABSENCE is not a failure -- fall back to the mcc/bw-only result (the same
        # health-as-truth posture used for reduced-mode, which has no metric).
        eff = (await get_conduit_config_view()).max_personal_clients.effective
        personal_ok = (eff is None) or (eff == n)

        if ok and personal_ok:
            reported = eff if eff is not None else n
            return JSONResponse(
                status_code=200,
                content={"status": "applied", "active": (reported or 0) > 0,
                         "max_personal_clients": reported},
            )

        # mcc/bw health OR personal-effective verification failed -> roll back;
        # decide rolled_back vs rollback_failed by post-rollback service health.
        await rollback_conduit_config()
        post_ok = await _service_healthy()
        eff = (await get_conduit_config_view()).max_personal_clients.effective
        reported = eff if eff is not None else current
        out = {"status": "rolled_back" if post_ok else "rollback_failed",
               "active": (reported or 0) > 0, "max_personal_clients": reported}
        return JSONResponse(status_code=(200 if post_ok else 503), content=out)
