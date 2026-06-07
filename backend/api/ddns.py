"""
backend/api/ddns.py
-------------------
Cloudflare DDNS status endpoint.

Implemented in:
  Issue #42 -- GET /api/ddns/status (parse DDNS log, expose last known state)

Log parsing
-----------
Reads the last 50 lines of ddns_log_path (AppConfig, default
/var/log/conduit-cc/ddns.log).  Each line is expected to be a JSON object
written by scripts/cloudflare-ddns.sh.  Malformed lines are skipped silently.

Response is cached for ddns_status_cache_seconds (AppConfig, default 30 s).

Security
--------
CF_API_TOKEN is NEVER accessed or returned by this module.
Only cf_record_name is read from Settings (a public domain name, not a secret).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.config import get_app_config, get_settings
from backend.dependencies import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ddns"])

_LOG_TAIL_LINES = 50


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DdnsStatusResponse(BaseModel):
    """Response body for GET /api/ddns/status."""

    hostname: Optional[str] = Field(
        default=None,
        description="CF_RECORD_NAME from .env (public domain name), or null if not configured.",
    )
    current_ip: Optional[str] = Field(
        default=None,
        description="Public IP from the most recent log entry, or null.",
    )
    last_updated: Optional[str] = Field(
        default=None,
        description="ISO 8601 UTC timestamp of the most recent log entry, or null.",
    )
    last_result: str = Field(
        description=(
            'Most recent DDNS result: "updated", "no_change", "error", or "unknown".'
        ),
    )
    last_message: Optional[str] = Field(
        default=None,
        description="Human-readable message from the most recent log entry, or null.",
    )
    consecutive_errors: int = Field(
        default=0,
        description=(
            "Number of trailing log entries with result=error. "
            "Resets to 0 when a non-error entry is encountered."
        ),
    )


# ---------------------------------------------------------------------------
# Module-level cache (same pattern as backend/api/metrics.py)
#
# Thread safety: uvicorn runs --workers 1 on the Pi; single process, single
# event loop.  A stale read during a concurrent cache refresh is harmless.
# ---------------------------------------------------------------------------

_ddns_cache: Optional[DdnsStatusResponse] = None
_ddns_cache_ts: float = 0.0


def _cache_valid() -> bool:
    """Return True if the cached response is still within its TTL."""
    ttl = get_app_config().ddns_status_cache_seconds
    return _ddns_cache is not None and (time.monotonic() - _ddns_cache_ts) < ttl


def _reset_ddns_cache() -> None:
    """
    Reset the module-level DDNS status cache.

    Call in test setup and teardown to guarantee test isolation.
    Not needed in production -- the cache expires naturally after
    ddns_status_cache_seconds (default 30 s).
    """
    global _ddns_cache, _ddns_cache_ts
    _ddns_cache = None
    _ddns_cache_ts = 0.0


# ---------------------------------------------------------------------------
# Log parser helpers
# ---------------------------------------------------------------------------


def _unknown_status(hostname: Optional[str]) -> DdnsStatusResponse:
    """Return the empty/no-data-yet response used on a fresh install."""
    return DdnsStatusResponse(
        hostname=hostname,
        current_ip=None,
        last_updated=None,
        last_result="unknown",
        last_message=None,
        consecutive_errors=0,
    )


def _parse_ddns_log(log_path: str, hostname: Optional[str]) -> DdnsStatusResponse:
    """
    Read the last _LOG_TAIL_LINES lines from log_path, parse JSON entries,
    and return a DdnsStatusResponse.

    Behaviour
    ---------
    - Log file missing or unreadable: returns unknown status (not an error).
    - Empty file or all-malformed lines: returns unknown status.
    - Malformed lines: skipped silently with a DEBUG log message.
    - consecutive_errors: count of trailing entries whose result == "error";
      resets to 0 on the first non-error entry scanning backwards from the end.
    - ip field of null in the log (error entries) becomes current_ip=None.

    Parameters
    ----------
    log_path : str
        Filesystem path to the DDNS log file.
    hostname : str or None
        CF_RECORD_NAME from Settings; passed through to the response unchanged.

    Returns
    -------
    DdnsStatusResponse
        Always returns a valid response object; never raises.
    """
    path = Path(log_path)

    if not path.exists():
        logger.debug("DDNS log not found at %s -- returning unknown status", log_path)
        return _unknown_status(hostname)

    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Could not read DDNS log %s: %s", log_path, exc)
        return _unknown_status(hostname)

    tail = raw_lines[-_LOG_TAIL_LINES:]

    entries: list[dict] = []
    for line in tail:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            logger.debug("Skipping malformed DDNS log line: %.80s", stripped)
            continue
        if isinstance(obj, dict):
            entries.append(obj)

    if not entries:
        return _unknown_status(hostname)

    last = entries[-1]

    # Count consecutive trailing error entries.
    # Scan backwards; stop at the first entry whose result is not "error".
    consecutive_errors = 0
    for entry in reversed(entries):
        if entry.get("result") == "error":
            consecutive_errors += 1
        else:
            break

    # The ip field is either a string (valid IP) or JSON null (error entries).
    ip_val = last.get("ip")
    current_ip: Optional[str] = ip_val if isinstance(ip_val, str) else None

    return DdnsStatusResponse(
        hostname=hostname,
        current_ip=current_ip,
        last_updated=last.get("timestamp") or None,
        last_result=last.get("result") or "unknown",
        last_message=last.get("message") or None,
        consecutive_errors=consecutive_errors,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="Cloudflare DDNS last update status",
    response_model=DdnsStatusResponse,
    responses={
        200: {
            "description": (
                "Most recent DDNS update state parsed from the log file. "
                'Returns last_result="unknown" on a fresh install before the first cron run.'
            )
        },
        401: {"description": "Not authenticated"},
    },
)
async def ddns_status(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> DdnsStatusResponse:
    """
    Return the most recent Cloudflare DDNS update status parsed from the DDNS log.

    Reads the last 50 lines of ``ddns_log_path`` (config.json, default
    ``/var/log/conduit-cc/ddns.log``).

    **Fresh install behaviour:** if the log file does not exist (the cron job
    has not run yet), returns HTTP 200 with ``last_result: "unknown"`` and
    ``null`` for all other fields.  This is not an error.

    **consecutive_errors:** counts how many of the most recent log entries
    have ``result: "error"`` in sequence from the end.  Resets to 0 when a
    ``"updated"`` or ``"no_change"`` entry is found.  If this reaches 3 or
    more, the frontend (Issue #43) shows a warning banner.

    Responses are cached for ``ddns_status_cache_seconds`` (config.json,
    default 30 s).  The DDNS script runs every 5 minutes; a 30-second cache
    is fine and avoids redundant file reads on every dashboard poll.

    **Security:** ``CF_API_TOKEN`` is never accessed or returned by this
    endpoint.  Only ``CF_RECORD_NAME`` (a public domain name) is read from
    the environment.
    """
    global _ddns_cache, _ddns_cache_ts  # noqa: PLW0603

    if _cache_valid():
        return _ddns_cache  # type: ignore[return-value]

    cfg = get_app_config()
    settings = get_settings()

    # CF_RECORD_NAME is a public domain name (not a secret).
    # An empty string (unconfigured .env) is normalised to None.
    hostname: Optional[str] = settings.cf_record_name or None

    result = _parse_ddns_log(cfg.ddns_log_path, hostname)

    _ddns_cache = result
    _ddns_cache_ts = time.monotonic()

    return result
