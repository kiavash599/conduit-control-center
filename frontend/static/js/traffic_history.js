/**
 * frontend/static/js/traffic_history.js
 * Lifetime & History traffic card — TC-1 (scaffold; chart added in TC-2)
 *
 * API endpoint
 * ------------
 *   GET /api/traffic/summary   polled every 60 seconds via startPolling()
 *   (GET /api/traffic/series is wired in TC-2 — not used here.)
 *
 * Response schema (backend/api/traffic.py, CI77)
 * ----------------------------------------------
 *   status           string        collector health (e.g. "running", "disabled")
 *   recording_since  string|null   ISO 8601 UTC of earliest epoch (null => never recorded)
 *   last_ok_ts_utc   string|null   ISO 8601 UTC of last successful collector tick
 *   lifetime         {bytes_up, bytes_down} | null   persistent totals (null => not recording)
 *   windows          { last_24h:{bytes_up,bytes_down}, last_7d:{bytes_up,bytes_down} }
 *
 * Four-state model (IA-1 vocabulary)
 * ----------------------------------
 *   loading                — before the first response
 *   populated              — recording_since present (lifetime may be zero)
 *   disabled/not recording — HTTP 200 with recording_since null (ship-dark default)
 *   error                  — fetch failure or non-2xx (except 401 -> /login redirect)
 *
 * Polling error strategy
 * ----------------------
 * Uses raw fetch() (not apiFetch) to suppress apiFetch's toast-on-error,
 * matching traffic.js / status.js / metrics.js. 401 -> redirect to /login.
 * All other non-2xx and network failures render the inline error state
 * silently (no toast). The /summary endpoint returns HTTP 200 even when the
 * collector is disabled, so "not recording" is distinguished from "error"
 * by inspecting recording_since on a 200 response.
 *
 * formatBytes / relativeTime are duplicated from traffic.js (no module system
 * in v0.1 — a small, contained duplication is preferable to polluting app.js).
 *
 * Script loading order:
 *   api.js -> app.js -> dashboard.js -> ... -> traffic.js -> traffic_history.js
 * window.CCC.pollers must exist (initialised by dashboard.js).
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       formatBytes — raw byte count to human-readable (B/KB/MB/GB).
       Duplicated from traffic.js (same tiers/labels for consistency).
    ------------------------------------------------------------------ */
    function formatBytes(bytes) {
        if (bytes == null) return '—';
        var gb = bytes / (1024 * 1024 * 1024);
        if (gb >= 1) return gb.toFixed(1) + ' GB';
        var mb = bytes / (1024 * 1024);
        if (mb >= 1) return mb.toFixed(1) + ' MB';
        var kb = bytes / 1024;
        if (kb >= 1) return Math.round(kb) + ' KB';
        return (bytes || 0) + ' B';
    }

    /* ------------------------------------------------------------------
       relativeTime — ISO 8601 to a relative string. From traffic.js.
    ------------------------------------------------------------------ */
    function relativeTime(isoStr) {
        if (!isoStr) return '—';
        var dt = new Date(isoStr);
        if (isNaN(dt.getTime())) return '—';
        var delta = Math.floor((Date.now() - dt.getTime()) / 1000);
        if (delta < 0)     delta = 0;
        if (delta < 10)    return 'just now';
        if (delta < 60)    return delta + ' seconds ago';
        if (delta < 3600)  return Math.floor(delta / 60) + ' minutes ago';
        if (delta < 86400) return Math.floor(delta / 3600) + ' hours ago';
        return Math.floor(delta / 86400) + ' days ago';
    }

    /* ------------------------------------------------------------------
       formatUtc — ISO 8601 to "YYYY-MM-DD HH:MM UTC".
       Always rendered in UTC to match the ledger's bucket semantics and
       avoid client-timezone ambiguity.
    ------------------------------------------------------------------ */
    function formatUtc(isoStr) {
        if (!isoStr) return '—';
        var dt = new Date(isoStr);
        if (isNaN(dt.getTime())) return '—';
        function p(n) { return (n < 10 ? '0' : '') + n; }
        return dt.getUTCFullYear() + '-' + p(dt.getUTCMonth() + 1) + '-' + p(dt.getUTCDate())
             + ' ' + p(dt.getUTCHours()) + ':' + p(dt.getUTCMinutes()) + ' UTC';
    }

    /* ------------------------------------------------------------------
       DOM helpers
    ------------------------------------------------------------------ */
    function el(id) { return document.getElementById(id); }
    function setText(id, text) { var e = el(id); if (e) e.textContent = text; }

    // The four mutually-exclusive state regions inside #traffic-history-card.
    var STATES = ['loading', 'error', 'empty', 'body'];

    function showState(name) {
        STATES.forEach(function (s) {
            var e = el('traffic-history-' + s);
            if (e) e.hidden = (s !== name);
        });
    }

    // Render a {bytes_up, bytes_down} window as "X sent · Y received".
    function pair(w) {
        if (!w) return '—';
        return formatBytes(w.bytes_up) + ' sent · ' + formatBytes(w.bytes_down) + ' received';
    }

    /* ------------------------------------------------------------------
       renderPopulated — collector is recording (lifetime may be zero).
    ------------------------------------------------------------------ */
    function renderPopulated(d) {
        var lt  = d.lifetime || { bytes_up: 0, bytes_down: 0 };
        var win = d.windows || {};
        setText('th-lifetime-up',   formatBytes(lt.bytes_up));
        setText('th-lifetime-down', formatBytes(lt.bytes_down));
        setText('th-24h',     pair(win.last_24h));
        setText('th-7d',      pair(win.last_7d));
        setText('th-since',   formatUtc(d.recording_since));
        setText('th-updated', relativeTime(d.last_ok_ts_utc));
        showState('body');
    }

    /* ------------------------------------------------------------------
       fetchSummaryPoll — raw fetch() to bypass apiFetch's toast-on-error.
         401          -> redirect to /login (session expired).
         other !ok    -> error state (silent).
         200          -> recording_since null  => not-recording (empty)
                         otherwise              => populated
         network err  -> error state (silent).
    ------------------------------------------------------------------ */
    function fetchSummaryPoll() {
        return fetch('/api/traffic/summary', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        })
        .then(function (response) {
            if (response.status === 401) {
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            if (!response.ok) {
                showState('error');
                return null;
            }
            return response.json();
        })
        .then(function (data) {
            if (!data) return;
            // /summary returns 200 even when disabled: distinguish
            // "not recording" (ship-dark default) from populated.
            if (data.recording_since == null || data.lifetime == null) {
                showState('empty');
            } else {
                renderPopulated(data);
            }
        })
        .catch(function () {
            // Network-level failure (offline, DNS, TLS).
            showState('error');
        });
    }

    /* ------------------------------------------------------------------
       Initialise — 60-second polling, registered in window.CCC.pollers so
       the logout handler stops it cleanly. No-op if the card is absent.
    ------------------------------------------------------------------ */
    onReady(function () {
        if (!el('traffic-history-card')) return;
        var handle = startPolling(fetchSummaryPoll, 60000);
        window.CCC.pollers.push(handle);
    });

})();
