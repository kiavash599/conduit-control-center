# SPDX-License-Identifier: MIT
"""C6d static/presence wiring for the Personal Mode card (Slices 1–3).

Pure file-content assertions (no app import, no runtime): the dashboard template
exposes the element ids personal.js consumes, the scripts are wired in the right
order, and the module is CSP-safe + token-lifecycle-safe.

Slice 3 adds the token panel + client-side QR, so personal.js now legitimately
reads the token from GET /token to render it; the Slice-2 `.token`-absent guard
is therefore replaced by lifecycle guards: the module must not persist or log the
token (no localStorage / sessionStorage / cookie write / console), and it must
clear the token on close. Guards the wiring in CI ("static green") without a JS
test toolchain.
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


def test_personal_token_ids_present():
    html = _dashboard()
    for needle in (
        'id="pm-view-btn"',
        'id="pm-token-panel"',
        'id="pm-token-text"',
        'id="pm-qr"',
        'id="pm-token-close"',
    ):
        assert needle in html, needle


def test_script_order_qrcodegen_then_personal_after_config():
    html = _dashboard()
    cc = html.find("js/conduit_config.js")
    qr = html.find("js/vendor/qrcodegen.js")
    pj = html.find("js/personal.js")
    assert cc != -1 and qr != -1 and pj != -1
    assert cc < qr < pj, "load order must be conduit_config.js -> qrcodegen.js -> personal.js"


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


def test_personal_js_token_wiring():
    js = _personal_js()
    assert "/api/conduit/personal/token" in js
    assert "qrcodegen.QrCode.encodeText" in js
    assert "closeTokenPanel" in js


def test_personal_max_ids_present():
    html = _dashboard()
    for needle in (
        'id="personal-max"',
        'id="personal-max-edit"',
        'id="pm-max-input"',
        'id="pm-max-apply"',
        'id="pm-max-confirm"',
        'id="pm-max-confirm-btn"',
        'id="pm-max-cancel"',
        'id="pm-max-summary"',
        'id="pm-max-status"',
    ):
        assert needle in html, needle


def test_personal_js_maxclients_wiring():
    js = _personal_js()
    assert "/api/conduit/personal/max-clients" in js
    assert "method: 'PUT'" in js
    assert "X-CSRF-Token" in js
    # Result routing must branch on body.status, not bare HTTP 200.
    for state in ("no-op", "applied", "rolled_back", "rollback_failed"):
        assert state in js, state


def test_personal_js_is_csp_safe_and_token_lifecycle_safe():
    js = _personal_js()
    # CSP-safe: no DOM-injection sink, no eval/Function. Match ".innerHTML",
    # not the bare word (which appears in the module docstring).
    assert ".innerHTML" not in js
    assert "eval(" not in js
    assert "Function(" not in js
    assert "document.write" not in js
    # Token-lifecycle-safe: never persist or log the token.
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "document.cookie =" not in js   # getCsrf only reads document.cookie
    assert "console" not in js
    # Slice 4 uses GET (status/token) + POST (create) + PUT (max-clients).
    # No DELETE is ever issued.
    for verb in ("method: 'DELETE'", 'method: "DELETE"'):
        assert verb not in js, verb
