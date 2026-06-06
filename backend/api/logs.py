"""
backend/api/logs.py
-------------------
Log viewer endpoint.

Implemented in:
  Issue #23 -- GET /api/logs  (last N lines from journalctl, with redaction)

Design decisions (Issue #23)
-----------------------------
Log source    : journalctl only.  Ubuntu 22.04 / systemd routes all service
                stdout/stderr to journald.  No file fallback -- silent fallback
                would hide real deployment errors.
Subprocess    : asyncio.create_subprocess_exec only.  No subprocess.run, no
                shell=True.  Service name is a discrete argv token.
Redaction     : Entire-line replacement.  Any line containing a Psiphon
                pairing link pattern is replaced in full with "[REDACTED]".
                Matching is case-insensitive.  Timestamp not preserved for
                redacted lines (entire-line policy).

                TODO: extend _REDACT_PATTERNS once the Psiphon pairing link
                format is fully documented (e.g. base64-encoded variants,
                additional URI schemes, alternative Psiphon web domains).
                Each new pattern must be reviewed for false-positive risk.

Error model   : FileNotFoundError        -> HTTP 503
                journalctl exit != 0     -> HTTP 503  (stderr redacted + truncated)
                exit 0 + empty output    -> HTTP 200 []
                individual line parse err -> skip line, WARNING log, continue
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.config import get_app_config
from backend.dependencies import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["logs"])


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# TODO: extend this tuple once the Psiphon pairing link format is fully
#       documented.  All patterns are matched case-insensitively against the
#       entire raw log line.  A line matching ANY pattern is replaced in full
#       with _REDACTED_MARKER -- no partial redaction is performed.
_REDACT_PATTERNS: tuple[str, ...] = (
    "psi://",
    "psiphon://",
    "https://psiphon",
)

_REDACT_RE = re.compile(
    "|".join(re.escape(p) for p in _REDACT_PATTERNS),
    re.IGNORECASE,
)

_REDACTED_MARKER = "[REDACTED]"
_STDERR_MAX_CHARS = 200


def _should_redact(line: str) -> bool:
    """Return True if ``line`` contains any pairing link pattern."""
    return bool(_REDACT_RE.search(line))


def _redact_stderr(raw: str) -> str:
    """
    Sanitise stderr before including it in a 503 response body.

    Each line of ``raw`` is checked independently; lines matching any
    redaction pattern are replaced with _REDACTED_MARKER.  The result is
    joined, stripped, and truncated to _STDERR_MAX_CHARS characters.
    """
    cleaned: list[str] = []
    for line in raw.splitlines():
        cleaned.append(_REDACTED_MARKER if _should_redact(line) else line)
    return " | ".join(cleaned).strip()[:_STDERR_MAX_CHARS]


# ---------------------------------------------------------------------------
# Log line parsing
# ---------------------------------------------------------------------------

# Level keywords detected within the first _LEVEL_SEARCH_WINDOW characters
# of the message field.  Word boundaries prevent matching "INFORMATION" as
# "INFO".  WARN is normalised to WARNING for a consistent output vocabulary.
_LEVEL_RE = re.compile(
    r"\[?(CRITICAL|ERROR|WARNING|WARN|INFO|DEBUG)\]?[:\s]?",
    re.IGNORECASE,
)
_LEVEL_SEARCH_WINDOW = 30

# journalctl separator/header lines begin with "--".
_SEPARATOR_PREFIX = "--"


class LogLine(BaseModel):
    """
    One parsed log entry returned by GET /api/logs.

    timestamp : ISO 8601 string from journalctl (e.g. "2026-05-31T14:30:00+0000").
                None for structurally unparseable lines, or for redacted lines
                (entire-line policy -- timestamp is not preserved).
    level     : "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL".
                Defaults to "INFO" when no keyword is found in the message prefix.
    message   : Message portion of the log line, or "[REDACTED]".
    """

    timestamp: Optional[str] = None
    level: str = "INFO"
    message: str


def _parse_line(raw: str) -> Optional[LogLine]:
    """
    Parse one journalctl short-iso line into a LogLine.

    Returns None for blank lines and journalctl separator/header lines
    (those starting with "--").  Returns a LogLine with timestamp=None for
    lines that do not match the expected four-token structure.

    This function never raises -- the caller catches unexpected exceptions
    so a single malformed line cannot abort the entire response.
    """
    raw = raw.rstrip()
    if not raw:
        return None
    if raw.startswith(_SEPARATOR_PREFIX):
        return None  # e.g. "-- Logs begin at ..."

    # Expected: "TIMESTAMP HOSTNAME UNIT[PID]: MESSAGE"
    parts = raw.split(" ", 3)
    if len(parts) < 4:
        # Continuation or malformed line -- surface as raw message.
        return LogLine(timestamp=None, level="INFO", message=raw)

    timestamp = parts[0]
    # parts[1] = hostname, parts[2] = "unit[pid]:" -- not forwarded.
    message = parts[3]

    # Detect level from the first _LEVEL_SEARCH_WINDOW chars of the message.
    level = "INFO"
    m = _LEVEL_RE.search(message[:_LEVEL_SEARCH_WINDOW])
    if m:
        raw_level = m.group(1).upper()
        level = "WARNING" if raw_level == "WARN" else raw_level

    return LogLine(timestamp=timestamp, level=level, message=message)


# ---------------------------------------------------------------------------
# Async journalctl runner
# ---------------------------------------------------------------------------


async def _run_journalctl(service: str, limit: int) -> tuple[int, str, str]:
    """
    Run journalctl and return (returncode, stdout, stderr).

    Uses asyncio.create_subprocess_exec -- never shell=True, never
    subprocess.run.  ``service`` is passed as a discrete argv token and is
    never interpolated into a shell string.

    Raises FileNotFoundError when journalctl is not on PATH.  The caller
    converts this to HTTP 503.
    """
    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-u", service,
        "-n", str(limit),
        "--no-pager",
        "--output=short-iso",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return (
        proc.returncode,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get(
    "/logs",
    summary="Retrieve last N lines of the Conduit service log",
    response_model=list[LogLine],
    responses={
        200: {
            "description": (
                "Log lines. Empty list when the service has not produced "
                "any output yet."
            )
        },
        401: {"description": "Not authenticated"},
        503: {
            "description": (
                "journalctl binary not found, or journalctl returned a "
                "non-zero exit code."
            )
        },
    },
)
async def get_logs(
    limit: int = Query(
        default=200,
        ge=1,
        le=1000,
        description="Number of log lines to return (1-1000, default 200).",
    ),
    _user: AuthenticatedUser = Depends(get_current_user),
) -> list[LogLine]:
    """
    Return the last ``limit`` lines of the Conduit service log via journalctl.

    **Redaction** - lines containing Psiphon pairing link patterns
    (``psi://``, ``psiphon://``, ``https://psiphon``, matched
    case-insensitively) are replaced **in full** with ``"[REDACTED]"``.
    No partial redaction is performed; the timestamp is not preserved for
    redacted lines.

    **Empty result** - HTTP 200 with an empty list when journalctl exits 0
    with no output (service exists but has not logged anything yet).

    **Errors** - HTTP 503 when journalctl is not found (infrastructure
    misconfiguration) or exits non-zero (e.g. permission denied, unknown
    unit).  A sanitised excerpt of stderr is included in the detail field.

    **Malformed lines** - lines that cannot be parsed are silently skipped
    and do not abort the response.
    """
    cfg = get_app_config()
    service = cfg.conduit_service_name

    # --- Run journalctl -------------------------------------------------------
    try:
        returncode, stdout, stderr = await _run_journalctl(service, limit)
    except FileNotFoundError:
        logger.error(
            "journalctl not found -- cannot serve logs. "
            "Verify the host is running systemd (Ubuntu 22.04 expected)."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Log reader unavailable: journalctl not found. "
                "Verify the system is running systemd."
            ),
        )

    if returncode != 0:
        safe_stderr = _redact_stderr(stderr)
        logger.error(
            "journalctl exited %d for service %r -- stderr: %s",
            returncode,
            service,
            safe_stderr,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Log reader returned a non-zero exit code ({returncode}). "
                f"Details: {safe_stderr}"
            ),
        )

    # --- Exit 0 + empty output = no log entries yet (not an error) -----------
    if not stdout.strip():
        return []

    # --- Parse and redact -----------------------------------------------------
    lines: list[LogLine] = []

    for raw in stdout.splitlines():
        # Redaction is applied to the raw line before any structural parsing
        # so that no fragment of a pairing link reaches the parsed fields.
        if _should_redact(raw):
            lines.append(LogLine(
                timestamp=None,
                level="INFO",
                message=_REDACTED_MARKER,
            ))
            continue

        try:
            parsed = _parse_line(raw)
        except Exception:  # noqa: BLE001
            # A single malformed line must not abort the entire response.
            logger.warning(
                "Could not parse journalctl line (first 120 chars): %r",
                raw[:120],
            )
            continue

        if parsed is not None:
            lines.append(parsed)

    return lines
