/**
 * frontend/static/js/ryve.js
 * Ryve claim / station identity (Epic #3, R3).
 *
 * KEY-GRADE: the claim QR reveals the station private identity. Generation is
 * gated behind an inline danger confirm-panel shown EVERY time; the QR is a
 * same-origin image element only; the claim is torn down (DELETE + cleared
 * image src) on close, on navigation, and best-effort on page hide. Nothing is
 * auto-restored on load. textContent only. 401 -> /login. Mirrors personal.js.
 */
(function () {
    'use strict';

    function el(id) { return document.getElementById(id); }

    function getCsrf() {
        var m = document.cookie.split('; ').find(function (c) {
            return c.indexOf('csrf_token=') === 0;
        });
        return m ? decodeURIComponent(m.split('=')[1]) : '';
    }

    function redirectLogin() {
        var next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = '/login?next=' + next;
    }

    var CLAIM_URL = '/api/conduit/ryve/claim';
    var IMAGE_BASE = '/api/conduit/ryve/claim/image/';

    var activeClaimId = null;

    /* ----- panels + status ----- */
    function showPanel(which) {
        if (el('ryve-idle')) el('ryve-idle').hidden = (which !== 'idle');
        if (el('ryve-warning')) el('ryve-warning').hidden = (which !== 'warning');
        if (el('ryve-display')) el('ryve-display').hidden = (which !== 'display');
    }
    function setStatus(msg, cls) {
        var e = el('ryve-status');
        if (!e) return;
        if (!msg) { e.hidden = true; e.textContent = ''; e.className = 'text-sm mt-4'; return; }
        e.className = 'text-sm mt-4 ' + (cls || '');
        e.textContent = msg;
        e.hidden = false;
    }
    function focusEl(id) {
        var e = el(id);
        if (e) { try { e.focus(); } catch (x) {} }
    }

    /* ----- teardown ----- */
    function clearImage() {
        var img = el('ryve-qr');
        if (img) img.removeAttribute('src');         // clear src; keep the node
        if (el('ryve-station')) el('ryve-station').textContent = '—';
        if (el('ryve-proxy')) el('ryve-proxy').textContent = '—';
    }
    function deleteClaim(id) {
        if (!id) return;
        // Best-effort discard; the UI clears regardless of the result.
        fetch(CLAIM_URL + '/' + encodeURIComponent(id), {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': getCsrf() },
            credentials: 'same-origin'
        }).catch(function () {});
    }
    function teardown() {
        var id = activeClaimId;
        activeClaimId = null;
        clearImage();
        deleteClaim(id);
    }

    /* ----- generate / display ----- */
    function onGenerate() {
        setStatus(null);
        if (activeClaimId) teardown();               // regenerate: discard the old first
        showPanel('warning');
        focusEl('ryve-warning-confirm');
    }
    function onCancel() {
        showPanel('idle');
        focusEl('ryve-generate-btn');
    }
    function onConfirm() {
        setStatus(null);
        var btn = el('ryve-warning-confirm');
        if (btn) btn.disabled = true;
        fetch(CLAIM_URL, {
            method: 'POST',
            headers: { 'Accept': 'application/json', 'X-CSRF-Token': getCsrf() },
            credentials: 'same-origin'
        }).then(function (r) {
            if (btn) btn.disabled = false;
            if (r.status === 401) { redirectLogin(); return null; }
            if (r.status === 403) {
                showPanel('idle');
                setStatus('Your session expired — reload and sign in again.', 'text-danger');
                return null;
            }
            if (r.status === 503) {
                showPanel('idle');
                setStatus('Ryve claim is unavailable on this server (helper not installed or not permitted).', 'text-danger');
                return null;
            }
            if (!r.ok) {
                showPanel('idle');
                setStatus('Unexpected error generating the claim.', 'text-danger');
                return null;
            }
            return r.json();
        }).then(function (d) {
            if (!d) return;
            activeClaimId = d.claim_id;
            if (el('ryve-station')) el('ryve-station').textContent = d.station_name || '—';
            if (el('ryve-proxy')) el('ryve-proxy').textContent = d.proxy_id || '—';
            var img = el('ryve-qr');
            if (img) img.setAttribute('src', IMAGE_BASE + encodeURIComponent(d.claim_id));
            showPanel('display');
            focusEl('ryve-close-btn');
        }).catch(function () {
            if (btn) btn.disabled = false;
            showPanel('idle');
            setStatus('Network error — check your connection and retry.', 'text-danger');
        });
    }
    function onClose() {
        teardown();
        showPanel('idle');
        setStatus(null);
        focusEl('ryve-generate-btn');
    }
    function onImgError() {
        // The image could not load: the claim expired or was evicted.
        teardown();
        showPanel('idle');
        setStatus('The claim expired — generate again.', 'text-warning');
    }

    /* ----- navigation / unload cleanup ----- */
    function onNavigate() {
        if (activeClaimId) { teardown(); showPanel('idle'); }
    }
    function onPageHide() {
        var id = activeClaimId;
        if (!id) return;
        activeClaimId = null;
        try {
            fetch(CLAIM_URL + '/' + encodeURIComponent(id), {
                method: 'DELETE',
                headers: { 'X-CSRF-Token': getCsrf() },
                credentials: 'same-origin',
                keepalive: true
            });
        } catch (x) {}
    }

    /* ----- Escape cancels the warning ----- */
    function onKeydown(ev) {
        if (ev.key === 'Escape' && el('ryve-warning') && !el('ryve-warning').hidden) {
            ev.preventDefault();
            onCancel();
        }
    }

    onReady(function () {
        if (!el('ryve-card')) return;
        if (el('ryve-generate-btn')) el('ryve-generate-btn').addEventListener('click', onGenerate);
        if (el('ryve-warning-confirm')) el('ryve-warning-confirm').addEventListener('click', onConfirm);
        if (el('ryve-warning-cancel')) el('ryve-warning-cancel').addEventListener('click', onCancel);
        if (el('ryve-close-btn')) el('ryve-close-btn').addEventListener('click', onClose);
        if (el('ryve-qr')) el('ryve-qr').addEventListener('error', onImgError);
        document.addEventListener('keydown', onKeydown);
        window.addEventListener('hashchange', onNavigate);
        window.addEventListener('pagehide', onPageHide);
    });
})();
