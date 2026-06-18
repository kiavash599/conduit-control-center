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
# Max personal clients (0 = personal mode off). Parity: MUST match the root
# wrapper ccc-apply-conduit-config (MPC_MIN/MPC_MAX); a parity test asserts it.
# C3 defines the range only; the validator function lands with the API (C6).
MPC_MIN = 0
MPC_MAX = 1000
BW_UNLIMITED = -1
BW_MIN = 1
DEFAULT_BW_MAX_MBPS = 1000  # soft cap; must match the wrapper's BW_MAX

# Reduced-window ranges (BS1). MUST match the root wrapper
# ccc-apply-conduit-config (RMIN_*/RMC_*/RBW_*). A parity test asserts equality.
RMIN_MIN = 0
RMIN_MAX = 1439          # minutes since midnight, UTC (00:00 .. 23:59)
RMC_MIN = 1
RMC_MAX = 1000
RBW_MIN = 1
RBW_MAX = 1000

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


def parse_hhmm(value: object) -> tuple[int | None, str | None]:
    """Parse 'HH:MM' (24-hour, UTC) into minutes-since-midnight.

    Returns (minutes, None) or (None, error_message). Only the canonical
    zero-padded HH:MM shape is accepted (matches the helper's emitted form).
    """
    if not isinstance(value, str):
        return None, "time must be a string in HH:MM (24-hour, UTC) format"
    s = value.strip()
    if len(s) != 5 or s[2] != ":" or not (s[:2].isdigit() and s[3:].isdigit()):
        return None, f"time must be HH:MM (24-hour, UTC): {value!r}"
    hh, mm = int(s[:2]), int(s[3:])
    if hh > 23 or mm > 59:
        return None, f"time out of range (00:00..23:59): {value!r}"
    return hh * 60 + mm, None


def validate_reduced(
    enabled: object,
    start: object,
    end: object,
    reduced_max_common_clients: object,
    reduced_bandwidth_mbps: object,
    max_common_clients: int,
) -> tuple[dict | None, list[dict]]:
    """Validate the OPTIONAL reduced-mode window.

    Returns (normalized, []) or (None, errors). ``normalized`` always carries
    integer fields ready for the adapter/helper boundary (no HH:MM string ever
    crosses to the privilege layer)::

        {start_min, end_min, reduced_max_common_clients, reduced_bandwidth_mbps}

    Disabled -> {-1, -1, 0, 0}. All-or-nothing: a partially specified window is
    rejected. Ranges mirror the root wrapper (parity); cross-field rule is
    reduced_max_common_clients <= max_common_clients (the new normal value).
    """
    if not enabled:
        return {
            "start_min": -1,
            "end_min": -1,
            "reduced_max_common_clients": 0,
            "reduced_bandwidth_mbps": 0,
        }, []

    errors: list[dict] = []
    smin, e_s = parse_hhmm(start)
    if e_s:
        errors.append({"field": "reduced_start", "message": e_s})
    emin, e_e = parse_hhmm(end)
    if e_e:
        errors.append({"field": "reduced_end", "message": e_e})
    if smin is not None and emin is not None and smin == emin:
        errors.append({"field": "reduced_end",
                       "message": "reduced start and end must differ"})

    if isinstance(reduced_max_common_clients, bool) or not isinstance(reduced_max_common_clients, int):
        errors.append({"field": "reduced_max_common_clients",
                       "message": "reduced_max_common_clients must be an integer"})
    elif reduced_max_common_clients < RMC_MIN or reduced_max_common_clients > RMC_MAX:
        errors.append({"field": "reduced_max_common_clients",
                       "message": f"reduced_max_common_clients must be between {RMC_MIN} and {RMC_MAX}"})
    elif reduced_max_common_clients > max_common_clients:
        errors.append({"field": "reduced_max_common_clients",
                       "message": "reduced_max_common_clients must not exceed max_common_clients"})

    if isinstance(reduced_bandwidth_mbps, bool) or not isinstance(reduced_bandwidth_mbps, int):
        errors.append({"field": "reduced_bandwidth_mbps",
                       "message": "reduced_bandwidth_mbps must be an integer"})
    elif reduced_bandwidth_mbps < RBW_MIN or reduced_bandwidth_mbps > RBW_MAX:
        errors.append({"field": "reduced_bandwidth_mbps",
                       "message": f"reduced_bandwidth_mbps must be between {RBW_MIN} and {RBW_MAX} when enabled"})

    if errors:
        return None, errors
    return {
        "start_min": smin,
        "end_min": emin,
        "reduced_max_common_clients": reduced_max_common_clients,
        "reduced_bandwidth_mbps": reduced_bandwidth_mbps,
    }, []
