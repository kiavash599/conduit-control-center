"""
backend/conduit/adapter.py
--------------------------
Systemd adapter for the Conduit service.

This is the ONLY module in the codebase that calls systemctl or reads
Conduit process output. All other code interacts with Conduit through
this adapter.

Public API
----------
    get_status()        -> ConduitStatus  ("running"|"stopped"|"starting"|
                                           "stopping"|"error")
    start()             -> ActionResult   (waited up to timeout for "running")
    stop()              -> ActionResult   (waited up to timeout for "stopped")
    restart()           -> ActionResult   (waited up to timeout for "running")
    get_last_changed()  -> str | None     (ISO 8601 UTC, or None)
    get_version()       -> str | None     (stub; implemented in Issue #18)

Exceptions
----------
    ConduitAdapterError    -- base; any adapter failure with a safe message
    ConduitPermissionError -- sudo/systemctl denied (sudoers rule missing or
                              misconfigured; install.sh has not been run)

Privilege model
---------------
READ-ONLY commands (get_status, get_last_changed) call systemctl directly:

    ["systemctl", "is-active", service_name]
    ["systemctl", "show",      service_name, "--property=..."]

On Ubuntu 22.04, non-root users can run these without any special privilege.
They do NOT require sudo.

STATE-CHANGING commands (start, stop, restart) call sudo systemctl:

    ["sudo", "systemctl", "start",   service_name]
    ["sudo", "systemctl", "stop",    service_name]
    ["sudo", "systemctl", "restart", service_name]

sudo is invoked explicitly in application code because the FastAPI service
runs as a non-root account (conduit-cc) that does not inherently have
systemd management privilege.

Required sudoers rule
---------------------
install.sh must create /etc/sudoers.d/conduit-cc with the following content
(validated with 'visudo -c' before activation):

    conduit-cc ALL=(root) NOPASSWD: /bin/systemctl start conduit
    conduit-cc ALL=(root) NOPASSWD: /bin/systemctl stop conduit
    conduit-cc ALL=(root) NOPASSWD: /bin/systemctl restart conduit

IMPORTANT -- service name coupling:
The service name in the sudoers rule must exactly match conduit_service_name
in config.json (default: "conduit"). Sudo performs exact command matching,
so if config.json says "psiphon-conduit" but the rule says "conduit", the
sudo call will be denied.

If the service name in config.json is changed:
  1. Update /etc/sudoers.d/conduit-cc with the new name.
  2. Validate with: sudo visudo -c -f /etc/sudoers.d/conduit-cc
  3. Reload: sudo systemctl daemon-reload

Future improvement (v1.0+)
--------------------------
A thin privileged wrapper script (/usr/local/bin/conduit-ctl) would decouple
the Python adapter from sudo entirely. The adapter would call
["sudo", "conduit-ctl", "start"] and the script would validate the action
before passing it to systemctl. This separates privilege management from
service communication, makes the adapter easier to unit-test, and allows
switching the privilege mechanism (e.g. to polkit) without touching Python
code. Not implemented in v0.1 to avoid unnecessary complexity for beginners.

Security notes
--------------
- State-changing calls prepend ["sudo"] to the argument list. The service name
  comes from get_app_config() (config.json), never from user input. There is
  no injection path.
- All subprocess calls use explicit argument lists. shell=False is the
  asyncio.create_subprocess_exec default and is never overridden here.
- Raw stderr from sudo/systemctl is logged at ERROR level on the server and
  is never included in exception messages or API responses.
- asyncio.create_subprocess_exec and asyncio.sleep are used throughout so
  the FastAPI event loop is never blocked.
- The sudoers rule uses exact command matching (no wildcards). The service
  account cannot use this rule to control any service other than the one
  named in the rule.

systemctl exit codes
--------------------
    0  -- command succeeded / service is active
    1  -- generic failure / permission denied / service not found
    3  -- service is inactive (is-active only)
    4  -- unit file not found

systemctl is-active output -> ConduitStatus mapping
----------------------------------------------------
    active       -> running
    activating   -> starting
    deactivating -> stopping
    inactive     -> stopped
    failed       -> error
    unknown      -> error
    (anything else) -> error
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Literal

from backend.config import get_app_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ConduitStatus = Literal["running", "stopped", "starting", "stopping", "error"]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConduitAdapterError(Exception):
    """
    Base exception for all adapter failures.

    The message is safe for operator display and API responses.
    Raw stderr from systemctl is never included here; it is logged separately.
    """


class ConduitPermissionError(ConduitAdapterError):
    """
    Raised when sudo/systemctl denies the operation due to insufficient
    privilege.

    Most common cause: the sudoers rule in /etc/sudoers.d/conduit-cc is
    missing, has the wrong service name, or install.sh has not been run.

    API callers should return HTTP 503 with a message indicating that the
    server is not configured for service control, so operators know to
    check the sudoers rule rather than the service itself.
    """


# ---------------------------------------------------------------------------
# Internal constants and helpers
# ---------------------------------------------------------------------------

# Poll interval and timeout for start/stop/restart wait loops.
_POLL_INTERVAL_S: float = 0.5
_ACTION_TIMEOUT_S: float = 5.0

# Version detection: timeout and semver pattern.
# The result is cached after the first attempt so we do not shell out on
# every status request. _version_checked=True means we have tried at least
# once; _version_cache holds the result (None = not determinable).
# Note: this is a module-level cache. CCC runs with --workers 1 so there
# is only one process; per-worker caching is correct and sufficient.
_VERSION_TIMEOUT_S: float = 2.0
_VERSION_PATTERN: re.Pattern[str] = re.compile(r"\d+\.\d+\.\d+")
_version_checked: bool = False
_version_cache: str | None = None

# Map systemctl is-active output strings to ConduitStatus values.
_SYSTEMCTL_STATUS_MAP: dict[str, ConduitStatus] = {
    "active":       "running",
    "activating":   "starting",
    "deactivating": "stopping",
    "inactive":     "stopped",
    "failed":       "error",
    "unknown":      "error",
}


def _service_name() -> str:
    """Return the configured Conduit service name from config.json."""
    return get_app_config().conduit_service_name


async def _run(args: list[str]) -> tuple[int, str, str]:
    """
    Run an external command and return (returncode, stdout, stderr).

    Parameters
    ----------
    args : list[str]
        Full command including any "sudo" prefix. MUST be a plain list --
        never a shell string. The service name from config.json may appear
        here but user-submitted input must never appear.

    Returns
    -------
    (returncode, stdout_text, stderr_text)
        All text is stripped of leading/trailing whitespace.

    Notes
    -----
    Uses asyncio.create_subprocess_exec (shell=False by definition) so the
    FastAPI event loop is not blocked while the subprocess runs.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    returncode = proc.returncode if proc.returncode is not None else -1
    return (
        returncode,
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
    )


def _check_permission_denied(returncode: int, stderr: str) -> bool:
    """
    Return True if the subprocess output indicates a permission denial.

    Checks both sudo and systemctl/polkit error patterns so this works
    whether the call went through sudo or direct systemctl.
    """
    if returncode == 0:
        return False
    stderr_lower = stderr.lower()
    return (
        "permission denied" in stderr_lower
        or "access denied" in stderr_lower
        or "interactive authentication required" in stderr_lower
        or "polkit" in stderr_lower
        or "sorry, user" in stderr_lower       # sudo: "sorry, user X is not allowed"
        or "is not in the sudoers" in stderr_lower
        or "sudoers" in stderr_lower
    )


# ---------------------------------------------------------------------------
# Public API -- read-only (no sudo)
# ---------------------------------------------------------------------------


async def get_status() -> ConduitStatus:
    """
    Return the current Conduit service status.

    Calls: systemctl is-active <service>   (no sudo -- read-only)

    Returns
    -------
    ConduitStatus
        One of: "running", "stopped", "starting", "stopping", "error"

    Raises
    ------
    ConduitAdapterError
        systemctl could not be executed or the unit file was not found.
    ConduitPermissionError
        Unexpected permission denial on a read-only command. Rare on
        Ubuntu 22.04 but handled for completeness.
    """
    service = _service_name()
    returncode, stdout, stderr = await _run(["systemctl", "is-active", service])

    if returncode == 4:
        logger.warning(
            "systemctl is-active %r: unit not found (rc=4, stderr=%r)",
            service, stderr,
        )
        raise ConduitAdapterError(
            f"Conduit service '{service}' was not found. "
            "Is Conduit installed?"
        )

    if _check_permission_denied(returncode, stderr):
        logger.error(
            "systemctl is-active %r: permission denied (rc=%d, stderr=%r)",
            service, returncode, stderr,
        )
        raise ConduitPermissionError(
            "Insufficient privilege to read Conduit service status. "
            "This is unexpected for a read-only command on Ubuntu 22.04. "
            "Check system configuration."
        )

    raw = stdout.lower()
    status = _SYSTEMCTL_STATUS_MAP.get(raw, "error")

    logger.debug(
        "systemctl is-active %r: %r -> %r (rc=%d)",
        service, raw, status, returncode,
    )
    return status


async def get_last_changed() -> str | None:
    """
    Return the last time Conduit entered the active (running) state.

    Calls: systemctl show <service> --property=ActiveEnterTimestamp
    (no sudo -- read-only)

    Returns
    -------
    str | None
        ISO 8601 UTC string (e.g. "2026-05-31T14:30:00Z"), or None if
        the timestamp is not available or cannot be parsed.

    Raises
    ------
    ConduitAdapterError
        systemctl show failed with an unexpected error.
    ConduitPermissionError
        Unexpected permission denial on a read-only command.
    """
    service = _service_name()
    returncode, stdout, stderr = await _run(
        ["systemctl", "show", service, "--property=ActiveEnterTimestamp"]
    )

    if returncode != 0:
        if _check_permission_denied(returncode, stderr):
            logger.error(
                "systemctl show %r: permission denied (rc=%d, stderr=%r)",
                service, returncode, stderr,
            )
            raise ConduitPermissionError(
                "Insufficient privilege to read Conduit service properties. "
                "This is unexpected for a read-only command on Ubuntu 22.04. "
                "Check system configuration."
            )
        logger.error(
            "systemctl show %r failed (rc=%d, stderr=%r)",
            service, returncode, stderr,
        )
        raise ConduitAdapterError(
            "Could not read Conduit service properties."
        )

    # stdout: "ActiveEnterTimestamp=Sat 2026-05-31 14:30:00 UTC"
    # An unstarted service returns "ActiveEnterTimestamp=" (empty value).
    value = ""
    for line in stdout.splitlines():
        if line.startswith("ActiveEnterTimestamp="):
            value = line.split("=", 1)[1].strip()
            break

    if not value:
        logger.debug(
            "systemctl show %r: ActiveEnterTimestamp is empty "
            "(service never started or timestamp unavailable)",
            service,
        )
        return None

    # Parse "Sat 2026-05-31 14:30:00 UTC" -> ISO 8601 UTC.
    # Split off the optional day-of-week prefix.
    try:
        parts = value.split()
        # ["Sat", "2026-05-31", "14:30:00", "UTC"]  (4 parts with weekday)
        # ["2026-05-31", "14:30:00", "UTC"]          (3 parts without)
        if len(parts) == 4:
            date_str = f"{parts[1]} {parts[2]}"
        elif len(parts) == 3:
            date_str = f"{parts[0]} {parts[1]}"
        else:
            raise ValueError(f"Unexpected timestamp format: {value!r}")

        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.debug(
            "systemctl show %r: ActiveEnterTimestamp -> %r", service, iso
        )
        return iso

    except (ValueError, IndexError) as exc:
        logger.warning(
            "systemctl show %r: could not parse ActiveEnterTimestamp=%r (%s)",
            service, value, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Public API -- state-changing (sudo required)
# ---------------------------------------------------------------------------


async def start() -> dict:
    """
    Start the Conduit service and wait up to _ACTION_TIMEOUT_S for "running".

    Calls: sudo systemctl start <service>

    Requires the sudoers rule for "systemctl start <service_name>".
    See the module docstring for the required sudoers entry.

    Returns
    -------
    dict with keys:
        success : bool           -- True if "running" was reached
        status  : ConduitStatus  -- final observed status
        message : str            -- operator-friendly description

    Raises
    ------
    ConduitPermissionError  -- sudoers rule missing or service name mismatch
    ConduitAdapterError     -- other systemctl failure
    """
    return await _control_action("start", "running")


async def stop() -> dict:
    """
    Stop the Conduit service and wait up to _ACTION_TIMEOUT_S for "stopped".

    Calls: sudo systemctl stop <service>

    Requires the sudoers rule for "systemctl stop <service_name>".

    Returns
    -------
    dict  (same schema as start())

    Raises
    ------
    ConduitPermissionError  -- sudoers rule missing or service name mismatch
    ConduitAdapterError     -- other systemctl failure
    """
    return await _control_action("stop", "stopped")


async def restart() -> dict:
    """
    Restart the Conduit service and wait up to _ACTION_TIMEOUT_S for "running".

    Calls: sudo systemctl restart <service>

    Requires the sudoers rule for "systemctl restart <service_name>".

    Note: Issue #17 spec does not require a post-restart wait, but returning
    before the service is up would give callers a false success signal. This
    implementation always waits for "running". Documented deviation from spec.

    Returns
    -------
    dict  (same schema as start())

    Raises
    ------
    ConduitPermissionError  -- sudoers rule missing or service name mismatch
    ConduitAdapterError     -- other systemctl failure
    """
    return await _control_action("restart", "running")


async def get_version() -> str | None:
    """
    Return the installed Conduit version string, or None if not determinable.

    Strategy: run 'conduit --version' and extract the first semver-like
    string (X.Y.Z) from stdout. Falls back to None if:
      - the conduit binary is not in PATH
      - the command times out (> _VERSION_TIMEOUT_S seconds)
      - the output does not contain a recognisable version string
      - any other unexpected error occurs

    The result is cached after the first attempt. Subsequent calls return
    the cached value without shelling out. If detection failed, None is
    returned on all subsequent calls until the service restarts.

    IMPORTANT: this has not been validated against a real Conduit installation.
    The binary name ('conduit'), the --version flag, and the output format
    must be confirmed on a device with Conduit installed. If 'conduit --version'
    is not the correct invocation, update this function accordingly.

    Returns
    -------
    str | None
        Semver string (e.g. "1.2.3") or None if not determinable.
    """
    global _version_checked, _version_cache  # noqa: PLW0603

    if _version_checked:
        return _version_cache

    try:
        returncode, stdout, stderr = await asyncio.wait_for(
            _run(["conduit", "--version"]),
            timeout=_VERSION_TIMEOUT_S,
        )
        if returncode == 0 and stdout:
            match = _VERSION_PATTERN.search(stdout)
            if match:
                _version_cache = match.group(0)
                logger.debug("Conduit version detected: %r", _version_cache)
            else:
                logger.warning(
                    "conduit --version output did not contain a semver string "
                    "(stdout=%r) -- version unavailable. Validate on a device "
                    "with Conduit installed.",
                    stdout,
                )
        else:
            logger.warning(
                "conduit --version returned rc=%d (stderr=%r) -- "
                "version unavailable.",
                returncode, stderr,
            )

    except asyncio.TimeoutError:
        logger.warning(
            "conduit --version timed out after %.1fs -- version unavailable.",
            _VERSION_TIMEOUT_S,
        )
    except FileNotFoundError:
        logger.warning(
            "'conduit' binary not found in PATH -- version unavailable. "
            "Validate the binary name and PATH on a device with Conduit installed."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_version() encountered an unexpected error: %s -- "
            "version unavailable.",
            exc,
        )
    finally:
        _version_checked = True

    return _version_cache


# Internal: shared action logic for start / stop / restart
# ---------------------------------------------------------------------------


async def _control_action(action: str, desired_status: ConduitStatus) -> dict:
    """
    Run 'sudo systemctl <action> <service>' then poll for desired_status.

    sudo is prepended here -- and ONLY here. Read-only functions (get_status,
    get_last_changed) call systemctl directly without sudo.

    Parameters
    ----------
    action         : "start" | "stop" | "restart"
    desired_status : ConduitStatus to wait for after the command

    Returns
    -------
    dict with keys:
        success : bool
        status  : ConduitStatus  (final observed status)
        message : str

    Raises
    ------
    ConduitPermissionError
        sudo denied the command. Most likely cause: the sudoers rule in
        /etc/sudoers.d/conduit-cc is missing or has the wrong service name.
        Remember: the service name in config.json must exactly match the
        name in the sudoers rule. If you change one, change the other and
        re-run install.sh.
    ConduitAdapterError
        systemctl returned a non-permission failure (e.g. service not found,
        dependency failure).
    """
    service = _service_name()
    logger.info("Conduit adapter: sudo systemctl %r %r", action, service)

    # sudo is prepended here intentionally. See module docstring for the
    # required sudoers rule and the service-name coupling requirement.
    returncode, _stdout, stderr = await _run(
        ["sudo", "systemctl", action, service]
    )

    if returncode != 0:
        if _check_permission_denied(returncode, stderr):
            logger.error(
                "sudo systemctl %r %r: permission denied (rc=%d, stderr=%r). "
                "Check /etc/sudoers.d/conduit-cc -- service name in the rule "
                "must match conduit_service_name in config.json (%r).",
                action, service, returncode, stderr, service,
            )
            raise ConduitPermissionError(
                f"Insufficient privilege to {action} the Conduit service. "
                "Run install.sh to configure the required sudoers rule, "
                "or verify that the service name in config.json matches "
                "the name in /etc/sudoers.d/conduit-cc."
            )
        logger.error(
            "sudo systemctl %r %r failed (rc=%d, stderr=%r)",
            action, service, returncode, stderr,
        )
        raise ConduitAdapterError(
            f"Failed to {action} the Conduit service. "
            "Check server logs for details."
        )

    # Poll for desired_status up to _ACTION_TIMEOUT_S.
    elapsed = 0.0
    final_status: ConduitStatus = "error"

    while elapsed < _ACTION_TIMEOUT_S:
        await asyncio.sleep(_POLL_INTERVAL_S)
        elapsed += _POLL_INTERVAL_S
        try:
            final_status = await get_status()
        except ConduitAdapterError:
            # Transient error during poll -- keep waiting
            continue

        if final_status == desired_status:
            logger.info(
                "Conduit %r succeeded: status=%r after %.1fs",
                action, final_status, elapsed,
            )
            return {
                "success": True,
                "status": final_status,
                "message": f"Conduit {action} successful.",
            }

    # Timeout: command was sent but desired state not reached.
    logger.warning(
        "Conduit %r timed out after %.1fs: final_status=%r (wanted %r)",
        action, elapsed, final_status, desired_status,
    )
    return {
        "success": False,
        "status": final_status,
        "message": (
            f"Conduit {action} command was sent but the service did not "
            f"reach '{desired_status}' within {_ACTION_TIMEOUT_S:.0f}s. "
            f"Current status: '{final_status}'."
        ),
    }
