"""
backend/api/settings.py
-----------------------
Application settings endpoints.

Implemented in:
  Issue #31 -- PUT /api/settings/password (change admin password)

Stub phase
----------
Returns HTTP 501 until Issue #31 is implemented.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["settings"])


@router.put("/password", summary="Change the admin password")
async def change_password():
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #31."},
    )
