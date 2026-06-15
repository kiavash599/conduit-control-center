# SPDX-License-Identifier: MIT
"""
backend/advisor/models.py
-------------------------
Typed contracts for the pure Contribution Advisor engine (A1.2).

Inputs (``AdvisorInput`` and its parts), outputs (``AdvisorItem`` /
``ContributionHealthSummary`` / ``AdvisorResult``), the caller-owned cooldown
``AdvisorState``, and the tunable ``AdvisorPolicy`` (all A1.0 thresholds in one
place). Dependency-free except for the pure leaf ``NodeRuntime``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum

from backend.traffic.models import NodeRuntime  # pure leaf; reused as the node input

__all__ = [
    "Severity",
    "Domain",
    "SystemSnapshot",
    "ConduitState",
    "BytesPair",
    "SeriesBucket",
    "TrafficSnapshot",
    "AdvisorInput",
    "AdvisorItem",
    "ContributionHealthSummary",
    "AdvisorState",
    "AdvisorResult",
    "AdvisorPolicy",
    "NodeRuntime",
]


class Severity(IntEnum):
    """Ranked so a plain numeric sort yields the display order."""

    WARNING = 0
    STRONG_SUGGESTION = 1
    SUGGESTION = 2
    INFO = 3


class Domain(str, Enum):
    CAPACITY = "capacity"
    REDUCED_MODE = "reduced_mode"
    HEALTH = "health"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SystemSnapshot:
    """Host metrics (psutil). The API supplies smoothed values (sustain windows)."""

    cpu_percent: float | None = None
    ram_percent: float | None = None
    cpu_temperature_celsius: float | None = None  # None on non-Pi / sensor absent


@dataclass(frozen=True)
class ConduitState:
    is_live: bool | None = None
    uptime_seconds: float | None = None


@dataclass(frozen=True)
class BytesPair:
    bytes_up: int = 0
    bytes_down: int = 0

    @property
    def total(self) -> int:
        return self.bytes_up + self.bytes_down


@dataclass(frozen=True)
class SeriesBucket:
    bucket_utc: str  # "YYYY-MM-DDTHH:00:00Z" (hourly)
    bytes_up: int = 0
    bytes_down: int = 0

    @property
    def total(self) -> int:
        return self.bytes_up + self.bytes_down


@dataclass(frozen=True)
class TrafficSnapshot:
    lifetime: BytesPair | None = None
    last_24h: BytesPair | None = None
    last_7d: BytesPair | None = None
    series_hourly: tuple[SeriesBucket, ...] | None = None  # dense hourly, oldest->newest
    recording_since: str | None = None
    history_days: int = 0  # whole days of hourly history available


@dataclass(frozen=True)
class AdvisorInput:
    system: SystemSnapshot | None = None
    node: NodeRuntime | None = None  # None => Conduit metrics unreachable
    conduit: ConduitState | None = None
    traffic: TrafficSnapshot | None = None


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AdvisorItem:
    severity: Severity
    domain: Domain
    title: str
    message: str
    rationale: str
    apply_hint: str | None = None


@dataclass(frozen=True)
class ContributionHealthSummary:
    status: str  # "live" | "starting" | "disconnected" | "offline" | "unknown"
    headline: str
    is_live: bool | None = None
    connected_clients: int | None = None
    lifetime_up: int | None = None
    lifetime_down: int | None = None
    recording_since: str | None = None


@dataclass(frozen=True)
class AdvisorState:
    """
    Caller-owned cooldown/hysteresis state. The engine consumes it and returns
    an updated copy; it never stores or persists it (A1.3 holds it in memory).
    """

    last_emitted_at: dict[str, datetime] = field(default_factory=dict)
    last_max_common_clients: int | None = None


@dataclass(frozen=True)
class AdvisorResult:
    items: list[AdvisorItem]
    summary: ContributionHealthSummary
    state: AdvisorState


# ---------------------------------------------------------------------------
# Policy (all A1.0 thresholds, tunable in one place)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AdvisorPolicy:
    # Capacity bands (percent)
    cpu_warn: float = 90.0
    cpu_grow_suggest: float = 40.0   # cpu < this => headroom
    cpu_grow_strong: float = 25.0    # cpu < this => deep headroom
    ram_warn: float = 85.0
    ram_grow_suggest: float = 70.0
    ram_grow_strong: float = 55.0
    temp_warn: float = 80.0          # >= this => thermal Warning (only if temp present)
    temp_grow_gate: float = 70.0     # temp must be < this to grow (only if temp present)

    # Client-limit growth
    demand_fraction: float = 0.80    # connected >= 80% of max_common_clients
    growth_pct: float = 0.25
    growth_step_min: int = 25
    growth_step_max: int = 100
    max_limit_ceiling: int = 1000
    growth_cooldown_hours: float = 24.0
    growth_enabled: bool = True      # A1.3 (Option Z): API sets False until sustained headroom is confirmed

    # Reduced-mode quiet window
    reduced_min_history_days: int = 7
    reduced_quiet_fraction: float = 0.30      # hour <= 30% of peak
    reduced_day_consistency: float = 0.70     # qualifies on >= 70% of days
    reduced_min_run_hours: int = 4
    reduced_max_run_hours: int = 12
    reduced_strong_run_hours: int = 6
    reduced_strong_consistency: float = 0.90

    # Contribution health
    idle_warn_hours: float = 12.0
    new_station_uptime_hours: float = 48.0
    new_station_traffic_floor_bytes: int = 10 * 1024 * 1024  # ~10 MB
    decline_fraction: float = 0.50
    decline_min_days: int = 2
