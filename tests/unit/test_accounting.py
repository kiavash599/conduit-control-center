# SPDX-License-Identifier: MIT
"""
Unit tests for backend/traffic/accounting.py (P0 Step 2 — pure state machine).

Test matrix category C (accounting state machine) and the bridge-classification
parts of category D:
  - bootstrap on empty DB -> initial_baseline (counted=0, bytes_delta=0)
  - normal increment -> counted delta, interval = Δuptime
  - uptime-decrease reset -> new epoch + epoch_baseline (anomaly='reset')
  - reset + build change -> reason 'build_change_with_reset'
  - counter decrease without uptime decrease -> negative_clamped (counted=0)
  - build-rev change without reset -> build_change_no_reset marker (counted=1)
  - gap_spanning when Δuptime exceeds the threshold
  - recovered on the first tick of a session (same epoch, no reset)
  - counted rule: 0 iff initial_baseline OR negative_clamped
  - seq strictly monotonic; whole-row clamp; exact large-magnitude deltas
  - reset precedence over the first-tick 'recovered' label

The accounting core is pure, so these tests construct inputs directly and need
no database, clock, or HTTP.
"""
from __future__ import annotations

from backend.traffic.accounting import (
    ANOMALY_BUILD_CHANGE_NO_RESET,
    ANOMALY_NEGATIVE_CLAMPED,
    ANOMALY_NONE,
    ANOMALY_RESET,
    REASON_BUILD_CHANGE_WITH_RESET,
    REASON_RESET,
    REASON_STARTUP,
    SOURCE_EPOCH_BASELINE,
    SOURCE_GAP_SPANNING,
    SOURCE_INITIAL_BASELINE,
    SOURCE_NORMAL,
    SOURCE_RECOVERED,
    Epoch,
    Snapshot,
    counted_for,
    decide,
)
from backend.traffic.models import CounterReading

NOW = "2026-06-12T15:00:00Z"


def _reading(up, down, uptime, build_rev=None):
    return CounterReading(bytes_up=up, bytes_down=down, uptime_seconds=uptime, build_rev=build_rev)


def _snap(id=1, seq=1, epoch_id=1, uptime=100.0, up=1000, down=2000):
    return Snapshot(
        id=id, seq=seq, epoch_id=epoch_id,
        uptime_seconds=uptime, bytes_up=up, bytes_down=down,
    )


def _epoch(id=1, build_rev="A"):
    return Epoch(id=id, build_rev=build_rev)


# ---------------------------------------------------------------------------
# (1) Bootstrap
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_empty_db_is_initial_baseline(self):
        d = decide(_reading(500, 700, 42.0, "rev1"), None, None, NOW, False)
        assert d.new_epoch is not None
        assert d.new_epoch.reason == REASON_STARTUP
        assert d.new_epoch.first_uptime_seconds == 42.0
        assert d.close_prev_epoch is False
        assert d.snapshot.seq == 1
        assert d.snapshot.bytes_up == 500 and d.snapshot.bytes_down == 700
        assert d.delta.source == SOURCE_INITIAL_BASELINE
        assert d.delta.counted == 0
        assert d.delta.bytes_up_delta == 0 and d.delta.bytes_down_delta == 0
        assert d.delta.prev_snapshot_id is None


# ---------------------------------------------------------------------------
# (5) Normal increment
# ---------------------------------------------------------------------------


class TestNormal:
    def test_normal_counted_delta(self):
        prev = _snap(id=7, seq=5, uptime=100.0, up=1000, down=2000)
        d = decide(_reading(1500, 2600, 160.0, "A"), prev, _epoch(), NOW, False)
        assert d.new_epoch is None and d.close_prev_epoch is False
        assert d.delta.source == SOURCE_NORMAL
        assert d.delta.anomaly_flag == ANOMALY_NONE
        assert d.delta.counted == 1
        assert d.delta.bytes_up_delta == 500 and d.delta.bytes_down_delta == 600
        assert d.delta.interval_seconds == 60.0
        assert d.delta.seq == 6
        assert d.snapshot.seq == 6
        assert d.delta.prev_snapshot_id == 7

    def test_seq_is_prev_plus_one(self):
        prev = _snap(seq=41)
        d = decide(_reading(1001, 2001, 110.0, "A"), prev, _epoch(), NOW, False)
        assert d.delta.seq == 42 and d.snapshot.seq == 42

    def test_large_magnitude_delta_exact(self):
        prev = _snap(up=2_000_000_000, down=3_000_000_000, uptime=100.0)
        d = decide(_reading(2_179_426_155, 3_292_319_749, 160.0, "A"), prev, _epoch(), NOW, False)
        assert d.delta.bytes_up_delta == 179_426_155
        assert d.delta.bytes_down_delta == 292_319_749


# ---------------------------------------------------------------------------
# (2) Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_uptime_decrease_is_epoch_baseline(self):
        prev = _snap(id=9, seq=10, uptime=1000.0, up=900, down=800)
        d = decide(_reading(50, 70, 5.0, "A"), prev, _epoch(build_rev="A"), NOW, False)
        assert d.new_epoch is not None
        assert d.new_epoch.reason == REASON_RESET
        assert d.close_prev_epoch is True
        assert d.delta.source == SOURCE_EPOCH_BASELINE
        assert d.delta.anomaly_flag == ANOMALY_RESET
        assert d.delta.counted == 1
        assert d.delta.bytes_up_delta == 50 and d.delta.bytes_down_delta == 70  # absolute
        assert d.delta.interval_seconds == 5.0
        assert d.delta.prev_snapshot_id == 9

    def test_reset_with_build_change(self):
        prev = _snap(uptime=1000.0)
        d = decide(_reading(10, 20, 3.0, "B"), prev, _epoch(build_rev="A"), NOW, False)
        assert d.new_epoch.reason == REASON_BUILD_CHANGE_WITH_RESET

    def test_reset_takes_precedence_over_first_tick(self):
        # Even on the first tick of a session, an uptime decrease is epoch_baseline,
        # not 'recovered'.
        prev = _snap(uptime=1000.0)
        d = decide(_reading(10, 20, 3.0, "A"), prev, _epoch(), NOW, True)
        assert d.delta.source == SOURCE_EPOCH_BASELINE


# ---------------------------------------------------------------------------
# (3) negative_clamped
# ---------------------------------------------------------------------------


class TestNegativeClamped:
    def test_counter_decrease_without_uptime_decrease_clamps(self):
        prev = _snap(id=4, seq=2, uptime=100.0, up=1000, down=2000)
        # uptime advances normally, but bytes_down went backwards
        d = decide(_reading(1100, 1900, 160.0, "A"), prev, _epoch(), NOW, False)
        assert d.new_epoch is None
        assert d.delta.anomaly_flag == ANOMALY_NEGATIVE_CLAMPED
        assert d.delta.counted == 0
        assert d.delta.bytes_up_delta == 0 and d.delta.bytes_down_delta == 0  # whole-row clamp
        # snapshot still records the current absolutes for future deltas
        assert d.snapshot.bytes_up == 1100 and d.snapshot.bytes_down == 1900

    def test_whole_row_clamp_when_only_one_counter_drops(self):
        prev = _snap(up=1000, down=2000, uptime=100.0)
        d = decide(_reading(999, 5000, 160.0, "A"), prev, _epoch(), NOW, False)
        assert d.delta.bytes_up_delta == 0 and d.delta.bytes_down_delta == 0


# ---------------------------------------------------------------------------
# (4) build_change_no_reset marker
# ---------------------------------------------------------------------------


class TestBuildChangeNoReset:
    def test_build_change_without_reset_is_marked_but_counted(self):
        prev = _snap(up=1000, down=2000, uptime=100.0)
        d = decide(_reading(1500, 2500, 160.0, "B"), prev, _epoch(build_rev="A"), NOW, False)
        assert d.new_epoch is None
        assert d.delta.anomaly_flag == ANOMALY_BUILD_CHANGE_NO_RESET
        assert d.delta.counted == 1
        assert d.delta.bytes_up_delta == 500 and d.delta.bytes_down_delta == 500


# ---------------------------------------------------------------------------
# gap_spanning / recovered
# ---------------------------------------------------------------------------


class TestGapAndRecovered:
    def test_gap_spanning_when_uptime_jump_exceeds_threshold(self):
        prev = _snap(uptime=100.0, up=1000, down=2000)
        d = decide(
            _reading(1100, 2100, 100.0 + 500.0, "A"),
            prev, _epoch(), NOW, False, gap_threshold_seconds=90.0,
        )
        assert d.delta.source == SOURCE_GAP_SPANNING
        assert d.delta.counted == 1
        assert d.delta.bytes_up_delta == 100

    def test_first_tick_of_session_is_recovered(self):
        prev = _snap(uptime=100.0, up=1000, down=2000)
        d = decide(_reading(1100, 2100, 130.0, "A"), prev, _epoch(), NOW, True)
        assert d.delta.source == SOURCE_RECOVERED
        assert d.delta.counted == 1

    def test_not_first_tick_small_interval_is_normal(self):
        prev = _snap(uptime=100.0)
        d = decide(_reading(1100, 2100, 130.0, "A"), prev, _epoch(), NOW, False)
        assert d.delta.source == SOURCE_NORMAL


# ---------------------------------------------------------------------------
# counted rule
# ---------------------------------------------------------------------------


class TestCountedRule:
    def test_counted_for_matrix(self):
        assert counted_for(SOURCE_INITIAL_BASELINE, ANOMALY_NONE) == 0
        assert counted_for(SOURCE_NORMAL, ANOMALY_NEGATIVE_CLAMPED) == 0
        assert counted_for(SOURCE_EPOCH_BASELINE, ANOMALY_RESET) == 1
        assert counted_for(SOURCE_NORMAL, ANOMALY_BUILD_CHANGE_NO_RESET) == 1
        assert counted_for(SOURCE_GAP_SPANNING, ANOMALY_NONE) == 1
        assert counted_for(SOURCE_RECOVERED, ANOMALY_NONE) == 1
