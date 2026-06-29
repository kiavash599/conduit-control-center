# Architecture

Conduit Control Center (CCC) is a lightweight, self-hosted web dashboard for
managing a single Psiphon Conduit node on a Linux server or Raspberry Pi. This
document gives a high-level overview of how the pieces fit together. It is kept
deliberately brief; the API surface is documented live (see
[API reference](#api-reference)) rather than duplicated here.

> **Update architecture:** the one-click update subsystem follows the Trusted
> Update Engine architecture — see
> [ADR-0001: Trusted Update Engine](adr/0001-trusted-update-engine.md)
> (index: [docs/adr/](adr/README.md)).

## Component model

CCC is organised as a four-layer chain. Each layer talks only to the one
beneath it:

```
Dashboard (HTML / CSS / JS)
        |
        v
Backend API (FastAPI, Python 3)
        |
        v
Conduit Adapter
        |
        v
Conduit CLI / binary
```

- **Dashboard** - static HTML, CSS, and JavaScript served to the operator's
  browser. No client-side framework; scripts and styles are external files so a
  strict Content-Security-Policy can be enforced.
- **Backend API** - a FastAPI application that handles authentication, serves
  the dashboard pages, and exposes the REST API under `/api`. It binds to
  `127.0.0.1:8000` only and is never exposed to the network directly.
- **Conduit Adapter** - the backend module that wraps all interaction with
  Conduit: service control via `systemctl`, reading Conduit's Prometheus
  metrics endpoint, and other CLI-mediated operations. It is the single point
  through which the application touches Conduit.
- **Conduit CLI / binary** - the Psiphon Conduit binary itself, run as a
  systemd service and managed entirely through the adapter.

## Request flow

A typical authenticated request flows through these hops:

```
Browser -> Cloudflare -> Nginx -> FastAPI (127.0.0.1:8000) -> Conduit Adapter -> Conduit
```

- **Cloudflare** provides the public DNS record and proxies traffic to the
  origin, terminating the public-facing TLS and forwarding the real visitor IP
  via the `CF-Connecting-IP` header.
- **Nginx** terminates TLS at the origin using a Cloudflare Origin Certificate,
  restores the real client IP, applies security headers and login rate
  limiting, serves `/static/` assets directly, and proxies everything else to
  the FastAPI process on the loopback interface.
- **FastAPI** authenticates the session, applies CSRF checks on state-changing
  requests, and routes the call to the appropriate handler.
- **Conduit Adapter** carries out the requested operation against the Conduit
  service or its metrics endpoint and returns a result the API can serialise.

## Data storage

CCC stores a small amount of operational state in a local SQLite database
(`/etc/conduit-cc/ccc.db` in production, or the project root in development).
It contains three tables:

- `sessions` - server-side web session records
- `failed_attempts` - login failure counts and lockout state
- `audit_log` - security-relevant events

The database holds no Conduit secrets. Conduit's keypair, pairing links, and
API tokens are never written to it.

## Runtime model

CCC runs as a **single** uvicorn worker. The metrics, DDNS, and adapter caches
and the Contribution Advisor's sample buffer and evaluation state are kept in
process memory and assume exactly one process; running multiple workers would
fragment that state. The traffic collector is more defensive — it is a
single-writer background task guarded by a file lock, so additional processes
would contend for the lock rather than double-write — but the in-memory state
above would still split across workers. Multiple workers are therefore not
supported; `deployment/conduit-cc.service` pins `--workers 1` to enforce this.

The **Contribution Advisor** is a read-only advisory layer in the Backend API
that combines system resource samples, Conduit runtime metrics, and traffic
history into Health, Capacity, and Reduced-mode guidance (`GET /api/advisor`).
It keeps a short in-memory sample buffer to smooth noisy readings; "scale up"
(growth) suggestions appear only after a warm-up period (~10 minutes of
continuous Dashboard viewing) and reset on service restart. The buffer advances
only while the Dashboard is open, so the Advisor is best-effort guidance, not a
background monitor. Aggregate-only: no per-client or per-region data.

## Security model

- **Loopback-only application.** FastAPI listens on `127.0.0.1` only; all
  external access goes through Nginx.
- **TLS via Cloudflare.** The primary model is Cloudflare Proxy + Origin
  Certificate with SSL/TLS mode set to Full (strict). Let's Encrypt is also
  supported (see [TLS setup](tls-setup.md)).
- **Secrets isolation.** The Cloudflare API token and other secrets live in
  `/etc/conduit-cc/.env` (permissions `640`, owned by the `conduit-cc` service
  user) and are never logged. CCC never stores passwords in plaintext, API
  tokens, pairing links, or private keys.
- **Authentication.** Password login with bcrypt verification, server-side
  sessions backed by SQLite, HttpOnly/Secure/SameSite=Strict cookies, account
  lockout after repeated failures, and an idle session timeout.
- **CSRF protection.** A double-submit cookie pattern guards all
  state-changing endpoints.
- **Hardening.** Nginx sends a strict Content-Security-Policy and related
  security headers. The CCC systemd unit runs with least-privilege sandboxing:
  `ProtectSystem=strict`, `ReadWritePaths=/etc/conduit-cc`, `UMask=0077`,
  `PrivateTmp=yes`, `ProtectHome=yes` (see *Service control privilege* below).

### Service control privilege (v0.1.1)

**CCC v0.1.1 production baseline: verified.** The control buttons
(start/stop/restart) run `sudo systemctl <action> conduit` as the unprivileged
`conduit-cc` user.

G14 (control buttons) initially failed with HTTP 503 - the journal showed
`sudo: The "no new privileges" flag is set, which prevents sudo from running as
root.` Root cause: several systemd hardening directives (`SystemCallFilter=`,
`RestrictAddressFamilies=`, `RestrictNamespaces=`, `LockPersonality=`,
`PrivateDevices=`) *implicitly* enable `NoNewPrivileges=yes`, which blocks
`sudo`'s setuid elevation.

**Hotfix:** the NNP-implying directives were removed from
`deployment/conduit-cc.service`. Retained hardening (none implies
`NoNewPrivileges`): `ProtectSystem=strict`, `ReadWritePaths=/etc/conduit-cc`,
`UMask=0077`, `PrivateTmp=yes`, `ProtectHome=yes`. This is a deliberate v0.1.x
trade-off - reduced syscall/namespace sandboxing in exchange for working
Conduit controls - and is **not** the intended long-term architecture.

**Planned for v1.x (not yet implemented):** replace `sudo`/`systemctl` control
with polkit + systemd D-Bus (no setuid required), which is compatible with
`NoNewPrivileges=yes` and allows the stronger hardening directives to be
restored.

## Technology stack

- **Platform:** Raspberry Pi 4 (4 GB) / Ubuntu 22.04 ARM64, public IP
- **DNS / edge:** Cloudflare
- **Web server / TLS:** Nginx
- **Application:** Python 3, FastAPI
- **Storage:** SQLite
- **Frontend:** HTML, CSS, JavaScript (no framework)

## API reference

The REST API is documented live by the running instance via auto-generated
OpenAPI:

- `/api/docs` - interactive Swagger UI
- `/api/redoc` - ReDoc view
- `/api/openapi.json` - raw OpenAPI schema

There is no separate hand-maintained API reference file; the live schema is the
single source of truth.
