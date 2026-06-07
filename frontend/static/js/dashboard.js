/**
 * frontend/static/js/dashboard.js
 * Dashboard shell — navigation and logout (Issue #26)
 *
 * Extracted from inline <script> in dashboard.html in Issue #44 to
 * enable strict CSP (script-src 'self') in Issue #32.
 *
 * Load order (enforced by dashboard.html {% block scripts %}):
 *   api.js → app.js → dashboard.js → status.js → … → ddns.js
 *
 * Depends on globals from api.js:  apiFetch
 * Depends on globals from app.js:  stopPolling
 *
 * Sections
 * --------
 * Hash-based navigation: #overview, #logs, #settings.
 * Default: #overview for empty or unrecognised hash.
 * The hashchange event updates active nav state, visible section,
 * and document.title without a page reload or API call.
 *
 * Polling registry
 * ----------------
 * window.CCC is a shared namespace for all dashboard JS.
 * window.CCC.pollers is an array where panel scripts (Issues #27–#30)
 * push their startPolling() handles.  Logout stops all registered pollers
 * before making the API call, preventing a 401 from a mid-logout poll from
 * triggering apiFetch's redirect before the explicit logout redirect fires.
 *
 * Logout
 * ------
 * Order: stop all pollers → disable button → apiFetch logout →
 *        redirect to /login (always, even on API error).
 */
(function () {
    'use strict';

    /* ------------------------------------------------------------------
       Shared namespace for dashboard JS.
       Panel scripts (Issues #27-#30) push polling handles here.
    ------------------------------------------------------------------ */
    window.CCC = window.CCC || {};
    window.CCC.pollers = window.CCC.pollers || [];

    /* ------------------------------------------------------------------
       Section metadata
    ------------------------------------------------------------------ */
    var SECTIONS = {
        overview: {
            id:       'section-overview',
            navId:    'nav-overview',
            title:    'Overview',
            subtitle: 'Node status and system health',
        },
        logs: {
            id:       'section-logs',
            navId:    'nav-logs',
            title:    'Logs',
            subtitle: 'Conduit service log (last 200 lines)',
        },
        settings: {
            id:       'section-settings',
            navId:    'nav-settings',
            title:    'Settings',
            subtitle: 'Account and application settings',
        },
    };

    var DEFAULT_SECTION = 'overview';

    /* ------------------------------------------------------------------
       Navigation: show section, update title, mark active nav item
    ------------------------------------------------------------------ */

    function getCurrentSection() {
        var hash = (window.location.hash || '').replace('#', '').toLowerCase();
        return SECTIONS[hash] ? hash : DEFAULT_SECTION;
    }

    function showSection(name) {
        var meta = SECTIONS[name] || SECTIONS[DEFAULT_SECTION];

        // Hide all sections, show the target.
        Object.keys(SECTIONS).forEach(function (key) {
            var el = document.getElementById(SECTIONS[key].id);
            if (el) { el.hidden = (key !== name); }
        });

        // Update nav active state.
        Object.keys(SECTIONS).forEach(function (key) {
            var navEl = document.getElementById(SECTIONS[key].navId);
            if (!navEl) { return; }
            var isActive = (key === name);
            navEl.classList.toggle('nav__item--active', isActive);
            navEl.setAttribute('aria-current', isActive ? 'page' : 'false');
        });

        // Update page header and document title.
        var titleEl    = document.getElementById('page-title');
        var subtitleEl = document.getElementById('page-subtitle');
        if (titleEl)    { titleEl.textContent    = meta.title; }
        if (subtitleEl) { subtitleEl.textContent = meta.subtitle; }
        document.title = meta.title + ' — Conduit Control Center';
    }

    // Initial render: apply section from hash (or default).
    showSection(getCurrentSection());

    // Update on hash change (back/forward, nav clicks).
    window.addEventListener('hashchange', function () {
        showSection(getCurrentSection());
    });

    /* ------------------------------------------------------------------
       Logout
    ------------------------------------------------------------------ */

    document.getElementById('logout-btn').addEventListener('click', function () {
        var btn = document.getElementById('logout-btn');

        // 1. Stop all active pollers first to prevent mid-logout 401 races.
        (window.CCC.pollers || []).forEach(function (handle) {
            stopPolling(handle);
        });
        window.CCC.pollers = [];

        // 2. Disable the button to prevent double-clicks.
        btn.disabled = true;
        btn.classList.add('btn--loading');

        // 3. Call the logout API.  Redirect to /login regardless of outcome.
        apiFetch('/api/auth/logout', { method: 'POST' })
            .catch(function () { /* ignore -- we always redirect */ })
            .finally(function () {
                window.location.href = '/login';
            });
    });

})();
