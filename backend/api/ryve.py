# SPDX-License-Identifier: MIT
"""
backend/api/ryve.py
-------------------
Ryve claim API (Epic #3, R2b). Three endpoints over a single-slot, process-local
RAM store that bridges a CSRF-protected POST (generate) to an <img>-loadable GET
(retrieve), so the key-grade QR is served as opaque binary from a same-origin URL
(no CSP change, no base64/data:/blob: in the DOM).

  * POST   /ryve/claim                  (auth + CSRF) -> {claim_id, station_name, proxy_id}
  * GET    /ryve/claim/image/{claim_id} (auth)        -> image/png (no-store)
  * DELETE /ryve/claim/{claim_id}       (auth + CSRF) -> 204

Store invariants: at most one live claim; a new POST invalidates the previous;
~120 s TTL; PNG bytes held in a bytearray and zeroed in place on eviction.
Single-worker uvicorn -> process-local state is correct.

Leakage rules: the PNG bytes and the claim_id are NEVER logged; responses are
always no-store; the PNG never appears in JSON or as base64/data URI.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from backend.conduit.errors import ConduitAdapterError, ConduitPermissionError
from backend.conduit.ryve import generate_ryve_claim
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_csrf_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ryve"])

TTL_SECONDS = 120.0

_IMAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Expires": "0",
    "Content-Disposition": "inline",
    "X-Content-Type-Options": "nosniff",
}


# ---------------------------------------------------------------------------
# Single-slot RAM store
# ---------------------------------------------------------------------------

class RyveClaimStore:
    """Holds at most one Ryve claim PNG in RAM. A new put() invalidates the
    previous claim; entries expire after TTL_SECONDS; PNG bytes are zeroed in
    place on eviction (best-effort -- removes the primary heap copy)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._claim_id: str | None = None
        self._png: bytearray | None = None
        self._expires_at: float = 0.0

    def _evict_locked(self) -> None:
        if self._png is not None:
            self._png[:] = b"\x00" * len(self._png)   # zero in place
            self._png = None
        self._claim_id = None
        self._expires_at = 0.0

    async def put(self, png_bytes: bytes) -> str:
        async with self._lock:
            self._evict_locked()                       # invalidate previous
            self._png = bytearray(png_bytes)
            self._claim_id = secrets.token_urlsafe(32)
            self._expires_at = time.monotonic() + TTL_SECONDS
            return self._claim_id

    async def get_png(self, claim_id: str) -> bytes | None:
        async with self._lock:
            if self._claim_id is None or claim_id != self._claim_id:
                return None
            if time.monotonic() >= self._expires_at:
                self._evict_locked()
                return None
            return bytes(self._png)                     # copy for the response

    async def delete(self, claim_id: str) -> None:
        async with self._lock:
            if self._claim_id is not None and claim_id == self._claim_id:
                self._evict_locked()

    async def clear(self) -> None:
        async with self._lock:
            self._evict_locked()


def ensure_ryve_store(app) -> None:
    """Create the per-process Ryve claim store if absent (single-worker)."""
    if not hasattr(app.state, "ryve_claim_store"):
        app.state.ryve_claim_store = RyveClaimStore()


def _http_for_ryve_error(exc: ConduitAdapterError) -> HTTPException:
    """Map an adapter error to HTTP. Never includes helper output or key/QR."""
    if isinstance(exc, ConduitPermissionError):
        return HTTPException(
            status_code=503,
            detail="Server is not permitted to run the Ryve claim helper (check sudoers).",
        )
    return HTTPException(status_code=503, detail="Ryve claim is unavailable on this server.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/ryve/claim",
    summary="Generate a Ryve claim QR (key-grade; RAM-only, no-store)",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        503: {"description": "Ryve claim helper unavailable"},
    },
)
async def create_ryve_claim(
    request: Request,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
) -> JSONResponse:
    """Run the helper, store the PNG in RAM under a fresh claim_id (invalidating
    any prior claim), and return only the non-secret handle + metadata."""
    ensure_ryve_store(request.app)
    try:
        claim = await generate_ryve_claim()
    except ConduitAdapterError as exc:
        raise _http_for_ryve_error(exc) from exc
    claim_id = await request.app.state.ryve_claim_store.put(claim.png)
    return JSONResponse(
        content={
            "claim_id": claim_id,
            "station_name": claim.station_name,
            "proxy_id": claim.proxy_id,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/ryve/claim/image/{claim_id}",
    summary="Stream the Ryve claim QR PNG (no-store; live claim only)",
    responses={
        401: {"description": "Not authenticated"},
        404: {"description": "Claim not found or expired"},
    },
)
async def get_ryve_claim_image(
    claim_id: str,
    request: Request,
    _user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    ensure_ryve_store(request.app)
    png = await request.app.state.ryve_claim_store.get_png(claim_id)
    if png is None:
        raise HTTPException(status_code=404, detail="Ryve claim not found or expired.")
    return Response(content=png, media_type="image/png", headers=_IMAGE_HEADERS)


@router.delete(
    "/ryve/claim/{claim_id}",
    status_code=204,
    summary="Discard the in-RAM Ryve claim (idempotent)",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
    },
)
async def delete_ryve_claim(
    claim_id: str,
    request: Request,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
) -> Response:
    ensure_ryve_store(request.app)
    await request.app.state.ryve_claim_store.delete(claim_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
