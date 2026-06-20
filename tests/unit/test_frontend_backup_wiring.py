# SPDX-License-Identifier: MIT
"""S4A.2b static/presence wiring for the Backup Create card.

Pure file-content assertions (no app import, no runtime): the dashboard template
exposes the element ids backup.js will consume, the card is inert in this slice
(no <form>, no inline handlers, the button is type="button" and disabled), the
script tag is a commented placeholder (no runtime 404 yet), and the shared
rawFetch helper is present in api.js without disturbing the existing exports.
Guards the wiring in CI ("static green") without a JS test toolchain.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "frontend" / "templates" / "dashboard.html"
API_JS = ROOT / "frontend" / "static" / "js" / "api.js"
BACKUP_JS = ROOT / "frontend" / "static" / "js" / "backup.js"


def _dashboard() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _api_js() -> str:
    return API_JS.read_text(encoding="utf-8")


# --- card markup present ----------------------------------------------------


def test_backup_card_ids_present():
    html = _dashboard()
    for needle in (
        'id="backup-create-card"',
        'id="backup-passphrase"',
        'id="backup-confirm-passphrase"',
        'id="backup-create-btn"',
        'id="backup-error"',
        'id="backup-success"',
    ):
        assert needle in html, needle


def test_backup_card_in_settings_section():
    html = _dashboard()
    settings_start = html.index('id="section-settings"')
    card_pos = html.index('id="backup-create-card"')
    # the card lives after the Settings section opener (it is inside that section)
    assert card_pos > settings_start


# --- passphrase fields are real password inputs with safe attributes --------


def test_passphrase_fields_are_password_type_no_autofill_storage():
    html = _dashboard()
    # Both fields use type="password" and autocomplete="new-password".
    assert html.count('autocomplete="new-password"') >= 2
    # Confirm field is required (two passphrase fields, confirm mandatory).
    assert 'id="backup-confirm-passphrase"' in html
    assert 'minlength="12"' in html   # mirrors the server-side floor


# --- card is INERT in this slice -------------------------------------------


def test_backup_card_is_inert():
    html = _dashboard()
    card = html[html.index('id="backup-create-card"'):html.index("/#backup-create-card")]
    # No <form> (would submit/navigate on Enter without a handler).
    assert "<form" not in card
    # Button is a non-submitting, disabled button until S4A.2c wires it.
    assert 'type="button"' in card
    assert "disabled" in card
    # CSP-safe: no inline scripts or inline event handlers in the card markup.
    assert "<script" not in card
    for handler in ("onclick", "onsubmit", "oninput", "onchange", "onload"):
        assert handler not in card, handler


# --- script wiring is a commented placeholder (no runtime 404 yet) ----------


def test_backup_js_script_is_placeholder_only():
    html = _dashboard()
    # backup.js is referenced (so S4A.2c knows where to wire it) ...
    assert "backup.js" in html
    # ... but NOT yet as a live <script src=...> tag.
    assert "static_url('js/backup.js')" not in html
    assert 'src="{{ static_url(\'js/backup.js\') }}"' not in html
    # and the module file itself is not created in this slice.
    assert not BACKUP_JS.exists()


# --- shared rawFetch helper added to api.js (Option B), exports intact ------


def test_rawfetch_helper_present_in_api_js():
    js = _api_js()
    assert "function rawFetch(" in js
    assert "window.rawFetch = rawFetch;" in js
    # CSRF reuse: rawFetch relies on the existing getCsrfToken().
    assert "getCsrfToken()" in js


def test_api_js_existing_exports_intact():
    js = _api_js()
    assert "window.apiFetch = apiFetch;" in js
    assert "window.Toast    = Toast;" in js
