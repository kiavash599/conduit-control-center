/**
 * frontend/static/js/settings.js
 * Settings page — change password form (Issue #31)
 *
 * API endpoint
 * ------------
 *   PUT /api/settings/password
 *   Body: { current_password, new_password, confirm_password }
 *
 * Validation strategy
 * --------------------
 * Client-side (before API call):
 *   - All three fields non-empty
 *   - new_password length >= 10 characters
 *   - new_password === confirm_password
 *   Errors shown inline below the form. Submit button not reached on failure.
 *
 * Server-side (enforced independently; client is not trusted):
 *   - Same length and match checks via Pydantic model_validator
 *   - current_password bcrypt verification
 *   Returns HTTP 400 for wrong current password (not 401, which would
 *   trigger apiFetch's session-expiry redirect).
 *
 * apiFetch usage
 * ---------------
 * The PUT call uses apiFetch (user-initiated one-shot action).
 * apiFetch toasts non-2xx errors automatically — acceptable for a settings
 * form where errors are infrequent and user-actionable.
 * HTTP 422 (Pydantic mismatch) and HTTP 400 (wrong current password) both
 * produce a visible toast from apiFetch.
 *
 * Success flow (order matters)
 * ----------------------------
 *   1. Stop all active pollers — prevents mid-redirect 401 races from
 *      status.js / metrics.js / traffic.js / logs.js polls hitting the
 *      now-invalidated session and triggering apiFetch's redirect BEFORE
 *      our explicit redirect fires (double-redirect / flash race).
 *   2. Disable the submit button and form fields.
 *   3. Show the success message.
 *   4. After 2500 ms, redirect to /login.
 *
 * Script loading order:
 *   api.js → app.js → [shell] → status.js → metrics.js →
 *   traffic.js → logs.js → settings.js
 * window.CCC and window.CCC.pollers must exist.
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       DOM helper
    ------------------------------------------------------------------ */

    function el(id) { return document.getElementById(id); }

    /* ------------------------------------------------------------------
       showError / clearError
       Inline validation error below the form.
    ------------------------------------------------------------------ */

    function showError(message) {
        var errEl = el('settings-error');
        if (!errEl) return;
        errEl.textContent = message;   // textContent — no HTML injection
        errEl.hidden      = false;
    }

    function clearError() {
        var errEl = el('settings-error');
        if (!errEl) return;
        errEl.textContent = '';
        errEl.hidden      = true;
    }

    /* ------------------------------------------------------------------
       setFormDisabled
       Disables all form inputs and the submit button.
       Called after a successful change to prevent double-submit and
       to make the "locked" state clear while the redirect countdown runs.
    ------------------------------------------------------------------ */

    function setFormDisabled(disabled) {
        ['settings-current-password',
         'settings-new-password',
         'settings-confirm-password',
         'settings-submit-btn'].forEach(function (id) {
            var e = el(id);
            if (e) e.disabled = disabled;
        });
    }

    /* ------------------------------------------------------------------
       validate
       Client-side validation. Returns an error string or '' on pass.
    ------------------------------------------------------------------ */

    function validate(current, newPwd, confirm) {
        if (!current)             return 'Current password is required.';
        if (!newPwd)              return 'New password is required.';
        if (newPwd.length < 10)   return 'New password must be at least 10 characters.';
        if (!confirm)             return 'Please confirm your new password.';
        if (newPwd !== confirm)   return 'New password and confirmation do not match.';
        return '';
    }

    /* ------------------------------------------------------------------
       Form submit handler
    ------------------------------------------------------------------ */

    onReady(function () {
        var form      = el('settings-password-form');
        var submitBtn = el('settings-submit-btn');
        var successEl = el('settings-success');

        if (!form) return;

        form.addEventListener('submit', function (evt) {
            evt.preventDefault();
            clearError();

            var currentPwd = (el('settings-current-password') || {}).value || '';
            var newPwd     = (el('settings-new-password')     || {}).value || '';
            var confirmPwd = (el('settings-confirm-password') || {}).value || '';

            // ── Client-side validation ────────────────────────────────
            var validationError = validate(currentPwd, newPwd, confirmPwd);
            if (validationError) {
                showError(validationError);
                return;
            }

            // ── Disable submit during in-flight request ───────────────
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.classList.add('btn--loading');
            }

            // ── API call ──────────────────────────────────────────────
            apiFetch('/api/settings/password', {
                method: 'PUT',
                body: JSON.stringify({
                    current_password:  currentPwd,
                    new_password:      newPwd,
                    confirm_password:  confirmPwd,
                }),
            })
            .then(function (data) {
                // ── SUCCESS ───────────────────────────────────────────
                //
                // Order is critical:
                //   1. Stop all pollers FIRST to prevent concurrent polls
                //      from hitting the now-invalid session and triggering
                //      apiFetch's 401-redirect before our explicit redirect.
                //   2. Disable the form (visual feedback + prevent retry).
                //   3. Show success message.
                //   4. Redirect to /login after 2500 ms.

                // 1. Stop pollers
                (window.CCC.pollers || []).forEach(function (handle) {
                    stopPolling(handle);
                });
                window.CCC.pollers = [];

                // 2. Disable form
                setFormDisabled(true);

                // 3. Show success message
                if (successEl) {
                    var msg = (data && data.message)
                        ? data.message
                        : 'Password changed. Please log in again.';
                    successEl.textContent = msg;   // textContent — no HTML injection
                    successEl.hidden      = false;
                }

                // 4. Redirect to login
                setTimeout(function () {
                    window.location.href = '/login';
                }, 2500);
            })
            .catch(function () {
                // apiFetch already showed a toast for the specific error.
                // Re-enable the submit button so the user can correct and retry.
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.classList.remove('btn--loading');
                }
            });
        });
    });

})();
