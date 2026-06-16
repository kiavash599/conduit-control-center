"""
backend/conduit/adapter.py
--------------------------
Systemd adapter for the Conduit service.

This is the ONLY module in the codebase that calls systemctl or reads
Conduit process output. All other code interacts with Conduit through
this adapter.

Public API
----------
    get_status()           -> ConduitStatus  ("running"|"stopped"|"starting"|
                                              "stopping"|"error")
    start()                -> ActionResult   (waited up to timeout for "running")
    stop()                 -> ActionResult   (waited up to timeout for "stopped")
    restart()              -> ActionResult   (waited up to timeout for "running")
    get_last_changed()     -> str | None     (ISO 8601 UTC, or None)
    get_version()          -> str | None     (stub; implemented in Issue #18)
    get_traffic_metrics()  -> dict | None    (bytes_uploaded/bytes_downloaded, or None)

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
import os
import pathlib
import re
import shlex
import stat
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Literal

from backend.config import get_app_config
from backend.conduit.models import ConduitConfigView, ConfigField
from backend.traffic.models import CounterReading, NodeRuntime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ConduitStatus = Literal["running", "stopped", "starting", "stopping", "error"]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
# The exception hierarchy lives in backend.conduit.errors (a dependency-free
# module) so lean consumers like the traffic collector can catch adapter errors
# without importing this module's heavy dependency chain. Re-exported here so
# existing ``from backend.conduit.adapter import ConduitAdapterError`` imports
# (and read_counters() raising these) continue to work unchanged.
from backend.conduit.errors import (  # noqa: E402  (kept beside the section it documents)
    ConduitAdapterError,
    ConduitPermissionError,
    ConduitUnreachableError,
    MetricsContractError,
)

__all__ = [
    "ConduitAdapterError",
    "ConduitPermissionError",
    "ConduitUnreachableError",
    "MetricsContractError",
]


# ---------------------------------------------------------------------------
# Internal constants and helpers
# ---------------------------------------------------------------------------

# Poll interval and timeout for start/stop/restart wait loops.
_POLL_INTERVAL_S: float = 0.5
# _ACTION_TIMEOUT_S removed: timeout now read from get_app_config().conduit_action_timeout_seconds

# Version detection: paths, timeout, and semver pattern.
# The result is cached after the first successful or fully-exhausted attempt
# so we do not shell out on every status request. _version_checked=True means
# we have tried at least once; _version_cache holds the result (None = not
# determinable).
# Note: this is a module-level cache. CCC runs with --workers 1 so there
# is only one process; per-worker caching is correct and sufficient.
#
# These paths must match CONDUIT_BIN_DIR in install.sh.  All three install
# options (Option A download, Option B local copy, Option C PATH) result in
# the binary landing at _CONDUIT_BIN_PATH after install.sh runs.
_CONDUIT_VERSION_FILE: str = "/opt/conduit/version"
_CONDUIT_BIN_PATH: str = "/opt/conduit/conduit"
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
    # Prepend "env TZ=UTC" so that systemd always returns the timestamp in UTC
    # regardless of the server's local timezone setting.  Without this, a Pi
    # configured for CEST (UTC+2) returns "23:57:11 CEST" which the parser
    # would stamp as UTC, producing a negative delta and a permanent "0s" uptime.
    returncode, stdout, stderr = await _run(
        ["env", "TZ=UTC", "systemctl", "show", service, "--property=ActiveEnterTimestamp"]
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


# ---------------------------------------------------------------------------
# Shared implementation for the state-changing actions (sudo required)
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

    # Read timeout from config; operator-configurable via conduit_action_timeout_seconds in config.json.
    timeout_s: float = get_app_config().conduit_action_timeout_seconds

    # Poll for desired_status up to timeout_s.
    elapsed = 0.0
    final_status: ConduitStatus = "error"

    while elapsed < timeout_s:
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
            f"reach '{desired_status}' within {timeout_s:.0f}s. "
            f"Current status: '{final_status}'."
        ),
    }


async def start() -> dict:
    """
    Start the Conduit service and wait up to conduit_action_timeout_seconds (config.json) for "running".

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
    Stop the Conduit service and wait up to conduit_action_timeout_seconds (config.json) for "stopped".

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
    Restart the Conduit service and wait up to conduit_action_timeout_seconds (config.json) for "running".

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

    Detection strategy (in priority order):

    1. Read _CONDUIT_VERSION_FILE (/opt/conduit/version) — a plain-text file
       written by install.sh (Phase 2x-d) and kept current by update.sh.
       Fast file read; no subprocess required. This is the standard path for
       all install.sh-managed deployments.

    2. Run _CONDUIT_BIN_PATH (/opt/conduit/conduit) --version with an
       absolute path — PATH-independent. Covers manual setups that have the
       binary at the canonical location but no version file. Works even though
       the conduit-cc service account has no /opt/conduit in its PATH.

    3. Run 'conduit --version' from PATH — fallback for non-standard
       installations where the Conduit binary is in the system PATH but not
       at the canonical install location.

    Falls back to None if all three strategies fail. The failure is non-fatal:
    the API returns null and the dashboard shows '—'. The result is cached
    after the first successful or fully-exhausted attempt; subsequent calls
    return the cached value immediately.

    Returns
    -------
    str | None
        Semver string (e.g. "2.0.0") or None if not determinable.
    """
    global _version_checked, _version_cache  # noqa: PLW0603

    if _version_checked:
        return _version_cache

    # ------------------------------------------------------------------
    # Step 1: Version file written by install.sh (fastest — no subprocess).
    # ------------------------------------------------------------------
    try:
        text = pathlib.Path(_CONDUIT_VERSION_FILE).read_text(encoding="utf-8").strip()
        match = _VERSION_PATTERN.search(text)
        if match:
            _version_cache = match.group(0)
            logger.debug(
                "Conduit version from %r: %r", _CONDUIT_VERSION_FILE, _version_cache
            )
            _version_checked = True
            return _version_cache
        logger.debug(
            "Version file %r exists but contains no semver string (%r) — "
            "trying binary",
            _CONDUIT_VERSION_FILE, text,
        )
    except OSError:
        logger.debug(
            "Version file %r not readable — trying binary", _CONDUIT_VERSION_FILE
        )

    # ------------------------------------------------------------------
    # Steps 2 and 3: shell out to the binary.
    # Try the canonical absolute path first, then the bare name (PATH).
    # ------------------------------------------------------------------
    for binary in [_CONDUIT_BIN_PATH, "conduit"]:
        try:
            returncode, stdout, stderr = await asyncio.wait_for(
                _run([binary, "--version"]),
                timeout=_VERSION_TIMEOUT_S,
            )
            if returncode == 0 and stdout:
                match = _VERSION_PATTERN.search(stdout)
                if match:
                    _version_cache = match.group(0)
                    logger.debug(
                        "Conduit version from binary %r: %r", binary, _version_cache
                    )
                    break
                logger.debug(
                    "Binary %r --version output has no semver (stdout=%r) — "
                    "trying next",
                    binary, stdout,
                )
            else:
                logger.debug(
                    "Binary %r --version returned rc=%d (stderr=%r) — trying next",
                    binary, returncode, stderr,
                )
        except asyncio.TimeoutError:
            logger.debug(
                "Binary %r --version timed out after %.1fs — trying next",
                binary, _VERSION_TIMEOUT_S,
            )
        except (FileNotFoundError, OSError):
            logger.debug("Binary %r not found or not executable — trying next", binary)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_version(): unexpected error with binary %r: %s", binary, exc
            )

    if _version_cache is None:
        logger.warning(
            "Conduit version not determinable: version file %r not readable and "
            "binary not found at %r or in PATH",
            _CONDUIT_VERSION_FILE,
            _CONDUIT_BIN_PATH,
        )

    _version_checked = True
    return _version_cache


# ---------------------------------------------------------------------------
# Public API -- pairing (NOT implemented in this release; safe stub)
# ---------------------------------------------------------------------------


async def pair(pairing_link: str) -> dict:
    """
    Safe stub -- Conduit pairing is NOT implemented in this release.

    There is no ``conduit pair`` subcommand in any current Conduit release, so
    this function must never attempt to invoke one. It immediately raises
    ``NotImplementedError`` and never:
      - spawns a subprocess,
      - logs, stores, or otherwise persists the pairing link, or
      - passes the link as a command-line argument.

    The ``pairing_link`` parameter is retained only to preserve the call
    signature for future Personal Mode work; it is never read, logged, or
    transmitted.

    Raises
    ------
    NotImplementedError
        Always. Pairing is deferred to future Personal Mode work.
    """
    raise NotImplementedError(
        "Conduit pairing is not implemented in this release."
    )


# ---------------------------------------------------------------------------
# Public API -- Prometheus traffic metrics  (Issue #22)
# ---------------------------------------------------------------------------

# Timeout for the localhost HTTP call to the Conduit metrics server.
# The endpoint is on the same host -- 2 seconds is already generous.
# Not configurable: an operator cannot meaningfully tune this value.
_METRICS_TIMEOUT_S: float = 2.0

# Prometheus metric names as defined by the Conduit CLI.
# Source: https://github.com/Psiphon-Inc/conduit/blob/main/cli/README.md
# Verified 2026-06-06.  If Psiphon renames these, update here and in
# docs/conduit-metrics-source.md.
_METRIC_BYTES_UPLOADED   = "conduit_bytes_uploaded"
_METRIC_BYTES_DOWNLOADED = "conduit_bytes_downloaded"


def _parse_prometheus_gauge(text: str, metric_name: str) -> int | None:
    """
    Extract a single unlabelled gauge value from a Prometheus text payload.

    Prometheus text format (simplified):
        # HELP conduit_bytes_uploaded ...
        # TYPE conduit_bytes_uploaded gauge
        conduit_bytes_uploaded 1073741824
        conduit_bytes_uploaded{scope="common",region="US"} 524288000

    We want only the unlabelled line.  The trailing-space check
    ``line.startswith(metric_name + " ")`` is the distinguishing rule:
    unlabelled lines have ``<name> <value>``, labelled lines have
    ``<name>{...} <value>``.  Lines starting with ``#`` are skipped
    because they begin with ``#``, not the metric name.

    Parameters
    ----------
    text        : full Prometheus text response body
    metric_name : exact metric name to look for (e.g. "conduit_bytes_uploaded")

    Returns
    -------
    int | None
        Gauge value rounded to the nearest integer, or None if not found or
        the value cannot be parsed as a number.
    """
    prefix = metric_name + " "
    for line in text.splitlines():
        if line.startswith(prefix):
            # Line format: "<name> <value>" or "<name> <value> <timestamp>"
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(float(parts[1]))
                except (ValueError, IndexError):
                    logger.warning(
                        "_parse_prometheus_gauge: could not parse value %r "
                        "for metric %r",
                        parts[1] if len(parts) > 1 else "<missing>",
                        metric_name,
                    )
                    return None
    return None


async def get_traffic_metrics() -> dict | None:
    """
    Fetch Conduit traffic counters from its local Prometheus metrics endpoint.

    Scrapes ``http://localhost:{conduit_metrics_port}/metrics`` and extracts:
      - ``conduit_bytes_uploaded``   -> bytes_uploaded
      - ``conduit_bytes_downloaded`` -> bytes_downloaded

    The metrics endpoint only exists if Conduit was started with the
    ``--metrics-addr :<port>`` flag.  See ``docs/conduit-metrics-source.md``
    and ``deployment/conduit.service`` for the required configuration.

    This function uses ``urllib.request.urlopen`` (stdlib) wrapped in
    ``asyncio.to_thread`` so the blocking HTTP call does not stall the
    FastAPI event loop.  No external HTTP library is required.

    Parameters
    ----------
    None.  The metrics port is read from ``get_app_config().conduit_metrics_port``.

    Returns
    -------
    dict | None
        On success: ``{"bytes_uploaded": int, "bytes_downloaded": int}``
        where either value may be ``None`` if the specific gauge was absent
        from the response.

        On any connection failure, timeout, or HTTP error: ``None``.
        Callers treat ``None`` as "metrics server not reachable" and
        respond with null byte fields at HTTP 200 -- not HTTP 503.

    Raises
    ------
    Never.  All exceptions are caught and logged; the function always returns.

    Parameters
    ----------
    None.  The metrics port is read from ``get_app_config().conduit_metrics_port``.

    Returns
    -------
    dict | None
        On success: ``{"bytes_uploaded": int, "bytes_downloaded": int}``
        where either value may be ``None`` if the specific gauge was absent
        from the response.

        On any connection failure, timeout, or HTTP error: ``None``.
        Callers treat ``None`` as "metrics server not reachable" and
        respond with null byte fields at HTTP 200 -- not HTTP 503.

    Raises
    ------
    Never.  All exceptions are caught and logged; the function always returns.
    """
    port = get_app_config().conduit_metrics_port
    url  = f"http://localhost:{port}/metrics"

    def _fetch() -> str:
        """Blocking urllib call -- runs in a thread via asyncio.to_thread."""
        with urllib.request.urlopen(url, timeout=_METRICS_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", errors="replace")

    try:
        text = await asyncio.to_thread(_fetch)
    except urllib.error.URLError as exc:
        # ConnectionRefusedError (metrics server not started) is wrapped here.
        logger.debug(
            "get_traffic_metrics: metrics endpoint %r not reachable (%s) -- "
            "returning None (Conduit may not be running or --metrics-addr "
            "was not passed at startup)",
            url,
            type(exc.reason).__name__ if hasattr(exc, "reason") else type(exc).__name__,
        )
        return None
    except OSError as exc:
        logger.debug(
            "get_traffic_metrics: OS error reaching %r (%s) -- returning None",
            url,
            exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_traffic_metrics: unexpected error fetching %r (type=%s) -- "
            "returning None",
            url,
            type(exc).__name__,
        )
        return None

    uploaded   = _parse_prometheus_gauge(text, _METRIC_BYTES_UPLOADED)
    downloaded = _parse_prometheus_gauge(text, _METRIC_BYTES_DOWNLOADED)

    logger.debug(
        "get_traffic_metrics: bytes_uploaded=%s bytes_downloaded=%s",
        uploaded,
        downloaded,
    )
    return {"bytes_uploaded": uploaded, "bytes_downloaded": downloaded}


# ---------------------------------------------------------------------------
# Public API -- typed counter reader for the traffic collector (P0 Step 0)
# ---------------------------------------------------------------------------
#
# read_counters() is the single, typed input to the traffic persistence
# collector. Unlike get_traffic_metrics() (which is forgiving and returns
# None/partial data for the dashboard), read_counters() is strict:
#   - required metrics missing/unparseable        -> MetricsContractError
#   - endpoint unreachable / non-2xx HTTP status  -> ConduitUnreachableError
# It never coerces a missing required counter to 0, because a fabricated zero
# would corrupt the delta ledger. It does not own ts/seq -- the collector
# assigns those.

# Additional Conduit metric names (see _METRIC_BYTES_* above).
_METRIC_UPTIME_SECONDS = "conduit_uptime_seconds"
_METRIC_BUILD_INFO     = "conduit_build_info"
_METRIC_IS_LIVE        = "conduit_is_live"

# Aggregate runtime gauges for the Contribution Advisor (A1.1). Read only as
# unlabelled scalars (via _extract_gauge_raw), so labelled per-scope series
# (e.g. conduit_connected_clients{scope="common"}) and per-region series
# (conduit_region_*) are never parsed — aggregate-only by construction.
_METRIC_CONNECTED_CLIENTS  = "conduit_connected_clients"
_METRIC_IDLE_SECONDS       = "conduit_idle_seconds"
_METRIC_MAX_COMMON_CLIENTS = "conduit_max_common_clients"

# build_rev is a label on the conduit_build_info gauge:
#   conduit_build_info{build_repo="...",build_rev="8531118",...} 1
_BUILD_REV_PATTERN: re.Pattern[str] = re.compile(r'build_rev="([^"]*)"')


def _extract_gauge_raw(text: str, metric_name: str) -> str | None:
    """
    Return the raw value token of an unlabelled gauge line, or None if absent.

    Same matching rule as ``_parse_prometheus_gauge`` (``"<name> <value>"``,
    skipping labelled ``<name>{...}`` and ``#`` comment lines) but returns the
    raw string so the caller can decide int vs float parsing.
    """
    prefix = metric_name + " "
    for line in text.splitlines():
        if line.startswith(prefix):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    return None


def _require_int(text: str, metric_name: str) -> int:
    """Parse a required cumulative byte counter; raise MetricsContractError on miss."""
    raw = _extract_gauge_raw(text, metric_name)
    if raw is None:
        raise MetricsContractError(
            f"Required Conduit metric '{metric_name}' is missing from the "
            "metrics response."
        )
    try:
        return int(float(raw))
    except (ValueError, OverflowError) as exc:
        raise MetricsContractError(
            f"Required Conduit metric '{metric_name}' has an unparseable value."
        ) from exc


def _require_float(text: str, metric_name: str) -> float:
    """Parse a required float gauge (uptime); raise MetricsContractError on miss."""
    raw = _extract_gauge_raw(text, metric_name)
    if raw is None:
        raise MetricsContractError(
            f"Required Conduit metric '{metric_name}' is missing from the "
            "metrics response."
        )
    try:
        return float(raw)
    except (ValueError, OverflowError) as exc:
        raise MetricsContractError(
            f"Required Conduit metric '{metric_name}' has an unparseable value."
        ) from exc


def _extract_build_rev(text: str) -> str | None:
    """Return the build_rev label from conduit_build_info, or None if absent."""
    for line in text.splitlines():
        if line.startswith(_METRIC_BUILD_INFO + "{"):
            match = _BUILD_REV_PATTERN.search(line)
            if match:
                return match.group(1)
    return None


def _parse_is_live(text: str) -> bool | None:
    """Return conduit_is_live as a bool, or None if absent/unparseable (optional)."""
    raw = _extract_gauge_raw(text, _METRIC_IS_LIVE)
    if raw is None:
        return None
    try:
        return bool(int(float(raw)))
    except (ValueError, OverflowError):
        return None


def _fetch_metrics_text(url: str) -> str:
    """Blocking urllib fetch of the Prometheus payload (run via asyncio.to_thread)."""
    with urllib.request.urlopen(url, timeout=_METRICS_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


async def read_counters() -> CounterReading:
    """
    Read Conduit's cumulative byte counters and uptime as a typed CounterReading.

    Scrapes ``http://localhost:{conduit_metrics_port}/metrics`` and extracts:
      - ``conduit_bytes_uploaded``   -> bytes_up        (required)
      - ``conduit_bytes_downloaded`` -> bytes_down      (required)
      - ``conduit_uptime_seconds``   -> uptime_seconds  (required, float)
      - ``conduit_build_info``       -> build_rev       (optional, None if absent)
      - ``conduit_is_live``          -> is_live         (optional, None if absent)

    Returns
    -------
    CounterReading

    Raises
    ------
    ConduitUnreachableError
        The metrics endpoint could not be reached (connection refused, timeout,
        or a non-2xx HTTP status). HTTPError is a URLError subclass and is
        therefore covered here.
    MetricsContractError
        A required metric was missing or unparseable. A fabricated zero is
        never returned, because it would corrupt the delta ledger.
    """
    port = get_app_config().conduit_metrics_port
    url = f"http://localhost:{port}/metrics"

    try:
        text = await asyncio.to_thread(_fetch_metrics_text, url)
    except (urllib.error.URLError, OSError) as exc:
        # URLError covers HTTPError (non-2xx) and connection failures;
        # OSError covers socket-level errors / timeouts not wrapped by urllib.
        logger.debug("read_counters: metrics endpoint %r unreachable (%s)", url, exc)
        raise ConduitUnreachableError(
            "Conduit metrics endpoint is unreachable."
        ) from exc

    bytes_up = _require_int(text, _METRIC_BYTES_UPLOADED)
    bytes_down = _require_int(text, _METRIC_BYTES_DOWNLOADED)
    uptime_seconds = _require_float(text, _METRIC_UPTIME_SECONDS)
    build_rev = _extract_build_rev(text)
    is_live = _parse_is_live(text)

    return CounterReading(
        bytes_up=bytes_up,
        bytes_down=bytes_down,
        uptime_seconds=uptime_seconds,
        build_rev=build_rev,
        is_live=is_live,
    )


# ---------------------------------------------------------------------------
# Public API -- forgiving runtime-gauge reader for the Contribution Advisor (A1.1)
# ---------------------------------------------------------------------------
#
# get_node_runtime() is the read-only, forgiving counterpart to read_counters().
# It scrapes the same Conduit Prometheus endpoint and extracts three aggregate
# runtime gauges. Unlike read_counters() it never raises: a missing/unparseable
# gauge becomes None on that field, and an unreachable endpoint returns None for
# the whole call (so the advisor can distinguish "Conduit not running" from
# "running, gauge absent"). Aggregate-only -- no region/scope/per-client data.

def _optional_int(text: str, metric_name: str) -> int | None:
    """Parse an unlabelled integer gauge; return None if absent/unparseable.

    Uses the same matching rule as _extract_gauge_raw (``"<name> <value>"``,
    skipping labelled ``<name>{...}`` and ``#`` comment lines), so labelled
    per-scope/per-region series are never matched. ``int(float(raw))`` tolerates
    values emitted as floats (e.g. ``"0.0"``). Never raises.
    """
    raw = _extract_gauge_raw(text, metric_name)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (ValueError, OverflowError):
        return None


async def get_node_runtime() -> NodeRuntime | None:
    """
    Forgiving, read-only read of Conduit's aggregate runtime gauges.

    Scrapes ``http://localhost:{conduit_metrics_port}/metrics`` and extracts:
      - ``conduit_connected_clients``   -> connected_clients
      - ``conduit_idle_seconds``        -> idle_seconds
      - ``conduit_max_common_clients``  -> max_common_clients

    Returns
    -------
    NodeRuntime | None
        ``None`` when the metrics endpoint is unreachable (Conduit stopped or
        ``--metrics-addr`` not configured). Otherwise a ``NodeRuntime`` whose
        fields are ``None`` individually for any gauge that is missing or
        unparseable. Aggregate-only: only the unlabelled scalars are read, so
        labelled per-scope and per-region series are never exposed.

    Raises
    ------
    Never. All transport and parse errors are handled internally.
    """
    port = get_app_config().conduit_metrics_port
    url = f"http://localhost:{port}/metrics"

    try:
        text = await asyncio.to_thread(_fetch_metrics_text, url)
    except (urllib.error.URLError, OSError) as exc:
        logger.debug("get_node_runtime: metrics endpoint %r unreachable (%s)", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_node_runtime: unexpected error fetching %r (type=%s) -- returning None",
            url,
            type(exc).__name__,
        )
        return None

    return NodeRuntime(
        connected_clients=_optional_int(text, _METRIC_CONNECTED_CLIENTS),
        idle_seconds=_optional_int(text, _METRIC_IDLE_SECONDS),
        max_common_clients=_optional_int(text, _METRIC_MAX_COMMON_CLIENTS),
    )


# ---------------------------------------------------------------------------
# Public API -- read-only Conduit configuration view (M1, §6.1)
# ---------------------------------------------------------------------------
#
# get_conduit_config_view() reports the two operator-tunable knobs in both their
# *configured* form (resolved from the unit ExecStart via `systemctl show`, no
# sudo) and their *effective* form (Conduit Prometheus gauges). Read-only and
# forgiving: every field degrades to None on miss; never raises. Introduces NO
# write, restart, or privileged operation.

_METRIC_BANDWIDTH_LIMIT = "conduit_bandwidth_limit_bytes_per_second"

# 1 Mbps (decimal) = 125_000 bytes/sec -- matches Conduit's --bandwidth flag.
_BYTES_PER_MBPS = 125_000


def _bps_to_mbps(bps: int | None) -> int | None:
    """Convert bytes/sec to integer decimal Mbps; None passes through."""
    if bps is None:
        return None
    return round(bps / _BYTES_PER_MBPS)


def _argv_from_execstart(blob: str | None) -> list[str]:
    """Tokenise the last ``argv[]=`` vector from `systemctl show -p ExecStart`.

    The structured output looks like::

        { path=/opt/conduit/conduit ; argv[]=/opt/conduit/conduit start \
          --max-common-clients 50 --bandwidth 40 ; ignore_errors=no ; ... }

    The last ``argv[]`` is used so a future drop-in that resets+redefines
    ExecStart (M2) is honoured. Tokenised with shlex (argv parsing), not a
    regex over the raw text. Never raises.
    """
    if not blob:
        return []
    marker = "argv[]="
    idx = blob.rfind(marker)
    if idx == -1:
        return []
    seg = blob[idx + len(marker):]
    end = seg.find(" ; ")          # argv[] ends at the next structured field
    if end != -1:
        seg = seg[:end]
    try:
        return shlex.split(seg)
    except ValueError:
        return seg.split()


def _flag_int(argv: list[str], flag: str) -> int | None:
    """Return the int value following ``flag`` in argv (``--f V`` or ``--f=V``).

    None if the flag is absent or its value is not an integer.
    """
    for i, tok in enumerate(argv):
        if tok == flag:
            if i + 1 < len(argv):
                try:
                    return int(argv[i + 1])
                except ValueError:
                    return None
            return None
        if tok.startswith(flag + "="):
            try:
                return int(tok.split("=", 1)[1])
            except ValueError:
                return None
    return None


async def _read_configured_execstart() -> str | None:
    """Resolved ExecStart for the conduit unit, or None. Read-only; no sudo.

    Backward-compat source for pre-M2 units that passed literal integer flags in
    ExecStart. Post-M2 the unit uses ``${CCC_*}`` braced vars, which
    ``systemctl show -p ExecStart`` prints *literally* (substitution happens at
    exec time), so configured values are read from the Environment instead (see
    _read_configured_environment). Never raises.
    """
    svc = get_app_config().conduit_service_name
    try:
        rc, out, _err = await _run(["systemctl", "show", svc, "--property=ExecStart"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("config view: systemctl show ExecStart failed (%s)", exc)
        return None
    return out if rc == 0 else None


async def _read_configured_environment() -> str | None:
    """Resolved Environment for the conduit unit, or None. Read-only; no sudo.

    `systemctl show -p Environment` returns the base unit's Environment= merged
    with any drop-in overrides (M2 writes CCC_MAX_COMMON_CLIENTS /
    CCC_BANDWIDTH_MBPS to conduit.service.d/ccc.conf). This is the authoritative
    *configured* (next-start) source post-M2. Never raises.
    """
    svc = get_app_config().conduit_service_name
    try:
        rc, out, _err = await _run(["systemctl", "show", svc, "--property=Environment"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("config view: systemctl show Environment failed (%s)", exc)
        return None
    return out if rc == 0 else None


def _parse_environment(blob: str | None) -> dict[str, str]:
    """Parse `Environment=KEY=VAL KEY2=VAL2` (systemctl show output) into a dict.

    Values may be shell-quoted by systemd; shlex handles that. Never raises.
    """
    if not blob:
        return {}
    line = blob.strip()
    if line.startswith("Environment="):
        line = line[len("Environment="):]
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    env: dict[str, str] = {}
    for tok in tokens:
        if "=" in tok:
            k, v = tok.split("=", 1)
            env[k] = v
    return env


def _env_int(env: dict[str, str], key: str) -> int | None:
    """Return env[key] as int, or None if absent/unparseable."""
    raw = env.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def get_conduit_config_view() -> ConduitConfigView:
    """Read-only configured+effective view of max-common-clients and bandwidth.

    Never raises. Effective values come from Conduit metrics (absent when the
    endpoint is unreachable). Configured values come from the resolved unit
    ExecStart (argv-parsed). No privileged/write/restart operation is performed.
    """
    port = get_app_config().conduit_metrics_port
    url = f"http://localhost:{port}/metrics"
    try:
        text: str | None = await asyncio.to_thread(_fetch_metrics_text, url)
    except Exception:  # noqa: BLE001
        text = None

    eff_mcc = _optional_int(text, _METRIC_MAX_COMMON_CLIENTS) if text else None
    eff_bw_bps = _optional_int(text, _METRIC_BANDWIDTH_LIMIT) if text else None

    # Configured values: Environment is authoritative post-M2 (ExecStart prints
    # the literal ${VAR}). Fall back to ExecStart argv only for pre-M2 units.
    env = _parse_environment(await _read_configured_environment())
    cfg_mcc = _env_int(env, "CCC_MAX_COMMON_CLIENTS")
    cfg_bw = _env_int(env, "CCC_BANDWIDTH_MBPS")
    if cfg_mcc is None or cfg_bw is None:
        argv = _argv_from_execstart(await _read_configured_execstart())
        if cfg_mcc is None:
            cfg_mcc = _flag_int(argv, "--max-common-clients")
        if cfg_bw is None:
            cfg_bw = _flag_int(argv, "--bandwidth")

    try:
        service_status: str = await get_status()
    except Exception:  # noqa: BLE001
        service_status = "unknown"

    return ConduitConfigView(
        service_status=service_status,
        max_common_clients=ConfigField(configured=cfg_mcc, effective=eff_mcc),
        bandwidth_mbps=ConfigField(
            configured=cfg_bw,
            effective=_bps_to_mbps(eff_bw_bps),
            unlimited_configured=(cfg_bw == -1),
            unlimited_effective=(eff_bw_bps == 0),
        ),
    )


# ---------------------------------------------------------------------------
# Privileged config write (M2) -- invokes the hardened root helper via sudo.
# ---------------------------------------------------------------------------
# CCC NEVER writes /etc/systemd/** or runs daemon-reload/restart directly. The
# only new privilege is one sudoers line for this exact helper, which validates
# its own input and writes only Environment= lines. argv-only; no shell.

_HELPER_PATH = "/opt/conduit-cc/bin/ccc-apply-conduit-config"


def helper_is_safe() -> bool:
    """True iff the root helper is a regular, root-owned file that is not group/
    other-writable (i.e. the app user cannot tamper with it). Read-only check.

    CCC startup / the apply endpoint use this to refuse to operate with a
    tampered or missing helper.
    """
    try:
        st = os.stat(_HELPER_PATH)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if st.st_uid != 0:
        return False
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False
    return True


async def _run_helper(*args: str) -> tuple[int, str]:
    """Run `sudo <helper> <args>` argv-only (no shell). Returns (rc, stderr).

    Never raises on subprocess failure: returns rc=-1 + a message instead.
    """
    cmd = ["sudo", _HELPER_PATH, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await proc.communicate()
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, err.decode("utf-8", errors="replace").strip()
    except OSError as exc:
        return -1, f"helper not runnable: {exc}"


async def apply_conduit_config(max_common_clients: int, bandwidth_mbps: int) -> tuple[int, str]:
    """Invoke the helper `apply` (write drop-in + daemon-reload + restart)."""
    return await _run_helper(
        "apply",
        "--max-common-clients", str(int(max_common_clients)),
        "--bandwidth-mbps", str(int(bandwidth_mbps)),
    )


async def rollback_conduit_config() -> tuple[int, str]:
    """Invoke the helper `rollback` (restore .bak or unlink + reload + restart)."""
    return await _run_helper("rollback")


async def verify_conduit_config_health(
    expected_mcc: int,
    expected_bw_mbps: int,
    *,
    timeout_s: float = 30.0,
    interval_s: float = 2.0,
) -> tuple[bool, str | None]:
    """Bounded poll of post-restart health. Required gates: service active,
    metrics reachable, and read-back of both knobs == requested. conduit_is_live
    is advisory and is intentionally NOT a gate (broker reconnect is slow).

    Returns (True, None) on success, else (False, last_failure_reason).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    reason = "verification did not start"
    while True:
        view = await get_conduit_config_view()
        reason = _health_reason(view, expected_mcc, expected_bw_mbps)
        if reason is None:
            return True, None
        if loop.time() >= deadline:
            return False, reason
        await asyncio.sleep(interval_s)


def _health_reason(
    view: ConduitConfigView, expected_mcc: int, expected_bw_mbps: int
) -> str | None:
    """None if healthy; otherwise the first failing required gate (pure)."""
    if view.service_status != "running":
        return f"service not active (status={view.service_status})"
    eff_mcc = view.max_common_clients.effective
    if eff_mcc is None:
        return "metrics endpoint unreachable after restart"
    if eff_mcc != expected_mcc:
        return f"max_common_clients read-back mismatch (got {eff_mcc}, want {expected_mcc})"
    bw = view.bandwidth_mbps
    if expected_bw_mbps == -1:
        if not bw.unlimited_effective:
            return "bandwidth read-back mismatch (expected unlimited)"
    elif bw.effective != expected_bw_mbps:
        return f"bandwidth read-back mismatch (got {bw.effective}, want {expected_bw_mbps})"
    return None
