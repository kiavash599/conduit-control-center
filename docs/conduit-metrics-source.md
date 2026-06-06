# Conduit Traffic Metrics — Data Source

**Issue:** #22  
**Verified:** 2026-06-06  
**Source:** [Psiphon-Inc/conduit CLI README](https://github.com/Psiphon-Inc/conduit/blob/main/cli/README.md)

---

## Summary

Conduit exposes traffic counters via a built-in Prometheus metrics HTTP server.
The server must be explicitly enabled at Conduit startup. If it is not enabled,
`GET /api/metrics/traffic` returns HTTP 200 with `null` byte fields — this is
normal, not an error.

---

## Enabling the metrics endpoint

Conduit must be started with the `--metrics-addr` flag:

```bash
conduit start --metrics-addr :9090
```

For production deployments managed by systemd, this flag belongs in the
`ExecStart` line of the Conduit service unit. See `deployment/conduit.service`
for a reference example.

The port is configurable in `config.json`:

```json
{
  "conduit": {
    "metrics_port": 9090
  }
}
```

`AppConfig.conduit_metrics_port` reads this value (default `9090`).

---

## Endpoint

| Property | Value |
|---|---|
| URL | `http://localhost:{conduit_metrics_port}/metrics` |
| Method | `GET` |
| Format | Prometheus text (plain text, `text/plain; version=0.0.4`) |
| Authentication | None (localhost only) |

---

## Metric names

The following gauges are scraped for `GET /api/metrics/traffic`:

| Prometheus gauge | API field | Description |
|---|---|---|
| `conduit_bytes_uploaded` | `bytes_sent` | Bytes sent by Conduit to users since last start |
| `conduit_bytes_downloaded` | `bytes_received` | Bytes received by Conduit from users since last start |

Both are **gauge** type (not counters): they represent cumulative totals
**since the most recent Conduit start** and reset to 0 on service restart.

Per-region variants also exist (`conduit_region_bytes_uploaded`, etc.) but
are not consumed by this endpoint.

---

## Parser

`backend/conduit/adapter.py :: _parse_prometheus_gauge()` performs a simple
line-by-line scan. The rule for matching the unlabelled aggregate gauge:

```
line.startswith("<metric_name> ")   # trailing space excludes labelled variants
```

Example Prometheus text:

```
# HELP conduit_bytes_uploaded Total bytes uploaded since last start
# TYPE conduit_bytes_uploaded gauge
conduit_bytes_uploaded 1073741824                        ← matched
conduit_bytes_uploaded{scope="common",region="US"} 524288000  ← skipped
```

The value token is parsed as `int(float(value))` to handle both integer
and floating-point representations.

---

## Graceful degradation

| Condition | bytes_sent / bytes_received | HTTP status |
|---|---|---|
| Conduit running, metrics configured | integer values | 200 |
| Conduit running, no traffic yet | 0 | 200 |
| Conduit stopped | null | 200 |
| `--metrics-addr` not configured | null | 200 |
| Metrics server timeout (> 2 s) | null | 200 |

The endpoint **never** returns 503 due to Conduit being stopped or
unconfigured. `null` byte fields are the contract for "unavailable".

---

## Caching

Responses are cached in memory for `metrics_cache_ttl_seconds` (config.json,
default 5 seconds). The dashboard polls every 30 seconds so the cache rarely
activates in normal use; it guards against burst requests from multiple
browser tabs or external monitoring tools.

---

## Hardware validation required

The metric names and endpoint behaviour were verified from the official
upstream CLI documentation. They have not yet been tested against a physical
Raspberry Pi running Conduit. Before shipping v0.1, verify on hardware:

1. Start Conduit with `--metrics-addr :9090`
2. Run `curl http://localhost:9090/metrics | grep conduit_bytes`
3. Confirm `conduit_bytes_uploaded` and `conduit_bytes_downloaded` appear
4. Confirm values increase as Conduit proxies traffic
5. Confirm values reset to 0 after `sudo systemctl restart conduit`

If metric names differ from the above, update `_METRIC_BYTES_UPLOADED` and
`_METRIC_BYTES_DOWNLOADED` in `backend/conduit/adapter.py` and this document.
