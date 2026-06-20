# SPDX-License-Identifier: MIT
"""Static/presence wiring for the Backup Create UI (Epic #4).

Pure file-content assertions (no app import, no runtime), so CI stays "static
green" without a JS toolchain:

  S4A.2b — the dashboard exposes the ids backup.js consumes, the card markup is
           progressive-enhancement-safe (no <form>, no inline handlers, the
           button ships disabled), and api.js carries the shared rawFetch helper.
  S4A.2c — backup.js exists and is wired with a live <script> tag, talks to
           POST /api/backup/create through rawFetch, downloads via a revoked
           object URL, and respects the security rules (textContent only; no
           storage/cookie/console; clears the passphrase fields).
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


def _backup_js() -> str:
    return BACKUP_JS.read_text(encoding="utf-8")


# --- card markup present (S4A.2b) ------------------------------------------


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
    assert card_pos > settings_start


def test_passphrase_fields_are_password_type_no_autofill_storage():
    html = _dashboard()
    assert html.count('autocomplete="new-password"') >= 2
    assert 'id="backup-confirm-passphrase"' in html
    assert 'minlength="12"' in html   # mirrors the server-side floor


def test_backup_card_markup_is_progressive_enhancement_safe():
    html = _dashboard()
    card = html[html.index('id="backup-create-card"'):html.index("/#backup-create-card")]
    # No <form> (would submit/navigate on Enter); button is non-submitting and
    # ships disabled (backup.js enables it once the handler is attached).
    assert "<form" not in card
    assert 'type="button"' in card
    assert "disabled" in card
    # CSP-safe: no inline scripts or inline event handlers in the card markup.
    assert "<script" not in card
    for handler in ("onclick", "onsubmit", "oninput", "onchange", "onload"):
        assert handler not in card, handler


# --- script wiring is now LIVE (S4A.2c) ------------------------------------


def test_backup_js_file_exists():
    assert BACKUP_JS.exists()


def test_dashboard_has_live_backup_script_tag():
    html = _dashboard()
    assert "static_url('js/backup.js')" in html
    assert '<script src="{{ static_url(\'js/backup.js\') }}"></script>' in html


def test_backup_placeholder_no_longer_active():
    # The S4A.2b commented placeholder text must be gone (replaced by the live tag).
    html = _dashboard()
    assert "Wiring placeholder only" not in html


# --- backup.js behaviour + security (S4A.2c) -------------------------------


def test_backup_js_uses_rawfetch_and_create_endpoint():
    js = _backup_js()
    assert "rawFetch(" in js
    assert "/api/backup/create" in js


def test_backup_js_downloads_via_revoked_object_url():
    js = _backup_js()
    assert "response.blob()" in js
    assert "URL.createObjectURL" in js
    assert "URL.revokeObjectURL" in js


def test_backup_js_parses_content_disposition_with_fallback():
    js = _backup_js()
    assert "Content-Disposition" in js
    assert "filename" in js


def test_backup_js_validates_passphrase_and_confirm():
    js = _backup_js()
    # length floor + a confirm/match comparison
    assert "12" in js
    assert ("!==" in js) or ("match" in js.lower())


def test_backup_js_uses_textcontent_not_innerhtml():
    js = _backup_js()
    assert ".textContent" in js
    assert "innerHTML" not in js


def test_backup_js_clears_passphrase_fields():
    js = _backup_js()
    assert "backup-passphrase" in js
    assert ".value = ''" in js


def test_backup_js_no_unsafe_storage_or_logging():
    js = _backup_js()
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "console.log"):
        assert forbidden not in js, forbidden


# --- api.js shared helper intact (unchanged in S4A.2c) ---------------------


def test_rawfetch_helper_present_in_api_js():
    js = _api_js()
    assert "function rawFetch(" in js
    assert "window.rawFetch = rawFetch;" in js
    assert "getCsrfToken()" in js


def test_api_js_existing_exports_intact():
    js = _api_js()
    assert "window.apiFetch = apiFetch;" in js
    assert "window.Toast    = Toast;" in js
