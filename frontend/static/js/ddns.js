/**
 * frontend/static/js/ddns.js
 * DDNS status panel — Issue #43
 *
 * API endpoint
 * ------------
 *   GET /api/ddns/status   polled every 60 seconds via startPolling()
 *
 * Response schema (from backend/api/ddns.py, Issue #42)
 * -------------------------------------------------------
 *   hostname           str|null   Cloudflare record name from settings
 *   current_ip         str|null   IP currently registered in Cloudflare
 *   last_updated       str|null   ISO-8601 timestamp of last run
 *   last_result        str        "updated"|"no_change"|"error"|"unknown"
 *   last_message       str|null   Human-readable message from DDNS script
 *   consecutive_errors int        Trailing run count with result="error"
 *
 * Warning banner
 * --------------
 * Shown when consecutive_errors >= 3.  Hidden otherwise.
 * Text: "DDNS has failed 3+ consecutive times. Your dashboard may become
 *        unreachable. Check /var/log/conduit-cc/ddns.log."
 *
 * Polling error strategy
 * ----------------------
 * Uses raw fetch() (not apiFetch) to avoid toast flooding at 60-second
 * intervals.  On any non-2xx or network error: renderUnavailable() sets
 * all values to "—" and the badge to neutral — no toast shown.
 * Exception: HTTP 401 redirects to /login?next=<current path>.
 *
 * Script loading order: api.js → app.js → [inline shell] → status.js →
 *   metrics.js → traffic.js → logs.js → settings.js → ddns.js
 * window.CCC and window.CCC.pollers must exist (initialised by dashboard.html).
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       DOM element IDs
       Must match the id= attributes in dashboard.html exactly.
    ------------------------------------------------------------------ */

    var ID = {
        hostname:      'ddns-hostname',
        ip:            'ddns-ip',
        lastUpdated:   'ddns-last-updated',
        resultBadge:   'ddns-result-badge',
        lastMessage:   'ddns-last-message',
        warningBanner: 'ddns-warning-banner',
    };

    /* ------------------------------------------------------------------
       Badge class mapping
       Matches the CSS badge classes defined in base.css (Issue #24).
         updated   -> badge--success  (green)
         no_change -> badge--neutral  (grey -- no blue badge class in v0.1)
         error     -> badge--danger   (red)
         unknown   -> badge--unknown  (grey)
    ------------------------------------------------------------------ */

    var BADGE_CLASS = {
        updated:   'badge--success',
        no_change: 'badge--neutral',
        error:     'badge--danger',
        unknown:   'badge--unknown',
    };

    /* Pre-compute flat array for class removal. */
    var ALL_BADGE_CLASSES = Object.keys(BADGE_CLASS).map(function (k) {
        return BADGE_CLASS[k];
    });

    /* ------------------------------------------------------------------
       DOM helpers
    ------------------------------------------------------------------ */

    function el(id) { return document.getElementById(id); }

    function setText(id, text) {
        var e = el(id);
        if (e) e.textContent = text;
    }

    /* ------------------------------------------------------------------
       formatRelativeTime
       Converts an ISO-8601 timestamp string to a human-readable relative
       time string.  Returns "Never" for null/undefined/unparseable input.

       Ranges (floor):
         < 60 s         -> "just now"
         < 3600 s       -> "N minute(s) ago"
         < 86400 s      -> "N hour(s) ago"
         >= 86400 s     -> "N day(s) ago"

       Clock-skew guard: negative diff -> "just now".
    ------------------------------------------------------------------ */

    function formatRelativeTime(isoString) {
        if (!isoString) return 'Never';
        var ts = Date.parse(isoString);
        if (isNaN(ts)) return 'Never';

        var diffSec = Math.floor((Date.now() - ts) / 1000);
        if (diffSec < 60) return 'just now';   /* covers negatives too */

        var diffMin = Math.floor(diffSec / 60);
        if (diffMin < 60) {
            return diffMin + (diffMin === 1 ? ' minute ago' : ' minutes ago');
        }

        var diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) {
            return diffHr + (diffHr === 1 ? ' hour ago' : ' hours ago');
        }

        var diffDay = Math.floor(diffHr / 24);
        return diffDay + (diffDay === 1 ? ' day ago' : ' days ago');
    }

    /* ------------------------------------------------------------------
       applyBadge
       Swaps the CSS modifier class on the result badge element and sets
       its text content to the result string.
       Centralised to avoid duplication between renderDdns and
       renderUnavailable.
    ------------------------------------------------------------------ */

    function applyBadge(result) {
        var badge = el(ID.resultBadge);
        if (!badge) return;
        ALL_BADGE_CLASSES.forEach(function (cls) {
            badge.classList.remove(cls);
        });
        var newClass = BADGE_CLASS[result] || BADGE_CLASS.unknown;
        badge.classList.add(newClass);
        badge.textContent = result;
    }

    /* ------------------------------------------------------------------
       renderDdns
       Called on a successful poll response (HTTP 200).
       Renders all DdnsStatusResponse fields into the panel DOM.
    ------------------------------------------------------------------ */

    function renderDdns(data) {
        /* hostname: null when CF_RECORD_NAME is unset in .env (fresh install). */
        setText(ID.hostname, data.hostname || '—');

        /* current_ip: null until the first successful DDNS run. */
        setText(ID.ip, data.current_ip || '—');

        /* last_updated: relative time string; "Never" when null. */
        setText(ID.lastUpdated, formatRelativeTime(data.last_updated));

        /* last_message: null before first run. */
        setText(ID.lastMessage, data.last_message || '—');

        /* Result badge. */
        applyBadge(data.last_result || 'unknown');

        /* Warning banner: visible when consecutive_errors >= 3. */
        var banner = el(ID.warningBanner);
        if (banner) {
            banner.style.display = (data.consecutive_errors >= 3) ? '' : 'none';
        }
    }

    /* ------------------------------------------------------------------
       renderUnavailable
       Called on poll failure (non-2xx response or network error).
       Sets all visible fields to "—" and applies the neutral badge.
       Does NOT show a Toast -- repeated failures must not flood the UI.
       Hides the warning banner: we cannot determine the error count.
    ------------------------------------------------------------------ */

    function renderUnavailable() {
        setText(ID.hostname,    '—');
        setText(ID.ip,          '—');
        setText(ID.lastUpdated, '—');
        setText(ID.lastMessage, '—');

        applyBadge('unknown');

        var banner = el(ID.warningBanner);
        if (banner) banner.style.display = 'none';
    }

    /* ------------------------------------------------------------------
       fetchDdnsPoll
       Uses raw fetch() to bypass apiFetch's toast-on-error behaviour.
       Session expiry (401) -> redirect to /login preserving current path.
       All other non-2xx or network errors -> renderUnavailable() (silent).
    ------------------------------------------------------------------ */

    function fetchDdnsPoll() {
        fetch('/api/ddns/status', {
            method:      'GET',
            headers:     { 'Accept': 'application/json' },
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
                renderUnavailable();
                return null;
            }
            return response.json();
        })
        .then(function (data) {
            if (data) renderDdns(data);
        })
        .catch(function () {
            /* Network-level failure (offline, DNS, TLS): silent inline reset. */
            renderUnavailable();
        });
    }

    /* ------------------------------------------------------------------
       Initialise
       Runs after the DOM is ready.  Starts 60-second polling and registers
       the handle in window.CCC.pollers so the logout handler can stop it.

       60 s interval rationale:
         - The backend caches for 30 s.
         - The DDNS cron job runs every 5 minutes.
         - 60 s is the spec requirement and gives fresh data within two cache
           TTLs without over-polling a low-power Pi 4.
    ------------------------------------------------------------------ */

    onReady(function () {
        var handle = startPolling(fetchDdnsPoll, 60000);
        window.CCC.pollers.push(handle);
    });

})();
