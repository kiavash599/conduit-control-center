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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend._version import APP_VERSION
from backend.backup.archiver import create_backup, open_backup
from backend.backup.crypto import BackupCryptoError
from backend.backup.exclusion import KeyExclusionError
from backend.backup.manifest import BackupArchiveError
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

# Inspect upload guard. Conservative ceiling, deliberately *under* the nginx
# default client_max_body_size of 1 MB. NOTE: nginx's 1 MB default remains the
# effective production cap (it 413s before the request reaches FastAPI) until a
# future deployment slice raises it; this app-level guard is defence in depth and
# bounds how many bytes we read into memory before decryption.
_MAX_INSPECT_BYTES = 900 * 1024  # 921_600 bytes (< 1 MB nginx default)


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


# ---------------------------------------------------------------------------
# Compatibility verdict (preview only)
# ---------------------------------------------------------------------------


def _parse_version(v: str):
    """Best-effort numeric tuple for an "x.y.z" version, ignoring any non-numeric
    suffix. Returns None if it cannot be parsed for a confident comparison."""
    parts = []
    for piece in str(v).split("."):
        num = ""
        for ch in piece:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            return None
        parts.append(int(num))
    return tuple(parts) if parts else None


def _compatibility(backup_app_version: str) -> dict:
    """Informational verdict for the preview. The hard manifest_version gate is
    already enforced inside open_backup (a newer manifest_version raises before we
    get here), so a manifest we can read is structurally restorable. This verdict
    only reflects the app_version relationship.

      backup <= current  -> compatible (older upgrades on next start)
      backup  > current  -> not compatible (update CCC before restoring)
      unparseable        -> compatible, with a neutral note
    """
    bv = _parse_version(backup_app_version)
    cv = _parse_version(APP_VERSION)
    if bv is None or cv is None:
        return {
            "compatible": True,
            "message": "Backup opened successfully; version could not be compared automatically.",
            "current_app_version": APP_VERSION,
        }
    if bv > cv:
        return {
            "compatible": False,
            "message": (
                "This backup was created by a newer version of CCC "
                f"({backup_app_version}). Update CCC to at least that version before restoring."
            ),
            "current_app_version": APP_VERSION,
        }
    if bv < cv:
        return {
            "compatible": True,
            "message": (
                f"Backup is from an older version ({backup_app_version}); "
                f"settings will be upgraded to {APP_VERSION} on next start."
            ),
            "current_app_version": APP_VERSION,
        }
    return {
        "compatible": True,
        "message": "Backup matches the current version of CCC.",
        "current_app_version": APP_VERSION,
    }


# ---------------------------------------------------------------------------
# POST /api/backup/inspect
# ---------------------------------------------------------------------------


@router.post(
    "/inspect",
    summary="Inspect an encrypted backup and preview its manifest",
    responses={
        200: {"description": "Manifest preview + compatibility verdict"},
        400: {"description": "Wrong passphrase, invalid/incompatible backup, or safety rejection"},
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        413: {"description": "Uploaded file exceeds the inspect size limit"},
        422: {"description": "Missing file or passphrase"},
        500: {"description": "Inspection failed"},
    },
)
async def inspect_backup_endpoint(
    file:       UploadFile = File(...),
    passphrase: str = Form(..., min_length=1, max_length=_PASSPHRASE_MAX_LEN),
    _user:      AuthenticatedUser = Depends(get_current_user),
    _csrf:      None = Depends(require_csrf_token),
) -> dict:
    """Decrypt + open an uploaded backup in memory and return a manifest preview.

    This is READ-ONLY: nothing is restored, nothing is written to disk. The
    decrypted plaintext exists only in memory inside open_backup. The passphrase
    is never logged. On any failure a generic, non-leaking message is returned."""
    # Read under a size guard; read at most the cap (+1 to detect overflow) so a
    # huge upload cannot exhaust memory. Always close the handle.
    try:
        blob = await file.read(_MAX_INSPECT_BYTES + 1)
    finally:
        await file.close()

    if len(blob) > _MAX_INSPECT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Backup file is too large to inspect.",
        )

    try:
        # open_backup is synchronous and CPU-bound (scrypt); run it off the loop.
        opened = await run_in_threadpool(open_backup, blob, passphrase)
    except KeyExclusionError:
        logger.warning("backup/inspect rejected: key-grade content in uploaded archive")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This backup was rejected by a safety check and cannot be inspected.",
        )
    except BackupCryptoError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wrong passphrase or invalid backup file.",
        )
    except BackupArchiveError as exc:
        # The "newer version" message is our own controlled string (no secrets),
        # so it is safe to surface a more helpful wording when distinguishable.
        if "newer" in str(exc).lower():
            detail = "This backup was created by a newer version of CCC and cannot be read by this version."
        else:
            detail = "The file is not a valid CCC backup."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    except HTTPException:
        raise
    except Exception:
        logger.exception("backup/inspect failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not inspect the backup.",
        )

    m = opened.manifest
    # Preview only: item name + size (no bytes, no sha256). Manifest carries no
    # secrets by design (collector excludes all key-grade material).
    items = [{"name": it.get("name"), "size": it.get("size")} for it in m.get("items", [])]
    return {
        "app_version":      m.get("app_version"),
        "created_utc":      m.get("created_utc"),
        "manifest_version": m.get("manifest_version"),
        "kind":             m.get("kind"),
        "items":            items,
        "excluded":         m.get("excluded", []),
        "compatibility":    _compatibility(m.get("app_version", "")),
    }
