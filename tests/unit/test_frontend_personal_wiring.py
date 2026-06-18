# SPDX-License-Identifier: MIT
"""C6d Slice 1: static/presence wiring for the read-only Personal Mode card.

Pure file-content assertions (no app import, no runtime): the dashboard template
exposes the element ids personal.js consumes, the personal.js module exists and
is read-only + CSP-safe (no innerHTML / eval, no write verbs), and the script is
wired after conduit_config.js. Guards the Slice 1 wiring in CI ("static green")
without introducing a JS test toolchain.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "frontend" / "templates" / "dashboard.html"
PERSONAL_JS = ROOT / "frontend" / "static" / "js" / "personal.js"


def _dashboard() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _personal_js() -> str:
    return PERSONAL_JS.read_text(encoding="utf-8")


def test_personal_card_ids_present():
    html = _dashboard()
    for needle in (
        'id="personal-card"',
        'id="personal-loading"',
        'id="personal-error"',
        'id="personal-body"',
        'id="pm-badge"',
        'id="pm-name"',
        'id="pm-max"',
    ):
        assert needle in html, needle


def test_personal_js_script_included_after_conduit_config():
    html = _dashboard()
    cc = html.find("js/conduit_config.js")
    pj = html.find("js/personal.js")
    assert cc != -1, "conduit_config.js script missing"
    assert pj != -1, "personal.js script missing"
    assert pj > cc, "personal.js must load after conduit_config.js"


def test_personal_js_exists_and_reads_status():
    js = _personal_js()
    assert js.strip(), "personal.js must not be empty"
    assert "'use strict'" in js
    assert "/api/conduit/personal/status" in js


def test_personal_js_is_read_only_and_csp_safe():
    js = _personal_js()
    # Slice 1 is read-only and CSP-safe: no DOM injection, no eval, no writes.
    # Match the actual sink (".innerHTML"), not the word in the file's docstring.
    assert ".innerHTML" not in js
    assert "eval(" not in js
    for verb in (
        "method: 'POST'", "method: 'PUT'", "method: 'DELETE'",
        'method: "POST"', 'method: "PUT"', 'method: "DELETE"',
    ):
        assert verb not in js, verb
