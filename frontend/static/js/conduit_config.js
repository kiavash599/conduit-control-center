/**
 * frontend/static/js/conduit_config.js
 * Read-only Conduit Configuration view (M1, §6.1).
 *
 * Fetches GET /api/conduit/config when the Settings section becomes visible
 * (refresh-on-view; NO polling). Renders configured vs effective + drift for
 * max_common_clients and bandwidth_mbps. textContent/DOM only; raw fetch;
 * 401 -> /login. No write/apply controls (write path is a later milestone).
 *
 * Response shape (structured):
 *   { service_status, drift,
 *     max_common_clients:{configured,effective,drift},
 *     bandwidth_mbps:{configured,effective,drift,
 *                     unlimited_configured,unlimited_effective} }
 *
 * Script loading order: api.js -> app.js -> dashboard.js -> … -> settings.js ->
 * conduit_config.js.
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

    function fmt(value, unit, unlimited) {
        if (unlimited) return 'Unlimited';
        if (value === null || value === undefined) return '—';
        return unit ? (value + ' ' + unit) : ('' + value);
    }

    function setDrift(spanId, drift) {
        var e = el(spanId);
        if (!e) return;
        if (drift === true) {
            e.className = 'badge badge--warning';
            e.textContent = 'Restart pending';
            e.hidden = false;
        } else if (drift === false) {
            e.className = 'badge badge--success';
            e.textContent = 'In sync';
            e.hidden = false;
        } else {
            e.hidden = true;   // unknown -> no chip
        }
    }

    function renderField(prefix, field, unit) {
        if (!field) return;
        var cfg = el('cc-' + prefix + '-configured');
        var eff = el('cc-' + prefix + '-effective');
        if (cfg) cfg.textContent = fmt(field.configured, unit, field.unlimited_configured);
        if (eff) eff.textContent = fmt(field.effective, unit, field.unlimited_effective);
        setDrift('cc-' + prefix + '-drift', field.drift);
    }

    function render(data) {
        renderField('mcc', data.max_common_clients, 'clients');
        renderField('bw', data.bandwidth_mbps, 'Mbps');
        showState('body');
    }

    function fetchConfig() {
        return fetch('/api/conduit/config', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        })
        .then(function (r) {
            if (r.status === 401) {
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            if (!r.ok) { showState('error'); return null; }
            return r.json();
        })
        .then(function (d) { if (d) render(d); })
        .catch(function () { showState('error'); });
    }

    function settingsVisible() {
        var s = el('section-settings');
        return !(s && s.hidden);
    }

    // Refresh-on-view: fetch when entering Settings (and on load if already there).
    function maybeLoad() {
        if (el('conduit-config-card') && settingsVisible()) {
            showState('loading');
            fetchConfig();
        }
    }

    onReady(function () {
        if (!el('conduit-config-card')) return;
        maybeLoad();
        window.addEventListener('hashchange', maybeLoad);
    });
})();
