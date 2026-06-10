# Conduit Control Center

[![CI](https://github.com/kiavash599/conduit-control-center/actions/workflows/ci.yml/badge.svg)](https://github.com/kiavash599/conduit-control-center/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%204%20%7C%20ARM64-lightgrey.svg)](docs/dev-setup.md)

A lightweight, open-source web dashboard for managing a [Psiphon Conduit](https://conduit.psiphon.ca) node on a Raspberry Pi 4.

Psiphon Conduit lets you share your internet bandwidth to help people in censored regions access the open internet. This dashboard replaces complex command-line operations with a clean, beginner-friendly web interface — install it once, then manage your node from any browser.

---

## Screenshot

> _Screenshot will be added after the v0.1.0 release._

---

## Features (v0.1)

| Feature | Description |
|---------|-------------|
| Node control | Start, stop, and restart your Conduit node with one click |
| Live status | Real-time status badge: Running / Stopped / Starting / Stopping / Error |
| System health | CPU %, RAM %, CPU temperature, and disk usage — updated every 10 seconds |
| Traffic counters | Bytes uploaded and downloaded since the last service start |
| Log viewer | Last 200 lines of Conduit logs with auto-refresh every 30 seconds |
| DDNS status | Current public IP and last Cloudflare DNS update result |
| Pairing | Not available in v0.1.0; planned for future Personal Mode work |
| Configuration viewer | Read-only view of current Conduit configuration |
| Secure login | Password-protected with account lockout after failed attempts |
| Change password | Update your admin password from the settings page |
| HTTPS | Full TLS via Cloudflare Proxy + Origin Certificate (recommended) |

---

## Requirements

### Hardware

- Raspberry Pi 4 (2 GB RAM minimum, 4 GB recommended)
- Ubuntu 22.04 LTS ARM64
- A stable internet connection with a public IP address

### Cloudflare (Recommended Deployment)

This project is designed to run behind the Cloudflare proxy. Before installing, you need:

- A domain managed by Cloudflare (e.g. `rockysystem.net`)
- A DNS A record for your dashboard hostname (e.g. `conduit.yourdomain.com`) with **Proxy enabled** (orange cloud ON)
- Cloudflare SSL/TLS mode set to **Full (strict)**
- A Cloudflare Origin Certificate (created in the Cloudflare dashboard)
- A Cloudflare API token with `Zone:DNS:Edit` and `Zone:Zone:Read` permissions

> **New to Cloudflare?** Read [docs/pre-install.md](docs/pre-install.md) first — it walks you through every step in the Cloudflare dashboard before you run the installer. The whole process takes about 10 minutes.

### Alternative: Direct IP (Let's Encrypt)

If you are not using the Cloudflare proxy, Let's Encrypt is supported. See [docs/tls-setup.md](docs/tls-setup.md).

> Self-signed certificates are **not supported** for public deployments.

---

## Quick Install

```bash
# 1. Complete the Cloudflare pre-install checklist (~10 minutes)
#    Read: docs/pre-install.md

# 2. Clone the repository
git clone https://github.com/kiavash599/conduit-control-center.git
cd conduit-control-center

# 3. Run the installer
chmod +x install.sh
sudo ./install.sh
```

The installer will:

1. Verify your OS and architecture
2. Prompt for your Cloudflare API token, zone name, and hostname
3. **Validate all inputs via the Cloudflare API** before making any system changes
4. Prompt for your Origin Certificate and private key file paths
5. Set up Nginx, systemd, UFW firewall, and the DDNS cron job automatically
6. Print your dashboard URL when complete

> If any validation step fails, the installer exits cleanly with a plain-English explanation. No system changes are made until all inputs are verified.

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/pre-install.md](docs/pre-install.md) | **Start here** — Cloudflare dashboard setup before running the installer |
| [docs/tls-setup.md](docs/tls-setup.md) | TLS certificate configuration (Cloudflare Origin Cert and Let's Encrypt) |
| [docs/dev-setup.md](docs/dev-setup.md) | Local development environment setup |
| [docs/architecture.md](docs/architecture.md) | System architecture overview |
| API reference | Interactive OpenAPI docs served by your running instance at `/api/docs` (Swagger UI), `/api/redoc` (ReDoc), and `/api/openapi.json` (raw schema) |

---

## Roadmap

| Version | Theme | Status |
|---------|-------|--------|
| **v0.1** | MVP — install, login, control, monitor, DDNS | 🔧 In development |
| v1.0 | Stable — charts, alerts, cert automation | 📋 Planned |
| v1.1 | Developer experience — dark mode, i18n, log filters | 📋 Planned |
| v1.2 | API & automation — JWT, backup/restore, webhooks | 📋 Planned |
| v1.3 | Security hardening — TOTP/2FA, session revocation | 📋 Planned |
| v2.0 | Multi-node — RBAC, Prometheus, Docker | 📋 Planned |

---

## Known Limitations in v0.1

These are deliberate decisions, not bugs. Each is planned for a future version.

- No time-series charts (planned for v1.0)
- No email or webhook alerts (planned for v1.1)
- Conduit configuration is read-only — no editor yet (planned for v1.1)
- No two-factor authentication (planned for v1.3)
- No multi-node support (planned for v2.0)
- Let's Encrypt renewal is documented but not automated by the installer (planned for v1.0)

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

This project is designed to be approachable for developers who are learning Linux, Python, and open-source workflows. If you get stuck, open a [Discussion](https://github.com/kiavash599/conduit-control-center/discussions) — there are no stupid questions.

---

## Security

Do not open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the responsible disclosure process.

---

## Licence

[MIT](LICENSE) — Copyright © 2026 Conduit Control Center Contributors
