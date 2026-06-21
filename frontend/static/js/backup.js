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
    // Shared client-side upload ceiling for inspect AND restore (S4B-2.3a).
    // Aligned to the server-side cap raised in S4B-2.4 (10 MiB, under the 12m
    // nginx limit and the helper's 16 MiB). Replaces the stale 900 KB inspect cap.
    var MAX_UPLOAD_BYTES = 10 * 1024 * 1024;   // 10 MiB

    // Restore state machine (S4B-2.3b).
    var _inspectSeq = 0;        // monotonic token; bumped to invalidate a verdict
    var _verdictFor = null;     // file signature the current compatible verdict is bound to
    var _inProgressPoller = null;
    var _restoreScheduled = false;

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
        invalidateVerdict();                 // tear down any prior restore eligibility

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
        if (file.size > MAX_UPLOAD_BYTES) {
            inspectShowError('This file is too large to inspect here (over 10 MB).');
            return;
        }

        // Capture the verdict token + file signature at request time. The
        // restore zone is revealed on success only if neither changed during the
        // round trip (race guard) and the backup is strictly compatible.
        var inspectSeq = _inspectSeq;
        var inspectSig = _fileSignature(fileEl);

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
                // Reveal restore only if nothing changed during the round trip
                // (race guard) and the backup is strictly compatible.
                if (inspectSeq === _inspectSeq &&
                    data && data.compatibility && data.compatibility.compatible === true) {
                    _verdictFor = inspectSig;
                    revealRestoreZone();
                }
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

    /* ==================================================================
       Restore (S4B-2.3b) — gated, destructive. Revealed only after a
       compatible inspect of the current file; four gates; terminal after
       a 202. POST /api/backup/restore restarts the dashboard out of band.
    ================================================================== */

    function _fileSignature(fileEl) {
        var f = fileEl && fileEl.files && fileEl.files[0];
        return f ? (f.name + '|' + f.size + '|' + f.lastModified) : '';
    }

    function restoreShowError(message) {
        var e = el('backup-restore-error');
        if (e) { e.textContent = message; e.hidden = false; }   // textContent only
    }

    function restoreClearError() {
        var e = el('backup-restore-error');
        if (e) { e.textContent = ''; e.hidden = true; }
    }

    function clearRestorePassphrase() {
        var p = el('backup-restore-passphrase');
        if (p) p.value = '';
    }

    function hideRestoreZone() {
        var zone = el('backup-restore-zone');
        if (zone) zone.hidden = true;
        var ack = el('backup-restore-ack'); if (ack) ack.checked = false;
        clearRestorePassphrase();
        var tok = el('backup-restore-token'); if (tok) tok.value = '';
        restoreClearError();
        var btn = el('backup-restore-btn'); if (btn) btn.disabled = true;
    }

    function invalidateVerdict() {
        _inspectSeq += 1;                    // stale any in-flight inspect's token
        _verdictFor = null;
        hideRestoreZone();
    }

    function recomputeRestoreGates() {
        var fileEl = el('backup-inspect-file');
        var g1 = !!_verdictFor && _verdictFor === _fileSignature(fileEl);          // verdict fresh
        var g2 = !!(el('backup-restore-ack') && el('backup-restore-ack').checked); // consequences
        var g3 = !!((el('backup-restore-passphrase') || {}).value || '');          // passphrase
        var g4 = ((el('backup-restore-token') || {}).value || '') === 'RESTORE';    // typed token
        var btn = el('backup-restore-btn');
        if (btn) btn.disabled = !(g1 && g2 && g3 && g4);
    }

    function revealRestoreZone() {
        var zone = el('backup-restore-zone');
        if (!zone) return;
        zone.hidden = false;
        recomputeRestoreGates();             // starts disabled until gates pass
        if (typeof zone.focus === 'function') {
            zone.setAttribute('tabindex', '-1');
            zone.focus();                    // a11y: move focus into the danger zone
        }
    }

    function stopAllPollers() {
        if (window.CCC && window.CCC.pollers) {
            window.CCC.pollers.forEach(function (h) { stopPolling(h); });
            window.CCC.pollers = [];
        }
        if (_inProgressPoller) { stopPolling(_inProgressPoller); _inProgressPoller = null; }
    }

    function enterScheduledState() {
        _restoreScheduled = true;
        ['backup-create-btn', 'backup-passphrase', 'backup-confirm-passphrase',
         'backup-inspect-btn', 'backup-inspect-file', 'backup-inspect-passphrase',
         'backup-restore-btn', 'backup-restore-ack', 'backup-restore-passphrase',
         'backup-restore-token'].forEach(function (id) {
            var e = el(id); if (e) e.disabled = true;
        });
        clearPassphrases();                  // create-card fields
        clearRestorePassphrase();
        var ip = el('backup-inspect-passphrase'); if (ip) ip.value = '';
        var st = el('backup-restore-status');
        if (st) {
            st.textContent = 'Restore started — the dashboard is restarting and you will be '
                + 'signed out. Please sign in again in about 30–60 seconds.';
            st.hidden = false;
        }
        stopAllPollers();
        // No redirect: the session becomes invalid when the service restarts.
    }

    function onRestore() {
        restoreClearError();
        var fileEl = el('backup-inspect-file');
        var file = fileEl && fileEl.files && fileEl.files[0];
        var pp = (el('backup-restore-passphrase') || {}).value || '';
        var token = (el('backup-restore-token') || {}).value || '';
        var ack = !!(el('backup-restore-ack') && el('backup-restore-ack').checked);

        // Defence-in-depth: re-check verdict freshness + the four gates.
        if (!_verdictFor || _verdictFor !== _fileSignature(fileEl) || !file) {
            hideRestoreZone();
            restoreShowError('Inspect a compatible backup again before restoring.');
            return;
        }
        if (!ack || !pp || token !== 'RESTORE') {
            restoreShowError('Complete all confirmation steps before restoring.');
            return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
            restoreShowError('This file is too large to restore (over 10 MB).');
            return;
        }

        var btn = el('backup-restore-btn');
        if (btn) btn.disabled = true;        // synchronous: prevent double-submit

        var form = new FormData();
        form.append('file', file);
        form.append('passphrase', pp);
        form.append('confirm', 'RESTORE');

        // No Content-Type header: the browser sets the multipart boundary.
        rawFetch('/api/backup/restore', { method: 'POST', body: form })
        .then(function (response) {
            if (response.status === 202) {
                enterScheduledState();
                return;
            }
            // Non-202: drain JSON or non-JSON (nginx 413 HTML) and signal by code.
            return response.json().then(function () {
                throw new Error('http-' + response.status);
            }, function () {
                throw new Error('http-' + response.status);
            });
        })
        .catch(function (err) {
            if (_restoreScheduled) return;   // 202 already handled; controls disabled
            // rawFetch already handled 401 (redirect) and 403 (toast).
            var code = (err && err.message ? err.message : '').replace('http-', '');
            var msg;
            if (code === '409') msg = 'A restore is already in progress. Check the restore status banner.';
            else if (code === '400') msg = 'Wrong passphrase or invalid backup file.';
            else if (code === '413') msg = 'This file is too large to restore.';
            else if (code === '422') msg = 'Type RESTORE to confirm.';
            else if (code === '503') msg = 'Restore is not available on this server yet.';
            else msg = 'Could not start the restore. Please try again.';
            restoreShowError(msg);
        })
        .then(function () {
            if (_restoreScheduled) return;   // leave controls intentionally disabled
            clearRestorePassphrase();        // re-enter to retry
            recomputeRestoreGates();         // button re-disabled until gates pass
        });
    }

    /* ==================================================================
       Restore status banner (S4B-2.3b) — sourced from GET .../status.
    ================================================================== */

    function _restoreLabel(state) {
        return {
            in_progress: 'Restore in progress',
            restored: 'Restore complete',
            rolled_back: 'Restore failed — previous state restored',
            rollback_failed: 'Restore failed — manual recovery may be required'
        }[state] || 'Restore status';
    }

    function renderRestoreBanner(data) {
        var banner = el('restore-status-banner');
        var msg = el('restore-status-message');
        if (!banner || !msg) return;
        var badgeClass = {
            in_progress: 'badge--neutral',
            restored: 'badge--success',
            rolled_back: 'badge--warning',
            rollback_failed: 'badge--danger'
        }[data.state] || 'badge--neutral';

        while (msg.firstChild) msg.removeChild(msg.firstChild);   // reset via DOM only
        var badge = document.createElement('span');
        badge.className = 'badge ' + badgeClass;
        badge.textContent = _restoreLabel(data.state);
        msg.appendChild(badge);
        var detail = [];
        if (data.message) detail.push(data.message);
        if (data.restore_id) detail.push('(' + data.restore_id + ')');
        if (detail.length) {
            var span = document.createElement('span');
            span.className = 'text-sm text-dim';
            span.textContent = ' ' + detail.join(' ');
            msg.appendChild(span);
        }
        banner.setAttribute('role',
            (data.state === 'rolled_back' || data.state === 'rollback_failed') ? 'alert' : 'status');
        banner.hidden = false;
    }

    function _readStatusOnce() {
        return rawFetch('/api/backup/restore/status', { method: 'GET' })
            .then(function (r) {
                if (!r || !r.ok) return null;
                return r.json().then(function (d) { return d; }, function () { return null; });
            });
    }

    function pollInProgress() {
        if (_inProgressPoller) return;       // single instance
        var started = Date.now();
        _inProgressPoller = startPolling(function () {
            return _readStatusOnce().then(function (d) {
                if (!d) return;              // tolerate transient/non-JSON (restart window)
                if (d.state && d.state !== 'in_progress') {
                    renderRestoreBanner(d);
                    if (_inProgressPoller) { stopPolling(_inProgressPoller); _inProgressPoller = null; }
                } else if (Date.now() - started > 120000) {   // ~2 min cap
                    if (_inProgressPoller) { stopPolling(_inProgressPoller); _inProgressPoller = null; }
                }
            });
        }, 5000);
    }

    function loadRestoreStatus() {
        _readStatusOnce().then(function (d) {
            if (!d || !d.state || d.state === 'idle' || d.state === 'unknown') return;  // no banner
            renderRestoreBanner(d);
            if (d.state === 'in_progress') pollInProgress();
        });
    }

    function dismissBanner() {
        var banner = el('restore-status-banner');
        if (banner) banner.hidden = true;    // in-memory only; reappears on reload
        if (_inProgressPoller) { stopPolling(_inProgressPoller); _inProgressPoller = null; }
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

        // S4B-2.3b restore wiring (button stays disabled until gates pass).
        var fileEl = el('backup-inspect-file');
        if (fileEl) fileEl.addEventListener('change', invalidateVerdict);
        var inspPp = el('backup-inspect-passphrase');
        if (inspPp) inspPp.addEventListener('input', invalidateVerdict);
        ['backup-restore-ack', 'backup-restore-passphrase', 'backup-restore-token']
            .forEach(function (id) {
                var e = el(id);
                if (e) e.addEventListener(id === 'backup-restore-ack' ? 'change' : 'input',
                                          recomputeRestoreGates);
            });
        var restoreBtn = el('backup-restore-btn');
        if (restoreBtn) restoreBtn.addEventListener('click', onRestore);
        var dismiss = el('restore-status-banner-dismiss');
        if (dismiss) dismiss.addEventListener('click', dismissBanner);

        loadRestoreStatus();                 // one-shot on dashboard load
    });

}());
