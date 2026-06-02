"""
backend/api/metrics.py
----------------------
System and traffic metrics endpoints.

Implemented in:
  Issue #21 -- GET /api/metrics/system  (CPU, RAM, temp, disk via psutil)
  Issue #22 -- GET /api/metrics/traffic (bytes transferred by Conduit)

Stub phase
----------
Returns HTTP 501 until Issues #21 and #22 are implemented.
Authentication is enforced now (Issue #16) so the dependency is in place
when the routes are fully implemented.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(tags=["metrics"])


@router.get("/system", summary="System health metrics (CPU, RAM, temperature, disk)")
async def system_metrics(_user: AuthenticatedUser = Depends(get_current_user)):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #21."},
    )


@router.get("/traffic", summary="Conduit traffic counters (bytes sent/received)")
async def traffic_metrics(_user: AuthenticatedUser = Depends(get_current_user)):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #22."},
    )
