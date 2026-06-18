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

from backend.api.conduit import ensure_conduit_apply_lock
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
    return PersonalStatusResponse(
        compartment_exists=st.exists,
        valid=st.valid,
        backup_exists=st.backup,
        display_name=name,
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
