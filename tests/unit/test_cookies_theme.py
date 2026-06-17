# SPDX-License-Identifier: MIT
"""Unit tests for the theme cookie reader (Theme Support, TS2).

read_theme() is pure (no config/I/O): it validates the cookie value and
degrades any missing/tampered/unknown value to DEFAULT_THEME ("dark").
set_theme_cookie attributes are covered end-to-end by the API test.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.auth.cookies import DEFAULT_THEME, VALID_THEMES, read_theme


def _req(theme=None):
    return SimpleNamespace(cookies=({} if theme is None else {"theme": theme}))


def test_read_theme_valid_values():
    for t in ("light", "dark", "system"):
        assert read_theme(_req(t)) == t


def test_read_theme_missing_defaults_dark():
    assert read_theme(_req(None)) == "dark"


def test_read_theme_invalid_defaults_dark():
    assert read_theme(_req("neon")) == "dark"
    assert read_theme(_req("")) == "dark"
    assert read_theme(_req("LIGHT")) == "dark"   # case-sensitive; not a valid value


def test_constants():
    assert DEFAULT_THEME == "dark"
    assert set(VALID_THEMES) == {"light", "dark", "system"}
