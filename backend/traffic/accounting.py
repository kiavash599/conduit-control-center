# SPDX-License-Identifier: MIT
"""
backend/traffic/accounting.py
-----------------------------
Pure accounting state machine for the P0 traffic persistence collector.

This module is **pure**: no I/O, no clock, no database, no HTTP. It takes the
current Conduit counter reading plus the prior persisted state and returns a
``TickDecision`` describing exactly what the collector should persist. The
collector (Step 3) does the I/O, assigns row ids, and handles clock-sync
gating; this module only decides.

Decision precedence (first match wins), per the approved P0 design:

  1. Empty DB (``prev is None``)            -> BOOTSTRAP (initial_baseline, counted=0)
  2. uptime decrease                         -> RESET / new epoch (epoch_baseline)
  3. counter decrease w/o uptime decrease    -> negative_clamped (counted=0, stay)
  4. build_rev change w/o reset              -> build_change_no_reset marker (counted=1)
  5. otherwise                               -> normal / gap_spanning / recovered

Load-bearing rules:

- Reset is detected **only** by an uptime decrease (the authoritative signal).
  A counter decrease while uptime did *not* decrease is treated as a data-quality
  anomaly (``negative_clamped``), never a fabricated reset, so we never over-count.
- ``counted = 0`` iff ``source == 'initial_baseline'`` OR
  ``anomaly_flag == 'negative_clamped'``. Markers such as
  ``build_change_no_reset`` remain ``counted = 1``.
- Ordering uses ``uptime``/``seq``; ``ts_utc`` (supplied by the collector) is for
  display/bucketing only and never drives a decision.
- No back-fill: epoch_baseline / gap_spanning / recovered deltas carry the whole
  delta and the collector buckets them into the current hour.

Clock-sync gating (Option C) lives in the collector: while the clock is
unsynced the collector persists nothing and does not call ``decide()``. The
first synced tick after a gap is therefore a normal ``decide()`` call with
``is_first_tick_of_session=True`` -> ``recovered`` (or ``epoch_baseline`` on a
reset, or BOOTSTRAP on an empty DB).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.traffic.models import CounterReading

# ---------------------------------------------------------------------------
# Enum-like string constants (mirror the schema CHECK values)
# ---------------------------------------------------------------------------
SOURCE_NORMAL = "normal"
SOURCE_EPOCH_BASELINE = "epoch_baseline"
SOURCE_INITIAL_BASELINE = "initial_baseline"
SOURCE_GAP_SPANNING = "gap_spanning"
SOURCE_RECOVERED = "recovered"

ANOMALY_NONE = "none"
ANOMALY_NEGATIVE_CLAMPED = "negative_clamped"
ANOMALY_RESET = "reset"
ANOMALY_PARSE_GAP = "parse_gap"
ANOMALY_BUILD_CHANGE_NO_RESET = "build_change_no_reset"

REASON_STARTUP = "startup"
REASON_RESET = "reset"
REASON_BUILD_CHANGE_WITH_RESET = "build_change_with_reset"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
# Conduit's uptime is monotonic within a run, so any decrease means a restart.
# A small epsilon guards only against float-compare noise; real resets drop
# uptime by orders of magnitude (e.g. thousands of seconds -> a few seconds).
UPTIME_RESET_EPS_SECONDS = 1.0

# A gap (missed scrapes within a running collector session) is inferred when the
# elapsed uptime between readings exceeds this threshold. The collector passes
# its configured value (~1.5-2x the tick interval); this default suits 60 s.
DEFAULT_GAP_THRESHOLD_SECONDS = 90.0


# ---------------------------------------------------------------------------
# Input state (constructed by the collector from persisted rows)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Snapshot:
    """The last persisted snapshot, used as the comparison baseline (`prev`)."""

    id: int
    seq: int
    epoch_id: int
    uptime_seconds: float
    bytes_up: int
    bytes_down: int


@dataclass(frozen=True)
class Epoch:
    """Minimal current-epoch info accounting needs (for build-change detection)."""

    id: int
    build_rev: str | None


# ---------------------------------------------------------------------------
# Output specs (the collector assigns row ids / epoch_id on persist)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NewEpoch:
    reason: str
    started_at_utc: str
    first_uptime_seconds: float
    build_rev: str | None


@dataclass(frozen=True)
class SnapshotWrite:
    ts_utc: str
    seq: int
    uptime_seconds: float
    bytes_up: int
    bytes_down: int


@dataclass(frozen=True)
class DeltaWrite:
    ts_utc: str
    seq: int
    interval_seconds: float
    bytes_up_delta: int
    bytes_down_delta: int
    source: str
    anomaly_flag: str
    counted: int
    prev_snapshot_id: int | None


@dataclass(frozen=True)
class TickDecision:
    """What the collector should persist for one tick (single transaction)."""

    snapshot: SnapshotWrite
    delta: DeltaWrite
    new_epoch: NewEpoch | None = None
    close_prev_epoch: bool = False


# ---------------------------------------------------------------------------
# Counting policy (single source of truth)
# ---------------------------------------------------------------------------
def counted_for(source: str, anomaly_flag: str) -> int:
    """
    Resolve whether a delta contributes to lifetime/rollups.

    ``counted = 0`` iff the row is the pre-recording baseline
    (``initial_baseline``) or an untrusted clamped reading
    (``negative_clamped``); everything else counts, including marker flags such
    as ``build_change_no_reset``.
    """
    if source == SOURCE_INITIAL_BASELINE or anomaly_flag == ANOMALY_NEGATIVE_CLAMPED:
        return 0
    return 1


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------
def decide(
    reading: CounterReading,
    prev: Snapshot | None,
    current_epoch: Epoch | None,
    now_ts: str,
    is_first_tick_of_session: bool,
    *,
    gap_threshold_seconds: float = DEFAULT_GAP_THRESHOLD_SECONDS,
) -> TickDecision:
    """
    Decide what to persist for one synced tick. Pure: no side effects.

    Parameters
    ----------
    reading : CounterReading
        The current successful scrape (absolute counters + uptime + build_rev).
    prev : Snapshot | None
        The last persisted snapshot, or None for an empty database.
    current_epoch : Epoch | None
        The active epoch (for build-change detection); None on an empty DB.
    now_ts : str
        Wall-clock UTC (ISO-8601) for ts_utc fields. Display/bucketing only.
    is_first_tick_of_session : bool
        True on the collector's first tick after (re)start; used to label a
        same-epoch bridge delta as ``recovered``.
    gap_threshold_seconds : float
        Elapsed-uptime threshold above which a same-epoch delta is
        ``gap_spanning``.
    """
    # (1) Empty DB -> BOOTSTRAP. The snapshot holds the absolute baseline; the
    # delta is an uncounted "recording starts here" marker (recording_since is
    # derived as MIN(epoch.started_at_utc)).
    if prev is None:
        return TickDecision(
            new_epoch=NewEpoch(
                reason=REASON_STARTUP,
                started_at_utc=now_ts,
                first_uptime_seconds=reading.uptime_seconds,
                build_rev=reading.build_rev,
            ),
            close_prev_epoch=False,
            snapshot=SnapshotWrite(
                ts_utc=now_ts,
                seq=1,
                uptime_seconds=reading.uptime_seconds,
                bytes_up=reading.bytes_up,
                bytes_down=reading.bytes_down,
            ),
            delta=DeltaWrite(
                ts_utc=now_ts,
                seq=1,
                interval_seconds=reading.uptime_seconds,
                bytes_up_delta=0,
                bytes_down_delta=0,
                source=SOURCE_INITIAL_BASELINE,
                anomaly_flag=ANOMALY_NONE,
                counted=counted_for(SOURCE_INITIAL_BASELINE, ANOMALY_NONE),
                prev_snapshot_id=None,
            ),
        )

    seq = prev.seq + 1
    d_up = reading.bytes_up - prev.bytes_up
    d_down = reading.bytes_down - prev.bytes_down
    d_uptime = reading.uptime_seconds - prev.uptime_seconds

    snapshot = SnapshotWrite(
        ts_utc=now_ts,
        seq=seq,
        uptime_seconds=reading.uptime_seconds,
        bytes_up=reading.bytes_up,
        bytes_down=reading.bytes_down,
    )

    # (2) Authoritative reset: uptime decreased -> new epoch + epoch_baseline.
    if reading.uptime_seconds + UPTIME_RESET_EPS_SECONDS < prev.uptime_seconds:
        build_changed = (
            reading.build_rev is not None
            and current_epoch is not None
            and reading.build_rev != current_epoch.build_rev
        )
        reason = REASON_BUILD_CHANGE_WITH_RESET if build_changed else REASON_RESET
        return TickDecision(
            new_epoch=NewEpoch(
                reason=reason,
                started_at_utc=now_ts,
                first_uptime_seconds=reading.uptime_seconds,
                build_rev=reading.build_rev,
            ),
            close_prev_epoch=True,
            snapshot=snapshot,
            delta=DeltaWrite(
                ts_utc=now_ts,
                seq=seq,
                interval_seconds=reading.uptime_seconds,
                bytes_up_delta=reading.bytes_up,   # counters reset to 0 -> absolute is post-restart traffic
                bytes_down_delta=reading.bytes_down,
                source=SOURCE_EPOCH_BASELINE,
                anomaly_flag=ANOMALY_RESET,
                counted=counted_for(SOURCE_EPOCH_BASELINE, ANOMALY_RESET),
                prev_snapshot_id=prev.id,
            ),
        )

    # Same epoch from here. Determine provenance.
    if is_first_tick_of_session:
        source = SOURCE_RECOVERED
    elif d_uptime > gap_threshold_seconds:
        source = SOURCE_GAP_SPANNING
    else:
        source = SOURCE_NORMAL

    # (3) Counter decrease without uptime decrease -> negative_clamped (whole-row
    # clamp; not a reset). The snapshot still records the current absolutes so
    # subsequent in-epoch deltas are correct.
    if d_up < 0 or d_down < 0:
        return TickDecision(
            new_epoch=None,
            close_prev_epoch=False,
            snapshot=snapshot,
            delta=DeltaWrite(
                ts_utc=now_ts,
                seq=seq,
                interval_seconds=d_uptime,
                bytes_up_delta=0,
                bytes_down_delta=0,
                source=source,
                anomaly_flag=ANOMALY_NEGATIVE_CLAMPED,
                counted=counted_for(source, ANOMALY_NEGATIVE_CLAMPED),
                prev_snapshot_id=prev.id,
            ),
        )

    # (4) Build-rev change without reset -> marker (still counted).
    anomaly = ANOMALY_NONE
    if (
        reading.build_rev is not None
        and current_epoch is not None
        and reading.build_rev != current_epoch.build_rev
    ):
        anomaly = ANOMALY_BUILD_CHANGE_NO_RESET

    # (5) Normal / gap_spanning / recovered counted delta.
    return TickDecision(
        new_epoch=None,
        close_prev_epoch=False,
        snapshot=snapshot,
        delta=DeltaWrite(
            ts_utc=now_ts,
            seq=seq,
            interval_seconds=d_uptime,
            bytes_up_delta=d_up,
            bytes_down_delta=d_down,
            source=source,
            anomaly_flag=anomaly,
            counted=counted_for(source, anomaly),
            prev_snapshot_id=prev.id,
        ),
    )
