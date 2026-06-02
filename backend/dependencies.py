"""
backend/dependencies.py
-----------------------
Reusable FastAPI dependencies shared across route modules.

Dependencies defined here
-------------------------
get_db          — yields an aiosqlite connection for the duration of the request
get_current_user — validates the session cookie and returns the authenticated user;
                   raises HTTP 401 if no valid session exists

Usage
-----
    from backend.dependencies import get_db, get_current_user
    from fastapi import Depends

    @router.get("/protected")
    async def protected_route(
        user: dict = Depends(get_current_user),
        db: aiosqlite.Connection = Depends(get_db),
    ):
        ...

Notes
-----
- get_current_user is intentionally thin here — the full session lookup logic
  lives in backend/auth/sessions.py (Issue #13).  This module imports from
  there once it exists; for now it raises 501 so the app skeleton starts cleanly.
- HTML routes should redirect to /login on 401; API routes should return JSON 401.
  The routing layer (Issues #14, #16) is responsible for that distinction.
  get_current_user always raises HTTPException(401) — callers decide the response.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

import aiosqlite
from fastapi import Cookie, Depends, HTTPException, status

from backend.database import get_db as _get_db_ctx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database dependency
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    FastAPI dependency that opens a database connection for the request
    and closes it when the request finishes.

    Wraps the asynccontextmanager from database.py so FastAPI can use it
    as a standard async generator dependency.
    """
    async with _get_db_ctx() as db:
        yield db


# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------

# Placeholder type until auth module is implemented in Issue #13 / #16.
# Replace with a proper User model or TypedDict when auth is built.
_UserDict = dict


async def get_current_user(
    session_id: str | None = Cookie(default=None, alias="session_id"),
    db: aiosqlite.Connection = Depends(get_db),
) -> _UserDict:
    """
    Validate the session cookie and return the current user.

    Raises
    ------
    HTTPException(401)  — no cookie, expired session, or invalid session ID
    HTTPException(501)  — session store not yet implemented (skeleton phase)

    This dependency will be fully implemented in Issue #13 (session store)
    and Issue #16 (session validation middleware).  During the skeleton phase
    it raises 501 so protected routes clearly signal they are not yet active.
    """
    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # TODO (Issue #13): replace with real session lookup
    # from backend.auth.sessions import get_session
    # session = await get_session(db, session_id)
    # if session is None:
    #     raise HTTPException(status_code=401, detail="Session expired or invalid")
    # return {"user_id": session["user_id"]}

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Session validation not yet implemented. Tracked in Issue #13.",
    )
