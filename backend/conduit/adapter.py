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
import pathlib
import re
import urllib.error
import urllib.request
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
# Public API -- pairing (no sudo required; calls conduit binary directly)
# ---------------------------------------------------------------------------


async def pair(pairing_link: str) -> dict:
    """
    Submit a Psiphon Conduit pairing link via stdin to the Conduit CLI.

    SECURITY CONTRACT -- this function must never violate the following:
      - The pairing_link parameter is NEVER passed to any logger call.
      - The pairing_link is NEVER included in exception messages.
      - The pairing_link is NEVER passed as a command-line argument (argv).
      - The pairing_link is NEVER written to any file, database, or cache.
      - Raw CLI stdout/stderr are NOT logged (may echo back link content).
        Only the return code is logged.
      - All response strings are static; none are derived from the link.

    The link is passed to the Conduit CLI via stdin only.  The process
    list (ps aux) will show only "conduit pair" without any link data.

    TODO: The Conduit CLI pairing interface (binary name, command name,
    and whether it reads the pairing link from stdin) MUST be verified
    on a real Conduit installation before this is used in production.
    If the CLI does not read from stdin, this implementation will return
    {"status": "failed"} -- it will NOT fall back to argv.

    TODO: The Psiphon pairing link format must be validated against
    Psiphon documentation.  The current caller validates only structural
    constraints (non-empty, max 4096 chars, no control characters).
    A format-specific regex should be added once the format is confirmed.

    Parameters
    ----------
    pairing_link : str
        The Psiphon Conduit pairing link.  Caller is responsible for
        structural validation.  This value must never be logged.

    Returns
    -------
    dict with keys:
        status  : "paired" | "failed"
        message : static operator-facing string (never link-derived)

    Raises
    ------
    ConduitAdapterError
        Conduit binary not found, or an unexpected error occurred.
        Message is safe for API response.
    """
    timeout_s: float = get_app_config().conduit_action_timeout_seconds

    # Log intent only -- the link value is intentionally absent.
    logger.info("Conduit pair: submitting pairing link via stdin")

    try:
        proc = await asyncio.create_subprocess_exec(
            "conduit", "pair",
            # Link is written to stdin -- NEVER passed as argv.
            # This prevents the link from appearing in "ps aux" output.
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _stdout_b, _stderr_b = await asyncio.wait_for(
                proc.communicate(input=pairing_link.encode("utf-8")),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            # Kill the process so it does not linger as a zombie.
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "Conduit pair: timed out after %.1fs", timeout_s
            )
            return {"status": "failed", "message": "Pairing timed out."}

        returncode = proc.returncode if proc.returncode is not None else -1

        if returncode == 0:
            logger.info("Conduit pair: completed successfully (rc=0)")
            return {"status": "paired", "message": "Conduit paired successfully."}

        # Non-zero exit -- do NOT log stdout/stderr: the CLI may echo back
        # all or part of the pairing link in its output.
        logger.warning(
            "Conduit pair: CLI returned non-zero exit code (rc=%d). "
            "Check Conduit service logs for details.",
            returncode,
        )
        return {"status": "failed", "message": "Pairing failed. Check server logs."}

    except FileNotFoundError:
        logger.warning(
            "'conduit' binary not found in PATH -- pairing unavailable. "
            "Verify the Conduit CLI is installed and on PATH."
        )
        raise ConduitAdapterError(
            "Conduit binary not found. Is Conduit installed?"
        )

    except Exception as exc:  # noqa: BLE001
        # Log exception type only -- do NOT log str(exc) in case it
        # contains request-context data from the calling frame.
        logger.error(
            "Conduit pair: unexpected error (type=%s)",
            type(exc).__name__,
        )
        raise ConduitAdapterError(
            "Pairing failed due to an unexpected error. Check server logs."
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
