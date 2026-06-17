# SPDX-License-Identifier: MIT
"""
Static guard for the Live Operations additions to the Node Status card
(Commit 3): the broker badge + connecting/idle rows + build_rev span in markup,
and the status.js wiring (no innerHTML, the broker-state map + the five states,
build_rev append, "Clients" not "Users"). Pure file reads; no browser.
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DASH = _ROOT / "frontend" / "templates" / "dashboard.html"
_JS = _ROOT / "frontend" / "static" / "js" / "status.js"


def _card() -> str:
    html = _DASH.read_text(encoding="utf-8")
    start = html.index('id="node-status-card"')
    return html[start:html.index("/#node-status-card", start)]


def test_markup_has_live_hooks():
    card = _card()
    for hook in ('id="broker-badge"', 'id="status-connecting"', 'id="status-idle"',
                 'id="status-build-rev"', 'id="status-version"'):
        assert hook in card, hook
    assert "Broker" in card
    assert "Connecting clients" in card


def test_markup_clients_not_users():
    assert "Users" not in _card()


def test_status_js_wires_live():
    js = _JS.read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert ".live" in js  # renderLive(data.live)
    for hook in ("broker-badge", "status-connecting", "status-idle", "status-build-rev"):
        assert hook in js, hook
    for state in ("live", "starting", "disconnected", "not_running", "unknown"):
        assert state in js, state


def test_status_js_clients_not_users():
    assert "Users" not in _JS.read_text(encoding="utf-8")
