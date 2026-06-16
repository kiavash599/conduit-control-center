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

    /* ----- reduced-mode helpers (BS3.2): UTC <-> browser-local preview ----- */
    var RE_HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
    function localTz() {
        try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'local'; }
        catch (e) { return 'local'; }
    }
    function pad2(n) { return (n < 10 ? '0' : '') + n; }
    // Convert "HH:MM" UTC to the browser's local "HH:MM" using today's offset
    // (DST-aware). Returns null for malformed input. Display-only; tunnel-core
    // always evaluates the window in UTC, so the schedule itself is unaffected.
    function utcHHMMToLocal(hhmm) {
        if (!RE_HHMM.test(hhmm || '')) return null;
        var p = ('' + hhmm).split(':');
        var d = new Date();
        d.setUTCHours(parseInt(p[0], 10), parseInt(p[1], 10), 0, 0);
        return pad2(d.getHours()) + ':' + pad2(d.getMinutes());
    }
    function renderReduced(r) {
        var sum = el('cc-reduced-summary');
        var loc = el('cc-reduced-local');
        if (!sum) return;
        if (!r || !r.enabled) {
            sum.textContent = 'Off';
            if (loc) loc.textContent = '';
            return;
        }
        sum.textContent = r.start + '–' + r.end + ' UTC · max ' +
            r.max_common_clients + ' clients · ' + r.bandwidth_mbps + ' Mbps';
        if (loc) {
            var ls = utcHHMMToLocal(r.start), le = utcHHMMToLocal(r.end);
            loc.textContent = (ls && le) ? (' (' + ls + '–' + le + ' ' + localTz() + ')') : '';
        }
    }

    /* ----- module state ----- */
    var lastData = null;   // last GET response (for prefill + expected_effective)

    function render(data) {
        lastData = data;
        renderField('mcc', data.max_common_clients, 'clients');
        renderField('bw', data.bandwidth_mbps, 'Mbps');
        renderReduced(data.reduced);
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

    /* ----- reduced-mode edit helpers (BS3.2) ----- */
    function syncReducedFields() {
        var cb = el('cc-in-reduced-enabled');
        var on = !!(cb && cb.checked);
        if (el('cc-reduced-fields')) el('cc-reduced-fields').hidden = !on;
        if (cb) cb.setAttribute('aria-expanded', on ? 'true' : 'false');
    }
    function updateReducedPreview(inputId, hintId) {
        var inp = el(inputId), hint = el(hintId);
        if (!hint) return;
        var local = inp ? utcHHMMToLocal(inp.value) : null;
        hint.textContent = local ? ('= ' + local + ' ' + localTz()) : '';
    }
    function refreshReducedPreviews() {
        updateReducedPreview('cc-in-reduced-start', 'cc-in-reduced-start-local');
        updateReducedPreview('cc-in-reduced-end', 'cc-in-reduced-end-local');
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
        var red = lastData.reduced || {};
        if (el('cc-in-reduced-enabled')) el('cc-in-reduced-enabled').checked = !!red.enabled;
        if (el('cc-in-reduced-start')) el('cc-in-reduced-start').value = (red.start != null ? red.start : '');
        if (el('cc-in-reduced-end')) el('cc-in-reduced-end').value = (red.end != null ? red.end : '');
        if (el('cc-in-reduced-max')) el('cc-in-reduced-max').value = (red.max_common_clients != null ? red.max_common_clients : '');
        if (el('cc-in-reduced-bw')) el('cc-in-reduced-bw').value = (red.bandwidth_mbps != null ? red.bandwidth_mbps : '');
        syncReducedFields();
        refreshReducedPreviews();
        setFormError(null);
        setStatus(null);
        showWrite('editing');
    }

    function readReducedForm() {
        var on = !!(el('cc-in-reduced-enabled') && el('cc-in-reduced-enabled').checked);
        return {
            enabled: on,
            start: ((el('cc-in-reduced-start') || {}).value || '').trim() || null,
            end: ((el('cc-in-reduced-end') || {}).value || '').trim() || null,
            max_common_clients: on ? parseInt((el('cc-in-reduced-max') || {}).value, 10) : null,
            bandwidth_mbps: on ? parseInt((el('cc-in-reduced-bw') || {}).value, 10) : null
        };
    }
    function readForm() {
        var mcc = parseInt((el('cc-in-mcc') || {}).value, 10);
        var unlimited = !!(el('cc-in-unlimited') && el('cc-in-unlimited').checked);
        var bw = unlimited ? -1 : parseInt((el('cc-in-bw') || {}).value, 10);
        // Always include the full-state reduced object so the apply is complete
        // (enabled:false disables; an unchanged window is preserved).
        return { max_common_clients: mcc, bandwidth_mbps: bw, reduced: readReducedForm() };
    }

    // Client-side pre-flight (server /validate remains authoritative).
    function validateReducedClient(body) {
        var r = body.reduced;
        if (!r || !r.enabled) return null;
        if (!RE_HHMM.test(((el('cc-in-reduced-start') || {}).value || '').trim()))
            return 'Enter the reduced start time as HH:MM (24-hour, UTC).';
        if (!RE_HHMM.test(((el('cc-in-reduced-end') || {}).value || '').trim()))
            return 'Enter the reduced end time as HH:MM (24-hour, UTC).';
        if (r.start === r.end) return 'Reduced start and end times must differ.';
        if (isNaN(r.max_common_clients) || r.max_common_clients < 1 ||
            r.max_common_clients > body.max_common_clients)
            return 'Reduced max clients must be between 1 and ' + body.max_common_clients + '.';
        if (isNaN(r.bandwidth_mbps) || r.bandwidth_mbps < 1 || r.bandwidth_mbps > 1000)
            return 'Reduced bandwidth must be between 1 and 1000 Mbps.';
        return null;
    }
    function fmtWindow(r) {
        if (!r || !r.enabled) return 'Off';
        var ls = utcHHMMToLocal(r.start), le = utcHHMMToLocal(r.end);
        var local = (ls && le) ? (' (' + ls + '–' + le + ' ' + localTz() + ')') : '';
        return r.start + '–' + r.end + ' UTC' + local + ', max ' +
            r.max_common_clients + ' clients, ' + r.bandwidth_mbps + ' Mbps';
    }
    function renderConfirmSummary(body) {
        var box = el('cc-confirm-summary');
        if (!box) return;
        box.textContent = '';   // clear; rebuilt with createElement + textContent
        var old = lastData || {};
        var oldMcc = (old.max_common_clients || {}).configured;
        var oldBw = old.bandwidth_mbps || {};
        var oldBwStr = oldBw.unlimited_configured ? 'Unlimited'
            : (oldBw.configured != null ? oldBw.configured + ' Mbps' : '—');
        var newBwStr = body.bandwidth_mbps === -1 ? 'Unlimited' : body.bandwidth_mbps + ' Mbps';
        [
            'Max common clients: ' + (oldMcc != null ? oldMcc : '—') + ' → ' + body.max_common_clients,
            'Global bandwidth: ' + oldBwStr + ' → ' + newBwStr,
            'Reduced window: ' + fmtWindow(old.reduced) + ' → ' + fmtWindow(body.reduced)
        ].forEach(function (t) {
            var line = document.createElement('div');
            line.textContent = t;
            box.appendChild(line);
        });
        if (body.reduced && body.reduced.enabled) {
            var note = document.createElement('p');
            note.className = 'text-dim text-sm mt-2';
            note.textContent = 'The reduced schedule is evaluated automatically by Conduit. ' +
                'No restart occurs at the configured start or end time.';
            box.appendChild(note);
        }
        box.hidden = false;
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
        var rerr = validateReducedClient(body);
        if (rerr) { setFormError(rerr); return; }
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
            // Stage confirmation. pending is the full-state form body (incl. the
            // reduced HH:MM object); expected_effective stays normal-only.
            pending = body;
            var bw = (lastData && lastData.bandwidth_mbps) || {};
            var mcc = (lastData && lastData.max_common_clients) || {};
            expected = {
                max_common_clients: mcc.effective,
                bandwidth_mbps: (bw.unlimited_effective ? -1 : bw.effective)
            };
            renderConfirmSummary(body);
            showWrite('confirm');
        }).catch(function () {
            setFormError('Network error — please check your connection and retry.');
        });
    }

    function onConfirmClicked() {
        if (!pending) return;
        showWrite('clean');
        setStatus('Applying… restarting Conduit and verifying.', 'text-dim');
        var body = { max_common_clients: pending.max_common_clients,
                     bandwidth_mbps: pending.bandwidth_mbps,
                     reduced: pending.reduced,
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
        }).catch(function () {
            setStatus('Network error during apply — reload to check the current state.', 'text-danger');
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
        if (el('cc-in-reduced-enabled')) {
            el('cc-in-reduced-enabled').addEventListener('change', syncReducedFields);
        }
        if (el('cc-in-reduced-start')) {
            el('cc-in-reduced-start').addEventListener('input', function () {
                updateReducedPreview('cc-in-reduced-start', 'cc-in-reduced-start-local');
            });
        }
        if (el('cc-in-reduced-end')) {
            el('cc-in-reduced-end').addEventListener('input', function () {
                updateReducedPreview('cc-in-reduced-end', 'cc-in-reduced-end-local');
            });
        }
        maybeLoad();
        window.addEventListener('hashchange', maybeLoad);
    });
})();
