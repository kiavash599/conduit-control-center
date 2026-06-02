"""
backend/api/logs.py
-------------------
Log viewer endpoint.

Implemented in:
  Issue #23 — GET /api/logs  (last N lines from journalctl, with redaction)

Stub phase
----------
Returns HTTP 501 until Issue #23 is implemented.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["logs"])


@router.get("/logs", summary="Retrieve last N lines of Conduit service log")
async def get_logs(limit: int = 200):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #23."},
    )
