# SPDX-License-Identifier: MIT
"""R3: static/presence wiring + binding guards for the Ryve claim frontend.

Pure file-content assertions (no execution). Enforces the R3 binding decisions:
same-origin <img> only (no data:/blob:/copy/download/new-tab/anchor), inline
danger confirm-panel, teardown hooks, safe alt text, textContent only, and the
401/403/503/onerror handling. CSP is unaffected (no CSP file is touched).
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "frontend" / "templates" / "dashboard.html"
RYVE_JS = ROOT / "frontend" / "static" / "js" / "ryve.js"


def _dash() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _js() -> str:
    return RYVE_JS.read_text(encoding="utf-8")


def _card_slice() -> str:
    html = _dash()
    start = html.find('id="ryve-card"')
    end = html.find("/#ryve-card")
    assert start != -1 and end != -1 and end > start
    return html[start:end]


def test_card_ids_present():
    html = _dash()
    for needle in (
        'id="ryve-card"', 'id="ryve-idle"', 'id="ryve-warning"',
        'id="ryve-warning-confirm"', 'id="ryve-warning-cancel"',
        'id="ryve-display"', 'id="ryve-qr"', 'id="ryve-station"',
        'id="ryve-proxy"', 'id="ryve-close-btn"', 'id="ryve-generate-btn"',
        'id="ryve-status"',
    ):
        assert needle in html, needle


def test_card_after_personal_and_script_order():
    html = _dash()
    assert html.find("/#personal-card") < html.find('id="ryve-card"')
    assert html.find("js/personal.js") < html.find("js/ryve.js")


def test_card_safe_alt_and_no_unsafe_image_affordances():
    s = _card_slice()
    assert 'alt="Ryve claim QR code"' in s
    assert 'id="ryve-qr"' in s
    # No copy/download/new-tab/anchor-to-image in the card markup.
    assert 'target="_blank"' not in s
    assert "download" not in s
    assert "data:" not in s
    assert "blob:" not in s
    assert 'href="/api/conduit/ryve/claim/image' not in s
    assert "aria-live" in s              # #ryve-status is a live region


def test_js_api_and_csrf_wiring():
    js = _js()
    assert "'use strict'" in js
    assert "/api/conduit/ryve/claim" in js
    assert "/api/conduit/ryve/claim/image/" in js
    assert "method: 'POST'" in js
    assert "method: 'DELETE'" in js
    assert "X-CSRF-Token" in js
    assert "getCsrf" in js


def test_js_cleanup_and_error_hooks():
    js = _js()
    assert "hashchange" in js
    assert "pagehide" in js
    assert "addEventListener('error'" in js     # img onerror teardown
    assert "removeAttribute('src')" in js       # clear src, keep the node
    assert "Escape" in js
    assert "/login" in js
    assert "401" in js and "403" in js and "503" in js


def test_js_no_forbidden_constructs():
    js = _js()
    assert "data:" not in js          # no data URI
    assert "blob:" not in js          # no blob URI
    assert "createObjectURL" not in js
    assert "_blank" not in js
    assert "download" not in js
    assert "clipboard" not in js
    assert "window.open" not in js
    assert ".innerHTML" not in js
    assert "innerHTML" not in js
