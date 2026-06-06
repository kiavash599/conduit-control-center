/**
 * frontend/static/js/api.js
 * Conduit Control Center — Issue #24
 *
 * Fetch wrapper, CSRF helper, and toast notification system.
 *
 * Script loading order (required — no module system in v0.1):
 *   1. api.js   (this file)
 *   2. app.js
 *   3. page-specific scripts
 *
 * Globals exposed: apiFetch, Toast
 */

'use strict';

/* ============================================================================
   Toast notification system
   ============================================================================
   Singleton container created on first use and appended to <body>.
   Identical messages are deduplicated: if a toast with the same text is
   already visible, a second call is silently ignored.
   Auto-removes after 5 seconds. Dismissible on click.
   ============================================================================ */

const Toast = (() => {
    /** @type {HTMLElement|null} */
    let container = null;

    function getContainer() {
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            // ARIA live region: screen readers announce new toasts.
            container.setAttribute('role', 'status');
            container.setAttribute('aria-live', 'polite');
            container.setAttribute('aria-atomic', 'false');
            document.body.appendChild(container);
        }
        return container;
    }

    /**
     * Display a toast notification.
     *
     * @param {string} message  - Text to display
     * @param {'error'|'info'|'success'|'warning'} [type='error']
     */
    function show(message, type = 'error') {
        const c = getContainer();

        // Deduplicate: skip if an identical message is already visible.
        const existing = c.querySelectorAll('.toast');
        for (const el of existing) {
            if (el.dataset.message === message) return;
        }

        const toast = document.createElement('div');
        toast.className = `toast toast--${type}`;
        toast.dataset.message = message;
        toast.textContent = message;
        toast.title = 'Click to dismiss';

        toast.addEventListener('click', () => toast.remove());

        c.appendChild(toast);

        // Auto-remove after 5 seconds.
        setTimeout(() => {
            if (toast.parentNode) toast.remove();
        }, 5000);
    }

    return { show };
})();


/* ============================================================================
   CSRF helper
   ============================================================================
   Reads the csrf_token cookie set by the backend on login (Issue #33).
   Future-ready: this code is a no-op until Issue #33 ships because the
   backend does not yet set the cookie or validate the X-CSRF-Token header.
   When Issue #33 is implemented, this file requires no changes — the header
   will appear automatically once the backend sets the cookie.
   ============================================================================ */

/**
 * Return the value of the csrf_token cookie, or '' if not present.
 * @returns {string}
 */
function getCsrfToken() {
    const match = document.cookie
        .split(';')
        .map(c => c.trim())
        .find(c => c.startsWith('csrf_token='));
    return match ? decodeURIComponent(match.split('=').slice(1).join('=')) : '';
}


/* ============================================================================
   apiFetch
   ============================================================================
   Wrapper around fetch() for all Conduit Control Center API calls.

   Behaviour
   ---------
   - Always sends Content-Type: application/json.
   - Sends X-CSRF-Token header when the csrf_token cookie is present
     (future-ready for Issue #33 — currently a no-op, see getCsrfToken).
   - 401 response:
       If current path is NOT /login -> redirect to /login?next=<current path>.
       If current path IS  /login  -> return response as-is (caller handles
       wrong-credentials 401); prevents redirect loops.
   - 403 response -> toast "Session error — please reload the page."
   - Network error (fetch throws) -> toast with error message, re-throws.
   - Non-2xx (excluding 401/403) -> toast with detail from JSON body if
     available, re-throws.
   - 2xx -> returns parsed JSON body.

   @param {string}      path    - API path, e.g. '/api/status'
   @param {RequestInit} [opts]  - fetch() options (method, body, headers, …)
   @returns {Promise<any>}      - Parsed JSON response on success
   @throws  on network error or non-2xx response
   ============================================================================ */

async function apiFetch(path, opts = {}) {
    const csrfToken = getCsrfToken();

    const headers = {
        'Content-Type': 'application/json',
        // X-CSRF-Token: sent only when cookie is present.
        // No-op before Issue #33; backend ignores unknown headers.
        ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
        ...(opts.headers || {}),
    };

    let response;
    try {
        response = await fetch(path, { ...opts, headers });
    } catch (err) {
        // Network-level failure (offline, DNS, TLS, etc.)
        Toast.show(`Network error: ${err.message}`);
        throw err;
    }

    // 401 — session expired or never established.
    if (response.status === 401) {
        if (window.location.pathname !== '/login') {
            // Redirect to login, preserving the intended destination.
            const next = encodeURIComponent(
                window.location.pathname + window.location.search
            );
            window.location.href = `/login?next=${next}`;
            // Return a never-resolving promise: the page is navigating away.
            return new Promise(() => {});
        }
        // On /login itself, return the response so the page can display
        // the "invalid credentials" error without triggering a redirect loop.
        return response;
    }

    // 403 — CSRF token mismatch (Issue #33) or other forbidden.
    if (response.status === 403) {
        Toast.show('Session error — please reload the page.');
        throw new Error('403 Forbidden');
    }

    // Non-2xx: extract detail from JSON body if present, then toast + throw.
    if (!response.ok) {
        let detail = `Server error (${response.status})`;
        try {
            const body = await response.clone().json();
            if (body && body.detail) detail = String(body.detail);
        } catch (_) { /* non-JSON body — use generic message */ }
        Toast.show(detail);
        throw new Error(detail);
    }

    // 2xx: parse and return JSON body.
    // Guard against empty bodies (e.g. 204 No Content).
    const contentType = response.headers.get('Content-Type') || '';
    if (contentType.includes('application/json')) {
        return response.json();
    }
    return null;
}


/* ============================================================================
   Globals (no module system in v0.1 — scripts loaded via <script> tags)
   api.js must be loaded before any script that calls apiFetch or Toast.
   ============================================================================ */

window.apiFetch = apiFetch;
window.Toast    = Toast;
