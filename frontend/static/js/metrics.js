/**
 * frontend/static/js/metrics.js
 * System health panel — Issue #28
 *
 * API endpoint
 * ------------
 *   GET /api/metrics/system   polled every 10 seconds via startPolling()
 *
 * Response schema (nested — from backend/api/metrics.py, Issue #21)
 * -----------------------------------------------------------------
 *   data.cpu.usage_percent        float   0-100
 *   data.cpu.temperature_celsius  float|null   °C; null on non-Pi hardware
 *   data.ram.used_percent         float   0-100
 *   data.ram.used_bytes           int
 *   data.ram.total_bytes          int
 *   data.disk.used_percent        float   0-100
 *   data.disk.used_bytes          int
 *   data.disk.total_bytes         int
 *
 * Polling error strategy
 * ----------------------
 * Uses raw fetch() (not apiFetch) to avoid toast flooding at 10-second
 * intervals.  On any non-2xx or network error: renderUnavailable() sets
 * all values to "—" and all bars to 0% neutral — no toast shown.
 * Exception: HTTP 401 redirects to /login?next=<current path>, identical
 * to apiFetch behaviour.
 *
 * Temperature null handling
 * -------------------------
 * temperature_celsius === null is a data-level response from a successful
 * API call (hardware does not expose sensor data).  It is handled separately
 * from a connection error: the temp card shows "N/A" rather than "—", and
 * the progress bar is set to 0% with no threshold class.  Do not conflate
 * this with a transport failure.
 *
 * Script loading order: api.js → app.js → [inline shell] → status.js → metrics.js
 * window.CCC and window.CCC.pollers must exist (initialised by dashboard.html).
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       Threshold constants
       Values must match AppConfig defaults in backend/config.py (alerts section).
       CPU usage has no backend equivalent; frontend-only threshold used.
       All comparisons are >= (greater-than-or-equal).
       Temp thresholds are in °C; all others are percentages 0-100.
       v1.0: replace with GET /api/config/thresholds so operator overrides apply.
    ------------------------------------------------------------------ */

    var THRESH = {
        cpu:  { warn: 70, crit: 85 },   // frontend-only; no AppConfig equivalent
        ram:  { warn: 80, crit: 90 },   // matches ram_warning_percent / ram_critical_percent
        disk: { warn: 75, crit: 85 },   // matches disk_warning_percent / disk_critical_percent
        temp: { warn: 70, crit: 80 },   // matches cpu_temp_warning_celsius / cpu_temp_critical_celsius
    };

    /* ------------------------------------------------------------------
       DOM element IDs
       Must match the id= attributes in dashboard.html exactly.
    ------------------------------------------------------------------ */

    var ID = {
        cpuValue:   'metric-cpu-value',
        cpuBar:     'metric-cpu-bar',
        ramValue:   'metric-ram-value',
        ramDetail:  'metric-ram-detail',
        ramBar:     'metric-ram-bar',
        diskValue:  'metric-disk-value',
        diskDetail: 'metric-disk-detail',
        diskBar:    'metric-disk-bar',
        tempValue:  'metric-temp-value',
        tempBar:    'metric-temp-bar',
    };

    /* ------------------------------------------------------------------
       DOM helpers
    ------------------------------------------------------------------ */

    function el(id) { return document.getElementById(id); }

    function setText(id, text) {
        var e = el(id);
        if (e) e.textContent = text;
    }

    /* ------------------------------------------------------------------
       formatBytes
       Converts a raw byte count to a human-readable string.
         < 1 GiB  →  "NNN MB"   (integer)
         >= 1 GiB →  "N.N GB"   (one decimal)
       Returns "—" for null/undefined input.
    ------------------------------------------------------------------ */

    function formatBytes(bytes) {
        if (bytes == null) return '—';
        var gib = bytes / (1024 * 1024 * 1024);
        if (gib >= 1) return gib.toFixed(1) + ' GB';
        var mib = bytes / (1024 * 1024);
        return Math.round(mib) + ' MB';
    }

    /* ------------------------------------------------------------------
       barModClass
       Returns the CSS modifier class for a .progress-bar__fill element
       given a numeric value and a threshold object {warn, crit}.
       Returns:
         'progress-bar__fill--danger'   if value >= crit
         'progress-bar__fill--warning'  if value >= warn
         ''                             otherwise (green — base class only)
       Returns '' for null value (caller handles null separately).
    ------------------------------------------------------------------ */

    function barModClass(value, thresholds) {
        if (value == null) return '';
        if (value >= thresholds.crit) return 'progress-bar__fill--danger';
        if (value >= thresholds.warn) return 'progress-bar__fill--warning';
        return '';
    }

    /* ------------------------------------------------------------------
       updateBar
       Sets the width and colour modifier on a .progress-bar__fill element.
       @param {string}      id        Element ID
       @param {number|null} value     Raw value (percent 0-100 or °C); null = hide
       @param {string}      modClass  CSS modifier from barModClass()
    ------------------------------------------------------------------ */

    function updateBar(id, value, modClass) {
        var bar = el(id);
        if (!bar) return;

        // Always clear both modifier classes before re-applying.
        bar.classList.remove(
            'progress-bar__fill--warning',
            'progress-bar__fill--danger'
        );

        if (value == null) {
            // No data: zero-width bar (track background shows, looks neutral).
            bar.style.width = '0%';
        } else {
            // Clamp to [0, 100] — temperature values > 100 render at 100%.
            bar.style.width = Math.min(100, Math.max(0, value)) + '%';
            if (modClass) bar.classList.add(modClass);
        }
    }

    /* ------------------------------------------------------------------
       renderMetrics
       Called on a successful poll response.
       Renders all four metric cards from the nested API schema.
    ------------------------------------------------------------------ */

    function renderMetrics(data) {
        var cpu  = data.cpu;
        var ram  = data.ram;
        var disk = data.disk;

        // ── CPU ──────────────────────────────────────────────────────
        var cpuPct = (cpu && cpu.usage_percent != null) ? cpu.usage_percent : null;
        setText(ID.cpuValue, cpuPct != null ? cpuPct.toFixed(1) + '%' : '—');
        updateBar(ID.cpuBar, cpuPct, barModClass(cpuPct, THRESH.cpu));

        // ── RAM ──────────────────────────────────────────────────────
        var ramPct   = (ram && ram.used_percent  != null) ? ram.used_percent  : null;
        var ramUsed  = (ram && ram.used_bytes    != null) ? ram.used_bytes    : null;
        var ramTotal = (ram && ram.total_bytes   != null) ? ram.total_bytes   : null;
        setText(ID.ramValue, ramPct != null ? ramPct.toFixed(1) + '%' : '—');
        setText(ID.ramDetail,
            (ramUsed != null && ramTotal != null)
                ? formatBytes(ramUsed) + ' / ' + formatBytes(ramTotal)
                : ''
        );
        updateBar(ID.ramBar, ramPct, barModClass(ramPct, THRESH.ram));

        // ── Disk ─────────────────────────────────────────────────────
        var diskPct   = (disk && disk.used_percent != null) ? disk.used_percent : null;
        var diskUsed  = (disk && disk.used_bytes   != null) ? disk.used_bytes   : null;
        var diskTotal = (disk && disk.total_bytes  != null) ? disk.total_bytes  : null;
        setText(ID.diskValue, diskPct != null ? diskPct.toFixed(1) + '%' : '—');
        setText(ID.diskDetail,
            (diskUsed != null && diskTotal != null)
                ? formatBytes(diskUsed) + ' / ' + formatBytes(diskTotal)
                : ''
        );
        updateBar(ID.diskBar, diskPct, barModClass(diskPct, THRESH.disk));

        // ── Temperature ──────────────────────────────────────────────
        // temperature_celsius may be null on a successful API response
        // (hardware does not expose sensor data).  This is NOT a connection
        // error — show "N/A" rather than "—", and suppress the bar.
        var temp = (cpu && cpu.temperature_celsius !== undefined)
            ? cpu.temperature_celsius
            : null;

        if (temp != null) {
            setText(ID.tempValue, temp.toFixed(1) + ' °C');  // °C
            updateBar(ID.tempBar, temp, barModClass(temp, THRESH.temp));
        } else {
            setText(ID.tempValue, 'N/A');
            updateBar(ID.tempBar, null, '');
        }
    }

    /* ------------------------------------------------------------------
       renderUnavailable
       Called on poll failure (non-2xx, network error).
       Sets all values to "—" and zeros all bars.
       Does NOT show a toast — repeated poll failures must not flood the UI.
    ------------------------------------------------------------------ */

    function renderUnavailable() {
        setText(ID.cpuValue,  '—');
        setText(ID.ramValue,  '—');
        setText(ID.ramDetail, '');
        setText(ID.diskValue, '—');
        setText(ID.diskDetail,'');
        setText(ID.tempValue, '—');

        updateBar(ID.cpuBar,  null, '');
        updateBar(ID.ramBar,  null, '');
        updateBar(ID.diskBar, null, '');
        updateBar(ID.tempBar, null, '');
    }

    /* ------------------------------------------------------------------
       fetchMetricsPoll
       Uses raw fetch() to bypass apiFetch's toast-on-error behaviour.
       401 → redirect to /login (session expired).
       All other non-2xx or network errors → renderUnavailable().
    ------------------------------------------------------------------ */

    function fetchMetricsPoll() {
        fetch('/api/metrics/system', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        })
        .then(function (response) {
            if (response.status === 401) {
                // Session expired: redirect to login preserving current path.
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            if (!response.ok) {
                renderUnavailable();
                return null;
            }
            return response.json();
        })
        .then(function (data) {
            if (data) renderMetrics(data);
        })
        .catch(function () {
            // Network-level failure (offline, DNS, TLS): show inline, no toast.
            renderUnavailable();
        });
    }

    /* ------------------------------------------------------------------
       Initialise
       Runs after DOM is ready.  Starts 10-second polling and registers
       the handle in window.CCC.pollers so the logout handler can stop it.
    ------------------------------------------------------------------ */

    onReady(function () {
        var handle = startPolling(fetchMetricsPoll, 10000);
        window.CCC.pollers.push(handle);
    });

})();
