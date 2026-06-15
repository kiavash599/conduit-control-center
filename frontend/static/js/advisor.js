/**
 * frontend/static/js/advisor.js
 * Contribution Advisor card (A1.4b).
 *
 * Binds the static card from A1.4a (#advisor-card) to GET /api/advisor:
 *   - status chip      (summary.status -> badge--* + label)
 *   - one-line headline (summary.headline)
 *   - recommendation list (items[], in backend order — already severity-sorted)
 *   - per-item severity styling + apply_hint in a native <details>/<summary>
 *
 * API
 * ---
 *   GET /api/advisor   polled every 60 s while the Dashboard section is visible.
 *   Response: { summary{status,headline,is_live,connected_clients,
 *                        lifetime_up,lifetime_down,recording_since},
 *               items[]{severity,domain,title,message,rationale,apply_hint?},
 *               generated_at }
 *   The endpoint sends Cache-Control: no-store and degrades to 200 with an
 *   "offline"/"unknown" summary when inputs are unavailable, so a non-2xx here
 *   is a real transport/auth failure, not "no data".
 *
 * Card states (mutually exclusive, toggled via [hidden]): loading / error / body.
 * Slim-when-healthy: when items is empty the list is hidden, leaving just the
 * status chip + headline.
 *
 * Polling (Dashboard-section-aware, approved A1.4 design)
 *   - 60 s tick; skips the fetch when the Dashboard section is hidden (the
 *     warm-up sample buffer is meant to advance only while the Dashboard is
 *     viewed). startPolling also pauses on a hidden browser tab.
 *   - Immediate fetch when the user navigates into the Dashboard (hashchange),
 *     so switching back from System/Settings doesn't sit on "Loading…".
 *
 * Error strategy: raw fetch() (not apiFetch) to stay toast-silent, matching
 * traffic_history.js. 401 -> /login?next=… redirect; other non-2xx / network ->
 * the error state, no toast.
 *
 * DOM only — textContent + createElement, never innerHTML (defence in depth;
 * messages are server-generated but rendered as inert text). No CSS changes.
 *
 * Script loading order:
 *   api.js -> app.js -> dashboard.js -> … -> traffic_history.js -> advisor.js
 * window.CCC.pollers must exist (initialised by dashboard.js).
 */

(function () {
    'use strict';

    /* ===================== DOM helpers ===================== */

    function el(id) { return document.getElementById(id); }

    // Mutually-exclusive card states (match the A1.4a markup IDs).
    var STATES = ['loading', 'error', 'body'];
    function showState(name) {
        STATES.forEach(function (s) {
            var e = el('advisor-' + s);
            if (e) e.hidden = (s !== name);
        });
    }

    /* ===================== status chip ===================== */

    // summary.status -> existing badge variant + short label.
    // Statuses emitted by the engine: live / disconnected / offline / unknown.
    var STATUS_BADGE = {
        live:         { cls: 'badge--success', label: 'Live' },
        disconnected: { cls: 'badge--warning', label: 'Disconnected' },
        offline:      { cls: 'badge--stopped', label: 'Offline' },
        unknown:      { cls: 'badge--unknown', label: 'Unknown' }
    };

    // Last resolved status key actually rendered to the chip. Used to suppress
    // no-op DOM writes: rewriting textContent every poll mutates the node even
    // when the value is unchanged, which makes the aria-live="polite" chip
    // re-announce the identical status (e.g. "Live") each cycle (R1). Guarding
    // here preserves the first paint and real transitions while keeping the
    // aria-live markup intact.
    var lastChipStatus = null;

    function setChip(status) {
        var chip = el('advisor-status-chip');
        if (!chip) return;
        var key = STATUS_BADGE[status] ? status : 'unknown';
        if (key === lastChipStatus) return;   // unchanged -> no mutation, no re-announce
        lastChipStatus = key;
        var map = STATUS_BADGE[key];
        chip.className = 'badge ' + map.cls;
        chip.textContent = map.label;
    }

    /* ===================== severity ===================== */

    // item.severity -> CSS modifier + label (colour + text, never colour alone).
    var SEVERITY = {
        warning:           { cls: 'advisor-item--warning',    label: 'Warning' },
        strong_suggestion: { cls: 'advisor-item--strong',     label: 'Recommended' },
        suggestion:        { cls: 'advisor-item--suggestion', label: 'Suggestion' },
        info:              { cls: 'advisor-item--info',        label: 'Info' }
    };

    /* ===================== item rendering ===================== */

    // Build one <li.advisor-item> via DOM nodes only (no innerHTML).
    function buildItem(item) {
        var sev = SEVERITY[item.severity] || SEVERITY.info;

        var li = document.createElement('li');
        li.className = 'advisor-item ' + sev.cls;

        var sevEl = document.createElement('span');
        sevEl.className = 'advisor-item__severity';
        sevEl.textContent = sev.label;
        li.appendChild(sevEl);

        var titleEl = document.createElement('p');
        titleEl.className = 'advisor-item__title';
        titleEl.textContent = item.title || '';
        li.appendChild(titleEl);

        if (item.message) {
            var msgEl = document.createElement('p');
            msgEl.className = 'advisor-item__message';
            msgEl.textContent = item.message;
            li.appendChild(msgEl);
        }

        if (item.rationale) {
            var ratEl = document.createElement('p');
            ratEl.className = 'advisor-item__rationale';
            ratEl.textContent = item.rationale;
            li.appendChild(ratEl);
        }

        // apply_hint (optional) -> collapsed <details>; native keyboard a11y.
        if (item.apply_hint) {
            var details = document.createElement('details');
            details.className = 'advisor-item__hint';
            var summary = document.createElement('summary');
            summary.textContent = 'How to apply';
            details.appendChild(summary);
            var hintEl = document.createElement('p');
            hintEl.textContent = item.apply_hint;
            details.appendChild(hintEl);
            li.appendChild(details);
        }

        return li;
    }

    function renderItems(items) {
        var list = el('advisor-items');
        if (!list) return;
        list.textContent = '';   // clear previous render (no innerHTML)

        if (!items || !items.length) {
            list.hidden = true;  // slim-when-healthy: just chip + headline
            return;
        }
        items.forEach(function (it) {   // backend order preserved (severity-sorted)
            list.appendChild(buildItem(it));
        });
        list.hidden = false;
    }

    /* ===================== render ===================== */

    function render(data) {
        var summary = data.summary || {};
        setChip(summary.status || 'unknown');

        var headline = el('advisor-headline');
        if (headline) headline.textContent = summary.headline || '—';

        renderItems(data.items);
        showState('body');
    }

    /* ===================== fetch / poll ===================== */

    function fetchAdvice() {
        return fetch('/api/advisor', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        })
        .then(function (response) {
            if (response.status === 401) {
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            if (!response.ok) { showState('error'); return null; }
            return response.json();
        })
        .then(function (data) {
            if (!data) return;
            render(data);
        })
        .catch(function () {
            showState('error');   // network/parse failure — silent (no toast)
        });
    }

    function dashboardVisible() {
        var dash = el('section-dashboard');
        return !(dash && dash.hidden);
    }

    // 60 s tick: only fetch while the Dashboard section is visible.
    function tick() {
        if (!dashboardVisible()) return;
        return fetchAdvice();
    }

    /* ===================== init ===================== */

    onReady(function () {
        if (!el('advisor-card')) return;

        // 60 s poll, gated on Dashboard visibility (tick). startPolling fires
        // once immediately. Registered for logout teardown.
        window.CCC.pollers.push(startPolling(tick, 60000));

        // Immediate refresh when navigating into the Dashboard, so returning
        // from another section doesn't wait up to 60 s on "Loading…".
        // dashboard.js's hashchange handler runs first (registered earlier in
        // load order), so section visibility is already updated here.
        window.addEventListener('hashchange', function () {
            if (dashboardVisible()) fetchAdvice();
        });
    });

})();
