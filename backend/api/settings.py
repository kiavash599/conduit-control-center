"""
backend/api/settings.py
-----------------------
Application settings endpoints.

Implemented in:
  Issue #31 -- PUT /api/settings/password (change admin password)

Stub phase
----------
Returns HTTP 501 until Issue #31 is implemented.
Authentication is enforced now (Issue #16) so the dependency is in place
when the route is fully implemented.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(tags=["settings"])


@router.put("/password", summary="Change the admin password")
async def change_password(_user: AuthenticatedUser = Depends(get_current_user)):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #31."},
    )
