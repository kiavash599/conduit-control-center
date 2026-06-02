"""
backend/api/status.py
---------------------
Node status endpoint.

Implemented in:
  Issue #17 -- Conduit adapter (systemctl wrapper)
  Issue #18 -- GET /api/status response schema

Stub phase
----------
Returns HTTP 501 until Issue #18 is implemented.
Authentication is enforced now (Issue #16) so the dependency is in place
when the route is fully implemented.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(tags=["status"])


@router.get("/status", summary="Get Conduit node status")
async def get_status(_user: AuthenticatedUser = Depends(get_current_user)):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #18."},
    )
