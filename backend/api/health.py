"""
backend/api/health.py
---------------------
GET /api/health -- unauthenticated liveness check.

This endpoint is intentionally kept simple and dependency-free:
- No session required
- No database query
- Returns within milliseconds

Used by:
- install.sh Phase 2l (waits for 200 before declaring install successful)
- Nginx upstream health checks
- CI smoke tests

Response schema
---------------
{
    "status": "ok",
    "version": "0.1.0",
    "uptime_seconds": 42.7
}
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend._version import APP_VERSION

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Liveness check",
    response_description="Application is running",
    responses={
        200: {
            "description": "Application is healthy",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "version": "0.1.0",
                        "uptime_seconds": 42.7,
                    }
                }
            },
        }
    },
)
async def health(request: Request) -> JSONResponse:
    """
    Unauthenticated health check.

    Returns HTTP 200 as long as the application process is running and
    able to handle requests.  Does not check Conduit service status or
    database connectivity -- those are separate concerns.
    """
    started_at: float = getattr(request.app.state, "started_at", time.time())
    uptime = round(time.time() - started_at, 1)

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "version": APP_VERSION,
            "uptime_seconds": uptime,
        },
    )
