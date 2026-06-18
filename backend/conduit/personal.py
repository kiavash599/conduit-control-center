# SPDX-License-Identifier: MIT
"""
backend/conduit/personal.py
---------------------------
Stateless backend adapter for the personal-compartment helper (Personal Mode,
C5). The ONLY bridge between the CCC backend and:

    sudo -u conduit /opt/conduit-cc/bin/ccc-personal-compartment <subcommand>

Scope (infrastructure only):
  * Execute the helper subcommands; pass the display name on STDIN (never argv).
  * Capture stdout/stderr; parse output structurally; map helper exit codes to
    typed exceptions (no raw exit codes leak to callers).

Explicitly NOT here (belong to the C6 orchestration layer):
  * No workflow orchestration, no status() gating, no business decisions.
  * No restart / systemctl / daemon-reload.
  * No apply-lock ownership (the helper owns its flock; the apply-lock is C6's).
  * No token caching or persistence; no raw-ID handling (the adapter never
    receives a raw ID -- only the displayable pairing token, as a return value).

Token-leakage rules (do not weaken):
  * The helper's stdout may contain the pairing token (create / show-token). It
    is returned to the caller and is NEVER logged, NEVER placed in an exception
    message, and NEVER cached. Only the generic stderr and the exit code are
    logged on failure.
"""
from __future__ import annotations

import asyncio
import logging

from backend.conduit.errors import (
    ConduitPermissionError,
    PersonalCompartmentError,
    PersonalDivergenceError,
    PersonalValidationError,
)
from backend.conduit.models import PersonalCompartmentStatus

logger = logging.getLogger(__name__)

# --- Hardcoded invocation constants (never built from user input) ----------
_SUDO = "sudo"
_RUNAS_USER = "conduit"
_HELPER_PATH = "/opt/conduit-cc/bin/ccc-personal-compartment"

_NAME_MAX = 32                 # light pre-check; the helper re-validates
_TIMEOUT_S = 40.0              # above the helper's own 30s conduit timeout

# Helper exit codes (see deployment/bin/ccc-personal-compartment).
_EXIT_USAGE = 2
_EXIT_DIVERGENCE = 5


# ---------------------------------------------------------------------------
# Private runner + mapping (the token must never be logged here)
# ---------------------------------------------------------------------------

async def _run_helper(subcommand: str, stdin_text: str | None = None) -> tuple[int, str, str]:
    """Run one helper subcommand. Returns (returncode, stdout, stderr).

    The display name (when present) is delivered on STDIN, so it never appears
    in argv / the process list. stdout is captured and returned UNLOGGED.
    """
    args = [_SUDO, "-u", _RUNAS_USER, _HELPER_PATH, subcommand]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise PersonalCompartmentError(
            "could not run the personal compartment helper"
        ) from exc

    payload = stdin_text.encode("utf-8") if stdin_text is not None else None
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(input=payload), timeout=_TIMEOUT_S
        )
    except asyncio.TimeoutError as exc:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise PersonalCompartmentError(
            "personal compartment helper timed out"
        ) from exc

    rc = proc.returncode if proc.returncode is not None else -1
    # stdout is NOT stripped/inspected here and is NEVER logged (it may be the
    # pairing token). stderr is generic (the helper scrubs it) and safe to log.
    return rc, out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace").strip()


def _looks_like_permission_denied(stderr: str) -> bool:
    s = stderr.lower()
    return (
        "is not allowed to execute" in s
        or "is not in the sudoers" in s
        or "a password is required" in s
        or "sudo: a terminal is required" in s
    )


def _raise_for_exit(rc: int, stderr: str) -> None:
    """Map a non-zero helper exit to a typed exception. Never includes the
    token or stdout; messages are generic and operator-safe."""
    if rc == 0:
        return
    if _looks_like_permission_denied(stderr):
        raise ConduitPermissionError(
            "not permitted to run the personal compartment helper (check sudoers)"
        )
    if rc == _EXIT_USAGE:
        raise PersonalValidationError(
            "the personal compartment helper rejected the input"
        )
    if rc == _EXIT_DIVERGENCE:
        raise PersonalDivergenceError(
            "personal pairing token format mismatch; an update is required"
        )
    # exit 3 (fs/safety), 4 (conduit), or any other non-zero.
    raise PersonalCompartmentError("personal compartment helper operation failed")


def _parse_status(stdout: str) -> PersonalCompartmentStatus:
    vals: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            vals[key.strip()] = value.strip().lower()
    if not {"exists", "valid", "backup"} <= vals.keys():
        raise PersonalCompartmentError(
            "malformed status output from the personal compartment helper"
        )
    return PersonalCompartmentStatus(
        exists=vals["exists"] == "true",
        valid=vals["valid"] == "true",
        backup=vals["backup"] == "true",
    )


def _parse_token(stdout: str) -> str:
    token = stdout.strip()
    if not token or "\n" in token:
        raise PersonalCompartmentError(
            "the personal compartment helper returned no token"
        )
    return token


def _precheck_name(name: str) -> str:
    """Light, fast pre-check (the helper re-validates authoritatively)."""
    n = (name or "").strip()
    if not n:
        raise PersonalValidationError("display name is required")
    if len(n) > _NAME_MAX:
        raise PersonalValidationError(
            f"display name must be at most {_NAME_MAX} characters"
        )
    return n


# ---------------------------------------------------------------------------
# Public adapter interface (stateless)
# ---------------------------------------------------------------------------

async def personal_status() -> PersonalCompartmentStatus:
    """Return the structural compartment state (exists / valid / backup)."""
    rc, out, err = await _run_helper("status")
    if rc != 0:
        logger.error("personal status failed (rc=%d): %s", rc, err)
        _raise_for_exit(rc, err)
    return _parse_status(out)


async def personal_create(name: str) -> str:
    """Create (or regenerate) the compartment; return the pairing token.

    The token is returned to the caller and retained nowhere. Never logged.
    """
    n = _precheck_name(name)
    rc, out, err = await _run_helper("create", stdin_text=n)
    if rc != 0:
        logger.error("personal create failed (rc=%d): %s", rc, err)
        _raise_for_exit(rc, err)
    logger.info("personal compartment create ok")
    return _parse_token(out)


async def personal_restore() -> None:
    """Restore the previous compartment from the helper's .bak."""
    rc, _out, err = await _run_helper("restore-bak")
    if rc != 0:
        logger.error("personal restore failed (rc=%d): %s", rc, err)
        _raise_for_exit(rc, err)
    logger.info("personal compartment restore ok")


async def personal_show_token(name: str) -> str:
    """Rebuild and return the pairing token for the existing compartment.

    The token is returned to the caller and retained nowhere. Never logged.
    """
    n = _precheck_name(name)
    rc, out, err = await _run_helper("show-token", stdin_text=n)
    if rc != 0:
        logger.error("personal show-token failed (rc=%d): %s", rc, err)
        _raise_for_exit(rc, err)
    return _parse_token(out)
