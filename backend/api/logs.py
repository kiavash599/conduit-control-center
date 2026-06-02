"""
backend/api/logs.py
-------------------
Log viewer endpoint.

Implemented in:
  Issue #23 -- GET /api/logs  (last N lines from journalctl, with redaction)

Stub phase
----------
Returns HTTP 501 until Issue #23 is implemented.
Authentication is enforced now (Issue #16) so the dependency is in place
when the route is fully implemented.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(tags=["logs"])


@router.get("/logs", summary="Retrieve last N lines of Conduit service log")
async def get_logs(
    limit: int = 200,
    _user: AuthenticatedUser = Depends(get_current_user),
):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #23."},
    )
