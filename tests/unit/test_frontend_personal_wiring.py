# SPDX-License-Identifier: MIT
"""C6d static/presence wiring for the Personal Mode card (Slice 1 + Slice 2).

Pure file-content assertions (no app import, no runtime): the dashboard template
exposes the element ids personal.js consumes, the module is wired after
conduit_config.js, and it is CSP-safe + token-safe. Slice 2 adds the create
flow, so POST is now legitimate (PUT/DELETE are not), and a guard enforces that
the module never references the pairing token (`.token`). Guards the wiring in
CI ("static green") without a JS test toolchain.
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


def test_personal_create_ids_present():
    html = _dashboard()
    for needle in (
        'id="personal-create"',
        'id="pm-create-name"',
        'id="pm-create-error"',
        'id="pm-create-btn"',
        'id="pm-status"',
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


def test_personal_js_create_wiring():
    js = _personal_js()
    assert "/api/conduit/personal/compartment" in js
    assert "method: 'POST'" in js
    assert "getCsrf" in js
    assert "X-CSRF-Token" in js


def test_personal_js_is_csp_safe_and_token_safe():
    js = _personal_js()
    # CSP-safe: no DOM-injection sink, no eval. Match ".innerHTML", not the bare
    # word (which appears in the module docstring).
    assert ".innerHTML" not in js
    assert "eval(" not in js
    # Token-safe: Slice 2 must never read the pairing token from the response.
    assert ".token" not in js
    # Slice 2 uses POST (create) only; no other mutating verbs yet.
    for verb in (
        "method: 'PUT'", "method: 'DELETE'",
        'method: "PUT"', 'method: "DELETE"',
    ):
        assert verb not in js, verb
