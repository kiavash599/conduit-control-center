# SPDX-License-Identifier: MIT
"""
Static markup guard for the Bandwidth Scheduling fields added to the Conduit
Configuration card (BS3.1). Confirms the read/edit/confirm hooks BS3.2 will wire
exist, the reduced field group is hidden by default, UTC labelling + "Clients"
terminology are present, accessibility hooks are set, and no inline script/style
was introduced. Pure file reads; no browser, no JS.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DASH = _ROOT / "frontend" / "templates" / "dashboard.html"
_CSS = _ROOT / "frontend" / "static" / "css" / "base.css"


def _card() -> str:
    html = _DASH.read_text(encoding="utf-8")
    start = html.index('id="conduit-config-card"')
    end = html.index("/#conduit-config-card", start)
    return html[start:end]


def _form() -> str:
    card = _card()
    start = card.index('id="cc-form"')
    return card[start:card.index("</form>", start)]


def test_reduced_read_row_in_body():
    card = _card()
    body = card[card.index('id="conduit-config-body"'):card.index("</dl>")]
    assert 'id="cc-reduced-summary"' in body
    assert 'id="cc-reduced-local"' in body
    assert "Reduced window" in body


def test_reduced_edit_fields_inside_form():
    form = _form()
    for _id in ("cc-in-reduced-enabled", "cc-in-reduced-start", "cc-in-reduced-end",
                "cc-in-reduced-max", "cc-in-reduced-bw", "cc-reduced-fields"):
        assert f'id="{_id}"' in form, _id
    assert "Start time (UTC)" in form and "End time (UTC)" in form


def test_reduced_fields_hidden_by_default():
    card = _card()
    i = card.index('id="cc-reduced-fields"')
    assert "hidden" in card[i:card.index(">", i)]


def test_confirm_summary_placeholder_present():
    assert 'id="cc-confirm-summary"' in _card()


def test_clients_terminology_not_users():
    card = _card()
    assert "clients" in card  # "max common clients" / "Reduced max common clients"
    assert "Users" not in card


def test_accessibility_hooks_present():
    card = _card()
    assert 'aria-controls="cc-reduced-fields"' in card
    assert 'aria-describedby="cc-in-reduced-start-local"' in card
    assert 'aria-describedby="cc-in-reduced-end-local"' in card


def test_no_inline_script_or_style_in_card():
    card = _card()
    assert "<script" not in card
    assert "style=" not in card


def test_css_group_class_present():
    assert ".cc-reduced-group" in _CSS.read_text(encoding="utf-8")
