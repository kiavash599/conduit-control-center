# SPDX-License-Identifier: MIT
"""
Frontend / product guard tests for Regional Analytics (RA-2c).

Static guards over the Regions card markup + JS (no browser needed):
  * the literal "Users" must never appear -- CCC is aggregate-only and must not
    imply user identity; the UI uses "Clients";
  * regions.js must not contain ``innerHTML`` -- rendering is DOM/textContent
    only (XSS-safe), and the string is kept out of the file so a simple
    grep-based guard stays clean.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REGIONS_JS = _ROOT / "frontend" / "static" / "js" / "regions.js"
_DASHBOARD = _ROOT / "frontend" / "templates" / "dashboard.html"


def _regions_card_markup() -> str:
    """Return only the #regions-card block of dashboard.html."""
    html = _DASHBOARD.read_text(encoding="utf-8")
    start = html.index('id="regions-card"')
    end = html.index("/#regions-card", start)
    return html[start:end]


def test_regions_js_exists():
    assert _REGIONS_JS.is_file()


def test_no_users_in_regions_js():
    assert "Users" not in _REGIONS_JS.read_text(encoding="utf-8")


def test_no_users_in_regions_markup():
    assert "Users" not in _regions_card_markup()


def test_regions_markup_uses_clients_label():
    assert "Clients" in _regions_card_markup()


def test_regions_js_has_no_innerhtml():
    # DOM/textContent-only rendering; the literal must not appear anywhere
    # (including comments) so the grep-based guard is clean.
    assert "innerHTML" not in _REGIONS_JS.read_text(encoding="utf-8")
