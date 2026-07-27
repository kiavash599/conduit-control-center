# SPDX-License-Identifier: MIT
"""
Unit tests for backend/display_prefs.py — the dashboard display timezone.

The preference affects RENDERING ONLY; storage and transport stay UTC. These
tests cover validation, the fail-closed default, and the persist path. The
`zoneinfo` lookups need an IANA database: Linux provides one system-wide, and
`tzdata` is declared in requirements-dev.txt so Windows test runs also resolve.
"""
from __future__ import annotations

import pytest

from backend import display_prefs as dp


class TestValidation:
    @pytest.mark.parametrize(
        "name",
        ["UTC", "Europe/Stockholm", "Asia/Yangon", "Asia/Kathmandu", "America/New_York"],
    )
    def test_known_zones_are_valid(self, name):
        assert dp.is_valid_timezone(name) is True

    @pytest.mark.parametrize(
        "name",
        ["", "   ", None, "Not/AZone", "Mars/Olympus", "UTC+2", 5, [], "../../etc/passwd"],
    )
    def test_unknown_or_malformed_are_invalid(self, name):
        assert dp.is_valid_timezone(name) is False

    def test_list_timezones_is_sorted_and_contains_utc(self):
        zones = dp.list_timezones()
        assert zones == sorted(zones)
        assert "UTC" in zones


class TestEffectiveTimezone:
    async def test_absent_setting_defaults_to_utc(self, monkeypatch):
        async def _get(key, default=None):
            return None

        monkeypatch.setattr(dp, "get_setting", _get)
        assert await dp.effective_timezone_name() == "UTC"
        assert str(await dp.effective_timezone()) == "UTC"

    async def test_valid_override_is_used(self, monkeypatch):
        async def _get(key, default=None):
            assert key == dp.DASHBOARD_TIMEZONE_KEY
            return "Asia/Yangon"

        monkeypatch.setattr(dp, "get_setting", _get)
        assert await dp.effective_timezone_name() == "Asia/Yangon"
        assert str(await dp.effective_timezone()) == "Asia/Yangon"

    async def test_whitespace_is_tolerated(self, monkeypatch):
        async def _get(key, default=None):
            return "  Europe/Stockholm  "

        monkeypatch.setattr(dp, "get_setting", _get)
        assert await dp.effective_timezone_name() == "Europe/Stockholm"

    async def test_invalid_stored_value_fails_closed_to_utc(self, monkeypatch):
        """A stale or hand-edited row must never break page rendering."""
        async def _get(key, default=None):
            return "Not/AZone"

        monkeypatch.setattr(dp, "get_setting", _get)
        assert await dp.effective_timezone_name() == "UTC"
        assert str(await dp.effective_timezone()) == "UTC"


class TestSetTimezone:
    async def test_valid_zone_is_persisted(self, monkeypatch):
        captured = {}

        async def _set(key, value):
            captured["key"] = key
            captured["value"] = value

        monkeypatch.setattr(dp, "set_setting", _set)
        saved = await dp.set_timezone("Asia/Yangon")
        assert saved == "Asia/Yangon"
        assert captured == {"key": dp.DASHBOARD_TIMEZONE_KEY, "value": "Asia/Yangon"}

    async def test_unknown_zone_raises_and_persists_nothing(self, monkeypatch):
        calls = []

        async def _set(key, value):
            calls.append((key, value))

        monkeypatch.setattr(dp, "set_setting", _set)
        with pytest.raises(ValueError):
            await dp.set_timezone("Not/AZone")
        assert calls == []
