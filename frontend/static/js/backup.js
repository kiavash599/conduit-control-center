/**
 * frontend/static/js/backup.js
 * Backup & Restore (Epic #4) — S4A.2c: Create UI behaviour + download flow.
 *
 * Wires the (previously inert) Create Backup card in Settings:
 *   - validates the two passphrase fields client-side,
 *   - POSTs { passphrase } to /api/backup/create via the shared rawFetch()
 *     helper (CSRF + 401/403 handling live in api.js),
 *   - downloads the returned application/octet-stream as a file using a
 *     temporary object URL (which is revoked immediately after),
 *   - clears the passphrase fields and reports success/failure inline.
 *
 * API: POST /api/backup/create  (shipped in S4A.1) -> octet-stream attachment.
 *
 * DOM ids consumed (declared inert in dashboard.html by S4A.2b):
 *   backup-passphrase, backup-confirm-passphrase, backup-create-btn,
 *   backup-error, backup-success
 *
 * Security
 * --------
 *   - The passphrase travels in the JSON request body only — never a URL/query,
 *     never the filename, never logged, never stored (no localStorage /
 *     sessionStorage / document.cookie).
 *   - All user-facing messages use textContent (never innerHTML).
 *   - Both passphrase fields are cleared after success and after any
 *     API/network error so the secret does not linger in the DOM.
 *   - The object URL holding the encrypted bytes is revoked right after the
 *     download is triggered.
 *
 * Script loading order: api.js -> app.js -> [shell] -> ... -> backup.js.
 * Relies on globals: rawFetch (api.js), onReady (app.js).
 */

(function () {
    'use strict';

    var MIN_PASSPHRASE_LEN = 12;          // mirrors the server-side floor (S4A.1)
    var FALLBACK_FILENAME = 'ccc-backup.cccbak';

    /* ------------------------------------------------------------------
       DOM helpers
    ------------------------------------------------------------------ */

    function el(id) { return document.getElementById(id); }

    function showError(message) {
        var e = el('backup-error');
        if (e) {
            e.textContent = message;       // textContent — no HTML injection
            e.hidden = false;
        }
        var s = el('backup-success');
        if (s) { s.textContent = ''; s.hidden = true; }
    }

    function clearError() {
        var e = el('backup-error');
        if (!e) return;
        e.textContent = '';
        e.hidden = true;
    }

    function showSuccess(message) {
        clearError();
        var s = el('backup-success');
        if (!s) return;
        s.textContent = message;           // textContent — no HTML injection
        s.hidden = false;
    }

    function clearPassphrases() {
        var p = el('backup-passphrase');
        var c = el('backup-confirm-passphrase');
        if (p) p.value = '';
        if (c) c.value = '';
    }

    function setBusy(busy) {
        ['backup-passphrase', 'backup-confirm-passphrase', 'backup-create-btn']
            .forEach(function (id) {
                var e = el(id);
                if (e) e.disabled = busy;
            });
        var btn = el('backup-create-btn');
        if (btn) {
            if (busy) btn.classList.add('btn--loading');
            else btn.classList.remove('btn--loading');
        }
    }

    /* ------------------------------------------------------------------
       Validation — returns an error string, or '' on pass.
    ------------------------------------------------------------------ */

    function validate(passphrase, confirm) {
        if (!passphrase) return 'Backup passphrase is required.';
        if (passphrase.length < MIN_PASSPHRASE_LEN) {
            return 'Passphrase must be at least ' + MIN_PASSPHRASE_LEN + ' characters.';
        }
        if (!confirm) return 'Please confirm your passphrase.';
        if (passphrase !== confirm) return 'Passphrase and confirmation do not match.';
        return '';
    }

    /* ------------------------------------------------------------------
       Download helpers
    ------------------------------------------------------------------ */

    // Extract the filename from a Content-Disposition header; fall back if
    // absent/malformed. The server generates a timestamped, secret-free name.
    function filenameFromDisposition(headerValue) {
        if (!headerValue) return FALLBACK_FILENAME;
        var m = /filename="?([^"]+)"?/.exec(headerValue);
        return (m && m[1]) ? m[1] : FALLBACK_FILENAME;
    }

    function triggerDownload(blob, filename) {
        var url = URL.createObjectURL(blob);
        try {
            var a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);       // never leave the bytes referenced
        }
    }

    /* ------------------------------------------------------------------
       Create handler
    ------------------------------------------------------------------ */

    function onCreate() {
        clearError();

        var passEl = el('backup-passphrase');
        var confEl = el('backup-confirm-passphrase');
        var passphrase = (passEl || {}).value || '';
        var confirm    = (confEl || {}).value || '';

        // Client-side validation — no API call on failure; fields kept so the
        // operator can correct them.
        var validationError = validate(passphrase, confirm);
        if (validationError) {
            showError(validationError);
            return;
        }

        setBusy(true);

        rawFetch('/api/backup/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ passphrase: passphrase }),
        })
        .then(function (response) {
            if (!response.ok) {
                // Drain the body (the server detail is already generic) but do
                // not echo server internals to the user.
                return response.text().then(function () {
                    throw new Error('create-failed');
                });
            }
            var filename = filenameFromDisposition(
                response.headers.get('Content-Disposition'));
            return response.blob().then(function (blob) {
                triggerDownload(blob, filename);
                clearPassphrases();
                showSuccess(
                    'Backup downloaded. Keep your passphrase safe — it cannot be recovered.');
            });
        })
        .catch(function () {
            // rawFetch already handled 401 (redirect) and 403 (toast). For any
            // other failure show a generic, secret-free message and clear input.
            clearPassphrases();
            showError('Could not create the backup. Please try again.');
        })
        .then(function () {
            setBusy(false);                 // always re-enable the form
        });
    }

    /* ------------------------------------------------------------------
       Wire up
    ------------------------------------------------------------------ */

    onReady(function () {
        var btn = el('backup-create-btn');
        if (!btn) return;
        btn.disabled = false;               // progressive enhancement: enable now
        btn.addEventListener('click', onCreate);
    });

}());
