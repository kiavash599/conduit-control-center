# Changelog

All notable changes to Conduit Control Center are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added — Bandwidth Scheduling (reduced-mode window)

- Operator-configurable **daily reduced-mode window** for Conduit, set through the
  existing Settings → Conduit Configuration workflow (validate → confirm →
  restart → verify, with rollback).
- **UTC reduced window** — Start and End as `HH:MM` (24-hour, UTC), with a
  browser-local preview in the UI.
- **Reduced max common clients** and **reduced bandwidth (Mbps)** applied during
  the window; normal limits apply outside it.
- **Restart-on-apply, no boundary restarts** — Conduit restarts once when the
  schedule values change; psiphon-tunnel-core then performs the daily
  normal⇄reduced transition internally with no restart at the start/end times and
  no disconnect of already-connected clients. CCC runs no scheduler (no cron, no
  APScheduler, no systemd timers).
- API — `GET /api/conduit/config` reports the configured reduced window
  (configured-only; no effective metric exists); `POST /config/validate` and
  `POST /config/apply` accept the reduced fields. Aggregate-only: no per-client,
  session, IP, or identity data. The privilege boundary stays integer-only (the
  root helper formats `HH:MM` from validated minutes), preserving the M2 security
  model. `update.sh`/`install.sh` migrate and guard the reduced-capable helper +
  unit. Validated end-to-end on a Raspberry Pi 4 (Conduit 2.0.0); see
  `docs/closure/bandwidth-scheduling-closure.md`.

### Added — Regional Analytics

- Regions dashboard card — aggregate-only, top 10 active regions by traffic,
  `scope="common"`, sorted Traffic DESC. Columns: No., Country (flag + name),
  Traffic, Clients. Dashboard-aware 60s polling; mobile responsive.
- Regions API — `GET /api/conduit/regions` (auth-required, aggregate-only).
  Per region returns `{region, traffic_bytes, clients}` where
  `traffic_bytes = conduit_region_bytes_uploaded + conduit_region_bytes_downloaded`
  and `clients = conduit_region_connected_clients`. No IP, session, or
  per-client data; degrades to an empty list when metrics are unavailable.
- "Clients" terminology enforced in the UI (never "Users"); frontend guard
  tests added (`tests/unit/test_regions_frontend_guard.py`).
- Known limitation: Unicode flag emoji depend on platform font support; some
  desktop environments display the ISO letters instead of a flag (cosmetic;
  accepted, not a defect).

### Added — Contribution Advisor

- Contribution Advisor — read-only, aggregate-only guidance card at the top of
  the Dashboard. Surfaces Health, Capacity, and Reduced-mode recommendations
  plus a contribution health summary (Live/Offline status chip + headline).
- Advisor API — `GET /api/advisor` (auth-required, `Cache-Control: no-store`);
  deterministic engine with cooldown and a growth warm-up gate; degrades
  gracefully (never 5xx) when inputs are unavailable.
- Configurable via the `advisor` block in `config.json` (sampling/warm-up
  knobs; see `config.example.json`). Defaults are safe; aggregate-only — no
  per-client or per-region data.

### Added — Traffic history and dashboard information architecture

- Persistent traffic collector — aggregate-only byte ledger in SQLite with
  hourly/daily rollups, lifetime checkpoints, and configurable retention
  (default 30 days). Ship-dark: disabled by default (`traffic_collector_enabled`).
- Traffic Read API — `GET /api/traffic/summary` and `GET /api/traffic/series`
  (24h / 7d / 30d), read-only and aggregate-only.
- Dashboard information architecture (M-IA) — sections restructured to
  Dashboard / System / Settings with hash migration (`#overview`→`#dashboard`,
  `#logs`→`#system`) and additive pattern/state CSS conventions.
- "Lifetime & history" traffic card — persistent totals, recent-window figures,
  and a hand-built SVG grouped-bar time-series chart (CSP-safe; accessible
  data-table fallback).
- Static asset cache-busting — `static_url()` appends a per-file mtime query
  token so frontend deploys no longer require a manual CDN purge.

### Added — Psiphon Conduit end-to-end deployment (Issue #45)

**Psiphon Conduit end-to-end deployment**

- `install.sh` — Phase 1x: detect Conduit binary (PATH → `./conduit` →
  offer GitHub download with SHA-256 verification)
- `install.sh` — Phase 2x: create `conduit` system user; create
  `/opt/conduit/` (binary) and `/var/lib/conduit/` (data, keypair);
  pre-swap validation (4 steps); install binary; install + enable
  `conduit.service`; post-start verification; UFW reminder
- `deployment/conduit.service` — production systemd unit: `conduit` user,
  `/opt/conduit/conduit`, `--metrics-addr 127.0.0.1:9090`,
  `--max-common-clients 50`, `--bandwidth 40`, `ProtectSystem=strict`,
  `ReadWritePaths=/var/lib/conduit`, `PrivateTmp=yes`, `NoNewPrivileges=yes`
- `update.sh` — `phase2b_conduit_update`: detect new binary, 4-step
  pre-swap validation, `.bak` rollback copy, stop/swap/start, 3-check
  post-swap verification with automatic rollback
- `uninstall.sh` — `phase4b_conduit_remove`: stop/disable `conduit.service`,
  remove binary directory; preserve `conduit_key.json` and `conduit` user by
  default; `--purge` removes data directory and user
- `config.example.json` — added `_comment_metrics_port` to document the
  `metrics_port` ↔ `--metrics-addr` coupling
- `docs/pre-install.md` — Step 1a: Conduit binary options (download, local
  copy, PATH); post-install UFW firewall discovery procedure

### Fixed — deployment and access (post-0.1.1)

- Grant `journalctl` access so the Logs page can read the Conduit journal
- Drop `NoNewPrivileges`-implying hardening so sudo-based Conduit controls work
- Document the v0.1.1 control-hardening trade-off

---

## [0.1.1] — 2026-06-11

Maintenance release. Contains only the changes included in the `v0.1.1` tag.

### Security
- Upgrade Starlette to >= 1.0.1 (PYSEC-2026-161)

### Fixed
- Resolve shellcheck warnings in the install / update / uninstall scripts
- Render ANSI colours with `%b` after SC2059 cleanup

---

## [0.1.0] — MVP

> 0.1.0 was not separately tagged and first shipped within the v0.1.1 release.

All items listed below were committed to the repository and validated
end-to-end on a Raspberry Pi 4 (Ubuntu 22.04 ARM64) in Issue #38.

### Added

**Authentication**
- Password-protected web dashboard
- Login page with bcrypt password verification (cost factor 12)
- Server-side session management backed by SQLite (HttpOnly, Secure, SameSite=Strict cookies)
- Account lockout after 5 consecutive failed login attempts (15-minute lock)
- `ccc-unlock` CLI command to manually unlock a locked account
- Session idle timeout (default 60 minutes, configurable)
- Change password form — requires current password; invalidates all existing sessions on change

**Conduit Node Management**
- Node status display with colour-coded badge (Running / Stopped / Starting / Stopping / Error)
- Start, Stop, and Restart controls with confirmation dialog and loading spinner
- Pairing workflow guide — pairing link processed in memory only, never stored or logged
- Conduit configuration viewer (read-only; sensitive values masked)

**Monitoring**
- System health panel: CPU %, RAM MB/%, CPU temperature (°C), disk usage GB/% — updates every 10 seconds
- Traffic counter widget: bytes uploaded and downloaded since last service start
- Log viewer: last 200 lines of Conduit service log with auto-refresh every 30 seconds
- DDNS status panel: current public IP, hostname, last update time, last result (success/failure)

**Infrastructure**
- `install.sh` — automated installer for Ubuntu 22.04 ARM64
  - Interactive prompts for Cloudflare API token, zone name, hostname, and Origin Certificate
  - Validates all inputs via the Cloudflare API before making any system changes
  - Installs and configures Nginx, systemd, UFW, and DDNS cron job
- `uninstall.sh` — clean removal script
- `update.sh` — in-place upgrade script with automatic rollback on failure
- `cloudflare-ddns.sh` — Cloudflare DDNS update script (Script B: preserves proxy status); runs every 5 minutes via cron; structured JSON logging to `/var/log/conduit-cc/ddns.log`
- Nginx virtual host with Cloudflare Origin Certificate support, `ngx_http_realip_module` (restores real visitor IP from `CF-Connecting-IP`), security headers, and login rate limiting (10 req/s per IP)
- `conduit-cc.service` — systemd unit with sandboxing (`ProtectSystem=strict`, `PrivateTmp=yes`, `NoNewPrivileges=yes`) and automatic restart
- UFW firewall rules: allow ports 22, 80, and 443 only

**API**
- REST API with auto-generated OpenAPI documentation at `/api/docs`
- Unauthenticated health check: `GET /api/health`
- CSRF protection (double-submit cookie pattern) on all state-changing endpoints

**Documentation**
- `docs/pre-install.md` — Cloudflare dashboard setup checklist
- `docs/tls-setup.md` — Cloudflare Origin Certificate and Let's Encrypt setup
- `docs/dev-setup.md` — local development environment guide
- `docs/architecture.md` — system architecture overview
- API reference provided by the live OpenAPI docs at `/api/docs`, `/api/redoc`, and `/api/openapi.json` (no separate hand-maintained reference file)

### Security

- `CF_API_TOKEN` stored in `/etc/conduit-cc/.env` (permissions 640, owned by `conduit-cc` service user); never logged
- Primary TLS model: Cloudflare Proxy + Origin Certificate + Full (strict) SSL mode
- Nginx rate limiting on login endpoint (10 req/s per IP, using restored real IP)
- Security headers on all responses: HSTS, Content-Security-Policy, X-Frame-Options: DENY, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer
- Pairing links never appear in logs, database, or API responses
- FastAPI binds to `127.0.0.1` only — not exposed directly to the internet

### Known Limitations

- Historical traffic charts were not part of the original 0.1.0 MVP scope
- No email or webhook alerting (planned for v1.1)
- Conduit configuration is read-only — no editor (planned for v1.1)
- No two-factor authentication / TOTP (planned for v1.3)
- No multi-node support (planned for v2.0)
- Let's Encrypt certificate renewal is documented but not automated by the installer (planned for v1.0)

---

## Version Roadmap

| Version | Theme | Target |
|---------|-------|--------|
| **0.1.1** | MVP + maintenance | ✅ Released 2026-06-11 |
| 1.0.0 | S