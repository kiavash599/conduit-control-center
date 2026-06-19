# SPDX-License-Identifier: MIT
"""
backend/conduit/ryve.py
-----------------------
Stateless backend adapter for the Ryve claim helper (Epic #3, R2a). The ONLY
bridge between the CCC backend and:

    sudo -u conduit /opt/conduit-cc/bin/ccc-ryve-claim

The helper emits a KEY-GRADE artifact: the Ryve claim QR PNG encodes the station
identity (data.key). This adapter:
  * runs the helper via asyncio.create_subprocess_exec (argv-only; NEVER a shell);
  * captures stdout as BYTES (it carries raw PNG bytes) and stderr as a pipe;
  * parses the binary-safe frame structurally and validates the PNG magic;
  * maps failures to typed exceptions.

Leakage rules (do not weaken): stdout, stderr, the PNG bytes, the station name,
the proxy id, and any key/QR material are NEVER logged and NEVER placed in an
exception message. Only a generic event and the integer exit code are logged --
stderr is NOT logged even on failure.

Frame format emitted by the helper (binary-safe):
    CCC-RYVE-CLAIM/1\n
    station_name: <single line>\n
    proxy_id: <single line>\n
    png_len: <N>\n
    \n
    <exactly N raw PNG bytes>
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from backend.conduit.errors import ConduitPermissionError, RyveClaimError

logger = logging.getLogger(__name__)

# --- Hardcoded invocation constants (never built from user input) ----------
_SUDO = "sudo"
_RUNAS_USER = "conduit"
_HELPER_PATH = "/opt/conduit-cc/bin/ccc-ryve-claim"
_TIMEOUT_S = 40.0                   # above the helper's own 30s conduit timeout

_FRAME_VERSION = "CCC-RYVE-CLAIM/1"
_SEP = b"\n\n"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class RyveClaim:
    """A parsed Ryve claim. `png` is the raw QR PNG (key-grade); the two string
    fields are non-secret display metadata."""

    station_name: str
    proxy_id: str
    png: bytes


def _looks_like_permission_denied(stderr: str) -> bool:
    s = stderr.lower()
    return (
        "is not allowed to execute" in s
        or "is not in the sudoers" in s
        or "a password is required" in s
        or "sudo: a terminal is required" in s
    )


async def _run_helper() -> tuple[int, bytes, str]:
    """Run the helper once. Returns (returncode, stdout_bytes, stderr_text).

    stdout is returned as RAW BYTES (it contains the PNG); it is NEVER decoded as
    a whole and NEVER logged. stderr is decoded ONLY for permission detection and
    is NEVER logged (even on failure)."""
    args = [_SUDO, "-u", _RUNAS_USER, _HELPER_PATH]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise RyveClaimError("could not run the Ryve claim helper") from exc

    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RyveClaimError("the Ryve claim helper timed out") from exc

    rc = proc.returncode if proc.returncode is not None else -1
    return rc, out_b, err_b.decode("utf-8", errors="replace")


def _parse_frame(out_bytes: bytes) -> RyveClaim:
    """Parse the binary-safe helper frame; reject any malformation generically.

    The split is on the FIRST blank line only, then EXACTLY png_len bytes are
    taken as the PNG -- so a PNG that itself contains b"\\n\\n" round-trips, and
    a short body or trailing bytes are rejected."""
    idx = out_bytes.find(_SEP)
    if idx == -1:
        raise RyveClaimError("malformed Ryve claim output")
    header = out_bytes[:idx].decode("ascii", errors="replace")
    body = out_bytes[idx + len(_SEP):]

    lines = header.split("\n")
    if not lines or lines[0] != _FRAME_VERSION:
        raise RyveClaimError("unexpected Ryve claim frame version")

    fields: dict[str, str] = {}
    for ln in lines[1:]:
        key, sep, val = ln.partition(": ")
        if sep:
            fields[key] = val

    if "png_len" not in fields:
        raise RyveClaimError("malformed Ryve claim output")
    try:
        png_len = int(fields["png_len"])
    except ValueError as exc:
        raise RyveClaimError("malformed Ryve claim output") from exc
    # Exact length required: reject a short body AND trailing bytes.
    if png_len < 0 or len(body) != png_len:
        raise RyveClaimError("malformed Ryve claim output")
    if not body.startswith(_PNG_MAGIC):
        raise RyveClaimError("Ryve claim image is not a valid PNG")

    return RyveClaim(
        station_name=fields.get("station_name", ""),
        proxy_id=fields.get("proxy_id", ""),
        png=bytes(body),
    )


async def generate_ryve_claim() -> RyveClaim:
    """Generate a fresh Ryve claim (station name, proxy id, QR PNG bytes).

    Stateless: nothing is cached or persisted here. The PNG is returned to the
    caller and is NEVER logged."""
    rc, out_bytes, stderr = await _run_helper()
    if rc != 0:
        # rc ONLY -- stderr is never logged (it may reference the warning/output).
        logger.error("ryve claim helper failed (rc=%d)", rc)
        if _looks_like_permission_denied(stderr):
            raise ConduitPermissionError(
                "not permitted to run the Ryve claim helper (check sudoers)"
            )
        raise RyveClaimError("Ryve claim helper operation failed")
    claim = _parse_frame(out_bytes)
    logger.info("ryve claim generated ok")
    return claim
