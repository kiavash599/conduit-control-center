# SPDX-License-Identifier: MIT
"""
backend/api/backup.py
---------------------
Backup & Restore HTTP adapter (Epic #4, slice S4A.1).

This slice exposes a SINGLE endpoint:

    POST /api/backup/create  -- create an encrypted backup and stream it back
                                as a file download.

It is a thin adapter over the frozen, CI-green backup package: it validates the
request, calls create_backup() (S2C orchestration: collect -> pack -> encrypt),
and returns the encrypted bytes as an attachment. No restore, no inspect, no
upload, no service control, no disk writes -- the encrypted blob exists only in
memory and is handed straight to the response.

Security properties
-------------------
* Authenticated operator only (get_current_user -> 401 if not).
* CSRF protected (require_csrf_token -> 403 on missing/invalid token).
* The passphrase arrives in the JSON body only -- never a URL/query string, and
  it is never logged. Errors are mapped to generic, non-leaking messages.
* create_backup() is fail-closed: a key-grade item in the source aborts the
  whole operation (KeyExclusionError) and no partial output is produced.
* scrypt is deliberately expensive, so the (synchronous) create_backup call is
  offloaded to a worker thread to avoid blocking the event loop.

Error mapping
-------------
  HTTP 200  -- success; body is the encrypted backup (application/octet-stream)
  HTTP 401  -- not authenticated            (get_current_user dependency)
  HTTP 403  -- CSRF token missing/invalid   (require_csrf_token dependency)
  HTTP 422  -- request body validation failed (passphrase too short/long/absent)
  HTTP 500  -- KeyExclusionError (server-side safety abort) or any other failure;
               always a generic detail, never the passphrase or file contents
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend.backup.archiver import create_backup
from backend.backup.exclusion import KeyExclusionError
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_csrf_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backup"])

# Minimum matches the admin-password floor (install.sh MIN_PW_LEN / settings.py).
_PASSPHRASE_MIN_LEN = 12
_PASSPHRASE_MAX_LEN = 1024


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class CreateBackupRequest(BaseModel):
    """Body for POST /api/backup/create.

    The passphrase is the only input. It is bounded server-side (defence in
    depth; the UI will validate too) and is never logged or echoed back."""

    passphrase: str = Field(min_length=_PASSPHRASE_MIN_LEN, max_length=_PASSPHRASE_MAX_LEN)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _download_filename() -> str:
    """A timestamped, secret-free attachment filename, e.g.
    ``ccc-backup-20260620T140501Z.cccbak``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"ccc-backup-{stamp}.cccbak"


# ---------------------------------------------------------------------------
# POST /api/backup/create
# ---------------------------------------------------------------------------


@router.post(
    "/create",
    summary="Create an encrypted backup and download it",
    responses={
        200: {
            "description": "Encrypted backup file",
            "content": {"application/octet-stream": {}},
        },
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        422: {"description": "Request body validation failed"},
        500: {"description": "Backup creation failed"},
    },
)
async def create_backup_endpoint(
    body:  CreateBackupRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None              = Depends(require_csrf_token),
) -> Response:
    """Produce an encrypted backup of CCC state and return it as a file download.

    The encrypted bytes are built entirely in memory and streamed back with a
    Content-Disposition attachment header; nothing is written to disk. On any
    failure the response carries a generic message with no secret material."""
    try:
        # create_backup is synchronous and CPU-bound (scrypt); run it off the
        # event loop. The passphrase is passed positionally and never logged.
        blob = await run_in_threadpool(create_backup, body.passphrase)
    except KeyExclusionError:
        # Fail-closed: the source contained key-grade material where none is
        # allowed. No backup was produced. Generic message; details only to logs.
        logger.warning("backup/create aborted: key-grade content detected in source")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Backup aborted: the server failed a safety check and produced no file.",
        )
    except Exception:
        # Any other failure (e.g. missing source files, OS error). Log the
        # exception (which never contains the passphrase) and return generic 500.
        logger.exception("backup/create failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Backup creation failed.",
        )

    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{_download_filename()}"',
            # Defence in depth: never let a proxy/browser cache the backup bytes.
            "Cache-Control": "no-store",
        },
    )
