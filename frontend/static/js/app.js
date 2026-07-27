/**
 * frontend/static/js/app.js
 * Conduit Control Center — Issue #24
 *
 * Polling manager and page initialisation helpers.
 *
 * Script loading order (required — no module system in v0.1):
 *   1. api.js   — must load first (apiFetch, Toast)
 *   2. app.js   (this file)
 *   3. page-specific scripts
 *
 * Globals exposed: startPolling, stopPolling, onReady
 */

'use strict';

/* ============================================================================
   Polling manager
   ============================================================================
   startPolling(fn, intervalMs) -> handle
     - Calls fn() immediately, then every intervalMs milliseconds.
     - Automatically pauses when the browser tab becomes hidden
       (document.visibilityState === 'hidden') to avoid unnecessary API
       traffic and CPU load on the target Raspberry Pi 4.
     - On tab visible again: calls fn() immediately (stale-data prevention),
       then restarts the interval from zero.

   stopPolling(handle)
     - Clears the interval and removes the visibility listener.
     - Must be called when navigating away from a page to prevent
       leaked intervals. Losing a handle without calling stopPolling()
       leaks both the interval and the event listener.

   Usage
     const handle = startPolling(() => fetchAndRenderStatus(), 5000);
     // ... later, on page unload or route change:
     stopPolling(handle);
   ============================================================================ */

/**
 * Start polling a function at a fixed interval.
 *
 * @param {Function} fn          - Function to call on each poll tick.
 *                                 May be async; errors are caught and logged.
 * @param {number}   intervalMs  - Poll interval in milliseconds.
 * @returns {{ fn, intervalMs, timerId, active, _visibilityListener }}
 *          Opaque handle — pass to stopPolling() to cancel.
 */
function startPolling(fn, intervalMs) {
    const handle = {
        fn,
        intervalMs,
        timerId: null,
        active: true,
        _visibilityListener: null,
    };

    function tick() {
        if (!handle.active) return;
        try {
            // fn() may return a Promise; errors in async fns are caught below.
            const result = handle.fn();
            if (result && typeof result.catch === 'function') {
                result.catch(err => {
                    // apiFetch already shows a toast on API errors.
                    // Log here for developer visibility only.
                    console.warn('[CCC] Poll error:', err);
                });
            }
        } catch (err) {
            console.warn('[CCC] Poll error (sync):', err);
        }
    }

    function startInterval() {
        handle.timerId = setInterval(tick, handle.intervalMs);
    }

    function onVisibilityChange() {
        if (!handle.active) return;

        if (document.visibilityState === 'hidden') {
            // Tab hidden: pause the interval to reduce unnecessary load.
            clearInterval(handle.timerId);
            handle.timerId = null;
        } else {
            // Tab visible again: fire immediately (fresh data on focus),
            // then restart the interval from zero.
            tick();
            startInterval();
        }
    }

    handle._visibilityListener = onVisibilityChange;
    document.addEventListener('visibilitychange', onVisibilityChange);

    // Fire immediately on start, then begin the interval.
    tick();
    startInterval();

    return handle;
}

/**
 * Stop a polling handle and clean up all resources.
 *
 * Safe to call with null/undefined (no-op).
 *
 * @param {object|null} handle - Handle returned by startPolling().
 */
function stopPolling(handle) {
    if (!handle) return;
    handle.active = false;
    clearInterval(handle.timerId);
    handle.timerId = null;
    if (handle._visibilityListener) {
        document.removeEventListener('visibilitychange', handle._visibilityListener);
        handle._visibilityListener = null;
    }
}


/* ============================================================================
   Page initialisation helpers
   ============================================================================ */

/**
 * Run a callback after the DOM is ready.
 * Safe to call even if DOMContentLoaded has already fired.
 *
 * @param {Function} fn
 */
function onReady(fn) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fn, { once: true });
    } else {
        // DOM already parsed — call synchronously so callers don't need
        // to handle the async case separately.
        fn();
    }
}


/* ============================================================================
   Display timezone (v0.3.22)
   ----------------------------------------------------------------------------
   Storage and transport stay UTC everywhere; this affects rendering only. The
   effective IANA zone is set server-side (Settings -> Display timezone) and
   published here once, so every card formats times identically.

   Conduit's reduced-mode schedule is HH:MM UTC by contract — that field shows
   BOTH UTC and local and is never converted in place (see conduit_config.js).
   ============================================================================ */

var CCC_TZ = 'UTC';   // effective zone; replaced by setDisplayTimezone()

function setDisplayTimezone(name) {
    if (name && typeof name === 'string') CCC_TZ = name;
}

function getDisplayTimezone() { return CCC_TZ; }

/** The browser's own IANA zone, or '' when unavailable (Settings pre-select). */
function browserTimezone() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (e) {
        return '';
    }
}

/**
 * Format an ISO-8601 UTC instant in the effective display timezone.
 * Returns an em dash for null/invalid input (matching the card placeholders).
 *
 * @param {string} isoStr        ISO instant, e.g. "2026-07-25T12:03:00Z"
 * @param {object} [opts]        {date:bool, time:bool, zone:bool}
 */
function formatTime(isoStr, opts) {
    if (!isoStr) return '—';
    var dt = new Date(isoStr);
    if (isNaN(dt.getTime())) return '—';
    var o = opts || {};
    var fmt = {
        timeZone: CCC_TZ,
        hour12: false,
    };
    if (o.date !== false) {
        fmt.year = 'numeric'; fmt.month = '2-digit'; fmt.day = '2-digit';
    }
    if (o.time !== false) {
        fmt.hour = '2-digit'; fmt.minute = '2-digit';
    }
    if (o.zone) fmt.timeZoneName = 'short';
    try {
        return new Intl.DateTimeFormat('sv-SE', fmt).format(dt);
    } catch (e) {
        // Unknown zone (or no tz data): fall back to a UTC rendering rather
        // than breaking the card.
        return isoStr;
    }
}

/** "HH:MM" in the effective zone (chart axis labels). */
function formatHourLabel(isoStr) {
    return formatTime(isoStr, { date: false, time: true });
}

/** A short zone suffix for headings, e.g. "Asia/Yangon". */
function displayZoneLabel() { return CCC_TZ; }


/* ============================================================================
   Globals (no module system in v0.1 — scripts loaded via <script> tags)
   ============================================================================ */

window.startPolling        = startPolling;
window.stopPolling         = stopPolling;
window.onReady             = onReady;
window.setDisplayTimezone  = setDisplayTimezone;
window.getDisplayTimezone  = getDisplayTimezone;
window.browserTimezone     = browserTimezone;
window.formatTime          = formatTime;
window.formatHourLabel     = formatHourLabel;
window.displayZoneLabel    = displayZoneLabel;
