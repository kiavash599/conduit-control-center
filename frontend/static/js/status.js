/**
 * frontend/static/js/status.js
 * Node status and control panel — Issue #27
 *
 * API endpoints used
 * ------------------
 *   GET  /api/status          -- polled every 5 seconds via startPolling()
 *   POST /api/conduit/start   -- start the Conduit service
 *   POST /api/conduit/stop    -- stop the Conduit service
 *   POST /api/conduit/restart -- restart the Conduit service
 *
 * Polling vs action error strategy
 * ---------------------------------
 * Polling failures (GET /api/status non-2xx or network error):
 *   Update the badge to "Connection error" inline.
 *   Do NOT toast: repeated 5-second poll failures would flood the screen
 *   even with deduplication (toast auto-removes after 5 s, then reappears).
 *   fetchStatusPoll() uses raw fetch() to bypass apiFetch's toast logic.
 *   Exception: 401 (session expired) redirects to /login, same as apiFetch.
 *
 * Action failures (POST /api/conduit/* non-2xx):
 *   Let apiFetch toast the error detail -- user-initiated, one-shot.
 *
 * Script loading order: api.js -> app.js -> status.js
 * window.CCC and window.CCC.pollers must exist (initialised by dashboard.html).
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       Badge configuration
       Maps node_status values to CSS class and display text.
       Classes are defined in base.css [data-theme="dark"].
    ------------------------------------------------------------------ */

    var BADGE = {
        running:  { cls: 'badge--running',  text: 'Running',  pulse: true  },
        stopped:  { cls: 'badge--stopped',  text: 'Stopped',  pulse: false },
        starting: { cls: 'badge--starting', text: 'Starting', pulse: true  },
        stopping: { cls: 'badge--stopping', text: 'Stopping', pulse: true  },
        error:    { cls: 'badge--error',    text: 'Error',    pulse: false },
    };

    /* ------------------------------------------------------------------
       Button enable matrix
       Mirrors the backend _BLOCKED logic in backend/api/conduit.py.
       true = button enabled for that status.

       Backend rules (Issue #19):
         start   blocked: running, starting, stopping
         stop    blocked: stopped, starting, stopping
         restart blocked: starting, stopping
       Therefore restart IS allowed from stopped (systemctl restart starts it).
    ------------------------------------------------------------------ */

    var BTN_MATRIX = {
        running:  { start: false, stop: true,  restart: true  },
        stopped:  { start: true,  stop: false, restart: true  },
        starting: { start: false, stop: false, restart: false },
        stopping: { start: false, stop: false, restart: false },
        error:    { start: true,  stop: true,  restart: true  },
    };

    /* ------------------------------------------------------------------
       Broker badge configuration (Live Operations, Option 1).
       Maps broker_state (from GET /api/status .live) to an existing badge
       class + label. Reuses the running/starting/error/stopped/neutral
       classes (no new CSS): live=green, starting=yellow, disconnected=red,
       not_running=grey, unknown=neutral.
    ------------------------------------------------------------------ */

    var BROKER_BADGE = {
        live:         { cls: 'badge--running',  text: 'Live',         pulse: true  },
        starting:     { cls: 'badge--starting', text: 'Starting',     pulse: true  },
        disconnected: { cls: 'badge--error',    text: 'Disconnected', pulse: false },
        not_running:  { cls: 'badge--stopped',  text: 'Not running',  pulse: false },
        unknown:      { cls: 'badge--neutral',  text: 'Unknown',      pulse: false },
    };

    /* ------------------------------------------------------------------
       Module state
    ------------------------------------------------------------------ */

    var actionInFlight = false;   // true while start/stop/restart is in flight
    var lastKnownStatus = null;   // last node_status string from successful poll

    /* ------------------------------------------------------------------
       Relative time helper
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
       Uptime formatter
    ------------------------------------------------------------------ */

    function formatUptime(seconds) {
        if (seconds == null || seconds === undefined) return '—';
        var s = Math.floor(seconds);
        if (s < 60)   return s + 's';
        if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
        var h = Math.floor(s / 3600);
        var m = Math.floor((s % 3600) / 60);
        return h + 'h ' + m + 'm';
    }

    /* ------------------------------------------------------------------
       DOM helpers
    ------------------------------------------------------------------ */

    function el(id) { return document.getElementById(id); }

    function setText(id, txt) {
        var e = el(id);
        if (e) e.textContent = txt;
    }

    /* ------------------------------------------------------------------
       Badge update
    ------------------------------------------------------------------ */

    function updateBadge(status) {
        var badge = el('status-badge');
        if (!badge) return;

        // Remove all known badge modifier classes.
        ['badge--running', 'badge--stopped', 'badge--starting',
         'badge--stopping', 'badge--error', 'badge--neutral',
         'badge--pulse'].forEach(function (c) { badge.classList.remove(c); });

        var cfg = BADGE[status];
        if (cfg) {
            badge.classList.add(cfg.cls);
            if (cfg.pulse) badge.classList.add('badge--pulse');
            badge.textContent = cfg.text;
        } else {
            // Unknown or connection error state.
            badge.classList.add('badge--neutral');
            badge.textContent = status || '—';
        }
    }

    /* ------------------------------------------------------------------
       Broker badge + live fields (Live Operations, Option 1)
    ------------------------------------------------------------------ */

    function updateBrokerBadge(state) {
        var badge = el('broker-badge');
        if (!badge) return;
        ['badge--running', 'badge--stopped', 'badge--starting',
         'badge--stopping', 'badge--error', 'badge--neutral',
         'badge--pulse'].forEach(function (c) { badge.classList.remove(c); });
        var cfg = BROKER_BADGE[state] || BROKER_BADGE.unknown;
        badge.classList.add(cfg.cls);
        if (cfg.pulse) badge.classList.add('badge--pulse');
        badge.textContent = cfg.text;
    }

    // idle_seconds: 0 (or null<=0) means clients are active; >0 is a duration.
    function formatIdle(seconds) {
        if (seconds == null) return '—';
        if (seconds <= 0) return 'Active';
        return formatUptime(seconds);
    }

    function renderLive(live) {
        live = live || {};
        updateBrokerBadge(live.broker_state);
        setText('status-connecting',
            (live.connecting_clients == null ? '—' : String(live.connecting_clients)));
        setText('status-idle', formatIdle(live.idle_seconds));
        // Append build_rev to the existing version line (no duplication).
        setText('status-build-rev', live.build_rev ? (' · ' + live.build_rev) : '');
    }

    /* ------------------------------------------------------------------
       Button state update
       Never re-enables buttons while an action is in flight.
    ------------------------------------------------------------------ */

    function updateButtons(status) {
        var matrix = BTN_MATRIX[status] || { start: false, stop: false, restart: false };

        ['start', 'stop', 'restart'].forEach(function (action) {
            var btn = el('btn-' + action);
            if (!btn) return;
            btn.disabled = actionInFlight ? true : !matrix[action];
        });
    }

    /* ------------------------------------------------------------------
       Full status render (successful poll response)
    ------------------------------------------------------------------ */

    function renderStatus(data) {
        lastKnownStatus = data.node_status;
        updateBadge(data.node_status);
        updateButtons(data.node_status);
        setText('status-last-changed', relativeTime(data.last_changed));
        setText('status-version',      data.conduit_version || '—');
        setText('status-uptime',       formatUptime(data.uptime_seconds));
        renderLive(data.live);
    }

    /* ------------------------------------------------------------------
       Connection error render (polling failure)
       Badge shows "Connection error"; all buttons disabled; fields reset.
       No toast is shown -- see module comment for rationale.
    ------------------------------------------------------------------ */

    function renderConnectionError() {
        lastKnownStatus = null;
        updateBadge('Connection error');   // falls through to badge--neutral
        updateButtons(null);              // all disabled
        setText('status-last-changed', '—');
        setText('status-uptime',       '—');
        updateBrokerBadge('unknown');     // broker badge -> Unknown on poll failure
        setText('status-connecting', '—');
        setText('status-idle',       '—');
        // Preserve version + build_rev if previously known (unchanged on net loss).
    }

    /* ------------------------------------------------------------------
       fetchStatusPoll
       Uses raw fetch() to avoid apiFetch's toast-on-error behaviour.
       Handles 401 (session expired) → redirect to login (same as apiFetch).
       All other non-2xx or network errors → renderConnectionError().
    ------------------------------------------------------------------ */

    function fetchStatusPoll() {
        fetch('/api/status', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        })
        .then(function (response) {
            if (response.status === 401) {
                // Session expired: redirect to login preserving current path.
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            if (!response.ok) {
                // Non-2xx (503, etc.): show inline connection error, no toast.
                renderConnectionError();
                return null;
            }
            return response.json();
        })
        .then(function (data) {
            if (data) renderStatus(data);
        })
        .catch(function () {
            // Network-level failure (offline, DNS, TLS): show inline, no toast.
            renderConnectionError();
        });
    }

    /* ------------------------------------------------------------------
       Action handlers
    ------------------------------------------------------------------ */

    /**
     * Run a control action (start / stop / restart).
     *
     * @param {string}      action      "start" | "stop" | "restart"
     * @param {string|null} confirmMsg  Shown in window.confirm(); null = no dialog.
     */
    function runAction(action, confirmMsg) {
        // Confirmation gate (Stop and Restart only).
        if (confirmMsg && !window.confirm(confirmMsg)) {
            return;
        }

        // Mark action in flight: disables all buttons immediately.
        actionInFlight = true;
        updateButtons(lastKnownStatus);

        // Show spinner on the clicked button.
        var btn = el('btn-' + action);
        if (btn) btn.classList.add('btn--loading');

        // POST to the Conduit control endpoint via apiFetch.
        // apiFetch toasts on 409/503 -- appropriate for user-initiated actions.
        apiFetch('/api/conduit/' + action, { method: 'POST' })
            .then(function () {
                // Action succeeded: fetch fresh status immediately.
                fetchStatusPoll();
            })
            .catch(function () {
                // apiFetch already showed a toast.
                // Still fetch status so the panel reflects any partial state change.
                fetchStatusPoll();
            })
            .finally(function () {
                // Clear in-flight state and spinner.
                // Buttons stay disabled until fetchStatusPoll() resolves
                // and calls updateButtons() with the authoritative new status.
                actionInFlight = false;
                if (btn) btn.classList.remove('btn--loading');
            });
    }

    /* ------------------------------------------------------------------
       Button event listeners
    ------------------------------------------------------------------ */

    onReady(function () {
        var btnStart   = el('btn-start');
        var btnStop    = el('btn-stop');
        var btnRestart = el('btn-restart');

        if (btnStart) {
            btnStart.addEventListener('click', function () {
                runAction('start', null);
            });
        }

        if (btnStop) {
            btnStop.addEventListener('click', function () {
                runAction('stop',
                    'Are you sure you want to stop Conduit? ' +
                    'Active connections will be dropped immediately.');
            });
        }

        if (btnRestart) {
            btnRestart.addEventListener('click', function () {
                runAction('restart',
                    'Are you sure you want to restart Conduit? ' +
                    'Active connections will be interrupted briefly.');
            });
        }

        /* ---------------------------------------------------------------
           Start polling: 5-second interval, visibility-aware (handled by
           app.js startPolling).  Push handle to window.CCC.pollers so the
           dashboard logout handler can stop it before calling the API.
        --------------------------------------------------------------- */
        var pollerHandle = startPolling(fetchStatusPoll, 5000);
        window.CCC.pollers.push(pollerHandle);
    });

})();
