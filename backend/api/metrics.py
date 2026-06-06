"""
backend/api/metrics.py
----------------------
System and traffic metrics endpoints.

Implemented in:
  Issue #21 -- GET /api/metrics/system  (CPU, RAM, temp, disk via psutil)
  Issue #22 -- GET /api/metrics/traffic (bytes transferred by Conduit)

Traffic metrics data source
----------------------------
GET /api/metrics/traffic scrapes the Conduit Prometheus metrics endpoint at
``http://localhost:{conduit_metrics_port}/metrics``.  The endpoint only exists
when Conduit is started with ``--metrics-addr :<port>``.  See
docs/conduit-metrics-source.md and deployment/conduit.service for details.

If the metrics endpoint is unreachable (Conduit stopped, or --metrics-addr
not configured), bytes_sent and bytes_received are returned as null at
HTTP 200.  This is normal operation, not an error.

Caching
-------
GET /api/metrics/traffic: time-based module-level cache keyed on
    AppConfig.metrics_cache_ttl_seconds (default 5 s).  The dashboard polls
    every 30 s, but the cache guards against bursts from multiple browser tabs
    or monitoring tools.

GET /api/metrics/system: no caching applied at this time.
    TODO (future): add the same time-based cache pattern when profiling shows
    psutil overhead at high poll rates.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import psutil
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.conduit.adapter import (
    ConduitAdapterError,
    get_last_changed,
    get_traffic_metrics,
)
from backend.config import get_app_config
from backend.dependencies import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CpuMetrics(BaseModel):
    """CPU usage and optional temperature."""

    usage_percent: float = Field(
        description="CPU utilisation across all cores, 0-100."
    )
    temperature_celsius: Optional[float] = Field(
        default=None,
        description=(
            "CPU temperature in degrees C from psutil.sensors_temperatures(), "
            "or null if the platform does not expose sensor data "
            "(e.g. non-Raspberry Pi Linux, macOS, Windows)."
        ),
    )


class RamMetrics(BaseModel):
    """Physical memory (RAM) usage."""

    total_bytes: int = Field(description="Total installed RAM in bytes.")
    used_bytes: int = Field(description="RAM currently in use (excluding buffers/cache).")
    used_percent: float = Field(description="RAM utilisation, 0-100.")


class DiskMetrics(BaseModel):
    """Root filesystem disk usage."""

    total_bytes: int = Field(description="Total disk capacity in bytes.")
    used_bytes: int = Field(description="Disk space in use in bytes.")
    used_percent: float = Field(description="Disk utilisation, 0-100.")


class SystemMetrics(BaseModel):
    """Response body for GET /api/metrics/system."""

    cpu: CpuMetrics
    ram: RamMetrics
    disk: DiskMetrics


class TrafficMetrics(BaseModel):
    """
    Response body for GET /api/metrics/traffic.

    bytes_sent and bytes_received are null when the Conduit Prometheus metrics
    endpoint is unreachable (Conduit not running, or --metrics-addr not
    configured at Conduit startup).  A null value is NOT an error; it is the
    expected response when Conduit is stopped.

    bytes_sent maps to the Prometheus gauge ``conduit_bytes_uploaded``.
    bytes_received maps to the Prometheus gauge ``conduit_bytes_downloaded``.

    session_start is the ISO 8601 UTC timestamp of the last time Conduit
    entered the active (running) state, from systemctl ActiveEnterTimestamp.
    It is null if the service has never been started.

    All byte values are cumulative since the most recent Conduit start.
    They reset to 0 when Conduit restarts.
    """

    bytes_sent:     Optional[int] = Field(
        default=None,
        description=(
            "Total bytes sent (uploaded) by Conduit since last service start. "
            "null when the Conduit metrics endpoint is not reachable."
        ),
    )
    bytes_received: Optional[int] = Field(
        default=None,
        description=(
            "Total bytes received (downloaded) by Conduit since last service start. "
            "null when the Conduit metrics endpoint is not reachable."
        ),
    )
    session_start:  Optional[str] = Field(
        default=None,
        description=(
            "ISO 8601 UTC timestamp of the last Conduit service start, "
            "or null if the service has never been started."
        ),
    )
    timestamp:      str = Field(
        description="ISO 8601 UTC timestamp of this reading."
    )


# ---------------------------------------------------------------------------
# Traffic metrics cache
# ---------------------------------------------------------------------------
# Simple module-level time-based cache.  Thread safety is not a concern:
# uvicorn runs with --workers 1 on the Pi so there is only one process and
# one event loop.  A stale read during a concurrent cache refresh is harmless
# (the refresh completes on the next poll cycle).

_traffic_cache: Optional[TrafficMetrics] = None
_traffic_cache_ts: float = 0.0  # monotonic timestamp of last fill


def _traffic_cache_valid() -> bool:
    """Return True if the cached value is still within its TTL."""
    ttl = get_app_config().metrics_cache_ttl_seconds
    return _traffic_cache is not None and (time.monotonic() - _traffic_cache_ts) < ttl


# ---------------------------------------------------------------------------
# psutil helpers (system metrics)
# ---------------------------------------------------------------------------


def _get_cpu_temperature() -> Optional[float]:
    """
    Return the CPU temperature in degrees C, or None if unavailable.

    Strategy (Raspberry Pi / Linux):
      1. psutil.sensors_temperatures() returns a dict of sensor groups.
      2. We look for common keys used on Raspberry Pi and generic Linux:
         "cpu_thermal", "coretemp", "k10temp", "acpitz".
      3. The first reading from the first matching group is returned.
      4. If sensors_temperatures is absent (Windows, macOS, some Docker
         images) or returns an empty dict, we return None gracefully.

    This function never raises -- all exceptions are caught and logged.
    """
    try:
        if not hasattr(psutil, "sensors_temperatures"):
            return None

        temps = psutil.sensors_temperatures()
        if not temps:
            return None

        _PREFERRED_KEYS = ("cpu_thermal", "coretemp", "k10temp", "acpitz")
        for key in _PREFERRED_KEYS:
            entries = temps.get(key)
            if entries:
                return round(entries[0].current, 1)

        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)

    except Exception:  # noqa: BLE001
        logger.warning("Could not read CPU temperature from psutil", exc_info=True)

    return None


def _collect_system_metrics() -> SystemMetrics:
    """
    Collect CPU, RAM, and disk metrics using psutil.

    cpu_percent(interval=None) returns the non-blocking cached value from
    the last psutil poll cycle.  On the very first call this returns 0.0,
    which is acceptable -- subsequent dashboard polls will return real values.
    Using a blocking interval= here would stall the event loop thread.
    """
    cpu_usage = psutil.cpu_percent(interval=None)
    cpu_temp  = _get_cpu_temperature()

    vm   = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return SystemMetrics(
        cpu=CpuMetrics(
            usage_percent=round(cpu_usage, 1),
            temperature_celsius=cpu_temp,
        ),
        ram=RamMetrics(
            total_bytes=vm.total,
            used_bytes=vm.used,
            used_percent=round(vm.percent, 1),
        ),
        disk=DiskMetrics(
            total_bytes=disk.total,
            used_bytes=disk.used,
            used_percent=round(disk.percent, 1),
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/system",
    summary="System health metrics (CPU, RAM, temperature, disk)",
    response_model=SystemMetrics,
    responses={
        200: {"description": "Current system metrics snapshot"},
        401: {"description": "Not authenticated"},
        500: {"description": "Unexpected error collecting metrics"},
    },
)
async def system_metrics(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> SystemMetrics:
    """
    Return a snapshot of CPU, RAM, and disk usage collected via psutil.

    **cpu.temperature_celsius** is ``null`` when the host platform does not
    expose sensor data (non-Pi Linux, macOS, Windows, or containerised
    environments without hardware access).

    **cpu.usage_percent** may read ``0.0`` on the very first call after
    startup; subsequent calls return an accurate rolling average.

    No caching is applied at this time.
    """
    return _collect_system_metrics()


@router.get(
    "/traffic",
    summary="Conduit traffic counters (bytes sent/received)",
    response_model=TrafficMetrics,
    responses={
        200: {
            "description": (
                "Traffic counters, or null byte fields when the Conduit "
                "metrics endpoint is unreachable (Conduit stopped or "
                "--metrics-addr not configured)."
            )
        },
        401: {"description": "Not authenticated"},
    },
)
async def traffic_metrics(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> TrafficMetrics:
    """
    Return Conduit traffic counters scraped from its Prometheus metrics endpoint.

    The endpoint is ``http://localhost:{conduit_metrics_port}/metrics``.
    It only exists when Conduit is running AND was started with
    ``--metrics-addr :<port>``.  See ``docs/conduit-metrics-source.md``.

    **bytes_sent** and **bytes_received** are ``null`` (not an error) when:
    - Conduit is stopped
    - Conduit was started without ``--metrics-addr``
    - The metrics server has not yet responded within 2 seconds

    **bytes_sent** = 0 and **bytes_received** = 0 means Conduit is running
    and configured, but no traffic has been proxied yet this session.

    Responses are cached for ``metrics_cache_ttl_seconds`` (config.json,
    default 5 s) to guard against burst requests from multiple browser tabs.
    """
    global _traffic_cache, _traffic_cache_ts  # noqa: PLW0603

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Return cached result if still valid.
    if _traffic_cache_valid():
        # Return a fresh copy with the current timestamp so the client always
        # sees an up-to-date "timestamp" field even during cache hits.
        cached = _traffic_cache
        return TrafficMetrics(
            bytes_sent=cached.bytes_sent,
            bytes_received=cached.bytes_received,
            session_start=cached.session_start,
            timestamp=now_ts,
        )

    # -- Fetch traffic counters from Conduit Prometheus endpoint ---------------
    raw = await get_traffic_metrics()

    if raw is None:
        # Metrics server unreachable: Conduit stopped or --metrics-addr absent.
        bytes_sent     = None
        bytes_received = None
    else:
        bytes_sent     = raw.get("bytes_uploaded")
        bytes_received = raw.get("bytes_downloaded")

    # -- Fetch session start time from systemctl --------------------------------
    # get_last_changed() reads ActiveEnterTimestamp (no sudo, read-only).
    # If Conduit has never started, it returns None.
    # If it raises (unexpected systemctl error), we degrade to None -- the
    # traffic counter values are still valid and more useful than a 503.
    try:
        session_start = await get_last_changed()
    except ConduitAdapterError:
        logger.warning(
            "traffic_metrics: get_last_changed() raised ConduitAdapterError -- "
            "session_start will be null"
        )
        session_start = None

    # -- Build response and update cache ---------------------------------------
    result = TrafficMetrics(
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        session_start=session_start,
        timestamp=now_ts,
    )

    _traffic_cache    = result
    _traffic_cache_ts = time.monotonic()

    return result
