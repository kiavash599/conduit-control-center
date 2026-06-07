/**
 * frontend/static/js/login.js
 * Login form — JS enhancement layer (Issue #25)
 *
 * Extracted from inline <script> in login.html in Issue #44 to
 * enable strict CSP (script-src 'self') in Issue #32.
 *
 * Uses fetch() directly rather than apiFetch() because:
 *   1. This page is pre-session: apiFetch's 401->redirect logic would
 *      loop immediately (we ARE on /login).
 *   2. HTTP 429 requires reading the Retry-After header from the raw
 *      Response object; apiFetch discards the response when throwing
 *      on non-2xx status codes.
 * This is a documented, intentional exception to the apiFetch convention.
 *
 * No dependency on globals from api.js or app.js — all standard DOM APIs.
 */
(function () {
    'use strict';

    /* ------------------------------------------------------------------
       next redirect helper
       Five-rule validation mirroring _is_safe_next() in backend/pages.py
       and the backend validation from Issue #16.  Both sides MUST apply
       the same rules.
    ------------------------------------------------------------------ */

    function isSafeNext(url) {
        if (!url || typeof url !== 'string') return false;
        if (!url.startsWith('/'))  return false;  // must be relative
        if (url.startsWith('//')) return false;  // no protocol-relative
        if (url.includes('://')) return false;  // no URI scheme
        if (url.includes('@'))   return false;  // no user@host
        if (url.includes('\\')) return false;  // no backslash (Windows)
        return true;
    }

    function getRedirectTarget() {
        var params = new URLSearchParams(window.location.search);
        var next   = params.get('next') || '';
        return isSafeNext(next) ? next : '/dashboard';
    }

    /* ------------------------------------------------------------------
       429 lockout message
       Handles three cases: missing/malformed Retry-After, <120 seconds,
       and >=120 seconds.
    ------------------------------------------------------------------ */

    function formatLockoutMessage(retryAfterHeader) {
        var seconds = parseInt(retryAfterHeader, 10);
        if (!Number.isFinite(seconds) || isNaN(seconds) || seconds <= 0) {
            return 'Account locked. Please wait before trying again.';
        }
        if (seconds < 120) {
            return 'Account locked. Try again in ' + seconds + ' seconds.';
        }
        var minutes = Math.ceil(seconds / 60);
        return 'Account locked. Try again in ' + minutes + ' minutes.';
    }

    /* ------------------------------------------------------------------
       Error display
    ------------------------------------------------------------------ */

    function showError(msg) {
        var el = document.getElementById('login-error-js');
        el.textContent = msg;
        el.removeAttribute('hidden');
    }

    function clearError() {
        var el = document.getElementById('login-error-js');
        el.textContent = '';
        el.setAttribute('hidden', '');
    }

    /* ------------------------------------------------------------------
       Button loading state
    ------------------------------------------------------------------ */

    function setLoading(loading) {
        var btn = document.getElementById('login-btn');
        btn.disabled = loading;
        if (loading) {
            btn.classList.add('btn--loading');
        } else {
            btn.classList.remove('btn--loading');
        }
    }

    /* ------------------------------------------------------------------
       If the page was re-rendered by POST /login with a pre-filled
       username (error path), move focus to the password field so the
       user can immediately retype their password.
    ------------------------------------------------------------------ */

    (function focusIfUsernameFilled() {
        var u = document.getElementById('username');
        if (u && u.value) {
            var p = document.getElementById('password');
            if (p) { p.focus(); }
        }
    })();

    /* ------------------------------------------------------------------
       Form submit handler
    ------------------------------------------------------------------ */

    document.getElementById('login-form').addEventListener('submit', function (e) {
        e.preventDefault();
        clearError();

        var username = document.getElementById('username').value.trim();
        var password = document.getElementById('password').value;

        if (!username || !password) {
            showError('Username and password are required.');
            return;
        }

        setLoading(true);

        fetch('/api/auth/login', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ username: username, password: password }),
        })
        .then(function (response) {
            if (response.ok) {
                // Success: navigate to next or /dashboard.
                // Do not re-enable the button; page is navigating away.
                window.location.href = getRedirectTarget();
                return;
            }

            // All error paths: clear password, keep username, re-enable button.
            document.getElementById('password').value = '';
            setLoading(false);

            if (response.status === 429) {
                var retryAfter = response.headers.get('Retry-After');
                showError(formatLockoutMessage(retryAfter));
                return;
            }

            if (response.status === 401) {
                showError('Invalid credentials. Please try again.');
                return;
            }

            if (response.status === 503) {
                showError('Server configuration error. Contact your administrator.');
                return;
            }

            showError('Login failed (' + response.status + '). Please try again.');
        })
        .catch(function () {
            document.getElementById('password').value = '';
            setLoading(false);
            showError('Network error. Check your connection and try again.');
        });
    });

})();
