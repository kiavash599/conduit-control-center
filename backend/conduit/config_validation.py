# SPDX-License-Identifier: MIT
"""
backend/conduit/config_validation.py
------------------------------------
Pure validation + conversion for the Conduit config write path (M2).

Used by the API layer. The root wrapper (ccc-apply-conduit-config) re-implements
the SAME rules independently -- the wrapper is the privilege boundary and must
never trust the caller. Keep the two in sync (ranges below mirror the wrapper).

No I/O, no privilege; safe to import anywhere.
"""
from __future__ import annotations

MCC_MIN = 1
MCC_MAX = 1000
BW_UNLIMITED = -1
BW_MIN = 1
DEFAULT_BW_MAX_MBPS = 1000  # soft cap; must match the wrapper's BW_MAX

_BYTES_PER_MBPS = 125_000  # decimal Mbps, matches Conduit's --bandwidth


def validate_max_common_clients(value: object) -> tuple[int | None, str | None]:
    """Return (normalized_int, None) or (None, error_message)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "max_common_clients must be an integer"
    if value < MCC_MIN or value > MCC_MAX:
        return None, f"max_common_clients must be between {MCC_MIN} and {MCC_MAX}"
    return value, None


def validate_bandwidth_mbps(
    value: object, *, max_mbps: int = DEFAULT_BW_MAX_MBPS
) -> tuple[int | None, str | None]:
    """Return (normalized_int, None) or (None, error_message). -1 = unlimited."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "bandwidth_mbps must be an integer"
    if value == BW_UNLIMITED:
        return value, None
    if value < BW_MIN or value > max_mbps:
        return None, (
            f"bandwidth_mbps must be -1 (unlimited) or between {BW_MIN} and {max_mbps}"
        )
    return value, None


def mbps_to_bps(mbps: int) -> int:
    """Convert configured Mbps to the effective metric's bytes/sec.

    Unlimited: configured -1 maps to the metric's 0.
    """
    if mbps == BW_UNLIMITED:
        return 0
    return mbps * _BYTES_PER_MBPS
