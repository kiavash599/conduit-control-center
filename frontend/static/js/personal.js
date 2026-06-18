/**
 * frontend/static/js/personal.js
 * Personal Mode view (C6d Slice 1 read-only status + Slice 2 create flow).
 *
 * Read: GET /api/conduit/personal/status when Settings becomes visible
 * (refresh-on-view; no polling). Renders one of three states — Not set up /
 * Created — inactive / Active.
 *
 * Create (Slice 2): in the "Not set up" state, POST a display name to create
 * the personal compartment. The compartment is inert until Max personal clients
 * is raised above 0 (a later slice). The create response carries a pairing
 * token; this module DELIBERATELY never reads, stores, logs, or renders it
 * (token surfacing is Slice 3). The display name participates in token
 * generation and is immutable after creation, so it is editable ONLY here.
 *
 * Not yet implemented (later slices): token panel, QR, View/share, max-clients
 * apply, regenerate, restore, dashboard indicator.
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

    function getCsrf() {
        var m = document.cookie.split('; ').find(function (c) {
            return c.indexOf('csrf_token=') === 0;
        });
        return m ? decodeURIComponent(m.split('=')[1]) : '';
    }

    /* ----- status messages ----- */
    function setCreateError(msg) {
        var e = el('pm-create-error');
        if (!e) return;
        if (!msg) { e.hidden = true; e.textContent = ''; return; }
        e.textContent = msg;
        e.hidden = false;
    }
    function setStatus(msg, cls) {
        var e = el('pm-status');
        if (!e) return;
        if (!msg) { e.hidden = true; e.textContent = ''; e.className = 'text-sm mt-4'; return; }
        e.className = 'text-sm mt-4 ' + (cls || '');
        e.textContent = msg;
        e.hidden = false;
    }

    /* ----- render (read-only status) ----- */
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
        // Create panel: visible only in the "Not set up" state.
        var create = el('personal-create');
        if (create) create.hidden = !!status.compartment_exists;
        // Later-slice action controls stay hidden.
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

    /* ----- create flow (Slice 2) ----- */
    function postJson(path, body) {
        return fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json',
                       'X-CSRF-Token': getCsrf() },
            credentials: 'same-origin',
            body: JSON.stringify(body)
        }).then(function (r) {
            if (r.status === 401) { redirectLogin(); return null; }
            return r.json().then(function (j) { return { status: r.status, body: j }; },
                                 function () { return { status: r.status, body: {} }; });
        });
    }

    function onCreate() {
        var input = el('pm-create-name');
        var name = input ? (input.value || '').trim() : '';
        if (!name || name.length > 32) {
            setCreateError('Enter a name of 1–32 characters.');
            return;
        }
        setCreateError(null);
        setStatus(null);
        var btn = el('pm-create-btn');
        if (btn) btn.disabled = true;

        postJson('/api/conduit/personal/compartment', { display_name: name })
        .then(function (res) {
            if (!res) return;   // 401 -> redirecting to /login
            var s = res.status;
            if (s === 200 || s === 201) {
                // SUCCESS. The response body carries a pairing token; it is
                // intentionally NOT read here (token surfacing is Slice 3).
                // Refresh authoritative status and show next-step guidance.
                fetchStatus();
                setStatus('Identity created. Set Max personal clients above 0 to enable Personal Mode.', 'text-success');
                return;
            }
            if (btn) btn.disabled = false;
            if (s === 409) {
                setStatus('A personal identity already exists.', 'text-warning');
                fetchStatus();
            } else if (s === 422) {
                setCreateError('Enter a name of 1–32 characters.');
            } else if (s === 403) {
                setStatus('Your session expired — reload and sign in again.', 'text-danger');
            } else if (s === 503) {
                setStatus('Personal mode is unavailable on this server (helper not installed or misconfigured).', 'text-danger');
            } else {
                setStatus('Unexpected error creating the identity.', 'text-danger');
            }
        })
        .catch(function () {
            if (btn) btn.disabled = false;
            setStatus('Network error — check your connection and retry.', 'text-danger');
        });
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
        if (el('pm-create-btn')) el('pm-create-btn').addEventListener('click', onCreate);
        if (el('pm-create-name')) {
            el('pm-create-name').addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter') { ev.preventDefault(); onCreate(); }
            });
        }
        maybeLoad();
        window.addEventListener('hashchange', maybeLoad);
    });
})();
