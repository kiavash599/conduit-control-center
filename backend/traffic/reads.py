# SPDX-License-Identifier: MIT
"""
backend/traffic/reads.py
------------------------
Read-only data access for the Traffic Read API (v0.2).

Serves the dashboard "Lifetime & history" surface from the persisted P0 tables.
Pure reads — no writes, no transaction management. Reuses
``retention.compute_lifetime`` for the lifetime total; everything else is small
SELECTs over the rollup / epoch / health tables.

Like the rest of the traffic package, this references ``aiosqlite`` only as a
type hint and exposes its SQL as constants, so it can be imported and
unit-tested without the aiosqlite runtime. All timestamps/buckets are UTC;
display-timezone conversion is the client's concern.

Read surfaces:
  - get_summary(db, now_ts) -> headline (status, recording_since, lifetime,
    last_24h / last_7d windows)
  - get_series(db, range_key, now_ts) -> dense, zero-filled time buckets for the
    chart (range_key in {"24h","7d","30d"})
  - get_hourly_series(db, hours, now_ts) -> dense, zero-filled hourly buckets over
    a multi-day window (advisor-internal; default 168 h = 7 days)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from backend.traffic.retention import compute_lifetime

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite

# range_key -> (granularity, number of buckets in the dense grid)
RANGES: dict[str, tuple[str, int]] = {
    "24h": ("hour", 24),
    "7d": ("day", 7),
    "30d": ("day", 30),
}

# ---------------------------------------------------------------------------
# SQL (read-only)
# ---------------------------------------------------------------------------
SQL_RECORDING_SINCE = "SELECT MIN(started_at_utc) FROM traffic_epoch"
SQL_HEALTH = "SELECT status, last_ok_ts_utc FROM collector_health WHERE id = 1"
SQL_SUM_HOURLY_SINCE = (
    "SELECT COALESCE(SUM(bytes_up), 0), COALESCE(SUM(bytes_down), 0) "
    "FROM traffic_rollup_hourly WHERE bucket_utc >= ?"
)
SQL_SUM_DAILY_SINCE = (
    "SELECT COALESCE(SUM(bytes_up), 0), COALESCE(SUM(bytes_down), 0) "
    "FROM traffic_rollup_daily WHERE bucket_utc >= ?"
)
SQL_HOURLY_RANGE = (
    "SELECT bucket_utc, bytes_up, bytes_down FROM traffic_rollup_hourly "
    "WHERE bucket_utc >= ? AND bucket_utc <= ?"
)
SQL_DAILY_RANGE = (
    "SELECT bucket_utc, bytes_up, bytes_down FROM traffic_rollup_daily "
    "WHERE bucket_utc >= ? AND bucket_utc <= ?"
)
# Exact partial-hour sums for local-day boundaries that bisect a UTC hour
# (zones with a sub-hour offset, e.g. Asia/Yangon +06:30). Only counted rows
# contribute, matching the rollup semantics.
SQL_SUM_DELTAS_RANGE = (
    "SELECT COALESCE(SUM(bytes_up_delta), 0), COALESCE(SUM(bytes_down_delta), 0) "
    "FROM traffic_delta WHERE ts_utc >= ? AND ts_utc < ? AND counted = 1"
)
SQL_MIN_DELTA_TS = "SELECT MIN(ts_utc) FROM traffic_delta WHERE counted = 1"


# ---------------------------------------------------------------------------
# Pure UTC bucket-grid helpers
# ---------------------------------------------------------------------------
def _parse(ts_utc: str) -> datetime:
    return datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:00:00Z")


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _hour_keys(now_ts: str, n: int) -> list[str]:
    """The n hourly bucket keys ending at the current hour (oldest -> newest)."""
    end = _parse(now_ts).replace(minute=0, second=0, microsecond=0)
    return [_hour_key(end - timedelta(hours=i)) for i in range(n - 1, -1, -1)]


def _day_keys(now_ts: str, n: int) -> list[str]:
    """The n daily bucket keys ending today (oldest -> newest)."""
    end = _parse(now_ts).replace(hour=0, minute=0, second=0, microsecond=0)
    return [_day_key(end - timedelta(days=i)) for i in range(n - 1, -1, -1)]


# ---------------------------------------------------------------------------
# Read surfaces
# ---------------------------------------------------------------------------
async def get_summary(db: "aiosqlite.Connection", *, now_ts: str) -> dict:
    """
    Headline for the Lifetime & history card.

    Returns recording status, recording_since, lifetime totals (None when nothing
    has been recorded), and last-24h / last-7d windows (zeros when empty). No
    collector internals (holder_id, last_error) are exposed.
    """
    cur = await db.execute(SQL_RECORDING_SINCE)
    recording_since = (await cur.fetchone())[0]

    cur = await db.execute(SQL_HEALTH)
    health = await cur.fetchone()
    status = health["status"] if health is not None else "disabled"
    last_ok = health["last_ok_ts_utc"] if health is not None else None

    lifetime = None
    if recording_since is not None:
        up, down = await compute_lifetime(db)
        lifetime = {"bytes_up": up, "bytes_down": down}

    cutoff_24h = _hour_keys(now_ts, 24)[0]
    cutoff_7d = _day_keys(now_ts, 7)[0]
    cur = await db.execute(SQL_SUM_HOURLY_SINCE, (cutoff_24h,))
    h24_up, h24_down = await cur.fetchone()
    cur = await db.execute(SQL_SUM_DAILY_SINCE, (cutoff_7d,))
    d7_up, d7_down = await cur.fetchone()

    return {
        "status": status,
        "recording_since": recording_since,
        "last_ok_ts_utc": last_ok,
        "lifetime": lifetime,
        "windows": {
            "last_24h": {"bytes_up": h24_up, "bytes_down": h24_down},
            "last_7d": {"bytes_up": d7_up, "bytes_down": d7_down},
        },
    }


# ---------------------------------------------------------------------------
# Local-day bucket grid (display timezone)
# ---------------------------------------------------------------------------
# Storage stays UTC. For the 7d/30d ranges the dense grid may instead be built
# from the operator's display timezone, because a UTC calendar day is NOT a
# local calendar day (in Asia/Yangon, +06:30, the UTC day 2026-07-25 spans
# 06:30 on the 25th to 06:30 on the 26th local). Relabelling a UTC-day rollup
# with a local date would misstate it, so local days are re-aggregated at read
# time from `traffic_rollup_hourly` -- the documented source of truth for the
# series, retained far longer (default 180 days) than these views need. The
# `traffic_rollup_daily` table remains the UTC-day convenience cache.
#
# Each whole UTC hour is summed from the hourly rollup. When the zone's offset
# is not a whole hour the day boundary bisects an hour; those (at most two)
# partial hours are resolved EXACTLY from `traffic_delta` (precise `ts_utc`,
# default 90-day retention -- covering both the 7d and 30d views). If the
# deltas for a boundary have already aged out, the hour is attributed to the
# local day in which it STARTS and the bucket is flagged `approximate` rather
# than silently fabricating a split.


def _hour_floor(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _local_day_keys(now_ts: str, n: int, tz) -> list[str]:
    """The n local calendar-day keys ending today in *tz* (oldest -> newest)."""
    today = _parse(now_ts).astimezone(tz).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _local_day_utc_range(day_key: str, tz) -> tuple[datetime, datetime]:
    """UTC [start, end) instants for a local calendar day.

    Derived from consecutive local midnights, so DST transition days are
    correctly 23 or 25 hours long rather than an assumed 24.
    """
    y, m, d = (int(p) for p in day_key.split("-"))
    start_local = datetime(y, m, d, tzinfo=tz)
    end_local = (start_local + timedelta(days=2)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Step back to the next local midnight (the +2/-1 dance avoids landing on a
    # skipped local midnight in zones whose DST shift occurs at 00:00).
    end_local -= timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def _sum_hours(db: "aiosqlite.Connection", first: datetime, stop: datetime) -> tuple[int, int]:
    """Sum hourly rollups for whole hours starting in [first, stop)."""
    if first >= stop:
        return 0, 0
    cur = await db.execute(
        SQL_HOURLY_RANGE, (_hour_key(first), _hour_key(stop - timedelta(hours=1)))
    )
    up = down = 0
    for row in await cur.fetchall():
        up += row["bytes_up"]
        down += row["bytes_down"]
    return up, down


async def _sum_deltas(db: "aiosqlite.Connection", start: datetime, end: datetime) -> tuple[int, int]:
    """Exact sum of counted deltas in [start, end)."""
    cur = await db.execute(
        SQL_SUM_DELTAS_RANGE,
        (start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    row = await cur.fetchone()
    return (row[0] or 0), (row[1] or 0)


async def _local_day_totals(
    db: "aiosqlite.Connection", start: datetime, end: datetime, delta_floor: datetime | None
) -> tuple[int, int, bool]:
    """Bytes in [start, end): whole hours from the rollup, partial boundary
    hours from deltas. Returns (up, down, approximate)."""
    first_full = start if start == _hour_floor(start) else _hour_floor(start) + timedelta(hours=1)
    last_full = _hour_floor(end)

    deltas_cover = delta_floor is not None and delta_floor <= start
    if not deltas_cover:
        # Fallback: attribute each hour to the local day in which it starts.
        stop = last_full + timedelta(hours=1) if last_full < end else last_full
        up, down = await _sum_hours(db, first_full, stop)
        # Only genuinely approximate when a boundary actually bisects an hour.
        approx = (start != _hour_floor(start)) or (end != _hour_floor(end))
        return up, down, approx

    up, down = await _sum_hours(db, first_full, last_full)
    for a, b in ((start, first_full), (last_full, end)):
        if a < b:
            du, dd = await _sum_deltas(db, a, b)
            up += du
            down += dd
    return up, down, False


async def _get_local_day_series(
    db: "aiosqlite.Connection", *, range_key: str, now_ts: str, n: int, tz
) -> dict:
    """Dense local-day buckets for the 7d/30d ranges in the display timezone."""
    cur = await db.execute(SQL_MIN_DELTA_TS)
    row = await cur.fetchone()
    delta_floor = _parse(row[0]) if row and row[0] else None

    buckets = []
    for key in _local_day_keys(now_ts, n, tz):
        start, end = _local_day_utc_range(key, tz)
        up, down, approx = await _local_day_totals(db, start, end, delta_floor)
        bucket = {"bucket_utc": key, "bytes_up": up, "bytes_down": down}
        if approx:
            bucket["approximate"] = True
        buckets.append(bucket)
    return {"range": range_key, "granularity": "day", "buckets": buckets}


async def get_series(db: "aiosqlite.Connection", *, range_key: str, now_ts: str, tz=None) -> dict:
    """
    Dense, zero-filled time buckets for the trend chart.

    range_key '24h' -> 24 hourly buckets; '7d'/'30d' -> 7/30 daily buckets.
    Buckets are ordered oldest -> newest; gaps are zero-filled so the client
    renders a continuous axis. Raises ValueError on an unknown range.

    *tz* is an optional display timezone (``zoneinfo.ZoneInfo``). When supplied
    for a daily range, buckets are LOCAL calendar days re-aggregated from the
    hourly rollup (see the local-day grid section above); ``bucket_utc`` then
    carries the local date. Hourly buckets are instants and need no
    re-aggregation. Omitting *tz* preserves the original UTC-day behaviour.
    """
    if range_key not in RANGES:
        raise ValueError(f"unknown range: {range_key!r}")
    granularity, n = RANGES[range_key]
    if granularity == "day" and tz is not None and str(tz) != "UTC":
        return await _get_local_day_series(
            db, range_key=range_key, now_ts=now_ts, n=n, tz=tz
        )
    if granularity == "hour":
        keys = _hour_keys(now_ts, n)
        sql = SQL_HOURLY_RANGE
    else:
        keys = _day_keys(now_ts, n)
        sql = SQL_DAILY_RANGE

    cur = await db.execute(sql, (keys[0], keys[-1]))
    found = {
        row["bucket_utc"]: (row["bytes_up"], row["bytes_down"])
        for row in await cur.fetchall()
    }
    buckets = [
        {
            "bucket_utc": k,
            "bytes_up": found.get(k, (0, 0))[0],
            "bytes_down": found.get(k, (0, 0))[1],
        }
        for k in keys
    ]
    return {"range": range_key, "granularity": granularity, "buckets": buckets}


async def get_hourly_series(
    db: "aiosqlite.Connection", *, hours: int = 168, now_ts: str
) -> list[dict]:
    """
    Dense, zero-filled hourly buckets for the last ``hours`` hours (oldest -> newest).

    Advisor-internal (A1.3): supplies the multi-day hourly history the Contribution
    Advisor needs for its reduced-mode and decline analysis (default 168 h = 7 days).
    The public ``/api/traffic/series`` range enum is intentionally left unchanged.

    Read-only and aggregate-only: each bucket is ``{bucket_utc, bytes_up, bytes_down}``
    from ``traffic_rollup_hourly`` (no per-region / per-scope data). Missing hours
    are zero-filled so the caller gets a continuous, fixed-length series. Raises
    ``ValueError`` on a non-positive ``hours``.
    """
    if hours <= 0:
        raise ValueError(f"hours must be positive: {hours!r}")
    keys = _hour_keys(now_ts, hours)
    cur = await db.execute(SQL_HOURLY_RANGE, (keys[0], keys[-1]))
    found = {
        row["bucket_utc"]: (row["bytes_up"], row["bytes_down"])
        for row in await cur.fetchall()
    }
    return [
        {
            "bucket_utc": k,
            "bytes_up": found.get(k, (0, 0))[0],
            "bytes_down": found.get(k, (0, 0))[1],
        }
        for k in keys
    ]
