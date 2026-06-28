/**
 * frontend/static/js/updates.js
 * Software Updates card (Feature 2) — one-click CCC update.
 *
 * API
 * ---
 *   GET  /api/update/check?force=<bool>
 *   POST /api/update/install   { version }
 *   GET  /api/update/status
 *
 * Behaviour
 * ---------
 *  - On load: read the cached check result and render; resume polling if an
 *    update is already in progress.
 *  - "Check Now": force a fresh check (?force=true).
 *  - "Install Update": if a Conduit-Core warning is present, require an inline
 *    "Continue Anyway / Cancel" confirmation first; then POST /install.
 *  - After 202, poll /status every 5s and TOLERATE the connection drop while
 *    CCC restarts (mirrors the restore reconnect pattern). Refresh versions on
 *    success.
 *  - GitHub unreachable: the backend returns reachable=false with cached/current
 *    data; we show an offline notice and never error the dashboard.
 *
 * Globals used (defined in api.js / app.js): rawFetch, startPolling,
 * stopPolling, onReady.
 */
(function () {
    'use strict';

    function el(id) { return document.getElementById(id); }
    function setText(id, val) {
        var e = el(id);
        if (e) e.textContent = (val === null || val === undefined || val === '') ? '—' : String(val);
    }
    function show(id, visible) { var e = el(id); if (e) e.hidden = !visible; }

    var _poller = null;

    /* ------------------------------------------------------------------
       Render the check result
    ------------------------------------------------------------------ */
    function renderNotes(list) {
        var ul = el('upd-notes');
        if (!ul) return;
        ul.textContent = '';                       // clear (no innerHTML)
        (list || []).forEach(function (line) {
            var li = document.createElement('li');
            li.textContent = line;                 // textContent — no HTML injection
            ul.appendChild(li);
        });
        show('upd-notes-wrap', !!(list && list.length));
    }

    function renderCheck(d) {
        if (!d) { setText('upd-status', "Couldn't check"); return; }
        setText('upd-current', d.current);
        if (d.reachable) {
            setText('upd-latest', d.latest || '—');
        } else {
            setText('upd-latest', d.latest ? (d.latest + ' (cached)') : 'unknown');
        }
        setText('upd-last-checked', d.last_checked || 'never');
        renderNotes(d.notes_preview);
        show('upd-offline', !d.reachable);

        if (d.core_warning) {
            setText('upd-core-current', d.installed_core);
            setText('upd-core-recommended', d.recommended_core);
            show('upd-core-warning', true);
        } else {
            show('upd-core-warning', false);
        }

        var installBtn = el('upd-install-btn');
        if (d.update_available && d.latest) {
            setText('upd-status', 'Update available (' + d.current + ' → ' + d.latest + ')');
            if (installBtn) { installBtn.hidden = false; installBtn.dataset.version = d.latest; installBtn.disabled = false; }
        } else {
            setText('upd-status', d.reachable ? 'Up to date' : "Couldn't check");
            if (installBtn) installBtn.hidden = true;
        }
    }

    function loadCheck(force) {
        setText('upd-status', 'Checking…');
        return rawFetch('/api/update/check' + (force ? '?force=true' : ''), { method: 'GET' })
            .then(function (r) { return (r && r.ok) ? r.json() : null; })
            .then(function (d) { if (d) renderCheck(d); else setText('upd-status', "Couldn't check"); })
            .catch(function () { setText('upd-status', "Couldn't check"); });
    }

    /* ------------------------------------------------------------------
       Status / reconnect
    ------------------------------------------------------------------ */
    function renderStatus(d) {
        if (!d || !d.state || d.state === 'idle') { show('upd-progress', false); return; }
        show('upd-progress', true);
        var msg = {
            in_progress: 'Installing update… the dashboard will restart and reconnect.',
            success: 'Updated to ' + (d.to_version || '') + '.',
            rolled_back: 'Update failed; rolled back to ' + (d.from_version || '') + '.',
            failed: 'Update failed. ' + (d.message || ''),
            unknown: (d.message || 'A previous update did not complete.')
        }[d.state] || (d.message || '');
        setText('upd-progress-msg', msg);
    }

    function readStatusOnce() {
        return rawFetch('/api/update/status', { method: 'GET' })
            .then(function (r) { return (r && r.ok) ? r.json() : null; }, function () { return null; })
            .catch(function () { return null; });
    }

    function pollStatus() {
        if (_poller) return;                        // single instance
        var started = Date.now();
        _poller = startPolling(function () {
            return readStatusOnce().then(function (d) {
                if (!d) return;                     // tolerate the restart window
                renderStatus(d);
                if (d.state && d.state !== 'in_progress') {
                    stopPolling(_poller); _poller = null;
                    if (d.state === 'success') {
                        var b = el('upd-install-btn'); if (b) b.hidden = true;
                        loadCheck(false);           // refresh versions after restart
                    } else {
                        var ib = el('upd-install-btn'); if (ib) ib.disabled = false;
                    }
                } else if (Date.now() - started > 600000) {   // ~10 min cap
                    stopPolling(_poller); _poller = null;
                }
            });
        }, 5000);
    }

    /* ------------------------------------------------------------------
       Install
    ------------------------------------------------------------------ */
    /* Render a backend error payload's `detail` as readable text.
       - string         -> the string itself
       - array (FastAPI 422 validation list) -> join the entries' `msg` fields,
                           falling back to the generic message if none are simple
       - object         -> its `msg` if it is a simple string, else generic
       - missing/other  -> "Could not start the update."
       Prevents FastAPI's structured 422 detail from rendering as "[object Object]". */
    function formatDetail(d) {
        var generic = 'Could not start the update.';
        if (!d) return generic;
        var detail = d.detail;
        if (typeof detail === 'string') return detail;
        if (Array.isArray(detail)) {
            var msgs = detail
                .map(function (e) { return (e && typeof e.msg === 'string') ? e.msg : null; })
                .filter(function (m) { return !!m; });
            return msgs.length ? msgs.join('; ') : generic;
        }
        if (detail && typeof detail === 'object' && typeof detail.msg === 'string') {
            return detail.msg;
        }
        return generic;
    }

    function doInstall(version) {
        if (!version) return;
        show('upd-confirm-modal', false);
        show('upd-progress', true);
        setText('upd-progress-msg', 'Starting update…');
        var btn = el('upd-install-btn'); if (btn) btn.disabled = true;

        rawFetch('/api/update/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version: version })
        })
            .then(function (r) {
                if (r && r.status === 202) { pollStatus(); return; }
                return (r ? r.json() : Promise.resolve(null))
                    .then(function (d) { setText('upd-progress-msg', formatDetail(d)); },
                          function () { setText('upd-progress-msg', 'Could not start the update.'); })
                    .then(function () { if (btn) btn.disabled = false; });
            })
            .catch(function () {
                setText('upd-progress-msg', 'Could not start the update.');
                if (btn) btn.disabled = false;
            });
    }

    function onInstallClick() {
        var btn = el('upd-install-btn');
        var version = btn && btn.dataset ? btn.dataset.version : null;
        if (!version) return;
        var warn = el('upd-core-warning');
        if (warn && !warn.hidden) {                 // Core mismatch → confirm first
            var modal = el('upd-confirm-modal');
            if (modal) { modal.dataset.version = version; modal.hidden = false; }
            return;
        }
        doInstall(version);
    }

    /* ------------------------------------------------------------------
       Wire up
    ------------------------------------------------------------------ */
    onReady(function () {
        if (!el('section-updates')) return;         // card not present

        var checkBtn = el('upd-check-btn');
        if (checkBtn) checkBtn.addEventListener('click', function () { loadCheck(true); });

        var installBtn = el('upd-install-btn');
        if (installBtn) installBtn.addEventListener('click', onInstallClick);

        var yes = el('upd-confirm-yes');
        if (yes) yes.addEventListener('click', function () {
            var modal = el('upd-confirm-modal');
            var v = modal && modal.dataset ? modal.dataset.version : null;
            doInstall(v);
        });
        var no = el('upd-confirm-no');
        if (no) no.addEventListener('click', function () { show('upd-confirm-modal', false); });

        loadCheck(false);                           // cached on load
        readStatusOnce().then(function (d) {
            if (d && d.state === 'in_progress') { renderStatus(d); pollStatus(); }
        });
    });
})();
