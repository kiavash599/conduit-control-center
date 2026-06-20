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
 *     never the filename, never logged, and never written to browser storage or
 *     cookies.
 *   - All user-facing messages are written with the textContent API (never as
 *     raw HTML), so message text cannot inject markup.
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
    var MAX_INSPECT_BYTES = 900 * 1024;   // mirrors the server-side inspect cap (S4B-1a)

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

    /* ==================================================================
       Inspect / Preview (S4B-1b) — read-only; never restores or writes.
    ================================================================== */

    function inspectShowError(message) {
        var e = el('backup-inspect-error');
        if (e) {
            e.textContent = message;        // textContent — no markup injection
            e.hidden = false;
        }
    }

    function inspectClearError() {
        var e = el('backup-inspect-error');
        if (!e) return;
        e.textContent = '';
        e.hidden = true;
    }

    function inspectClearPassphrase() {
        var p = el('backup-inspect-passphrase');
        if (p) p.value = '';
    }

    function inspectSetBusy(busy) {
        ['backup-inspect-file', 'backup-inspect-passphrase', 'backup-inspect-btn']
            .forEach(function (id) {
                var e = el(id);
                if (e) e.disabled = busy;
            });
        var btn = el('backup-inspect-btn');
        if (btn) {
            if (busy) btn.classList.add('btn--loading');
            else btn.classList.remove('btn--loading');
        }
    }

    // Hide and empty the preview panel so a previous result never lingers.
    function inspectResetPreview() {
        var panel = el('backup-inspect-preview');
        if (!panel) return;
        panel.hidden = true;
        while (panel.firstChild) panel.removeChild(panel.firstChild);
    }

    function humanSize(bytes) {
        var n = Number(bytes);
        if (!isFinite(n) || n < 0) return '';
        if (n < 1024) return n + ' B';
        if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
        return (n / (1024 * 1024)).toFixed(1) + ' MB';
    }

    // Build a "key: value" row using only createElement + textContent.
    function metaRow(key, value) {
        var row = document.createElement('div');
        row.className = 'status-meta__row';
        var dt = document.createElement('dt');
        dt.className = 'status-meta__key';
        dt.textContent = key;
        var dd = document.createElement('dd');
        dd.className = 'status-meta__val';
        dd.textContent = (value === null || value === undefined) ? '—' : String(value);
        row.appendChild(dt);
        row.appendChild(dd);
        return row;
    }

    function renderPreview(data) {
        var panel = el('backup-inspect-preview');
        if (!panel) return;
        inspectResetPreview();

        var compat = data.compatibility || {};

        // Compatibility badge (reuses existing badge--* classes; no new CSS).
        var badge = document.createElement('span');
        badge.className = 'badge ' + (compat.compatible ? 'badge--success' : 'badge--warning');
        badge.textContent = compat.compatible ? 'Compatible' : 'Not compatible';
        var badgeWrap = document.createElement('p');
        badgeWrap.appendChild(badge);
        if (compat.message) {
            var msg = document.createElement('span');
            msg.className = 'text-sm text-dim';
            msg.textContent = ' ' + compat.message;
            badgeWrap.appendChild(msg);
        }
        panel.appendChild(badgeWrap);

        // Manifest metadata rows.
        var dl = document.createElement('dl');
        dl.className = 'status-meta status-meta--divided';
        dl.appendChild(metaRow('Created (UTC)', data.created_utc));
        dl.appendChild(metaRow('Backup app version', data.app_version));
        dl.appendChild(metaRow('This CCC version', compat.current_app_version));
        dl.appendChild(metaRow('Manifest version', data.manifest_version));
        dl.appendChild(metaRow('Kind', data.kind));
        panel.appendChild(dl);

        // Item list (name + human-readable size).
        var itemsHeading = document.createElement('h3');
        itemsHeading.className = 'text-sm';
        itemsHeading.textContent = 'Contents';
        panel.appendChild(itemsHeading);
        var ul = document.createElement('ul');
        (data.items || []).forEach(function (it) {
            var li = document.createElement('li');
            li.textContent = (it.name || '?') + ' (' + humanSize(it.size) + ')';
            ul.appendChild(li);
        });
        panel.appendChild(ul);

        // Excluded info — what is intentionally never included in a backup.
        var excluded = data.excluded || [];
        if (excluded.length) {
            var exInfo = document.createElement('p');
            exInfo.className = 'form-hint';
            exInfo.textContent =
                'Never included (kept on this device only): ' + excluded.join(', ') + '.';
            panel.appendChild(exInfo);
        }

        panel.hidden = false;
    }

    function onInspect() {
        inspectClearError();
        inspectResetPreview();               // no stale preview while we work

        var fileEl = el('backup-inspect-file');
        var passEl = el('backup-inspect-passphrase');
        var file = fileEl && fileEl.files && fileEl.files[0];
        var passphrase = (passEl || {}).value || '';

        // Client-side validation — no API call on failure.
        if (!file) {
            inspectShowError('Choose a backup file to inspect.');
            return;
        }
        if (!passphrase) {
            inspectShowError('Enter the passphrase for this backup.');
            return;
        }
        // Size pre-check mirrors the server cap; avoids a pointless upload + a
        // raw nginx 413 for oversize files.
        if (file.size > MAX_INSPECT_BYTES) {
            inspectShowError('This file is too large to inspect here (over 900 KB).');
            return;
        }

        var form = new FormData();
        form.append('file', file);
        form.append('passphrase', passphrase);

        inspectSetBusy(true);

        // No Content-Type header: the browser sets the multipart boundary.
        rawFetch('/api/backup/inspect', { method: 'POST', body: form })
        .then(function (response) {
            if (!response.ok) {
                // The body may be JSON ({detail}) or non-JSON (e.g. an nginx 413
                // HTML page). Try JSON; fall back to a generic message.
                return response.json().then(function (body) {
                    throw new Error((body && body.detail) ? body.detail : 'inspect-failed');
                }, function () {
                    throw new Error('inspect-failed');
                });
            }
            return response.json().then(function (data) {
                renderPreview(data);
                inspectClearPassphrase();    // success: do not retain the secret
            });
        })
        .catch(function (err) {
            // rawFetch already handled 401 (redirect) and 403 (toast). Show a
            // safe message (server details for inspect are already generic),
            // clear the passphrase, and leave no stale preview.
            inspectResetPreview();
            inspectClearPassphrase();
            var safe = (err && err.message && err.message !== 'inspect-failed')
                ? err.message
                : 'Could not inspect this backup. Check the file and passphrase, then try again.';
            inspectShowError(safe);
        })
        .then(function () {
            inspectSetBusy(false);
        });
    }

    /* ------------------------------------------------------------------
       Wire up
    ------------------------------------------------------------------ */

    onReady(function () {
        var createBtn = el('backup-create-btn');
        if (createBtn) {
            createBtn.disabled = false;     // progressive enhancement: enable now
            createBtn.addEventListener('click', onCreate);
        }
        var inspectBtn = el('backup-inspect-btn');
        if (inspectBtn) {
            inspectBtn.disabled = false;
            inspectBtn.addEventListener('click', onInspect);
        }
    });

}());
