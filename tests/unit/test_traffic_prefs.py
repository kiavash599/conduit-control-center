# SPDX-License-Identifier: MIT
"""
Unit tests for backend/traffic/prefs.py.

The resolver bridges the app_settings override and the config.json install
default. These tests monkeypatch both collaborators, so they need no database
or config file and run on any platform.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.traffic import prefs


@pytest.mark.parametrize(
    "stored,cfg_default,expected",
    [
        (None, False, False),          # no override, ship-dark default
        (None, True, True),            # no override, default on
        ("true", False, True),         # override on beats default off
        ("false", True, False),        # override off beats default on
        ("TRUE", False, True),         # case-insensitive
        (" false ", True, False),      # whitespace-tolerant
        ("garbage", True, True),       # unrecognised -> fall back to default
        ("garbage", False, False),
        ("", True, True),              # empty -> unrecognised -> default
    ],
)
async def test_effective_collector_enabled(monkeypatch, stored, cfg_default, expected):
    async def fake_get_setting(key, default=None):
        assert key == prefs.TRAFFIC_COLLECTOR_ENABLED_KEY
        return stored

    monkeypatch.setattr(prefs, "get_setting", fake_get_setting)
    monkeypatch.setattr(
        prefs, "get_app_config",
        lambda: SimpleNamespace(traffic_collector_enabled=cfg_default),
    )
    assert await prefs.effective_collector_enabled() is expected


@pytest.mark.parametrize("enabled,written", [(True, "true"), (False, "false")])
async def test_set_collector_enabled_writes_canonical(monkeypatch, enabled, written):
    captured = {}

    async def fake_set_setting(key, value):
        captured["key"] = key
        captured["value"] = value

    monkeypatch.setattr(prefs, "set_setting", fake_set_setting)
    await prefs.set_collector_enabled(enabled)
    assert captured == {
        "key": prefs.TRAFFIC_COLLECTOR_ENABLED_KEY,
        "value": written,
    }
