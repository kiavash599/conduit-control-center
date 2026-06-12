# SPDX-License-Identifier: MIT
"""
backend/traffic/models.py
-------------------------
Typed records for the P0 traffic persistence collector.

Step 0 introduces ``CounterReading`` — the single, immutable result type
returned by ``backend.conduit.adapter.read_counters()`` and consumed by the
(pure) accounting core in Step 2. Keeping it here, in a dependency-free leaf
module, means the accounting logic never has to import the heavy adapter
module (which pulls in config, urllib, subprocess handling, etc.).

A ``CounterReading`` describes a single successful scrape of Conduit's
Prometheus endpoint. It deliberately does **not** carry ``ts`` or ``seq``:
wall-clock time and the monotonic sequence number are assigned by the
collector, never by the reader.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CounterReading:
    """
    One successful read of Conduit's cumulative byte counters.

    Fields
    ------
    bytes_up : int
        ``conduit_bytes_uploaded`` (cumulative; resets on Conduit restart).
    bytes_down : int
        ``conduit_bytes_downloaded`` (cumulative; resets on Conduit restart).
    uptime_seconds : float
        ``conduit_uptime_seconds`` (monotonic within a run; resets on restart).
        Kept as a float — it is the load-bearing reset signal, compared with
        sub-second precision, so it must not be truncated to an int.
    build_rev : str | None
        ``build_rev`` label from ``conduit_build_info``. Optional metadata;
        ``None`` when the label is absent.
    is_live : bool | None
        ``conduit_is_live`` (0/1). Optional and informational; ``None`` when
        the gauge is absent or unparseable.

    ``bytes_up``, ``bytes_down`` and ``uptime_seconds`` are required: the reader
    raises rather than fabricating a zero when any of them is missing.
    """

    bytes_up: int
    bytes_down: int
    uptime_seconds: float
    build_rev: str | None = None
    is_live: bool | None = None
