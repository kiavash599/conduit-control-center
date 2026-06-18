# SPDX-License-Identifier: MIT
"""
backend/conduit/errors.py
-------------------------
Exception hierarchy for the Conduit adapter.

These live in a dependency-free module (no config / urllib / pydantic imports)
so that lean consumers — notably the traffic collector — can catch adapter
errors without importing the whole adapter module and its heavy dependency
chain. ``backend.conduit.adapter`` re-exports these names, so existing
``from backend.conduit.adapter import ConduitAdapterError`` imports continue to
work unchanged.
"""

from __future__ import annotations


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


class ConduitUnreachableError(ConduitAdapterError):
    """
    Raised by ``read_counters()`` when the Conduit Prometheus endpoint cannot
    be reached (connection refused, timeout, DNS, or a non-2xx HTTP status).

    Distinct from ``MetricsContractError``: this means "Conduit / the metrics
    server is down or unreachable", not "the metrics are malformed". The
    traffic collector treats this as a scrape gap (no delta, health failure),
    not a metric-format problem.
    """


class MetricsContractError(ConduitAdapterError):
    """
    Raised by ``read_counters()`` when a *required* Conduit metric
    (``conduit_bytes_uploaded`` / ``conduit_bytes_downloaded`` /
    ``conduit_uptime_seconds``) is missing from an otherwise-successful
    scrape, or its value cannot be parsed as a number.

    This signals that Conduit's metrics format has changed (an upgrade
    signal). The collector flags it (``parse_gap``) rather than fabricating a
    zero value, which would corrupt the delta ledger.
    """


class PersonalCompartmentError(ConduitAdapterError):
    """
    Base for failures of the personal-compartment helper
    (``ccc-personal-compartment``, C4/C5). Covers the helper's filesystem /
    safety errors and Conduit subprocess errors (helper exit 3 / 4), timeouts,
    and any unexpected non-zero exit.

    The message is generic and safe for operator display: it NEVER contains the
    pairing token, the compartment ID, or the helper's stdout.
    """


class PersonalValidationError(PersonalCompartmentError):
    """
    Raised when the helper rejects the input (helper exit 2) -- e.g. an empty,
    over-long, or multi-line display name -- or when the adapter's own light
    pre-check rejects it before spawning the subprocess.
    """


class PersonalDivergenceError(PersonalCompartmentError):
    """
    Raised when the helper's token divergence self-check fails (helper exit 5):
    the helper's rebuilt token did not match Conduit's canonical output, meaning
    the deployed token format and the helper are out of sync (an upgrade
    signal). Fail-closed: no token is produced.
    """
