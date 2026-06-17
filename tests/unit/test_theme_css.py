# SPDX-License-Identifier: MIT
"""
TS1 regression guard for theme tokens in base.css.

Asserts the light palette is populated, the system mapping (prefers-color-scheme)
exists, the new component tokens are defined, and the five audited colour leaks
are tokenized (the button/spinner component rules use var() tokens, and
--color-chart-down is no longer hard-coded in :root). Pure file read; no browser.
"""
from __future__ import annotations

import pathlib
import re

_CSS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "static" / "css" / "base.css"
).read_text(encoding="utf-8")


def _block(selector_regex: str) -> str:
    m = re.search(selector_regex + r"\s*\{(.*?)\}", _CSS, re.S)
    assert m, f"block not found: {selector_regex}"
    return m.group(1)


def test_light_theme_populated():
    body = _block(r'\[data-theme="light"\]')
    for tok in ("--color-bg:", "--color-surface:", "--color-text-primary:",
                "--color-accent:", "--color-success:"):
        assert tok in body, tok


def test_system_mapping_present():
    assert "prefers-color-scheme: light" in _CSS
    assert '[data-theme="system"]' in _CSS


def test_new_component_tokens_defined():
    for tok in ("--color-on-accent:", "--color-spinner-track:",
                "--color-spinner-head:", "--color-chart-down:"):
        assert tok in _CSS, tok


def test_buttons_use_on_accent_token():
    assert "var(--color-on-accent)" in _block(r"\.btn--primary")
    assert "var(--color-on-accent)" in _block(r"\.btn--danger")


def test_spinner_uses_tokens():
    body = _block(r"\.btn--loading::after")
    assert "var(--color-spinner-track)" in body
    assert "var(--color-spinner-head)" in body


def test_button_and_spinner_rules_have_no_hardcoded_white():
    for sel in (r"\.btn--primary", r"\.btn--danger", r"\.btn--loading::after"):
        body = _block(sel)
        assert "#ffffff" not in body, sel
        assert "rgba(255, 255, 255" not in body, sel


def test_chart_down_not_hardcoded_in_root():
    # The chart :root block keeps the theme-aware chart-up but no longer
    # hard-codes chart-down. (There are two :root blocks; target the chart one
    # by anchoring on --color-chart-up.)
    m = re.search(r":root\s*\{[^}]*--color-chart-up[^}]*\}", _CSS, re.S)
    assert m, "chart :root block not found"
    assert "--color-chart-down:" not in m.group(0)  # no definition (comment is fine)
