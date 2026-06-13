/**
 * frontend/static/js/logs.js
 * Log viewer panel — Issue #30
 *
 * API endpoint
 * ------------
 *   GET /api/logs?limit=200   polled every 30 s via startPolling()
 *
 * Response schema (Issue #23, fully implemented)
 * -----------------------------------------------
 *   Array of LogLine objects:
 *     line.timestamp  string|null  ISO 8601 from journalctl, or null for
 *                                  redacted/malformed lines
 *     line.level      string       DEBUG | INFO | WARNING | ERROR | CRITICAL
 *     line.message    string       log message, or "[REDACTED]"
 *
 * Polling strategy
 * ----------------
 * Uses raw fetch() — not apiFetch() — to avoid toast flooding at 30-second
 * intervals.  503 (journalctl unavailable) is displayed inline in the log
 * viewer; no toast is shown.  401 redirects to /login?next=<path>.
 *
 * Section-aware polling
 * ----------------------
 * GET /api/logs spawns a journalctl subprocess — more expensive than the
 * other dashboard poll endpoints.  The tick function returns immediately
 * if the System section is hidden (user is on Dashboard or Settings).
 * This avoids unnecessary subprocess calls on the Raspberry Pi while the
 * user is not actively viewing the log panel.
 *
 * Auto-refresh pause on scroll-up
 * --------------------------------
 * The auto-refresh tick also skips fetching if the user has scrolled up in
 * the log viewer (not at bottom).  The manual Refresh button always fetches
 * regardless of scroll position.  After scrolling back to the bottom, the
 * next 30-second tick resumes auto-refresh; the Refresh button provides
 * immediate refresh without waiting.
 *
 * XSS safety
 * ----------
 * ALL log content is written to the DOM exclusively via textContent.
 * innerHTML, insertAdjacentHTML, and template-string HTML injection are
 * never used.  Log messages come from journalctl and are untrusted.
 *
 * Script loading order:
 *   api.js → app.js → [inline shell] → status.js → metrics.js →
 *   traffic.js → logs.js
 * window.CCC and window.CCC.pollers must exist (initialised by dashboard.html).
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       Level → CSS modifier class
       CRITICAL maps to the same visual class as ERROR (both are --danger red).
       INFO maps to '' (base colour — no modifier class needed).
    ------------------------------------------------------------------ */

    var LEVEL_CLASS = {
        'DEBUG':    'log-line--debug',
        'INFO':     '',
        'WARNING':  'log-line--warning',
        'ERROR':    'log-line--error',
        'CRITICAL': 'log-line--error',
    };

    /* ------------------------------------------------------------------
       Module state
    ------------------------------------------------------------------ */

    // true after the first successful fetch completes.
    // Used by the tick to decide whether to fetch on section-enter.
    var hasLoaded = false;

    /* ------------------------------------------------------------------
       DOM helper
    ------------------------------------------------------------------ */

    function el(id) { return document.getElementById(id); }

    /* ------------------------------------------------------------------
       isLogsVisible
       Returns true when the section containing the log viewer is the active
       (non-hidden) section. Since IA-2 the viewer lives in #section-system.
       Reads el.hidden set by dashboard.html's showSection() — no coupling
       to the navigation mechanism itself.
    ------------------------------------------------------------------ */

    function isLogsVisible() {
        var section = el('section-system');
        return section ? !section.hidden : false;
    }

    /* ------------------------------------------------------------------
       isAtBottom
       Returns true when the viewer is at (or within 4px of) the bottom.
       The 4px tolerance handles sub-pixel rendering and scrollbar widths.
    ------------------------------------------------------------------ */

    function isAtBottom(container) {
        return container.scrollHeight - container.scrollTop - container.clientHeight < 4;
    }

    /* ------------------------------------------------------------------
       scrollToBottom
    ------------------------------------------------------------------ */

    function scrollToBottom(container) {
        container.scrollTop = container.scrollHeight;
    }

    /* ------------------------------------------------------------------
       updateScrollButton
       Shows or hides the "↓ Latest" button based on scroll position.
    ------------------------------------------------------------------ */

    function updateScrollButton(container) {
        var btn = el('log-scroll-btn');
        if (btn) btn.hidden = isAtBottom(container);
    }

    /* ------------------------------------------------------------------
       renderLines
       Renders an array of LogLine objects into the log viewer.

       SECURITY — XSS prevention:
         Every field (timestamp, message) is written via textContent only.
         No innerHTML, no insertAdjacentHTML, no template-string injection.
         Even if a log line contains '<script>alert(1)</script>', it will
         render as literal text in a <div> element, never as HTML.
    ------------------------------------------------------------------ */

    function renderLines(lines, viewer) {
        var frag = document.createDocumentFragment();

        if (lines.length === 0) {
            var empty = document.createElement('div');
            empty.className = 'log-viewer__empty';
            // SECURITY: textContent — not innerHTML.
            empty.textContent = 'No log entries yet.';
            frag.appendChild(empty);
        } else {
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i];
                var row  = document.createElement('div');

                // Determine CSS modifier class.
                // [REDACTED] check comes before level lookup because the
                // backend sets level="INFO" for redacted lines regardless.
                var modClass = '';
                if (line.message === '[REDACTED]') {
                    modClass = 'log-line--redacted';
                } else {
                    modClass = LEVEL_CLASS[line.level] || '';
                }

                row.className = 'log-line' + (modClass ? ' ' + modClass : '');

                // Build text content: optional timestamp prefix + message.
                // Null timestamps (redacted or malformed lines) are omitted —
                // not rendered as "null" or "—". See logs.py LogLine docstring.
                // SECURITY: textContent assignment prevents any HTML injection.
                var text = '';
                if (line.timestamp !== null && line.timestamp !== undefined && line.timestamp !== '') {
                    text = line.timestamp + ' ';
                }
                text += line.message;
                row.textContent = text;  // SECURITY: textContent, never innerHTML

                frag.appendChild(row);
            }
        }

        // Replace viewer content in a single batch DOM operation.
        while (viewer.firstChild) {
            viewer.removeChild(viewer.firstChild);
        }
        viewer.appendChild(frag);

        // Update line count badge.
        var countEl = el('log-line-count');
        if (countEl) {
            // SECURITY: textContent — count is a number, but belt-and-suspenders.
            countEl.textContent = lines.length > 0 ? lines.length + ' lines' : '';
        }
    }

    /* ------------------------------------------------------------------
       renderError
       Shown inline in the log viewer when the API returns 503 or a
       network error occurs.  No toast is shown.
    ------------------------------------------------------------------ */

    function renderError(message) {
        var viewer = el('log-viewer');
        if (!viewer) return;

        while (viewer.firstChild) {
            viewer.removeChild(viewer.firstChild);
        }

        var msg = document.createElement('div');
        msg.className = 'log-viewer__empty';
        msg.textContent = message;  // SECURITY: textContent, never innerHTML

        viewer.appendChild(msg);

        var countEl = el('log-line-count');
        if (countEl) countEl.textContent = '';
    }

    /* ------------------------------------------------------------------
       fetchLogs
       @param {boolean} forceScroll
         true  — always scroll to bottom after render (manual Refresh)
         false — scroll to bottom only if viewer was already at the bottom
                 before the fetch (auto-tick)
    ------------------------------------------------------------------ */

    function fetchLogs(forceScroll) {
        fetch('/api/logs', {
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
            if (!response.ok) {
                // 503 (journalctl unavailable) or any other error.
                // Show inline message — no toast.
                renderError(
                    'Logs unavailable (HTTP ' + response.status + '). ' +
                    'Check journalctl on the server.'
                );
                return null;
            }
            return response.json();
        })
        .then(function (lines) {
            if (lines === null) return;

            var viewer = el('log-viewer');
            if (!viewer) return;

            var wasAtBottom = isAtBottom(viewer);

            renderLines(lines, viewer);
            hasLoaded = true;

            // Scroll to bottom when:
            //   - Manual Refresh (forceScroll = true), OR
            //   - Auto-tick and viewer was already at the bottom before render
            if (forceScroll || wasAtBottom) {
                scrollToBottom(viewer);
            }

            updateScrollButton(viewer);
        })
        .catch(function () {
            // Network-level failure (offline, DNS, TLS).
            renderError('Network error — could not reach the log endpoint.');
        });
    }

    /* ------------------------------------------------------------------
       logsPollTick
       Called by startPolling every 30 seconds.

       Guards (both must pass before fetching):
         1. Section visible  — skips if #section-system is hidden
         2. At bottom        — skips if user has scrolled up (not at bottom)
            Exception: !hasLoaded — always fetch on first view (before
            any data has loaded), so the viewer is not empty when the
            user first navigates to the Logs section.
    ------------------------------------------------------------------ */

    function logsPollTick() {
        // Guard 1: section must be visible.
        if (!isLogsVisible()) return;

        var viewer = el('log-viewer');

        // Guard 2: skip auto-refresh if user has scrolled up.
        // Exception: always fetch if no data has been loaded yet.
        if (hasLoaded && viewer && !isAtBottom(viewer)) return;

        fetchLogs(false);
    }

    /* ------------------------------------------------------------------
       Initialise on DOM ready
    ------------------------------------------------------------------ */

    onReady(function () {
        var viewer     = el('log-viewer');
        var refreshBtn = el('log-refresh-btn');
        var scrollBtn  = el('log-scroll-btn');

        // Scroll event: update the scroll-to-bottom button visibility.
        if (viewer) {
            viewer.addEventListener('scroll', function () {
                updateScrollButton(viewer);
            });
        }

        // Manual Refresh: always fetch, always scroll to bottom.
        if (refreshBtn) {
            refreshBtn.addEventListener('click', function () {
                fetchLogs(true);
            });
        }

        // Scroll-to-bottom button.
        if (scrollBtn) {
            scrollBtn.addEventListener('click', function () {
                if (viewer) { scrollToBottom(viewer); }
                updateScrollButton(viewer);
            });
        }

        // hashchange listener: trigger an immediate first-load fetch when
        // the user navigates to #system before the next 30-second tick fires.
        // This prevents the viewer from appearing empty for up to 30 seconds
        // after navigating to the System section.
        window.addEventListener('hashchange', function () {
            if (isLogsVisible() && !hasLoaded) {
                fetchLogs(true);
            }
        });

        // Start 30-second polling. Register handle in window.CCC.pollers
        // so the logout handler stops it before calling /api/auth/logout.
        var handle = startPolling(logsPollTick, 30000);
        window.CCC.pollers.push(handle);
    });

})();
