/**
 * frontend/static/js/personal.js
 * Personal Mode status view (C6d Slice 1 — read-only).
 *
 * Read: fetch GET /api/conduit/personal/status when Settings becomes visible
 * (refresh-on-view; no polling). Renders one of three states — Not set up /
 * Created — inactive / Active — with a read-only display name and the current
 * max personal clients.
 *
 * This slice is READ-ONLY: no create, no max-clients apply, no token, no QR,
 * and no vendored dependency (all added in later C6d slices). The display name
 * participates in pairing-token generation and is immutable for the life of a
 * compartment, so it is rendered as text — never an input.
 *
 * textContent/DOM only (no innerHTML); 401 -> /login. Mirrors conduit_config.js.
 */
(function () {
    'use strict';

    function el(id) { return document.getElementById(id); }

    var STATES = ['loading', 'error', 'body'];
    function showState(name) {
        STATES.forEach(function (s) {
            var e = el('personal-' + s);
            if (e) e.hidden = (s !== name);
        });
    }

    function redirectLogin() {
        var next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = '/login?next=' + next;
    }

    /* ----- render (read-only) ----- */
    function setBadge(status) {
        var b = el('pm-badge');
        if (!b) return;
        if (!status.compartment_exists) {
            b.className = 'badge badge--neutral';
            b.textContent = 'Not set up';
        } else if (!status.active) {
            b.className = 'badge badge--warning';
            b.textContent = 'Created — inactive';
        } else {
            b.className = 'badge badge--success';
            var n = status.max_personal_clients;
            b.textContent = 'Active · ' + (n != null ? n : 0) + ' personal clients';
        }
    }

    function render(status) {
        setBadge(status);
        var name = el('pm-name');
        if (name) {
            name.textContent = status.compartment_exists
                ? (status.display_name || '—') : '—';
        }
        var max = el('pm-max');
        if (max) {
            var n = status.max_personal_clients;
            max.textContent = (status.compartment_exists && n != null) ? ('' + n) : '—';
        }
        // Action controls (create / max-clients / view-share / regenerate /
        // restore) arrive in later C6d slices; keep the placeholder hidden.
        var actions = el('personal-actions');
        if (actions) actions.hidden = true;
        showState('body');
    }

    function fetchStatus() {
        return fetch('/api/conduit/personal/status', {
            method: 'GET', headers: { 'Accept': 'application/json' }, credentials: 'same-origin'
        })
        .then(function (r) {
            if (r.status === 401) { redirectLogin(); return null; }
            if (!r.ok) { showState('error'); return null; }
            return r.json();
        })
        .then(function (d) { if (d) render(d); })
        .catch(function () { showState('error'); });
    }

    /* ----- section visibility (refresh-on-view) ----- */
    function settingsVisible() {
        var s = el('section-settings');
        return !(s && s.hidden);
    }
    function maybeLoad() {
        if (el('personal-card') && settingsVisible()) {
            showState('loading');
            fetchStatus();
        }
    }

    onReady(function () {
        if (!el('personal-card')) return;
        maybeLoad();
        window.addEventListener('hashchange', maybeLoad);
    });
})();
