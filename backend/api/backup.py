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

import json
import subprocess
import uuid

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

# Inspect/restore upload guard (S4B-2.4). Sized from the backup-archive evidence:
# the dominant term is the 90-day traffic_delta window, giving a steady-state
# ccc.db of ~20-25 MB raw, which gzips (the archive is tar.gz) to a ~6-8 MB
# encrypted upload in the worst realistic default-cadence case. 10 MB leaves
# headroom while staying under nginx (client_max_body_size 12m in /api/) and the
# helper's 16 MB MAX_BLOB_BYTES. Ordering: API (10M) <= nginx (12m) <= helper
# (16M), so an in-range upload reaches FastAPI and gets a clean app-level 413.
_MAX_INSPECT_BYTES = 10 * 1024 * 1024  # 10 MiB

# ---------------------------------------------------------------------------
# Restore (S4B-2.2) -- thin HTTP layer over the privileged helper.
# ---------------------------------------------------------------------------
# The restore *execution* runs out-of-process in the root helper
# ccc-restore-apply (committed 1bf5ac7): the API pre-validates, then streams the
# encrypted blob + passphrase to the helper over stdin and returns 202 as soon as
# the helper acks + detaches. The actual stop/restore/restart happens in the
# detached worker; its result is read back from the outcome file (the source of
# truth), never from this request.
#
# NOTE: the sudoers grant for the helper, the nginx body-limit raise, and
# /var/lib/conduit-cc provisioning are deferred to S4B-2.4, so this path is not
# end-to-end on the Pi yet. The unit tests stub the helper invocation.
_HELPER_PATH = "/opt/conduit-cc/bin/ccc-restore-apply"
_OUTCOME_PATH = "/var/lib/conduit-cc/restore-status.json"
_MAX_RESTORE_BYTES = _MAX_INSPECT_BYTES          # mirror the inspect cap
_CONFIRM_TOKEN = "RESTORE"
_HELPER_TIMEOUT_S = 30

# Helper foreground (pre-detach) exit codes -- mirror of ccc-restore-apply.
_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_FS = 3
_EXIT_SYSTEMCTL = 4
_EXIT_BUSY = 5
_EXIT_PREFLIGHT = 6
_EXIT_INTERNAL = 7


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


# ---------------------------------------------------------------------------
# Restore helper plumbing (S4B-2.2)
# ---------------------------------------------------------------------------


def _build_restore_frame(restore_id: str, blob: bytes, passphrase: bytes) -> bytes:
    """Build the CCC-RESTORE/1 stdin frame the helper parses. Secrets go on
    stdin only -- never argv/env."""
    header = (
        "CCC-RESTORE/1\n"
        f"restore_id: {restore_id}\n"
        f"blob_len: {len(blob)}\n"
        f"passphrase_len: {len(passphrase)}\n\n"
    ).encode("ascii")
    return header + blob + passphrase


def _invoke_restore_helper(frame: bytes):
    """Run the privileged helper, feeding the frame on stdin. Returns
    (returncode, stdout_text). Raises subprocess.TimeoutExpired on timeout.

    Stubbed in unit tests; real end-to-end requires the S4B-2.4 sudoers grant."""
    proc = subprocess.run(
        ["sudo", _HELPER_PATH, "apply"],
        input=frame,
        capture_output=True,
        timeout=_HELPER_TIMEOUT_S,
        shell=False,
    )
    return proc.returncode, proc.stdout.decode("ascii", "replace")


def _map_helper_exit(returncode: int) -> HTTPException:
    """Map a helper FOREGROUND (pre-detach) exit code to an HTTP error.

    EXIT_SYSTEMCTL never appears here -- service control happens in the detached
    worker and is surfaced via the outcome file, not this response."""
    if returncode == _EXIT_BUSY:
        return HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail="A restore is already in progress.")
    if returncode == _EXIT_PREFLIGHT:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                             detail="Wrong passphrase or invalid backup file.")
    if returncode == _EXIT_FS:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                             detail="Restore is not available on this server yet.")
    # EXIT_USAGE / EXIT_INTERNAL / anything unexpected -> generic 500.
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                         detail="Could not start the restore.")


def _read_outcome() -> dict:
    """Read the helper's outcome file (the source of truth). Absent -> idle.
    Unreadable/corrupt -> a safe generic state (never a secret, never a 500)."""
    try:
        with open(_OUTCOME_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"state": "idle", "restore_id": None, "started_utc": None,
                "finished_utc": None, "restart_ok": None,
                "message": "No restore has been run."}
    except (OSError, ValueError):
        return {"state": "unknown", "restore_id": None, "started_utc": None,
                "finished_utc": None, "restart_ok": None,
                "message": "Restore status is unavailable."}
    if not isinstance(data, dict) or data.get("schema") != 1:
        return {"state": "unknown", "restore_id": None, "started_utc": None,
                "finished_utc": None, "restart_ok": None,
                "message": "Restore status is unavailable."}
    return {
        "state":        data.get("state", "unknown"),
        "restore_id":   data.get("restore_id"),
        "started_utc":  data.get("started_utc"),
        "finished_utc": data.get("finished_utc"),
        "restart_ok":   data.get("restart_ok"),
        "message":      data.get("message", ""),
    }


# ---------------------------------------------------------------------------
# POST /api/backup/restore
# ---------------------------------------------------------------------------


@router.post(
    "/restore",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restore CCC state from an encrypted backup (destructive; restarts the dashboard)",
    responses={
        202: {"description": "Restore scheduled; the dashboard will restart"},
        400: {"description": "Wrong passphrase or invalid/incompatible backup"},
        401: {"description": "Not authenticated"},
        403: {"description": "CSRF token missing or invalid"},
        409: {"description": "A restore is already in progress"},
        413: {"description": "Uploaded file exceeds the restore size limit"},
        422: {"description": "Missing fields or confirmation not provided"},
        500: {"description": "Could not start the restore"},
        503: {"description": "Restore is not available on this server yet"},
    },
)
async def restore_backup_endpoint(
    file:       UploadFile = File(...),
    passphrase: str = Form(..., min_length=1, max_length=_PASSPHRASE_MAX_LEN),
    confirm:    str = Form(...),
    _user:      AuthenticatedUser = Depends(get_current_user),
    _csrf:      None = Depends(require_csrf_token),
) -> dict:
    """Destructive restore. Pre-validates the upload in memory, then hands the
    encrypted blob + passphrase to the privileged helper and returns 202 the
    moment the helper acks + detaches. The real stop/restore/restart runs in the
    detached worker; its outcome is read later via GET /api/backup/restore/status.

    The passphrase is never logged, never placed in argv/env, and never echoed."""
    if confirm != _CONFIRM_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'Type {_CONFIRM_TOKEN} to confirm this destructive restore.',
        )

    try:
        blob = await file.read(_MAX_RESTORE_BYTES + 1)
    finally:
        await file.close()
    if len(blob) > _MAX_RESTORE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Backup file is too large to restore.",
        )

    # Fast-fail pre-validation in memory (the helper re-validates authoritatively).
    try:
        await run_in_threadpool(open_backup, blob, passphrase)
    except (KeyExclusionError, BackupCryptoError, BackupArchiveError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wrong passphrase or invalid backup file.",
        )
    except Exception:
        logger.exception("backup/restore pre-validation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start the restore.",
        )

    restore_id = str(uuid.uuid4())
    frame = _build_restore_frame(restore_id, blob, passphrase.encode("utf-8"))

    try:
        returncode, stdout = await run_in_threadpool(_invoke_restore_helper, frame)
    except subprocess.TimeoutExpired:
        logger.error("backup/restore helper timed out")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start the restore.",
        )
    except Exception:
        logger.exception("backup/restore helper could not be launched")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start the restore.",
        )
    finally:
        # Drop secret references promptly.
        frame = None
        blob = None

    if returncode != _EXIT_OK:
        raise _map_helper_exit(returncode)

    # Strict ack match: returncode 0 must be accompanied by "accepted <restore_id>".
    if stdout.strip() != f"accepted {restore_id}":
        logger.error("backup/restore helper ack mismatch")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start the restore.",
        )

    return {
        "restore_id": restore_id,
        "state": "scheduled",
        "message": "Restore starting; the dashboard will restart and you will be signed out.",
    }


# ---------------------------------------------------------------------------
# GET /api/backup/restore/status
# ---------------------------------------------------------------------------


@router.get(
    "/restore/status",
    summary="Read the most recent restore outcome",
    responses={
        200: {"description": "Restore status"},
        401: {"description": "Not authenticated"},
    },
)
async def restore_status_endpoint(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Return the most recent restore outcome from the helper's outcome file (the
    source of truth). No clearing/acking: the next restore overwrites it."""
    return _read_outcome()
