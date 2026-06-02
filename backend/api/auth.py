"""
backend/api/auth.py
-------------------
Authentication endpoints.

Implemented in:
  Issue #14 -- POST /api/auth/login, POST /api/auth/logout
  Issue #15 -- Account lockout, ccc-unlock CLI

Stub phase
----------
Routes are registered so the URL structure is established.
All return HTTP 501 until Issues #13 and #14 are implemented.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["auth"])

_NOT_IMPLEMENTED = JSONResponse(
    status_code=501,
    content={"detail": "Not implemented. Tracked in Issue #14."},
)


@router.post("/login", summary="Log in with username and password")
async def login():
    return _NOT_IMPLEMENTED


@router.post("/logout", summary="Invalidate current session and clear cookie")
async def logout():
    return _NOT_IMPLEMENTED
