/**
 * frontend/static/js/regions.js
 * Regional Analytics card (RA-2b). Read-only, aggregate-only.
 *
 * Fetches GET /api/conduit/regions and renders the Top-10 countries by traffic
 * into #regions-tbody. Aggregate-only: only {region, traffic_bytes, clients} --
 * no IPs, sessions, or per-client data. Rendered with textContent/DOM nodes only.
 *
 * Country names come from the browser's Intl.DisplayNames (the canonical ISO
 * 3166-1 region mapping) -- no bundled country list. Flags are derived
 * algorithmically from the ISO alpha-2 code (Unicode regional indicators).
 * Unknown / unmappable codes fall back to the raw code.
 *
 * Dashboard-section-aware 60 s polling via startPolling + window.CCC.pollers,
 * matching advisor.js / traffic_history.js. 401 -> /login?next=...
 */
(function () {
    'use strict';

    function el(id) { return document.getElementById(id); }

    var STATES = ['loading', 'error', 'empty', 'body'];
    function showState(name) {
        STATES.forEach(function (s) {
            var e = el('regions-' + s);
            if (e) e.hidden = (s !== name);
        });
    }

    // Binary byte formatting (KiB/MiB/GiB).
    function formatBytes(bytes) {
        if (bytes == null) return '—';
        var gib = bytes / (1024 * 1024 * 1024);
        if (gib >= 1) return gib.toFixed(1) + ' GiB';
        var mib = bytes / (1024 * 1024);
        if (mib >= 1) return mib.toFixed(1) + ' MiB';
        var kib = bytes / 1024;
        if (kib >= 1) return Math.round(kib) + ' KiB';
        return (bytes || 0) + ' B';
    }

    // ISO 3166-1 alpha-2 -> flag glyph via Unicode regional indicators.
    function isoToFlag(code) {
        if (!/^[A-Za-z]{2}$/.test(code)) return '';
        var cc = code.toUpperCase();
        return String.fromCodePoint(
            0x1F1E6 + (cc.charCodeAt(0) - 65),
            0x1F1E6 + (cc.charCodeAt(1) - 65)
        );
    }

    // ISO 3166-1 alpha-2 -> country name via the browser's canonical mapping.
    // No bundled list. Unknown / unsupported -> raw code.
    var _names = null;
    try {
        if (typeof Intl !== 'undefined' && Intl.DisplayNames) {
            _names = new Intl.DisplayNames(['en'], { type: 'region' });
        }
    } catch (e) { _names = null; }

    function isoToName(code) {
        if (_names && /^[A-Za-z]{2}$/.test(code)) {
            try {
                var n = _names.of(code.toUpperCase());
                if (n && n !== code.toUpperCase()) return n;
            } catch (e) { /* fall through to raw code */ }
        }
        return code;   // unknown / unmappable -> raw code
    }

    function cell(text, className) {
        var td = document.createElement('td');
        if (className) td.className = className;
        td.textContent = text;
        return td;
    }

    function renderRows(regions) {
        var tbody = el('regions-tbody');
        if (!tbody) return;
        tbody.textContent = '';   // clear previous render (DOM nodes only)
        regions.forEach(function (r, i) {
            var tr = document.createElement('tr');
            tr.appendChild(cell(String(i + 1), 'regions-num'));
            var flag = cell(isoToFlag(r.region), 'regions-flag');
            flag.setAttribute('aria-hidden', 'true');
            tr.appendChild(flag);
            tr.appendChild(cell(isoToName(r.region)));
            tr.appendChild(cell(formatBytes(r.traffic_bytes), 'regions-num'));
            tr.appendChild(cell(String(r.clients == null ? 0 : r.clients), 'regions-num'));
            tbody.appendChild(tr);
        });
    }

    function render(data) {
        var regions = (data && data.regions) || [];
        if (!regions.length) { showState('empty'); return; }
        renderRows(regions);
        showState('body');
    }

    function redirectLogin() {
        var next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = '/login?next=' + next;
    }

    function fetchRegions() {
        return fetch('/api/conduit/regions', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        })
        .then(function (r) {
            if (r.status === 401) { redirectLogin(); return null; }
            if (!r.ok) { showState('error'); return null; }
            return r.json();
        })
        .then(function (d) { if (d) render(d); })
        .catch(function () { showState('error'); });   // network/parse -> silent error state
    }

    function dashboardVisible() {
        var s = el('section-dashboard');
        return !(s && s.hidden);
    }

    // 60 s tick: only fetch while the Dashboard section is visible.
    function tick() {
        if (!dashboardVisible()) return;
        return fetchRegions();
    }

    onReady(function () {
        if (!el('regions-card')) return;
        // Dashboard-aware poll, registered for logout teardown (window.CCC.pollers).
        window.CCC.pollers.push(startPolling(tick, 60000));
        // Immediate refresh when navigating into the Dashboard.
        window.addEventListener('hashchange', function () {
            if (dashboardVisible()) fetchRegions();
        });
    });
})();
