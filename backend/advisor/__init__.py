# SPDX-License-Identifier: MIT
"""
backend/advisor
---------------
Pure Contribution Advisor engine (A1.2).

This package is a dependency-free leaf: it performs no I/O, no network, no
database access, no clock reads, and holds no state. ``evaluate()`` is a pure
function of its inputs (including an injected ``now`` and an optional caller-
owned ``AdvisorState``). All storage, persistence, caching, timers, and metric
gathering are the API layer's concern (A1.3), not this package's.
"""
from backend.advisor.engine import evaluate
from backend.advisor.models import (
    AdvisorInput,
    AdvisorItem,
    AdvisorPolicy,
    AdvisorResult,
    AdvisorState,
    BytesPair,
    ConduitState,
    ContributionHealthSummary,
    Domain,
    SeriesBucket,
    Severity,
    SystemSnapshot,
    TrafficSnapshot,
)

__all__ = [
    "evaluate",
    "AdvisorInput",
    "AdvisorItem",
    "AdvisorPolicy",
    "AdvisorResult",
    "AdvisorState",
    "BytesPair",
    "ConduitState",
    "ContributionHealthSummary",
    "Domain",
    "SeriesBucket",
    "Severity",
    "SystemSnapshot",
    "TrafficSnapshot",
]
