"""
backend/api/ddns.py
-------------------
Cloudflare DDNS status endpoint.

Implemented in:
  Issue #42 -- GET /api/ddns/status (parse DDNS log, expose last known state)

Stub phase
----------
Returns HTTP 501 until Issue #42 is implemented.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["ddns"])


@router.get("/status", summary="Cloudflare DDNS last update status")
async def ddns_status():
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #42."},
    )
