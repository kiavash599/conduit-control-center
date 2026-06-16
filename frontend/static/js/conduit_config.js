/**
 * frontend/static/js/conduit_config.js
 * Conduit Configuration view + write (M1 read, M2 write).
 *
 * Read: fetch GET /api/conduit/config when Settings becomes visible
 * (refresh-on-view; no polling). Write: Edit -> validate -> confirm restart ->
 * apply, with explicit state handling for applied / rolled_back / conflict /
 * validation error / rollback_failed. No auto-apply; restart is always confirmed.
 *
 * POSTs use raw fetch with the X-CSRF-Token header (read from the csrf_token
 * cookie) so this module can render custom states instead of apiFetch toasts.
 * textContent/DOM only; 401 -> /login.
 */
(function () {
    'use strict';

    function el(id) { return document.getElementById(id); }

    var STATES = ['loading', 'error', 'body'];
    function showState(name) {
        STATES.forEach(function (s) {
            var e = el('conduit-config-' + s);
            if (e) e.hidden = (s !== name);
        });
    }

    function getCsrf() {
        var m = document.cookie.split('; ').find(function (c) {
            return c.indexOf('csrf_token=') === 0;
        });
        return m ? decodeURIComponent(m.split('=')[1]) : '';
    }

    /* ----- formatting / status chips (read) ----- */
    function fmt(value, unit, unlimited) {
        if (unlimited) return 'Unlimited';
        if (value === null || value === undefined) return '—';
        return unit ? (value + ' ' + unit) : ('' + value);
    }
    function setDrift(spanId, drift) {
        var e = el(spanId);
        if (!e) return;
        if (drift === true) { e.className = 'badge badge--warning'; e.textContent = 'Restart pending'; e.hidden = false; }
        else if (drift === false) { e.className = 'badge badge--success'; e.textContent = 'In sync'; e.hidden = false; }
        else { e.hidden = true; }
    }
    function renderField(prefix, field, unit) {
        if (!field) return;
        var cfg = el('cc-' + prefix + '-configured');
        var eff = el('cc-' + prefix + '-effective');
        if (cfg) cfg.textContent = fmt(field.configured, unit, field.unlimited_configured);
        if (eff) eff.textContent = fmt(field.effective, unit, field.unlimited_effective);
        setDrift('cc-' + prefix + '-drift', field.drift);
    }

    /* ----- module state ----- */
    var lastData = null;   // last GET response (for prefill + expected_effective)

    function render(data) {
        lastData = data;
        renderField('mcc', data.max_common_clients, 'clients');
        renderField('bw', data.bandwidth_mbps, 'Mbps');
        showState('body');
        // Reveal edit affordance once a read has succeeded.
        var actions = el('cc-actions');
        if (actions) actions.hidden = false;
    }

    function fetchConfig() {
        return fetch('/api/conduit/config', {
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

    function redirectLogin() {
        var next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = '/login?next=' + next;
    }

    /* ----- write flow ----- */
    function showWrite(which) {
        // which: 'clean' | 'editing' | 'confirm'
        if (el('cc-actions')) el('cc-actions').hidden = (which !== 'clean');
        if (el('cc-form')) el('cc-form').hidden = (which !== 'editing');
        if (el('cc-confirm')) el('cc-confirm').hidden = (which !== 'confirm');
    }
    function setStatus(msg, cls) {
        var e = el('cc-status');
        if (!e) return;
        if (!msg) { e.hidden = true; e.textContent = ''; return; }
        e.className = 'text-sm mt-4 ' + (cls || '');
        e.textContent = msg;
        e.hidden = false;
    }
    function setFormError(msg) {
        var e = el('cc-form-error');
        if (!e) return;
        if (!msg) { e.hidden = true; e.textContent = ''; return; }
        e.textContent = msg;
        e.hidden = false;
    }

    function startEdit() {
        if (!lastData) return;
        var mcc = lastData.max_common_clients || {};
        var bw = lastData.bandwidth_mbps || {};
        if (el('cc-in-mcc')) el('cc-in-mcc').value = (mcc.configured != null ? mcc.configured : '');
        var unlimited = !!bw.unlimited_configured;
        if (el('cc-in-unlimited')) el('cc-in-unlimited').checked = unlimited;
        if (el('cc-in-bw')) {
            el('cc-in-bw').value = (!unlimited && bw.configured != null ? bw.configured : '');
            el('cc-in-bw').disabled = unlimited;
        }
        setFormError(null);
        setStatus(null);
        showWrite('editing');
    }

    function readForm() {
        var mcc = parseInt((el('cc-in-mcc') || {}).value, 10);
        var unlimited = !!(el('cc-in-unlimited') && el('cc-in-unlimited').checked);
        var bw = unlimited ? -1 : parseInt((el('cc-in-bw') || {}).value, 10);
        return { max_common_clients: mcc, bandwidth_mbps: bw };
    }

    var pending = null;   // normalized body awaiting confirmation
    var expected = null;  // expected_effective snapshot

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

    function onApplyClicked(ev) {
        ev.preventDefault();
        var body = readForm();
        if (isNaN(body.max_common_clients) || (body.bandwidth_mbps !== -1 && isNaN(body.bandwidth_mbps))) {
            setFormError('Enter whole numbers (or tick Unlimited for bandwidth).');
            return;
        }
        setFormError(null);
        postJson('/api/conduit/config/validate', body).then(function (res) {
            if (!res) return;
            if (res.status === 422) {
                var msgs = (res.body.errors || []).map(function (e) { return e.message; }).join('; ');
                setFormError(msgs || 'Validation failed.');
                return;
            }
            if (!res.body.changed) {
                setFormError('No changes to apply.');
                return;
            }
            // Stage confirmation with the snapshot used for optimistic concurrency.
            pending = res.body.normalized;
            var bw = (lastData && lastData.bandwidth_mbps) || {};
            var mcc = (lastData && lastData.max_common_clients) || {};
            expected = {
                max_common_clients: mcc.effective,
                bandwidth_mbps: (bw.unlimited_effective ? -1 : bw.effective)
            };
            showWrite('confirm');
        });
    }

    function onConfirmClicked() {
        if (!pending) return;
        showWrite('clean');
        setStatus('Applying… restarting Conduit and verifying.', 'text-dim');
        var body = { max_common_clients: pending.max_common_clients,
                     bandwidth_mbps: pending.bandwidth_mbps,
                     expected_effective: expected };
        postJson('/api/conduit/config/apply', body).then(function (res) {
            if (!res) return;
            var b = res.body || {};
            if (res.status === 200 && b.status === 'applied') {
                setStatus('Configuration applied. Conduit restarted and is reconnecting to the broker.', 'text-success');
                fetchConfig();
            } else if (res.status === 200 && b.status === 'rolled_back') {
                setStatus('The new configuration prevented Conduit from starting. Previous settings were restored and Conduit is running again. (' + (b.reason || '') + ')', 'text-warning');
                fetchConfig();
            } else if (res.status === 409) {
                setStatus(b.reason === 'drift'
                    ? 'Configuration changed elsewhere; reloading current values. Please retry.'
                    : 'Another apply is in progress. Try again shortly.', 'text-warning');
                fetchConfig();
            } else if (res.status === 422) {
                var msgs = (b.errors || []).map(function (e) { return e.message; }).join('; ');
                setStatus('Validation failed: ' + (msgs || ''), 'text-danger');
            } else if (res.status === 503) {
                setStatus('Configuration changes are unavailable on this server (' + (b.reason || 'helper unavailable') + ').', 'text-danger');
            } else if (res.status === 500 && b.status === 'rollback_failed') {
                setStatus('Critical: the change failed and automatic revert did not succeed. Conduit may be down — run: sudo systemctl restart conduit', 'text-danger');
            } else {
                setStatus('Unexpected response applying configuration.', 'text-danger');
            }
            pending = null; expected = null;
        });
    }

    function cancel() {
        pending = null; expected = null;
        setFormError(null); setStatus(null);
        showWrite('clean');
    }

    /* ----- section visibility (refresh-on-view) ----- */
    function settingsVisible() {
        var s = el('section-settings');
        return !(s && s.hidden);
    }
    function maybeLoad() {
        if (el('conduit-config-card') && settingsVisible()) {
            showWrite('clean'); setStatus(null);
            showState('loading');
            fetchConfig();
        }
    }

    onReady(function () {
        if (!el('conduit-config-card')) return;
        if (el('cc-edit-btn')) el('cc-edit-btn').addEventListener('click', startEdit);
        if (el('cc-cancel-btn')) el('cc-cancel-btn').addEventListener('click', cancel);
        if (el('cc-confirm-cancel')) el('cc-confirm-cancel').addEventListener('click', cancel);
        if (el('cc-confirm-btn')) el('cc-confirm-btn').addEventListener('click', onConfirmClicked);
        if (el('cc-form')) el('cc-form').addEventListener('submit', onApplyClicked);
        if (el('cc-in-unlimited')) {
            el('cc-in-unlimited').addEventListener('change', function () {
                if (el('cc-in-bw')) el('cc-in-bw').disabled = el('cc-in-unlimited').checked;
            });
        }
        maybeLoad();
        window.addEventListener('hashchange', maybeLoad);
    });
})();
