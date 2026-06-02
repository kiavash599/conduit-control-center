"""
backend/api/ddns.py
-------------------
Cloudflare DDNS status endpoint.

Implemented in:
  Issue #42 -- GET /api/ddns/status (parse DDNS log, expose last known state)

Stub phase
----------
Returns HTTP 501 until Issue #42 is implemented.
Authentication is enforced now (Issue #16) so the dependency is in place
when the route is fully implemented.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(tags=["ddns"])


@router.get("/status", summary="Cloudflare DDNS last update status")
async def ddns_status(_user: AuthenticatedUser = Depends(get_current_user)):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #42."},
    )
