"""
backend/api/conduit.py
----------------------
Conduit node control endpoints.

Implemented in:
  Issue #17 -- Conduit adapter (systemctl wrapper)
  Issue #19 -- POST /api/conduit/start, stop, restart
  Issue #20 -- POST /api/conduit/pair (transient pairing, no storage)

Stub phase
----------
Routes are registered so the URL structure is established.
All return HTTP 501 until Issues #19 and #20 are implemented.
Authentication is enforced now (Issue #16) so the dependency is in place
when the routes are fully implemented.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(tags=["conduit"])

_NOT_IMPLEMENTED_19 = JSONResponse(
    status_code=501,
    content={"detail": "Not implemented. Tracked in Issue #19."},
)

_NOT_IMPLEMENTED_20 = JSONResponse(
    status_code=501,
    content={"detail": "Not implemented. Tracked in Issue #20."},
)


@router.post("/start", summary="Start the Conduit service")
async def start(_user: AuthenticatedUser = Depends(get_current_user)):
    return _NOT_IMPLEMENTED_19


@router.post("/stop", summary="Stop the Conduit service")
async def stop(_user: AuthenticatedUser = Depends(get_current_user)):
    return _NOT_IMPLEMENTED_19


@router.post("/restart", summary="Restart the Conduit service")
async def restart(_user: AuthenticatedUser = Depends(get_current_user)):
    return _NOT_IMPLEMENTED_19


@router.post("/pair", summary="Pair Conduit node (pairing link never stored)")
async def pair(_user: AuthenticatedUser = Depends(get_current_user)):
    return _NOT_IMPLEMENTED_20
