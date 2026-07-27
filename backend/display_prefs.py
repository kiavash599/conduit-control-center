# SPDX-License-Identifier: MIT
"""
backend/display_prefs.py
------------------------
Operator display preferences — currently the dashboard **display timezone**.

Storage and transport stay **UTC everywhere**; this preference affects only how
absolute times are *rendered* (dashboard timestamps, the hourly chart axis, and
the local-day grid for the 7d/30d ranges). Conduit's own schedule contract
(``InproxyReducedStartTime`` / ``EndTime``) is HH:MM **UTC** and is unaffected —
the dashboard shows both UTC and local for that field.

The value is a non-secret IANA zone name (e.g. ``Europe/Stockholm``,
``Asia/Yangon``) kept in the existing ``app_settings`` key/value table, the same
store used by the v0.3.20 recording toggle. There is **no privileged
``config.json`` write**, so the root-owned config boundary is untouched.

Precedence:
    app_settings override, when it names a resolvable IANA zone
    else -> "UTC" (unchanged behaviour for existing installs)

Unknown or unresolvable names fail closed to UTC rather than raising, so a
stale or hand-edited row can never break page rendering.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from backend.database import get_setting, set_setting

DASHBOARD_TIMEZONE_KEY = "dashboard_timezone"
DEFAULT_TIMEZONE = "UTC"


def is_valid_timezone(name: str | None) -> bool:
    """True when *name* resolves to a real IANA zone."""
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, ModuleNotFoundError):
        return False
    return True


def list_timezones() -> list[str]:
    """Sorted IANA zone names available on this host (for the Settings picker)."""
    try:
        return sorted(available_timezones())
    except Exception:  # noqa: BLE001 - a missing tzdata must not break the page
        return [DEFAULT_TIMEZONE]


async def effective_timezone_name() -> str:
    """The effective display timezone name (validated; UTC when unset/invalid)."""
    stored = await get_setting(DASHBOARD_TIMEZONE_KEY)
    if isinstance(stored, str):
        stored = stored.strip()
    return stored if is_valid_timezone(stored) else DEFAULT_TIMEZONE


async def effective_timezone() -> ZoneInfo:
    """The effective display timezone as a ``ZoneInfo`` (never raises)."""
    try:
        return ZoneInfo(await effective_timezone_name())
    except Exception:  # noqa: BLE001 - fail closed to UTC
        return ZoneInfo(DEFAULT_TIMEZONE)


async def set_timezone(name: str) -> str:
    """Persist a validated IANA zone name. Returns the stored value.

    Raises ValueError on an unknown zone so the API can answer 422 rather than
    silently storing something that would later fall back to UTC.
    """
    candidate = (name or "").strip()
    if not is_valid_timezone(candidate):
        raise ValueError(f"unknown IANA timezone: {name!r}")
    await set_setting(DASHBOARD_TIMEZONE_KEY, candidate)
    return candidate
