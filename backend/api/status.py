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
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["status"])


@router.get("/status", summary="Get Conduit node status")
async def get_status():
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #18."},
    )
