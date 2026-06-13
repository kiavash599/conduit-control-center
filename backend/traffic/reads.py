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

Two surfaces:
  - get_summary(db, now_ts) -> headline (status, recording_since, lifetime,
    last_24h / last_7d windows)
  - get_series(db, range_key, now_ts) -> dense, zero-filled time buckets for the
    chart (range_key in {"24h","7d","30d"})
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


async def get_series(db: "aiosqlite.Connection", *, range_key: str, now_ts: str) -> dict:
    """
    Dense, zero-filled time buckets for the trend chart.

    range_key '24h' -> 24 hourly buckets; '7d'/'30d' -> 7/30 daily buckets.
    Buckets are ordered oldest -> newest; gaps are zero-filled so the client
    renders a continuous axis. Raises ValueError on an unknown range.
    """
    if range_key not in RANGES:
        raise ValueError(f"unknown range: {range_key!r}")
    granularity, n = RANGES[range_key]
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
