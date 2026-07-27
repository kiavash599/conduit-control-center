/**
 * frontend/static/js/traffic_history.js
 * Lifetime & History traffic card.
 *   TC-1: summary binding + four card states.
 *   TC-2a: range selector + /series integration + race guard + chart sub-states
 *          + sr-only data table + range-aware polling. (No SVG yet — TC-2b.)
 *
 * API endpoints
 * -------------
 *   GET /api/traffic/summary                      polled every 60 s (card state)
 *   GET /api/traffic/series?range=24h|7d|30d      chart data (range-aware refresh)
 *
 * Summary schema (backend/api/traffic.py, CI77)
 *   status, enabled(bool), recording_since|null, last_ok_ts_utc|null,
 *   lifetime{bytes_up,bytes_down}|null, windows{last_24h,last_7d}
 *   enabled=false -> recording is off (empty state shows a turn-on CTA button).
 * Series schema
 *   range, granularity("hour"|"day"), buckets:[{bucket_utc,bytes_up,bytes_down}]
 *   Dense + zero-filled. Invalid range -> 422. Auth required -> 401.
 *
 * Card states (TC-1): loading / populated(body) / not-recording(empty) / error.
 * Chart sub-states (TC-2a, inside the body): loading / data / empty(no-history) /
 *   error. Summary and series are independent fetches, so a series failure shows
 *   the chart error sub-state WITHOUT blanking the lifetime totals.
 *
 * Range-aware polling (approved TC-2 refinement)
 *   - fetch on load + on range change (all ranges)
 *   - 24h only: visible-only 60 s refresh (matches collector tick + summary)
 *   - 7d / 30d: no timed refresh (daily buckets — load + range change only)
 *   Refresh is gated on the Dashboard section being visible; startPolling also
 *   pauses on a hidden tab.
 *
 * Request race guard: each /series fetch carries a monotonically increasing
 * token; only the latest response renders (prevents fast range-switch flicker).
 *
 * Polling error strategy: raw fetch() (not apiFetch) to stay toast-silent,
 * matching traffic.js. 401 -> /login redirect; other non-2xx / network -> the
 * relevant error state, no toast. /summary returns 200 even when disabled, so
 * "not recording" is read from recording_since, not from an error.
 *
 * formatBytes / relativeTime duplicated from traffic.js (no module system).
 *
 * Script loading order:
 *   api.js -> app.js -> dashboard.js -> ... -> traffic.js -> traffic_history.js
 * window.CCC.pollers must exist (initialised by dashboard.js).
 */

(function () {
    'use strict';

    /* ===================== formatting helpers ===================== */

    function formatBytes(bytes) {
        if (bytes == null) return '—';
        var gb = bytes / (1024 * 1024 * 1024);
        if (gb >= 1) return gb.toFixed(1) + ' GB';
        var mb = bytes / (1024 * 1024);
        if (mb >= 1) return mb.toFixed(1) + ' MB';
        var kb = bytes / 1024;
        if (kb >= 1) return Math.round(kb) + ' KB';
        return (bytes || 0) + ' B';
    }

    function relativeTime(isoStr) {
        if (!isoStr) return '—';
        var dt = new Date(isoStr);
        if (isNaN(dt.getTime())) return '—';
        var delta = Math.floor((Date.now() - dt.getTime()) / 1000);
        if (delta < 0)     delta = 0;
        if (delta < 10)    return 'just now';
        if (delta < 60)    return delta + ' seconds ago';
        if (delta < 3600)  return Math.floor(delta / 60) + ' minutes ago';
        if (delta < 86400) return Math.floor(delta / 3600) + ' hours ago';
        return Math.floor(delta / 86400) + ' days ago';
    }

    // ISO 8601 -> "YYYY-MM-DD HH:MM <Zone>" in the effective display timezone
    // (v0.3.22). Storage/transport remain UTC; this is rendering only.
    function formatUtc(isoStr) {
        if (!isoStr) return '—';
        var s = formatTime(isoStr);
        return s === '—' ? s : s + ' ' + displayZoneLabel();
    }

    // A bucket_utc is "YYYY-MM-DD" (daily) or "YYYY-MM-DDTHH:MM:SSZ" (hourly).
    function parseBucketStart(bucketUtc) {
        if (!bucketUtc) return new Date(NaN);
        return new Date(bucketUtc.length <= 10 ? (bucketUtc + 'T00:00:00Z') : bucketUtc);
    }

    // Daily bucket keys are already LOCAL calendar dates when a display
    // timezone is set (the backend re-aggregates them), so they are shown
    // as-is with the zone named. Hourly buckets are instants -> converted.
    function bucketLabel(bucketUtc, granularity) {
        if (granularity === 'day') return bucketUtc + ' (' + displayZoneLabel() + ')';
        return formatUtc(bucketUtc);
    }

    // Compact axis label: daily -> "MM-DD"; hourly -> "HH:MM" in the zone.
    function shortLabel(bucketUtc, granularity) {
        if (granularity === 'day') return bucketUtc.slice(5);
        return formatHourLabel(bucketUtc);
    }

    /* ===================== SVG chart helpers (TC-2b) ===================== */

    // Fixed chart height (px) — stable across viewports (UX review TC-2c).
    var CHART_H = 200;

    var SVG_NS = 'http://www.w3.org/2000/svg';
    // Build an SVG element with attributes set via setAttribute (presentation
    // attributes only — no inline style, so CSP style-src 'self' is satisfied).
    function svgEl(name, attrs) {
        var e = document.createElementNS(SVG_NS, name);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
        }
        return e;
    }
    // Round a positive value up to a "nice" 1/2/5 x 10^n bound for the Y axis.
    function niceCeil(v) {
        if (v <= 0) return 1;
        var p = Math.pow(10, Math.floor(Math.log10(v)));
        var f = v / p;
        var nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
        return nf * p;
    }

    /* ===================== DOM helpers ===================== */

    function el(id) { return document.getElementById(id); }
    function setText(id, text) { var e = el(id); if (e) e.textContent = text; }

    // Card-level states (TC-1).
    var CARD_STATES = ['loading', 'error', 'empty', 'body'];
    function showCardState(name) {
        CARD_STATES.forEach(function (s) {
            var e = el('traffic-history-' + s);
            if (e) e.hidden = (s !== name);
        });
    }

    // Chart sub-states (TC-2a), inside the body.
    var CHART_STATES = ['loading', 'error', 'empty', 'data'];
    function showChartState(name) {
        CHART_STATES.forEach(function (s) {
            var e = el('th-chart-' + s);
            if (e) e.hidden = (s !== name);
        });
    }

    function pair(w) {
        if (!w) return '—';
        return formatBytes(w.bytes_up) + ' sent · ' + formatBytes(w.bytes_down) + ' received';
    }

    /* ===================== module state ===================== */

    var currentRange      = '24h';   // active range (default 24h)
    var isRecording       = false;   // mirrors summary: body (recording) visible
    var lastRecordingSince = null;   // ISO from summary; used for the partial note
    var seriesReqSeq      = 0;       // monotonic token for the race guard
    var seriesLoadedRange = null;    // range whose series is currently rendered
    var lastSeries        = null;    // {buckets, granularity} cached for resize re-render

    /* ===================== summary (TC-1) ===================== */

    function renderPopulated(d) {
        var lt  = d.lifetime || { bytes_up: 0, bytes_down: 0 };
        var win = d.windows || {};
        setText('th-lifetime-up',   formatBytes(lt.bytes_up));
        setText('th-lifetime-down', formatBytes(lt.bytes_down));
        setText('th-24h',     pair(win.last_24h));
        setText('th-7d',      pair(win.last_7d));
        setText('th-since',   formatUtc(d.recording_since));
        setText('th-updated', relativeTime(d.last_ok_ts_utc));

        isRecording        = true;
        lastRecordingSince = d.recording_since || null;
        showCardState('body');
        ensureSeriesLoaded();
    }

    function setNotRecording(cardState) {
        isRecording        = false;
        seriesLoadedRange  = null;
        showCardState(cardState);
    }

    // Empty-state messaging depends on WHY there is no data:
    //   enabled=false -> recording is off (show the turn-on CTA button)
    //   enabled=true  -> recording on, first sample not yet persisted (waiting)
    function updateEmptyState(enabled) {
        var title = el('th-empty-title');
        var hint  = el('th-empty-hint');
        var btn   = el('th-enable-recording');
        if (enabled) {
            if (title) title.textContent = 'Waiting for first sample';
            if (hint) hint.textContent =
                'Recording is on. Lifetime & history appear within about a minute.';
            if (btn) btn.hidden = true;
        } else {
            if (title) title.textContent = 'Traffic recording is off';
            if (hint) hint.textContent =
                'Turn it on to record lifetime & history — aggregate totals only, ' +
                'no per-client data.';
            if (btn) btn.hidden = false;
        }
    }

    function fetchSummaryPoll() {
        return fetch('/api/traffic/summary', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        })
        .then(function (response) {
            if (response.status === 401) {
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            if (!response.ok) { setNotRecording('error'); return null; }
            return response.json();
        })
        .then(function (data) {
            if (!data) return;
            if (data.recording_since == null || data.lifetime == null) {
                updateEmptyState(!!data.enabled);   // off -> CTA; on -> waiting
                setNotRecording('empty');
            } else {
                renderPopulated(data);
            }
        })
        .catch(function () {
            setNotRecording('error');
        });
    }

    /* ===================== series chart (TC-2a) ===================== */

    function renderTable(buckets, granularity) {
        var table = el('th-chart-table');
        var tbody = table ? table.querySelector('tbody') : null;
        if (!tbody) return;
        tbody.textContent = '';   // clear (textContent only — no innerHTML)
        buckets.forEach(function (b) {
            var tr = document.createElement('tr');
            var tdP = document.createElement('td');
            tdP.textContent = bucketLabel(b.bucket_utc, granularity);
            var tdU = document.createElement('td');
            tdU.textContent = formatBytes(b.bytes_up);
            var tdD = document.createElement('td');
            tdD.textContent = formatBytes(b.bytes_down);
            tr.appendChild(tdP);
            tr.appendChild(tdU);
            tr.appendChild(tdD);
            tbody.appendChild(tr);
        });
    }

    // Hand-built SVG grouped bar chart. Sent/Received per bucket on a shared Y
    // scale. Rendered in real pixels (measured container width + fixed height)
    // so text stays legible at any width and the height is stable (TC-2c).
    // Styling is by CSS class only. The legend lives in HTML (dashboard.html).
    function renderChart(buckets, granularity) {
        var plot = el('th-chart-plot');
        if (!plot) return;
        var W = plot.clientWidth;
        if (!W) return;            // not laid out (hidden) — re-rendered when shown/resized
        var H = CHART_H;

        plot.textContent = '';     // clear any previous render

        var PAD_L = 50, PAD_R = 10, PAD_T = 8, PAD_B = 20;
        var x0 = PAD_L, x1 = W - PAD_R;
        var y0 = PAD_T, y1 = H - PAD_B;
        var plotH = y1 - y0, plotW = x1 - x0;
        if (plotW <= 0) return;    // container too narrow to draw

        var n = buckets.length;
        var maxVal = 0;
        buckets.forEach(function (b) {
            maxVal = Math.max(maxVal, b.bytes_up || 0, b.bytes_down || 0);
        });
        var top = niceCeil(maxVal);

        // No viewBox: 1 user unit = 1 px, so the CSS px font-size renders at its
        // true size regardless of card width (fixes mobile label legibility).
        var svg = svgEl('svg', {
            width: W, height: H,
            'class': 'th-chart__svg',
            focusable: 'false',
            'aria-hidden': 'true',
        });

        // Y gridlines + byte labels (0 / mid / top).
        [0, top / 2, top].forEach(function (val) {
            var y = y1 - (val / top) * plotH;
            svg.appendChild(svgEl('line', {
                'class': 'chart-gridline', x1: x0, y1: y, x2: x1, y2: y,
            }));
            var lbl = svgEl('text', {
                'class': 'chart-label', x: x0 - 6, y: y + 4, 'text-anchor': 'end',
            });
            lbl.textContent = formatBytes(val);
            svg.appendChild(lbl);
        });

        // Baseline axis.
        svg.appendChild(svgEl('line', {
            'class': 'chart-axis', x1: x0, y1: y1, x2: x1, y2: y1,
        }));

        // Smooth gradient-filled area curves, one per series, sharing the Y
        // scale. Received is drawn first (background); Sent is overlaid. Colour
        // comes from CSS classes only (fill references a gradient by id — a
        // presentation attribute, CSP-safe). The .sr-only table remains the
        // accessible representation; the SVG is aria-hidden.
        var xFor = function (i) {
            return n > 1 ? x0 + (i / (n - 1)) * plotW : (x0 + x1) / 2;
        };
        var yFor = function (v) { return y1 - (Math.max(0, v) / top) * plotH; };

        function smoothLine(pts) {
            if (!pts.length) return '';
            var d = 'M' + pts[0][0] + ',' + pts[0][1];
            for (var i = 0; i < pts.length - 1; i++) {
                var ax = pts[i][0], ay = pts[i][1];
                var bx = pts[i + 1][0], by = pts[i + 1][1];
                var mx = (ax + bx) / 2;
                d += ' C' + mx + ',' + ay + ' ' + mx + ',' + by + ' ' + bx + ',' + by;
            }
            return d;
        }

        function gradient(id, cls0, cls1) {
            var lg = svgEl('linearGradient', {
                id: id, x1: '0', y1: '0', x2: '0', y2: '1',
            });
            lg.appendChild(svgEl('stop', { offset: '0%', 'class': cls0 }));
            lg.appendChild(svgEl('stop', { offset: '100%', 'class': cls1 }));
            return lg;
        }

        var defs = svgEl('defs');
        svg.appendChild(defs);

        function series(key, gradId, gradCls0, gradCls1, lineCls) {
            var pts = buckets.map(function (b, i) {
                return [xFor(i), yFor(b[key] || 0)];
            });
            var line = smoothLine(pts);
            if (!line) return;
            var area = line
                + ' L' + pts[pts.length - 1][0] + ',' + y1
                + ' L' + pts[0][0] + ',' + y1 + ' Z';
            defs.appendChild(gradient(gradId, gradCls0, gradCls1));
            svg.appendChild(svgEl('path', {
                'class': 'th-area', d: area, fill: 'url(#' + gradId + ')',
            }));
            svg.appendChild(svgEl('path', { 'class': lineCls, d: line }));
        }

        series('bytes_down', 'thGradDown', 'th-grad-down-0', 'th-grad-down-1',
               'th-line th-line--down');
        series('bytes_up', 'thGradUp', 'th-grad-up-0', 'th-grad-up-1',
               'th-line th-line--up');

        // Peak marker: a subtle vertical line at the highest-total bucket
        // (echoes the "your result" marker from the reference design).
        var peakIdx = 0, peakTot = -1;
        buckets.forEach(function (b, i) {
            var t = (b.bytes_up || 0) + (b.bytes_down || 0);
            if (t > peakTot) { peakTot = t; peakIdx = i; }
        });
        if (peakTot > 0) {
            var px = xFor(peakIdx);
            svg.appendChild(svgEl('line', {
                'class': 'th-peak-marker', x1: px, y1: y0, x2: px, y2: y1,
            }));
        }

        // X-axis labels (fewer on narrow widths).
        var maxTicks = W < 400 ? 5 : 7;
        var step = Math.max(1, Math.ceil(n / maxTicks));
        buckets.forEach(function (b, i) {
            if (i % step === 0 || i === n - 1) {
                var xt = svgEl('text', {
                    'class': 'chart-label', x: xFor(i), y: H - 6, 'text-anchor': 'middle',
                });
                xt.textContent = shortLabel(b.bucket_utc, granularity);
                svg.appendChild(xt);
            }
        });

        plot.appendChild(svg);
    }

    function updatePartialNote(buckets) {
        var note = el('th-chart-partial');
        if (!note) return;
        if (lastRecordingSince && buckets.length) {
            var rs = new Date(lastRecordingSince);
            var first = parseBucketStart(buckets[0].bucket_utc);
            if (!isNaN(rs.getTime()) && !isNaN(first.getTime()) && rs > first) {
                note.textContent =
                    'Recording began ' + formatUtc(lastRecordingSince) +
                    ' — earlier periods show zero.';
                note.hidden = false;
                return;
            }
        }
        note.hidden = true;
    }

    function renderSeries(data) {
        // The series response carries the effective display timezone so every
        // label (and the daily grid the backend already localised) agree.
        if (data.timezone) setDisplayTimezone(data.timezone);
        var buckets = data.buckets || [];
        var total = 0;
        buckets.forEach(function (b) {
            total += (b.bytes_up || 0) + (b.bytes_down || 0);
        });
        if (total === 0) {
            lastSeries = null;
            showChartState('empty');     // recording, but no traffic in range
            return;
        }
        lastSeries = { buckets: buckets, granularity: data.granularity };
        renderTable(buckets, data.granularity);
        updatePartialNote(buckets);
        showChartState('data');          // show first so the plot has a measurable width
        renderChart(buckets, data.granularity);
    }

    /**
     * Fetch /series for a range. showLoading=true for user-initiated loads
     * (first load / range change); false for the silent 24h timed refresh.
     * Race guard: stale responses (a newer request started) are discarded.
     */
    function fetchSeries(range, showLoading) {
        var token = ++seriesReqSeq;
        if (showLoading) showChartState('loading');
        return fetch('/api/traffic/series?range=' + encodeURIComponent(range), {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        })
        .then(function (response) {
            if (token !== seriesReqSeq) return null;      // stale
            if (response.status === 401) {
                var next = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = '/login?next=' + next;
                return null;
            }
            if (!response.ok) { showChartState('error'); return null; }
            return response.json();
        })
        .then(function (data) {
            if (!data || token !== seriesReqSeq) return;  // stale
            seriesLoadedRange = range;
            renderSeries(data);
        })
        .catch(function () {
            if (token !== seriesReqSeq) return;           // stale
            showChartState('error');
        });
    }

    // Load the active range's series once the body (recording) is visible.
    function ensureSeriesLoaded() {
        if (isRecording && seriesLoadedRange !== currentRange) {
            fetchSeries(currentRange, true);
        }
    }

    function setRange(range) {
        if (range === currentRange) return;
        currentRange = range;
        ['24h', '7d', '30d'].forEach(function (x) {
            var b = el('th-range-' + x);
            if (b) b.setAttribute('aria-pressed', x === range ? 'true' : 'false');
        });
        if (isRecording) fetchSeries(range, true);
    }

    // Timed refresh tick (registered at 60 s). Range-aware + visible-only:
    // only 24h refreshes, only while recording and the Dashboard is visible.
    function seriesTick() {
        if (!isRecording) return;
        var dash = el('section-dashboard');
        if (dash && dash.hidden) return;
        if (currentRange !== '24h') return;
        fetchSeries('24h', false);
    }

    /* ===================== init ===================== */

    onReady(function () {
        if (!el('traffic-history-card')) return;

        // Range selector buttons (native buttons: Tab + Enter/Space operable).
        ['24h', '7d', '30d'].forEach(function (x) {
            var b = el('th-range-' + x);
            if (b) b.addEventListener('click', function () { setRange(x); });
        });

        // "Turn on traffic recording" CTA (shown only in the off state). A
        // user-initiated action, so it uses apiFetch (CSRF + error toast) unlike
        // the toast-silent polls. On success, re-poll to flip the card state.
        var enableBtn = el('th-enable-recording');
        if (enableBtn) {
            enableBtn.addEventListener('click', function () {
                enableBtn.disabled = true;
                apiFetch('/api/traffic/recording', {
                    method: 'POST',
                    body: JSON.stringify({ enabled: true }),
                })
                .then(function () { return fetchSummaryPoll(); })
                .catch(function () { /* apiFetch already toasts the error */ })
                .then(function () { enableBtn.disabled = false; });
            });
        }

        // Summary poll (TC-1): 60 s, drives card state. Registered for logout.
        window.CCC.pollers.push(startPolling(fetchSummaryPoll, 60000));

        // Series refresh poll (TC-2a): 60 s; guards inside seriesTick implement
        // the range-aware, visible-only cadence. Registered for logout.
        window.CCC.pollers.push(startPolling(seriesTick, 60000));

        // Re-render the chart on viewport resize (px-based geometry). Debounced;
        // only acts when the chart data sub-state is visible.
        var resizeTimer = null;
        window.addEventListener('resize', function () {
            if (resizeTimer) clearTimeout(resizeTimer);
            resizeTimer = setTimeout(function () {
                var dataEl = el('th-chart-data');
                if (lastSeries && dataEl && !dataEl.hidden) {
                    renderChart(lastSeries.buckets, lastSeries.granularity);
                }
            }, 150);
        });
    });

})();
