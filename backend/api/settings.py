"""
backend/api/settings.py
-----------------------
Application settings endpoints.

Routes (Issue #31)
------------------
  PUT /api/settings/password  -- change the admin password

Password change flow
---------------------
The change is performed in a specific order chosen for fail-safe behaviour:

  1. Verify current_password matches the stored bcrypt hash.
  2. DELETE all sessions from the database (delete-sessions-first ordering).
  3. Generate a new bcrypt hash for new_password.
  4. Write the new hash to the active .env file.
  5. Call get_settings.cache_clear() so the lru_cache picks up the new hash.
  6. Clear the session cookie on the response.
  7. Return 200 {"status": "ok", "message": "..."}.

Why delete sessions before writing the hash
--------------------------------------------
If step 4 (.env write) fails after step 2 (session deletion):
  - All sessions are gone; the old hash is still in .env.
  - The legitimate operator logs back in with the OLD password.
  - No security failure; minor UX inconvenience.

If the hash were written first and session deletion failed:
  - New hash active; old sessions still valid.
  - An attacker with an old session cookie retains access until expiry.
  - Security gap.

Delete-first ordering is therefore the safer choice for both outcomes.

Failure responses
-----------------
  HTTP 400  -- current_password incorrect
  HTTP 422  -- Pydantic model validation failed (new_password too short,
               new/confirm mismatch)
  HTTP 500  -- session deletion failed (safe: nothing changed; retry)
  HTTP 500  -- .env write failed (sessions deleted; message tells the operator
               to log in with original password and retry)
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator

from backend.auth.cookies import clear_session_cookie
from backend.auth.login import hash_password, verify_password
from backend.auth.sessions import delete_all_sessions
from backend.config import get_env_file_path, get_settings
from backend.dependencies import AuthenticatedUser, get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class ChangePasswordRequest(BaseModel):
    """
    Body accepted by PUT /api/settings/password.

    Server-side constraints (defence-in-depth; client also validates):
      - current_password : non-empty
      - new_password     : minimum 10 characters
      - confirm_password : must equal new_password
    """

    current_password:  str = Field(min_length=1, max_length=1024)
    new_password:      str = Field(min_length=10, max_length=1024)
    confirm_password:  str = Field(min_length=1,  max_length=1024)

    @model_validator(mode="after")
    def _passwords_must_match(self) -> "ChangePasswordRequest":
        """Reject requests where new_password and confirm_password differ."""
        if self.new_password != self.confirm_password:
            raise ValueError("new_password and confirm_password do not match")
        return self


# ---------------------------------------------------------------------------
# .env update helper
# ---------------------------------------------------------------------------


def _write_password_hash(new_hash: str) -> None:
    """
    Write new_hash to the ADMIN_PASSWORD_HASH key in the active .env file.

    Strategy: read the file, find and replace the ADMIN_PASSWORD_HASH= line,
    write back.  If the key is absent (bare dev environment with no .env),
    it is appended.

    Parameters
    ----------
    new_hash : str -- bcrypt hash string to store

    Raises
    ------
    OSError  -- file cannot be read or written (permissions, missing directory)
    """
    env_path: Path = get_env_file_path()
    target_key = "ADMIN_PASSWORD_HASH="
    new_line   = f"{target_key}{new_hash}\n"

    try:
        existing = env_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # No .env yet (bare dev clone). Create it with just the hash key.
        existing = ""

    lines    = existing.splitlines(keepends=True)
    replaced = False

    for i, line in enumerate(lines):
        if line.startswith(target_key):
            lines[i] = new_line
            replaced  = True
            break

    if not replaced:
        # Key missing — append it.
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(new_line)

    env_path.write_text("".join(lines), encoding="utf-8")
    logger.info("ADMIN_PASSWORD_HASH updated in %s", env_path)


# ---------------------------------------------------------------------------
# PUT /api/settings/password
# ---------------------------------------------------------------------------


@router.put(
    "/password",
    summary="Change the admin password",
    responses={
        200: {"description": "Password changed; all sessions invalidated"},
        400: {"description": "Current password is incorrect"},
        422: {"description": "Request body validation failed"},
        500: {"description": "Server error during password change"},
    },
)
async def change_password(
    body:     ChangePasswordRequest,
    response: Response,
    db:       aiosqlite.Connection    = Depends(get_db),
    _user:    AuthenticatedUser       = Depends(get_current_user),
) -> dict:
    """
    Change the admin password.

    Verifies the current password, invalidates all sessions, writes the new
    bcrypt hash to .env, and clears the session cookie.  The client must
    redirect to /login after receiving 200.

    Sessions are deleted BEFORE the .env write (delete-first ordering) so
    that a failed write leaves the system in a safe, recoverable state.
    See module docstring for full rationale.
    """
    # ------------------------------------------------------------------
    # Step 1 — verify current password
    # ------------------------------------------------------------------
    stored_hash = get_settings().admin_password_hash
    if not stored_hash or not verify_password(body.current_password, stored_hash):
        # Use 400 (not 401): the session IS valid; this specific action
        # was denied because the credential check failed.  HTTP 401 would
        # mislead apiFetch into treating it as a session-expiry redirect.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # ------------------------------------------------------------------
    # Step 2 — delete all sessions (delete-first ordering)
    # ------------------------------------------------------------------
    try:
        deleted = await delete_all_sessions(db)
        logger.info(
            "Password change: %d session(s) invalidated before hash write",
            deleted,
        )
    except Exception:
        logger.exception("Password change aborted: session deletion failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Password change failed due to a server error. "
                "Your password has not been changed. Please try again."
            ),
        )

    # ------------------------------------------------------------------
    # Step 3 — hash new password and write to .env
    # If this fails, all sessions are gone but the old password remains
    # valid.  The error message tells the operator to log in and retry.
    # ------------------------------------------------------------------
    new_hash = hash_password(body.new_password)
    try:
        _write_password_hash(new_hash)
        get_settings.cache_clear()  # force lru_cache reload on next request
        logger.info("Password change: new hash written and settings cache cleared")
    except OSError:
        logger.exception("Password change: failed to write new hash to .env")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Password change failed: could not save the new password. "
                "Your sessions have been cleared. "
                "Please log in with your original password and try again."
            ),
        )

    # ------------------------------------------------------------------
    # Step 4 — clear session cookie on the response
    # ------------------------------------------------------------------
    clear_session_cookie(response)

    return {
        "status":  "ok",
        "message": "Password changed. Please log in again.",
    }
