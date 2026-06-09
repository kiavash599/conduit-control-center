# Changelog

All notable changes to Conduit Control Center are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Minor cleanup items pending before the v0.1.0 tag:

- `docs/architecture.md` — system architecture overview (#3)
- `docs/api-reference.md` — REST API reference (#3)

---

## [0.1.0-dev] — In development (not yet tagged)

All items listed below are committed to the repository and were validated
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
- `docs/api-reference.md` — REST API reference

### Security

- `CF_API_TOKEN` stored in `/etc/conduit-cc/.env` (permissions 640, owned by `conduit-cc` service user); never logged
- Primary TLS model: Cloudflare Proxy + Origin Certificate + Full (strict) SSL mode
- Nginx rate limiting on login endpoint (10 req/s per IP, using restored real IP)
- Security headers on all responses: HSTS, Content-Security-Policy, X-Frame-Options: DENY, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer
- Pairing links never appear in logs, database, or API responses
- FastAPI binds to `127.0.0.1` only — not exposed directly to the internet

### Known Limitations

- No time-series metrics charts (planned for v1.0)
- No email or webhook alerting (planned for v1.1)
- Conduit configuration is read-only — no editor (planned for v1.1)
- No two-factor authentication / TOTP (planned for v1.3)
- No multi-node support (planned for v2.0)
- Let's Encrypt certificate renewal is documented but not automated by the installer (planned for v1.0)

---

## Version Roadmap

| Version | Theme | Target |
|---------|-------|--------|
| **0.1.0** | MVP | 🔧 In development |
| 1.0.0 | S