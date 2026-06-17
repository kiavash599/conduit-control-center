# SPDX-License-Identifier: MIT
"""
Static guard for the Settings theme toggle (Theme Support, TS3).

Asserts the Appearance card markup (radio group + server-rendered selection),
the .theme-option CSS class, and the settings.js wiring (POST /api/settings/theme,
instant apply via dataset.theme, setCheckedTheme helper, no innerHTML).
Pure file reads; no browser.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DASH = _ROOT / "frontend" / "templates" / "dashboard.html"
_JS = _ROOT / "frontend" / "static" / "js" / "settings.js"
_CSS = _ROOT / "frontend" / "static" / "css" / "base.css"


def _card() -> str:
    html = _DASH.read_text(encoding="utf-8")
    start = html.index('id="appearance-card"')
    return html[start:html.index("/#appearance-card", start)]


def test_appearance_card_radio_group():
    card = _card()
    assert 'id="theme-fieldset"' in card
    assert "<legend" in card
    for value in ("dark", "light", "system"):
        assert f'name="theme" value="{value}"' in card, value
    assert 'id="theme-status"' in card and 'aria-live="polite"' in card


def test_selection_is_server_rendered():
    card = _card()
    # current theme pre-checked via the Jinja default-aware expression
    assert "default('dark', true)) == 'dark'" in card
    assert "checked" in card


def test_css_theme_option_class():
    assert ".theme-option" in _CSS.read_text(encoding="utf-8")


def test_settings_js_wires_theme():
    js = _JS.read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert "/api/settings/theme" in js
    assert "document.documentElement.dataset.theme" in js
    assert "setCheckedTheme" in js
    assert "wireThemeToggle" in js
    assert 'name="theme"' in js


def test_settings_js_uses_apifetch_and_reverts():
    js = _JS.read_text(encoding="utf-8")
    assert "apiFetch('/api/settings/theme'" in js
    assert ".catch(" in js  # revert path on failure
