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
    # Personal Mode (D6 / C6b). Configured from CCC_MAX_PERSONAL_CLIENTS;
    # effective from the conduit_max_personal_clients gauge. Defaults to
    # unknown/unknown so existing positional constructions keep working.
    max_personal_clients: ConfigField = field(
        default_factory=lambda: ConfigField(configured=None, effective=None)
    )
    reduced: ReducedConfigView = field(default_factory=ReducedConfigView)

    @property
    def drift(self) -> bool | None:
        # Drift aggregate is the config-write knobs only (max_personal has no
        # config-write path here; its own .drift is available on the field).
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


@dataclass(frozen=True)
class LiveStatus:
    """Forgiving, aggregate-only read of Conduit's live broker/activity gauges,
    for the Node Status broker badge (Live Operations, Option 1).

    Returned by ``backend.conduit.adapter.get_live_status()``. The adapter
    returns ``None`` (not this object) when the metrics endpoint is unreachable;
    a populated object means "endpoint reachable; these gauges were present".
    Each field is ``None`` individually when its gauge is missing/unparseable.

    Deliberately omits connected_clients / bytes / uptime: those are already
    shown by the Advisor, Traffic, and Node Status cards (no duplication).

    Fields
    ------
    is_live : bool | None
        ``conduit_is_live`` (1 = broker connected).
    announcing : int | None
        ``conduit_announcing`` — in-flight announcement requests.
    connecting_clients : int | None
        ``conduit_connecting_clients`` — clients currently connecting.
    idle_seconds : int | None
        ``conduit_idle_seconds`` — seconds since the last client activity.
    build_rev : str | None
        ``build_rev`` label on ``conduit_build_info`` (short revision).
    """

    is_live: bool | None = None
    announcing: int | None = None
    connecting_clients: int | None = None
    idle_seconds: int | None = None
    build_rev: str | None = None


@dataclass(frozen=True)
class PersonalCompartmentStatus:
    """Structural state of the personal compartment, from the C4 helper's
    ``status`` subcommand (Personal Mode, C5).

    Carries ONLY non-sensitive booleans -- never the compartment ID or a token.

    Fields
    ------
    exists : bool
        A ``personal_compartment.json`` file is present.
    valid : bool
        That file parses and its compartment ID passes validation.
    backup : bool
        A ``.bak`` snapshot is present (restorable previous compartment).
    """

    exists: bool = False
    valid: bool = False
    backup: bool = False
