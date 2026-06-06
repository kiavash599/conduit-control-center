"""
backend/api/metrics.py
----------------------
System and traffic metrics endpoints.

Implemented in:
  Issue #21 -- GET /api/metrics/system  (CPU, RAM, temp, disk via psutil)
  Issue #22 -- GET /api/metrics/traffic (bytes transferred by Conduit)

Traffic metrics
---------------
GET /api/metrics/traffic returns HTTP 501 until Issue #22 is implemented.

Caching
-------
TODO (Issue #22 or later): add in-memory response caching keyed on
      AppConfig.metrics_cache_ttl_seconds to avoid hammering psutil on
      every dashboard poll.  Cache invalidation must be time-based only;
      no manual cache-clear endpoint is needed at this stage.
"""

from __future__ import annotations

import logging
from typing import Optional

import psutil
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.dependencies import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class CpuMetrics(BaseModel):
    """CPU usage and optional temperature."""

    usage_percent: float = Field(
        description="CPU utilisation across all cores, 0–100."
    )
    temperature_celsius: Optional[float] = Field(
        default=None,
        description=(
            "CPU temperature in °C from psutil.sensors_temperatures(), "
            "or null if the platform does not expose sensor data "
            "(e.g. non-Raspberry Pi Linux, macOS, Windows)."
        ),
    )


class RamMetrics(BaseModel):
    """Physical memory (RAM) usage."""

    total_bytes: int = Field(description="Total installed RAM in bytes.")
    used_bytes: int = Field(description="RAM currently in use (excluding buffers/cache).")
    used_percent: float = Field(description="RAM utilisation, 0–100.")


class DiskMetrics(BaseModel):
    """Root filesystem disk usage."""

    total_bytes: int = Field(description="Total disk capacity in bytes.")
    used_bytes: int = Field(description="Disk space in use in bytes.")
    used_percent: float = Field(description="Disk utilisation, 0–100.")


class SystemMetrics(BaseModel):
    """
    Response body for GET /api/metrics/system.

    All values are collected at request time with no caching.

    TODO (Issue #22 or later): wrap with time-based in-memory cache
         using AppConfig.metrics_cache_ttl_seconds to reduce psutil
         overhead on high-frequency dashboard polls.
    """

    cpu: CpuMetrics
    ram: RamMetrics
    disk: DiskMetrics


# ---------------------------------------------------------------------------
# psutil helpers
# ---------------------------------------------------------------------------


def _get_cpu_temperature() -> Optional[float]:
    """
    Return the CPU temperature in °C, or None if unavailable.

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
            # Platform does not support sensor queries (e.g. macOS, Windows).
            return None

        temps = psutil.sensors_temperatures()
        if not temps:
            return None

        # Probe in priority order for Raspberry Pi and common x86/ARM boards.
        _PREFERRED_KEYS = ("cpu_thermal", "coretemp", "k10temp", "acpitz")
        for key in _PREFERRED_KEYS:
            entries = temps.get(key)
            if entries:
                return round(entries[0].current, 1)

        # Fall back to the first reading from whatever group is available.
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
    cpu_temp = _get_cpu_temperature()

    vm = psutil.virtual_memory()
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

    No caching is applied at this time.  See module-level TODO for the
    planned ``metrics_cache_ttl_seconds`` implementation.
    """
    return _collect_system_metrics()


@router.get("/traffic", summary="Conduit traffic counters (bytes sent/received)")
async def traffic_metrics(_user: AuthenticatedUser = Depends(get_current_user)):
    return JSONResponse(
        status_code=501,
        content={"detail": "Not implemented. Tracked in Issue #22."},
    )
