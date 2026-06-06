/**
 * frontend/static/js/traffic.js
 * Traffic counter widget — Issue #29
 *
 * API endpoint
 * ------------
 *   GET /api/metrics/traffic   polled every 30 seconds via startPolling()
 *
 * Response schema (Issue #22 — not yet implemented)
 * -------------------------------------------------
 *   data.bytes_sent      int     bytes sent this session
 *   data.bytes_received  int     bytes received this session
 *   data.session_start   string  ISO 8601 timestamp of session start
 *   data.timestamp       string  ISO 8601 timestamp of reading
 *
 * Current backend state
 * ---------------------
 * GET /api/metrics/traffic returns HTTP 501 until Issue #22 ships.
 * HTTP 501 is treated as "no data" silently — no toast, no error badge.
 * This is intentional: toasting a known stub on every 30-second poll
 * for the entire v0.1 lifecycle would be actively harmful UX.
 *
 * Polling error strategy
 * ----------------------
 * Uses raw fetch() (not apiFetch) — same rationale as status.js and
 * metrics.js.  apiFetch toasts all non-2xx responses unconditionally;
 * that behaviour cannot be suppressed for specific status codes (e.g.
 * 501) without modifying the shared api.js utility.
 * All non-2xx responses (including 501, 503, network errors): call
 * renderNoData() — no toast shown.
 * Exception: HTTP 401 → redirect to /login?next=<path> (session expired).
 *
 * Shared helpers (formatBytes, relativeTime)
 * ------------------------------------------
 * Both helpers are duplicated from metrics.js and status.js respectively.
 * There is no module system in v0.1; polluting app.js with formatting
 * utilities is worse than a small, contained duplication.
 * formatBytes here adds KB/B tiers absent from the metrics.js copy
 * because traffic counters may show small values during short sessions.
 *
 * Script loading order:
 *   api.js → app.js → [inline shell] → status.js → metrics.js → traffic.js
 * window.CCC and window.CCC.pollers must exist (initialised by dashboard.html).
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       formatBytes
       Converts a raw byte count to a human-readable string.
       Tiers: B / KB / MB / GB
       Note: includes KB and B tiers unlike the metrics.js copy, because
       traffic counters may represent short-session totals in the low KB
       range during development or brief test runs.
    ------------------------------------------------------------------ */

    function formatBytes(bytes) {
        if (bytes == null) return '—';
        var gb = bytes / (1024 * 1024 * 1024);
        if (gb >= 1) return gb.toFixed(1) + ' GB';
        var mb = bytes / (1024 * 1024);
        if (mb >= 1) return mb.toFixed(1) + ' MB';
        var kb = bytes / 1024;
        if (kb >= 1) return Math.round(kb) + ' KB';
        return bytes + ' B';
    }

    /* ------------------------------------------------------------------
       relativeTime
       Converts an ISO 8601 timestamp to a human-readable relative string.
       Duplicated from status.js (same logic, same tiers).
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
       DOM helper
    ------------------------------------------------------------------ */

    function setText(id, text) {
        var e = document.getElementById(id);
        if (e) e.textContent = text;
    }

    /* ------------------------------------------------------------------
       renderTraffic
       Called on a successful poll response with non-null traffic data.
    ------------------------------------------------------------------ */

    function renderTraffic(data) {
        setText('traffic-sent',          formatBytes(data.bytes_sent));
        setText('traffic-received',      formatBytes(data.bytes_received));
        setText('traffic-session-start', relativeTime(data.session_start));
        setText('traffic-note',          '');
    }

    /* ------------------------------------------------------------------
       renderNoData
       Called when:
         - API returns any non-2xx (including 501 stub, 503, etc.)
         - API returns 2xx but bytes_sent and bytes_received are both null
         - Network-level failure
       Sets all values to "—" and shows a user-facing note.
       Does NOT show a toast.
    ------------------------------------------------------------------ */

    function renderNoData() {
        setText('traffic-sent',          '—');
        setText('traffic-received',      '—');
        setText('traffic-session-start', '—');
        setText('traffic-note',          'No data — available when Conduit is running.');
    }

    /* ------------------------------------------------------------------
       fetchTrafficPoll
       Uses raw fetch() to bypass apiFetch's toast-on-error behaviour.
       401 → redirect to /login (session expired).
       501 and all other non-2xx → renderNoData() silently.
    ------------------------------------------------------------------ */

    function fetchTrafficPoll() {
        fetch('/api/metrics/traffic', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        })
        .then(function (response) {
            if (response.status === 401) {
                // Session expired: redirect preserving current path.
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            // 501 (Issue #22 not yet implemented), 503, or any other
            // non-2xx: show "no data" state without a toast.
            if (!response.ok) {
                renderNoData();
                return null;
            }
            return response.json();
        })
        .then(function (data) {
            if (!data) return;
            // Conduit may not have run this session: both counters null.
            if (data.bytes_sent == null && data.bytes_received == null) {
                renderNoData();
            } else {
                renderTraffic(data);
            }
        })
        .catch(function () {
            // Network-level failure (offline, DNS, TLS).
            renderNoData();
        });
    }

    /* ------------------------------------------------------------------
       Initialise
       Starts 30-second polling on DOM ready and registers the handle in
       window.CCC.pollers so the logout handler can stop it cleanly.
    ------------------------------------------------------------------ */

    onReady(function () {
        var handle = startPolling(fetchTrafficPoll, 30000);
        window.CCC.pollers.push(handle);
    });

})();
