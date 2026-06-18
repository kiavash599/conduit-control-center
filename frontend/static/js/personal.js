/**
 * frontend/static/js/personal.js
 * Personal Mode view (C6d Slice 1 status + Slice 2 create + Slice 3 token/QR).
 *
 * Read: GET /api/conduit/personal/status when Settings becomes visible
 * (refresh-on-view; no polling). Renders one of three states — Not set up /
 * Created — inactive / Active.
 *
 * Create (Slice 2): POST a display name to create the personal compartment.
 *
 * View / share token (Slice 3): GET the pairing token on demand and render it
 * as text + a client-side QR (vendored Nayuki qrcodegen, loaded before this
 * file). Token handling rules: the token is held ONLY in a runtime variable
 * while the panel is open; it is never logged, stored, persisted, placed in a
 * URL, or written to web storage or cookies. On close — and on
 * navigation away — the token variable is nulled and the token text + QR canvas
 * are removed from the DOM. The token remains re-retrievable via GET /token, so
 * it is never described as "shown once".
 *
 * Not yet implemented (later slices): max-clients apply, regenerate, restore,
 * dashboard indicator.
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
        e.textContent = msg; e.hidden = false;
    }
    function setStatus(msg, cls) {
        var e = el('pm-status');
        if (!e) return;
        if (!msg) { e.hidden = true; e.textContent = ''; e.className = 'text-sm mt-4'; return; }
        e.className = 'text-sm mt-4 ' + (cls || ''); e.textContent = msg; e.hidden = false;
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
        // View / share button: visible once a compartment exists.
        var view = el('pm-view-btn');
        if (view) view.hidden = !status.compartment_exists;
        // If no compartment, ensure any open token panel is closed + cleared.
        if (!status.compartment_exists) closeTokenPanel();
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
                // SUCCESS. The create response body also carries the token, but
                // this handler does NOT read it; the token panel fetches the
                // token via GET /token through the single shared path below.
                fetchStatus();
                setStatus('Identity created. Set Max personal clients above 0 to enable Personal Mode.', 'text-success');
                openTokenPanel();   // auto-open after successful create
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

    /* ----- token / QR (Slice 3) -----
     * currentToken holds the pairing token only while the panel is open. It is
     * nulled (and its DOM nodes removed) on close and on navigation away. */
    var currentToken = null;

    function clearToken() {
        currentToken = null;
        var t = el('pm-token-text');
        if (t) t.textContent = '';                 // remove token text from DOM
        var q = el('pm-qr');
        if (q) { while (q.firstChild) q.removeChild(q.firstChild); }  // remove QR canvas
    }

    function closeTokenPanel() {
        clearToken();
        var p = el('pm-token-panel');
        if (p) p.hidden = true;
    }

    function renderQr(token) {
        var host = el('pm-qr');
        if (!host || typeof qrcodegen === 'undefined') return;
        while (host.firstChild) host.removeChild(host.firstChild);
        var qr = qrcodegen.QrCode.encodeText(token, qrcodegen.QrCode.Ecc.MEDIUM);
        var border = 4, scale = 6;
        var dim = (qr.size + border * 2) * scale;
        var canvas = document.createElement('canvas');
        canvas.width = dim;
        canvas.height = dim;
        canvas.setAttribute('aria-hidden', 'true');
        var ctx = canvas.getContext('2d');
        // Theme-independent: dark modules on a light background + quiet zone, so
        // the code stays scannable in both light and dark themes.
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, dim, dim);
        ctx.fillStyle = '#000000';
        for (var y = 0; y < qr.size; y++) {
            for (var x = 0; x < qr.size; x++) {
                if (qr.getModule(x, y)) {
                    ctx.fillRect((x + border) * scale, (y + border) * scale, scale, scale);
                }
            }
        }
        host.appendChild(canvas);
    }

    function fetchToken() {
        return fetch('/api/conduit/personal/token', {
            method: 'GET', headers: { 'Accept': 'application/json' }, credentials: 'same-origin'
        }).then(function (r) {
            if (r.status === 401) { redirectLogin(); return null; }
            return r.json().then(function (j) { return { status: r.status, body: j }; },
                                 function () { return { status: r.status, body: {} }; });
        });
    }

    function openTokenPanel() {
        clearToken();
        setStatus(null);
        var btn = el('pm-view-btn');
        if (btn) btn.disabled = true;
        fetchToken().then(function (res) {
            if (btn) btn.disabled = false;
            if (!res) return;   // 401 -> redirecting
            var s = res.status;
            if (s === 200) {
                currentToken = res.body.token;        // legitimate token read (panel only)
                var t = el('pm-token-text');
                if (t) t.textContent = currentToken;
                renderQr(currentToken);
                var p = el('pm-token-panel');
                if (p) p.hidden = false;
                return;
            }
            if (s === 404) {
                setStatus('No personal identity is configured.', 'text-warning');
                fetchStatus();
            } else if (s === 503) {
                setStatus('Token is unavailable on this server (helper missing or token-format mismatch).', 'text-danger');
            } else {
                setStatus('Could not load the token — reload and try again.', 'text-danger');
            }
        }).catch(function () {
            if (btn) btn.disabled = false;
            setStatus('Network error — could not load the token. Retry.', 'text-danger');
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
    function onNavigate() {
        // Navigation away must not leave a token lingering in the DOM/memory.
        closeTokenPanel();
        maybeLoad();
    }

    onReady(function () {
        if (!el('personal-card')) return;
        if (el('pm-create-btn')) el('pm-create-btn').addEventListener('click', onCreate);
        if (el('pm-create-name')) {
            el('pm-create-name').addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter') { ev.preventDefault(); onCreate(); }
            });
        }
        if (el('pm-view-btn')) el('pm-view-btn').addEventListener('click', openTokenPanel);
        if (el('pm-token-close')) el('pm-token-close').addEventListener('click', closeTokenPanel);
        maybeLoad();
        window.addEventListener('hashchange', onNavigate);
    });
})();
