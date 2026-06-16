# SPDX-License-Identifier: MIT
"""
backend/conduit/models.py
-------------------------
Typed value objects for the read-only Conduit configuration view (M1, §6.1).

ConfigField pairs a *configured* value (resolved from the systemd unit's
ExecStart -- what Conduit uses at next start) with the *effective* value (read
from Conduit's Prometheus metrics -- what it is running now). ``drift`` is True
only when both are known and differ; None when either is unknown.

For bandwidth, configured ``-1`` (the ``--bandwidth`` flag) and effective ``0``
(the ``conduit_bandwidth_limit_bytes_per_second`` gauge) both mean "unlimited";
the ``unlimited_*`` flags let callers compare/render without re-deriving.

Aggregate-only and read-only: no secrets, no per-client/region data.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReducedConfigView:
    """Configured-only view of the reduced-mode window (BS1).

    Reduced mode has NO effective/runtime metric: tunnel-core applies the daily
    HH:MM-UTC window internally, and ``conduit_max_common_clients`` is the static
    startup gauge. So only the *configured* (next-start) values are reported --
    never an invented effective/drift value. ``enabled`` is False when no window
    is set (empty start time / zero reduced-max).
    """

    enabled: bool = False
    start: str | None = None              # HH:MM, 24-hour, UTC
    end: str | None = None                # HH:MM, 24-hour, UTC
    max_common_clients: int | None = None
    bandwidth_mbps: int | None = None


@dataclass(frozen=True)
class ConfigField:
    configured: int | None
    effective: int | None
    unlimited_configured: bool = False
    unlimited_effective: bool = False

    @property
    def drift(self) -> bool | None:
        if self.configured is None or self.effective is None:
            return None
        if self.unlimited_configured or self.unlimited_effective:
            # Normalise unlimited so -1 (flag) == 0 (metric).
            return not (self.unlimited_configured and self.unlimited_effective)
        return self.configured != self.effective


@dataclass(frozen=True)
class ConduitConfigView:
    service_status: str  # "running" | "stopped" | "starting" | "stopping" | "error" | "unknown"
    max_common_clients: ConfigField
    bandwidth_mbps: ConfigField
    reduced: ReducedConfigView = field(default_factory=ReducedConfigView)

    @property
    def drift(self) -> bool | None:
        drifts = [self.max_common_clients.drift, self.bandwidth_mbps.drift]
        if any(d is True for d in drifts):
            return True
        if any(d is None for d in drifts):
            return None
        return False


@dataclass(frozen=True)
class RegionStat:
    """One row of the Regional Analytics table (aggregate-only, scope=common).

    region        ISO 3166-1 alpha-2 code (e.g. "SA").
    traffic_bytes uploaded + downloaded bytes for that region.
    clients       connected clients for that region.
    No IP, session, or per-client data is represented or derivable.
    """

    region: str
    traffic_bytes: int
    clients: int
