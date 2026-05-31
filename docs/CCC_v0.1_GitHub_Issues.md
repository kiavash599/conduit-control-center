# Conduit Control Center — GitHub Issues for v0.1 MVP

> **How to use this file**
> Each block below is one GitHub Issue. Create them in order (lower numbers first) so dependency references are valid.
> Apply the suggested labels before you start. Milestone: `v0.1`.
>
> **Suggested labels to create first:**
> `setup` · `infrastructure` · `backend` · `frontend` · `auth` · `conduit` · `metrics` · `logs` · `security` · `testing` · `docs` · `ci`

---

## Issue #1 — Initialize repository structure and branch protection

**WBS:** 1.1  
**Labels:** `setup`  
**Milestone:** v0.1  
**Effort:** 0.5 day

### Description

Set up the GitHub repository with the correct branch strategy and protection rules defined in the SRS v1.1 governance section.

### Tasks

- [ ] Create the GitHub repository `conduit-control-center`
- [ ] Create `main` and `develop` branches
- [ ] Enable branch protection on `main`: require pull request, require CI to pass, disable direct push
- [ ] Enable branch protection on `develop`: require CI to pass
- [ ] Add `.gitignore` (Python, Node, OS files, `.env`, `*.pem`, `*.db`)
- [ ] Add `.gitattributes` (line endings, shell scripts as text)
- [ ] Add MIT `LICENSE` file
- [ ] Add top-level `README.md` placeholder

### Acceptance Criteria

- [ ] Direct push to `main` is blocked
- [ ] `.env` and `*.pem` are listed in `.gitignore`
- [ ] `LICENSE` contains the MIT licence text with the correct copyright year

---

## Issue #2 — Create GitHub Actions CI pipeline

**WBS:** 1.2  
**Labels:** `setup`, `ci`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #1

### Description

Create `.github/workflows/ci.yml` that runs on every pull request and push to `main` and `develop`.

### Tasks

- [ ] Add lint step: `ruff check .`
- [ ] Add unit test step: `pytest tests/unit/ --cov=backend --cov-report=xml`
- [ ] Add integration test step: `pytest tests/integration/`
- [ ] Add security audit step: `pip-audit`
- [ ] Add install script syntax check: `bash -n install.sh`
- [ ] Upload coverage report as a CI artefact
- [ ] Fail the workflow if coverage drops below 60%

### Acceptance Criteria

- [ ] CI runs automatically on a test pull request
- [ ] All five checks (lint, unit, integration, audit, syntax) appear as separate steps in the workflow

---

## Issue #3 — Add issue templates and PR template

**WBS:** 1.3  
**Labels:** `setup`, `docs`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #1

### Description

Add GitHub issue and pull request templates so contributors know what information is expected.

### Tasks

- [ ] `.github/ISSUE_TEMPLATE/bug_report.md` — steps to reproduce, expected vs actual, OS/version
- [ ] `.github/ISSUE_TEMPLATE/feature_request.md` — user need, not implementation
- [ ] `.github/ISSUE_TEMPLATE/security_vulnerability.md` — redirect to SECURITY.md email (do not use this template publicly)
- [ ] `.github/PULL_REQUEST_TEMPLATE.md` — description, testing performed, checklist (linked issue, tests added, docs updated)

### Acceptance Criteria

- [ ] Opening a new issue shows the three template options
- [ ] Opening a new PR pre-fills the PR template

---

## Issue #4 — Define repository directory structure

**WBS:** 1.4  
**Labels:** `setup`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #1

### Description

Create the full directory tree with placeholder files so all contributors work in a consistent structure from day one.

### Directory structure

```
conduit-control-center/
  backend/
    __init__.py
    main.py
    config.py
    auth/
    conduit/
    metrics/
    logs/
    api/
  frontend/
    static/
      css/
      js/
    templates/
  scripts/
  tests/
    unit/
    integration/
  docs/
  .env.example
  config.example.json
  requirements.txt
  install.sh
  uninstall.sh
  update.sh
```

### Acceptance Criteria

- [ ] All directories exist with a `.gitkeep` or starter file
- [ ] `.env.example` contains all required keys with placeholder values and inline comments
- [ ] `config.example.json` mirrors the runtime config structure

---

## Issue #5 — Write developer environment setup guide

**WBS:** 1.5  
**Labels:** `docs`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #4

### Description

Write `docs/dev-setup.md` so a new contributor can get the project running locally in under 10 minutes.

### Contents

- [ ] Prerequisites (Python 3.10+, git)
- [ ] Clone and create virtual environment
- [ ] Install dependencies (`pip install -r requirements.txt`)
- [ ] Copy `.env.example` to `.env` and fill in values
- [ ] Run the dev server (`uvicorn backend.main:app --reload`)
- [ ] Run the tests (`pytest`)
- [ ] How to lint (`ruff check .`)

### Acceptance Criteria

- [ ] A fresh clone on Ubuntu 22.04 follows the guide to a running dev server without additional steps

---

## Issue #6 — Create systemd service unit

**WBS:** 2.1  
**Labels:** `infrastructure`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #4

### Description

Create `scripts/conduit-cc.service` — the systemd unit file that runs the FastAPI application.

### Requirements (from SRS DR-SVC-01 to DR-SVC-05)

- [ ] `User=conduit-cc` (dedicated service user, not root)
- [ ] `Restart=on-failure`, `RestartSec=5`
- [ ] `ProtectSystem=strict`
- [ ] `PrivateTmp=yes`
- [ ] `NoNewPrivileges=yes`
- [ ] `WorkingDirectory=/opt/conduit-cc`
- [ ] `ExecStart=/opt/conduit-cc/venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000`
- [ ] `WantedBy=multi-user.target`

### Acceptance Criteria

- [ ] `systemd-analyze verify` passes with no errors on the unit file
- [ ] Service starts as `conduit-cc` user (verified with `ps aux`)

---

## Issue #7 — Create Nginx virtual host configuration

**WBS:** 2.2  
**Labels:** `infrastructure`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #4

### Description

Create `scripts/conduit-cc.conf` — the Nginx server block that terminates TLS and proxies to FastAPI.

### Requirements

- [ ] HTTP (port 80) → HTTPS redirect (301)
- [ ] HTTPS (port 443) with `ssl_certificate` and `ssl_certificate_key` placeholders
- [ ] TLS 1.2 minimum, TLS 1.3 preferred
- [ ] `proxy_pass http://127.0.0.1:8000`
- [ ] `proxy_set_header` for Host, X-Real-IP, X-Forwarded-For, X-Forwarded-Proto
- [ ] Security headers (placeholder — detailed in #32)
- [ ] Rate limiting zone for `/api/auth/login` (placeholder — detailed in #34)
- [ ] `server_tokens off`

### Acceptance Criteria

- [ ] `nginx -t` passes with no errors
- [ ] HTTP request to the server returns 301 redirect to HTTPS

---

## Issue #8 — Write pre-install checklist and TLS setup guides

**WBS:** 2.3  
**Labels:** `infrastructure`, `docs`  
**Milestone:** v0.1  
**Effort:** 1.5 days  
**Depends on:** #7

### Description

Write two documents that a user reads **before** running `install.sh`:

- `docs/pre-install.md` — step-by-step Cloudflare dashboard setup (primary path)
- `docs/tls-setup.md` — Origin Certificate configuration (primary) and Let's Encrypt (alternative)

Every user of this project brings their own domain registered on Cloudflare. The install script validates the results of these steps via the Cloudflare API, so if the user skips a step the script will fail fast with a clear error rather than a cryptic mid-install failure.

---

### docs/pre-install.md

#### Section 1 — What you need before running install.sh

| Item | Where to get it |
|------|----------------|
| A domain managed by Cloudflare | cloudflare.com → Add a site |
| A subdomain for your dashboard | Cloudflare DNS tab |
| Cloudflare Proxy enabled (orange cloud) | Cloudflare DNS tab |
| SSL/TLS mode set to Full (strict) | Cloudflare SSL/TLS → Overview |
| Cloudflare Origin Certificate (cert + key files) | Cloudflare SSL/TLS → Origin Server |
| Cloudflare API token (Zone:DNS:Edit + Zone:Zone:Read) | Cloudflare My Profile → API Tokens |

#### Section 2 — Step-by-step instructions

- [ ] **Step 1 — Add your domain to Cloudflare** (if not already there)
  - Log in to Cloudflare → Add a site → follow the nameserver instructions

- [ ] **Step 2 — Create a DNS A record for your dashboard hostname**
  - DNS tab → Add record → Type: A, Name: `conduit` (or any subdomain), IPv4: `1.1.1.1` (placeholder — DDNS will correct it)
  - Enable Proxy: **ON** (orange cloud)
  - TTL: Auto
  - > The IP you enter here does not matter. The DDNS script will update it within 5 minutes of install.

- [ ] **Step 3 — Set SSL/TLS mode to Full (strict)**
  - SSL/TLS → Overview → select **Full (strict)**
  - > This is required. Without Full (strict), Cloudflare will not verify the Origin Certificate and the connection is downgraded or broken.

- [ ] **Step 4 — Create a Cloudflare Origin Certificate**
  - SSL/TLS → Origin Server → Create Certificate
  - Key type: RSA (2048)
  - Hostnames: add your dashboard hostname (e.g. `conduit.yourdomain.com`) and optionally the wildcard `*.yourdomain.com`
  - Certificate Validity: 15 years
  - Click Create — Cloudflare shows the certificate and private key **once only**
  - Save both to files on your Pi before proceeding (e.g. `/home/youruser/origin.pem` and `/home/youruser/origin.key`)
  - > These are not Let's Encrypt certificates. They are issued by Cloudflare's own CA and are trusted only when the Cloudflare proxy is in front of your server.

- [ ] **Step 5 — Create a Cloudflare API token**
  - My Profile → API Tokens → Create Token
  - Use template: **Edit zone DNS**
  - Permissions: Zone → DNS → **Edit** and Zone → Zone → **Read**
  - Zone Resources: Include → Specific zone → select your domain only
  - Click Continue to summary → Create Token
  - Copy the token immediately — Cloudflare shows it only once
  - > Use a scoped token (one zone, two permissions only). Do NOT use a Global API key.

- [ ] **Step 6 — Verify you have everything ready**
  - Your dashboard hostname DNS record exists in Cloudflare with proxy ON
  - SSL/TLS mode is Full (strict)
  - Origin Certificate and key files are saved on the Pi
  - API token is copied

You are now ready to run `install.sh`.

---

### docs/tls-setup.md

#### Primary path — Cloudflare Origin Certificate

- [ ] Document what a Cloudflare Origin Certificate is and why it is different from a Let's Encrypt cert
- [ ] Explain that the cert is only valid when the Cloudflare proxy is in front of the server
- [ ] Show the Nginx `ssl_certificate` / `ssl_certificate_key` directive using the installed paths (`/etc/conduit-cc/tls/`)
- [ ] Show how to verify: `curl -I https://your-hostname` should return HTTP 200 with `CF-RAY` header

#### Alternative path — Let's Encrypt (direct-IP deployments, no Cloudflare proxy)

- [ ] Note: Cloudflare proxy must be **OFF** (grey cloud) for HTTP-01 challenge to reach the server
- [ ] `sudo apt install certbot python3-certbot-nginx`
- [ ] `sudo certbot --nginx -d yourdomain.com`
- [ ] Verify renewal timer: `systemctl status certbot.timer`
- [ ] Note: without Cloudflare proxy, DDoS protection and the DDNS proxy-preserve behaviour are not available

#### Self-signed certificates

- [ ] Document clearly: self-signed certificates are **not supported for public deployments**
- [ ] Acceptable only for local testing on a private network (no public IP)
- [ ] The install script will detect a self-signed cert on a public IP and print a prominent warning before proceeding

### Acceptance Criteria

- [ ] A first-time user with no prior Cloudflare experience can complete `docs/pre-install.md` in under 15 minutes
- [ ] After following `docs/pre-install.md`, running `install.sh` succeeds on the first attempt without additional troubleshooting
- [ ] `docs/tls-setup.md` clearly explains why the Origin Certificate only works behind Cloudflare proxy

---

## Issue #9 — Write install.sh installation script

**WBS:** 2.4  
**Labels:** `infrastructure`  
**Milestone:** v0.1  
**Effort:** 3–4 days  
**Depends on:** #6, #7, #8

### Description

Write `install.sh` — the single-command installer for Conduit Control Center on Ubuntu 22.04 ARM64. This is the most important infrastructure deliverable for v0.1. Every user brings their own Cloudflare domain; the script must collect and validate their details interactively before doing any system configuration.

### Design principle

**Validate everything before changing anything.** All interactive prompts and Cloudflare API checks happen in Phase 1. Only after all inputs are validated does the script modify the system. A failure in Phase 1 exits cleanly with no side effects.

---

### Phase 1 — Validate (no system changes yet)

- [ ] **1a.** Check OS: `lsb_release -rs` must be `22.04`; `uname -m` must be `aarch64`. Exit with fix suggestion if either fails.
- [ ] **1b.** Print link to `docs/pre-install.md` and ask the user to confirm they have completed the pre-install checklist before continuing.
- [ ] **1c.** `apt-get update && apt-get install -y python3 python3-pip python3-venv nginx ufw curl jq`
- [ ] **1d.** [interactive] Prompt: `Enter your Cloudflare API token:` (input hidden with `read -s`)
  - Validate immediately: `curl -s -X GET "https://api.cloudflare.com/client/v4/zones?name=..." -H "Authorization: Bearer $CF_API_TOKEN"` — check `success: true` and that at least one zone is returned
  - If validation fails: print `ERROR: API token is invalid or has insufficient permissions. Required: Zone:DNS:Edit and Zone:Zone:Read.` and exit.
- [ ] **1e.** [interactive] Prompt: `Enter your Cloudflare zone name (e.g. rockysystem.net):`
  - Validate: zone lookup returns a result. Extract and store `ZONE_ID`.
  - If zone not found: print `ERROR: Zone not found. Make sure the domain is added to your Cloudflare account.` and exit.
- [ ] **1f.** [interactive] Prompt: `Enter your dashboard hostname (e.g. conduit.rockysystem.net):`
  - Validate: DNS record lookup `GET /zones/{ZONE_ID}/dns_records?name={CF_RECORD_NAME}` returns a result.
  - Check `proxied: true` — if false: print `WARNING: The DNS record exists but Cloudflare Proxy is OFF. The recommended setup requires the proxy to be enabled (orange cloud). Continue anyway? [y/N]`
  - Store existing `RECORD_ID` and `PROXY_STATUS` for the DDNS script.
- [ ] **1g.** [interactive] Prompt: `Enter the full path to your Cloudflare Origin Certificate file (.pem):`
  - Validate: file exists; `openssl x509 -noout -in "$CERT_PATH"` exits 0; issuer contains `Cloudflare`.
  - If invalid: print `ERROR: Certificate file is not valid. Follow docs/tls-setup.md to create a Cloudflare Origin Certificate.` and exit.
- [ ] **1h.** [interactive] Prompt: `Enter the full path to your Origin Certificate private key file (.key):`
  - Validate: file exists; `openssl rsa -noout -in "$KEY_PATH"` exits 0.
  - Validate cert/key pair match: compare public key moduli with `openssl`.
  - If mismatch: print `ERROR: Certificate and private key do not match.` and exit.
- [ ] **1i.** [interactive] Prompt: `Create your dashboard admin password (min 10 characters):`
  - Enforce minimum length. Prompt twice and verify they match.
- [ ] **1j.** Print a summary of all collected values (mask the token: show first 6 chars + `...`) and ask: `Proceed with installation? [y/N]`

---

### Phase 2 — Install (system changes)

- [ ] **2a.** Create system user `conduit-cc` (no home dir, no login shell) — idempotent.
- [ ] **2b.** Copy application files to `/opt/conduit-cc/`; set ownership to `conduit-cc`.
- [ ] **2c.** Create Python venv at `/opt/conduit-cc/venv/`; install pinned deps from `requirements.txt`.
- [ ] **2d.** Create `/etc/conduit-cc/` directory (750, conduit-cc).
- [ ] **2e.** Copy Origin Certificate to `/etc/conduit-cc/tls/origin.pem` (640, conduit-cc). Copy private key to `/etc/conduit-cc/tls/origin.key` (640, conduit-cc).
- [ ] **2f.** Generate 32-byte random session secret. Write `/etc/conduit-cc/.env` (640, conduit-cc) containing:
  ```
  SESSION_SECRET=<generated>
  CF_API_TOKEN=<collected>
  CF_ZONE_NAME=<collected>
  CF_RECORD_NAME=<collected>
  ```
- [ ] **2g.** Hash admin password with bcrypt (cost 12). Write to `/etc/conduit-cc/config.json` (640, conduit-cc).
- [ ] **2h.** Install Nginx config from template — substitute `CF_RECORD_NAME` as `server_name`, set cert paths. Run `nginx -t`; if it fails, print error and exit before reloading.
- [ ] **2i.** Configure UFW: `allow 22`, `allow 80`, `allow 443`, `--force enable`. Idempotent.
- [ ] **2j.** Install `scripts/cloudflare-ddns.sh` to `/opt/conduit-cc/scripts/` (750, conduit-cc). Install cron job: `*/5 * * * * conduit-cc /opt/conduit-cc/scripts/cloudflare-ddns.sh`. Run script once immediately — if it fails, print a warning but do not abort the install.
- [ ] **2k.** Install and enable `conduit-cc.service`. `systemctl daemon-reload && systemctl enable --now conduit-cc`.
- [ ] **2l.** Wait up to 10 seconds for the service to report healthy (`GET /api/health` returns 200).

---

### Phase 3 — Post-install summary

Print a formatted summary box containing:
- [ ] Dashboard URL: `https://{CF_RECORD_NAME}`
- [ ] Check service: `systemctl status conduit-cc`
- [ ] View logs: `journalctl -u conduit-cc -f`
- [ ] Check DDNS log: `tail -f /var/log/conduit-cc/ddns.log`
- [ ] Reminder: `Verify Cloudflare SSL/TLS mode is set to Full (strict) at: https://dash.cloudflare.com`
- [ ] Link to docs: `docs/tls-setup.md`, `docs/pre-install.md`

---

### Non-functional requirements

- [ ] Idempotent — safe to re-run without data loss or errors
- [ ] Every step prints a clear progress line: `[1/14] Checking operating system...`
- [ ] On any failure: print `ERROR: <plain English description>` + `FIX: <suggested action>` and exit 1
- [ ] The API token is never printed to the terminal (use `read -s`; never echo it in progress messages)
- [ ] Script passes `shellcheck` with no errors or warnings

### Acceptance Criteria

- [ ] Script runs to completion on a fresh Ubuntu 22.04 ARM64 image after completing `docs/pre-install.md`
- [ ] Phase 1 exits immediately if API token is wrong — no system changes made
- [ ] Phase 1 exits immediately if hostname DNS record does not exist — no system changes made
- [ ] Phase 1 exits immediately if cert/key files are invalid or mismatched
- [ ] Re-running the script does not overwrite existing secrets or certificates
- [ ] `systemctl status conduit-cc` shows `active (running)` after install
- [ ] `GET https://{CF_RECORD_NAME}/api/health` returns 200 JSON from outside the Pi
- [ ] `shellcheck install.sh` returns exit code 0

---

## Issue #10 — Write uninstall.sh

**WBS:** 2.5  
**Labels:** `infrastructure`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #9

### Description

Write `uninstall.sh` — reverses the installation cleanly.

### Tasks

- [ ] Stop and disable `conduit-cc.service`
- [ ] Remove `/opt/conduit-cc/`
- [ ] Prompt before removing `/etc/conduit-cc/` (contains user config and secrets)
- [ ] Remove Nginx site config and reload Nginx
- [ ] Remove systemd unit file and reload daemon
- [ ] Remove `conduit-cc` system user
- [ ] Print note: UFW rules are not removed automatically; manual steps provided

### Acceptance Criteria

- [ ] After running, `systemctl status conduit-cc` returns "not found"
- [ ] `/opt/conduit-cc` and `/etc/systemd/system/conduit-cc.service` no longer exist

---

## Issue #11 — Write update.sh upgrade script

**WBS:** 2.6  
**Labels:** `infrastructure`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #9

### Description

Write `update.sh` — upgrades the application in-place with minimal downtime.

### Tasks

- [ ] Back up `/etc/conduit-cc/` to a timestamped archive before any changes
- [ ] Pull latest code (git pull or download tarball)
- [ ] Install updated dependencies into the venv
- [ ] Run any pending database migrations
- [ ] Restart `conduit-cc.service`
- [ ] Verify service is running after restart
- [ ] On any failure, restore the backup and restart the previous version

### Acceptance Criteria

- [ ] Service downtime during upgrade is under 10 seconds
- [ ] If the upgrade fails, the previous version is automatically restored

---

## Issue #12 — Create FastAPI application skeleton

**WBS:** 3.1  
**Labels:** `backend`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #4

### Description

Build the FastAPI application foundation that all other backend features are built on top of.

### Tasks

- [ ] `backend/main.py` — app factory, router registration, startup/shutdown events
- [ ] `backend/config.py` — load settings from `.env` and `config.json` using Pydantic Settings
- [ ] `backend/dependencies.py` — reusable FastAPI dependencies (get_db, get_current_user)
- [ ] `backend/database.py` — SQLite connection helper; create tables on startup
- [ ] Register routers: `/api/auth`, `/api/status`, `/api/conduit`, `/api/metrics`, `/api/logs`, `/api/settings`, `/api/health`
- [ ] `GET /api/health` — unauthenticated; return `{version, uptime, status}` JSON
- [ ] Global error handler: return JSON errors, never expose stack traces
- [ ] Static file serving for `frontend/static/`
- [ ] Template rendering for `frontend/templates/`

### Acceptance Criteria

- [ ] `uvicorn backend.main:app` starts without errors
- [ ] `GET /api/health` returns HTTP 200 JSON
- [ ] `GET /api/docs` returns the OpenAPI UI (authentication required in later issue)

---

## Issue #13 — Implement SQLite session store

**WBS:** 3.2  
**Labels:** `backend`, `auth`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #12

### Description

Implement server-side session management backed by SQLite. No JWT — plain secure session cookies.

### Tasks

- [ ] `sessions` table: `(id TEXT PRIMARY KEY, user_id TEXT, created_at DATETIME, last_active DATETIME, expires_at DATETIME)`
- [ ] `create_session(user_id)` → generates 32-byte random session ID, inserts row, returns session ID
- [ ] `get_session(session_id)` → returns session if valid and not expired, else `None`
- [ ] `touch_session(session_id)` → updates `last_active`; extends expiry
- [ ] `delete_session(session_id)` → removes row (logout)
- [ ] `purge_expired_sessions()` → deletes expired rows; called on startup and hourly
- [ ] Session cookie settings: `HttpOnly=True`, `Secure=True`, `SameSite=strict`, `Path=/`

### Acceptance Criteria

- [ ] Session created on login; cookie visible in browser DevTools with correct flags
- [ ] Expired session redirects to login page
- [ ] Purge function removes rows older than the configured timeout

---

## Issue #14 — Implement login and logout endpoints

**WBS:** 3.3  
**Labels:** `backend`, `auth`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #13

### Description

Implement the two core authentication endpoints.

### POST /api/auth/login

- [ ] Accept `{username, password}` JSON body (Pydantic model)
- [ ] Load hashed password from config; verify with `bcrypt.checkpw`
- [ ] On success: create session (Issue #13), set session cookie, return `{status: "ok"}`
- [ ] On failure: return HTTP 401 with generic message `"Invalid credentials"` (no hint about which field was wrong)
- [ ] Call lockout check before password verification (Issue #15)

### POST /api/auth/logout

- [ ] Require valid session cookie
- [ ] Delete session from store
- [ ] Clear session cookie (set `Max-Age=0`)
- [ ] Return HTTP 200

### Acceptance Criteria

- [ ] Correct credentials → session cookie set, 200 response
- [ ] Wrong credentials → 401, no session cookie, no information leak
- [ ] After logout, the session ID is rejected by the session middleware

---

## Issue #15 — Implement account lockout and ccc-unlock CLI

**WBS:** 3.4  
**Labels:** `backend`, `auth`, `security`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #14

### Description

Protect the login endpoint against brute-force attacks.

### Tasks

- [ ] `failed_attempts` table: `(username TEXT PRIMARY KEY, count INTEGER, locked_until DATETIME)`
- [ ] On each failed login: increment count; if count ≥ 5, set `locked_until = now + 15 min`
- [ ] On successful login: reset count to 0
- [ ] Login endpoint: check `locked_until` before password verification; return HTTP 429 with `Retry-After` header if locked
- [ ] `scripts/ccc-unlock` CLI script: accepts username; clears the lockout record; prints confirmation
- [ ] Audit log entry for every lockout event

### Acceptance Criteria

- [ ] 5 failed attempts in sequence lock the account; 6th attempt returns 429
- [ ] `ccc-unlock admin` clears the lock and allows login again
- [ ] Successful login after lockout expiry resets the counter

---

## Issue #16 — Implement session validation middleware

**WBS:** 3.5  
**Labels:** `backend`, `auth`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #13

### Description

FastAPI dependency that protects every route except `/login` and `/api/health`.

### Tasks

- [ ] `get_current_user` dependency: reads session cookie, calls `get_session`, returns user object or raises `HTTPException(401)`
- [ ] API routes: return HTTP 401 JSON for unauthenticated requests
- [ ] HTML routes: redirect unauthenticated requests to `/login?next=<original_path>`
- [ ] After login, redirect to the `next` parameter if present and safe (validate it is a relative path)

### Acceptance Criteria

- [ ] Accessing `/dashboard` without a session redirects to `/login?next=/dashboard`
- [ ] After login, the user lands on `/dashboard`
- [ ] `GET /api/health` returns 200 without a session cookie

---

## Issue #17 — Implement Conduit adapter (systemctl wrapper)

**WBS:** 4.1  
**Labels:** `backend`, `conduit`  
**Milestone:** v0.1  
**Effort:** 1.5 days  
**Depends on:** #12

### Description

The Conduit adapter wraps the Conduit systemd service. It is the only module that calls `systemctl` or reads Conduit output.

### Tasks

- [ ] `backend/conduit/adapter.py`
- [ ] `get_status()` → calls `systemctl is-active conduit`; returns one of `running | stopped | starting | stopping | error`
- [ ] `start()` → `systemctl start conduit`; waits up to 5 s for status to become `running`; returns result
- [ ] `stop()` → `systemctl stop conduit`; waits up to 5 s; returns result
- [ ] `restart()` → `systemctl restart conduit`; returns result
- [ ] `get_last_changed()` → parse `systemctl show conduit --property=ActiveEnterTimestamp`
- [ ] All subprocess calls use explicit argument lists (no shell=True); never interpolate user input
- [ ] Raise `ConduitAdapterError` with a safe message on failure (do not surface raw stderr to the API)

### Acceptance Criteria

- [ ] `get_status()` returns the correct status when Conduit is running and when it is stopped
- [ ] `start()` and `stop()` change the actual service state (tested on a device with Conduit installed)
- [ ] No `shell=True` anywhere in the adapter

---

## Issue #18 — Implement GET /api/status

**WBS:** 4.2  
**Labels:** `backend`, `conduit`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #17

### Description

Simple status endpoint consumed by the frontend status polling loop.

### Response schema

```json
{
  "node_status": "running",
  "last_changed": "2026-05-31T14:30:00Z",
  "conduit_version": "1.2.3",
  "uptime_seconds": 3600
}
```

### Acceptance Criteria

- [ ] Returns correct `node_status` within 1 second
- [ ] `last_changed` reflects the most recent start/stop event
- [ ] Returns 401 without a valid session

---

## Issue #19 — Implement start/stop/restart endpoints

**WBS:** 4.3  
**Labels:** `backend`, `conduit`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #17

### Description

Three action endpoints. All require authentication.

- `POST /api/conduit/start`
- `POST /api/conduit/stop`
- `POST /api/conduit/restart`

### Tasks

- [ ] Call corresponding adapter method
- [ ] Return `{action, result, new_status}` JSON
- [ ] Return HTTP 409 if action is not valid in current state (e.g. start when already running)
- [ ] Audit log entry for each action with timestamp

### Acceptance Criteria

- [ ] Calling `POST /api/conduit/stop` on a running service stops it and returns `new_status: "stopped"`
- [ ] Starting an already-running service returns HTTP 409

---

## Issue #20 — Implement pairing workflow (transient, no storage)

**WBS:** 4.4  
**Labels:** `backend`, `conduit`, `security`  
**Milestone:** v0.1  
**Effort:** 1.5 days  
**Depends on:** #17

### Description

Guide the user through Psiphon Conduit pairing. The pairing link is the most sensitive piece of data in the system — it must never be persisted.

### Tasks

- [ ] `POST /api/conduit/pair` — accepts `{pairing_link: str}` in request body (Pydantic model)
- [ ] Validate that `pairing_link` matches the expected Psiphon pairing URL format
- [ ] Pass link directly to Conduit CLI in memory; do not write to any file or log
- [ ] Return `{status: "paired" | "failed", message: str}`
- [ ] After the request completes, the pairing link must be out of scope (no caching, no background retention)
- [ ] Security review: verify the link never appears in: FastAPI access logs, application logs, database, `.env`, Nginx logs

### Acceptance Criteria

- [ ] Grep of all log files after pairing shows zero occurrences of the pairing link
- [ ] The endpoint returns a structured result without exposing the link in the response

---

## Issue #21 — Implement GET /api/metrics/system

**WBS:** 5.1  
**Labels:** `backend`, `metrics`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #12

### Description

System health metrics endpoint using `psutil`.

### Response schema

```json
{
  "cpu_percent": 12.5,
  "ram_used_mb": 512,
  "ram_total_mb": 4096,
  "ram_percent": 12.5,
  "cpu_temp_celsius": 52.3,
  "disk_used_gb": 8.2,
  "disk_total_gb": 32.0,
  "disk_percent": 25.6,
  "timestamp": "2026-05-31T14:30:00Z"
}
```

### Tasks

- [ ] Use `psutil` for all metrics
- [ ] CPU temperature: read from `/sys/class/thermal/thermal_zone0/temp` (Raspberry Pi) with fallback to `psutil.sensors_temperatures()`
- [ ] Cache result for 5 seconds to avoid hammering the system on frequent polling
- [ ] Return null for values that cannot be read (e.g. temp unavailable on some hardware) rather than erroring

### Acceptance Criteria

- [ ] All fields present in response on Raspberry Pi 4
- [ ] `cpu_temp_celsius` returns a value between 30 and 90 on a real device
- [ ] Response time < 200 ms (including cache hit scenario)

---

## Issue #22 — Implement GET /api/metrics/traffic

**WBS:** 5.2  
**Labels:** `backend`, `metrics`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #12

### Description

Return total bytes transferred by Conduit since the service last started.

### Tasks

- [ ] Investigate where Conduit records transfer stats (log file, metrics file, or CLI query)
- [ ] Document the source in `docs/conduit-metrics-source.md`
- [ ] Parse the source and return structured data

### Response schema

```json
{
  "bytes_sent": 1073741824,
  "bytes_received": 2147483648,
  "session_start": "2026-05-31T08:00:00Z",
  "timestamp": "2026-05-31T14:30:00Z"
}
```

### Acceptance Criteria

- [ ] Returns non-zero values when Conduit has been running and transferring data
- [ ] `bytes_sent` and `bytes_received` increase on subsequent calls while Conduit is active

---

## Issue #23 — Implement GET /api/logs with redaction

**WBS:** 5.3  
**Labels:** `backend`, `logs`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #12

### Description

Return the last N lines of the Conduit service log. Redact any sensitive patterns before returning.

### Tasks

- [ ] Read from `journalctl -u conduit -n {limit} --no-pager --output=short-iso` or the Conduit log file
- [ ] Default `limit=200`; accept `?limit=N` query parameter (max 1000)
- [ ] Redact patterns before returning: Psiphon pairing link URLs, any string matching `psi://` or `psiphon://`
- [ ] Return JSON array of log line objects `{timestamp, level, message}`

### Acceptance Criteria

- [ ] Returns 200 lines by default
- [ ] Any line containing a pairing link pattern is replaced with `[REDACTED]`
- [ ] Response is valid JSON

---

## Issue #24 — Build base CSS layout and JS fetch wrapper

**WBS:** 6.1  
**Labels:** `frontend`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #12

### Description

Create the shared frontend foundation. All other frontend issues depend on this.

### Tasks

- [ ] `frontend/static/css/base.css`: CSS custom properties (colour palette, spacing, type scale), responsive grid, button styles, form styles, badge/status styles, toast notification styles
- [ ] `frontend/static/js/api.js`: `apiFetch(path, options)` wrapper — adds CSRF token header, handles 401 redirect, handles errors with toast notification
- [ ] `frontend/static/js/app.js`: polling manager (`startPolling(fn, interval)`, `stopPolling()`), page init helpers
- [ ] No external CSS frameworks; no external JS libraries for v0.1

### Acceptance Criteria

- [ ] Dashboard renders correctly at 768px, 1024px, and 1440px viewport widths
- [ ] `apiFetch` on a 401 response redirects to `/login`
- [ ] Network error shows a toast notification

---

## Issue #25 — Build login page

**WBS:** 6.2  
**Labels:** `frontend`, `auth`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #24, #14

### Description

Build `frontend/templates/login.html` — the unauthenticated entry point.

### Tasks

- [ ] Username and password fields with proper `autocomplete` attributes
- [ ] Client-side validation: both fields required
- [ ] Submit calls `POST /api/auth/login`; on success redirects to `/dashboard` (or `next` param)
- [ ] On failure: display error message below the form (do not clear the username field)
- [ ] On lockout (HTTP 429): display "Account locked. Try again in X minutes." using the `Retry-After` header
- [ ] Page is usable without JavaScript (progressive enhancement: plain HTML form POST)

### Acceptance Criteria

- [ ] Login succeeds with correct credentials and redirects to the dashboard
- [ ] Login fails gracefully with incorrect credentials (no page crash)
- [ ] Lockout message shows correct remaining time

---

## Issue #26 — Build dashboard shell and navigation

**WBS:** 6.3  
**Labels:** `frontend`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #24

### Description

Build the main application shell that all dashboard panels live inside.

### Tasks

- [ ] `frontend/templates/dashboard.html`: sidebar navigation, page header (project name + version), main content area, logout button
- [ ] Navigation items: Overview, Logs, Settings
- [ ] Active state styling for current page
- [ ] Logout button: calls `POST /api/auth/logout`; redirects to login page
- [ ] Page title updates to reflect current section

### Acceptance Criteria

- [ ] Navigation links work correctly
- [ ] Logout clears the session and returns to the login page
- [ ] Layout does not break at 768px

---

## Issue #27 — Build node status and control panel

**WBS:** 6.4  
**Labels:** `frontend`, `conduit`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #26, #18, #19

### Description

The most important UI component: lets the user see and control their Conduit node.

### Tasks

- [ ] Status badge with colour coding: green (running), red (stopped), orange (starting/stopping), grey (error)
- [ ] "Last changed" timestamp in human-readable relative format (e.g. "3 minutes ago")
- [ ] Start, Stop, Restart buttons
- [ ] Stop and Restart show a confirmation dialog before calling the API
- [ ] Buttons show a spinner while the action is in progress
- [ ] Buttons are disabled while an action is in progress
- [ ] Status polls `GET /api/status` every 5 seconds via `startPolling`

### Acceptance Criteria

- [ ] Status badge updates within 6 seconds of the actual service state change
- [ ] Clicking Stop shows a confirmation dialog; cancelling does not stop the service
- [ ] Spinner appears during the action; buttons re-enable after the action completes

---

## Issue #28 — Build system health panel

**WBS:** 6.5  
**Labels:** `frontend`, `metrics`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #26, #21

### Description

Display live system metrics in an at-a-glance panel.

### Tasks

- [ ] Four metric cards: CPU %, RAM %, CPU temperature, Disk %
- [ ] Each card shows current value + a simple horizontal bar
- [ ] Colour thresholds: green (normal), amber (warning), red (critical) — based on SRS FR-METRICS-03 values
- [ ] Polls `GET /api/metrics/system` every 10 seconds
- [ ] If a metric value is null (unavailable), show "N/A" gracefully

### Acceptance Criteria

- [ ] All four metrics display and update without a page reload
- [ ] CPU temperature card shows amber when temp > 70°C and red when > 80°C

---

## Issue #29 — Build traffic counter widget

**WBS:** 6.6  
**Labels:** `frontend`, `metrics`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #26, #22

### Description

Simple widget showing cumulative bytes transferred this session.

### Tasks

- [ ] Display bytes sent and bytes received in human-readable format (KB/MB/GB auto-scaled)
- [ ] Show session start time
- [ ] Polls `GET /api/metrics/traffic` every 30 seconds

### Acceptance Criteria

- [ ] Values display in a human-readable unit (not raw bytes)
- [ ] Widget shows "No data" gracefully when Conduit has not been running

---

## Issue #30 — Build log viewer panel

**WBS:** 6.7  
**Labels:** `frontend`, `logs`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #26, #23

### Description

Show the last 200 lines of Conduit logs in a readable, scrollable panel.

### Tasks

- [ ] Monospace scrollable container showing log lines
- [ ] Each line styled by level: INFO (default), WARNING (amber), ERROR (red)
- [ ] Manual "Refresh" button
- [ ] Auto-refresh every 30 seconds (pauses if user has scrolled up)
- [ ] Scroll-to-bottom button appears when not at the bottom
- [ ] `[REDACTED]` lines displayed in a muted grey style

### Acceptance Criteria

- [ ] Last 200 log lines display on page load
- [ ] ERROR lines are visually distinct
- [ ] No pairing link ever appears in the rendered log view

---

## Issue #31 — Build settings page (change password)

**WBS:** 6.8  
**Labels:** `frontend`, `auth`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #26, #14

### Description

Build the settings page with the change password form.

### Tasks

- [ ] `PUT /api/settings/password` endpoint: accept `{current_password, new_password, confirm_password}`; verify current; update hash; invalidate all existing sessions
- [ ] Frontend form: current password, new password, confirm new password fields
- [ ] Client-side validation: new password == confirm, minimum length 10 characters
- [ ] On success: show "Password changed. Please log in again." and redirect to login
- [ ] Session timeout display (read from config; edit deferred to v1.1)

### Acceptance Criteria

- [ ] Wrong current password returns 401 and shows an error
- [ ] After a successful password change, the old session is invalid
- [ ] Password mismatch is caught client-side before the API call

---

## Issue #32 — Add security headers to Nginx config

**WBS:** 7.1  
**Labels:** `security`, `infrastructure`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #7

### Description

Add all required security response headers to the Nginx virtual host.

### Headers to add (SRS SEC-NET-03, SEC-TLS-03)

- [ ] `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- [ ] `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'`
- [ ] `X-Frame-Options: DENY`
- [ ] `X-Content-Type-Options: nosniff`
- [ ] `Referrer-Policy: no-referrer`
- [ ] `Permissions-Policy: geolocation=(), camera=(), microphone=()`

### Acceptance Criteria

- [ ] `curl -I https://yourdomain.com` shows all six headers
- [ ] [securityheaders.com](https://securityheaders.com) scan returns grade A or better

---

## Issue #33 — Implement CSRF protection

**WBS:** 7.2  
**Labels:** `security`, `backend`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #16

### Description

Protect all state-changing endpoints against Cross-Site Request Forgery using the double-submit cookie pattern.

### Tasks

- [ ] On login, set a `csrf_token` cookie (not HttpOnly; must be readable by JS; Secure, SameSite=strict)
- [ ] All `POST`, `PUT`, `DELETE` API requests must include the `X-CSRF-Token` header matching the cookie value
- [ ] `apiFetch` (Issue #24) must automatically read the cookie and add the header
- [ ] Backend middleware validates the header against the cookie on every state-changing request; returns HTTP 403 on mismatch

### Acceptance Criteria

- [ ] A POST request without the `X-CSRF-Token` header returns 403
- [ ] A POST request with a mismatched token returns 403
- [ ] Normal frontend operations work without any user action required

---

## Issue #34 — Add login rate limiting in Nginx

**WBS:** 7.3  
**Labels:** `security`, `infrastructure`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #7

### Description

Rate-limit the login endpoint to slow down brute-force attacks at the network layer (SRS SEC-NET-02).

### Tasks

- [ ] Define `limit_req_zone` in Nginx config (zone based on `$binary_remote_addr`, rate `10r/s`)
- [ ] Apply `limit_req` to `location /api/auth/login`
- [ ] Return 429 with `Retry-After: 1` header when limit is exceeded
- [ ] Ensure legitimate login (single request) is not affected

### Acceptance Criteria

- [ ] 15 rapid POST requests to `/api/auth/login` from one IP results in 429 responses after the 10th
- [ ] A single login request succeeds immediately

---

## Issue #35 — Audit Pydantic model coverage for all API inputs

**WBS:** 7.4  
**Labels:** `security`, `backend`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #17, #21, #22, #23

### Description

Security review: verify that every API endpoint validates its inputs with a Pydantic model and that no raw request data reaches the adapter, filesystem, or database.

### Tasks

- [ ] List every endpoint and its input source (path params, query params, body)
- [ ] Confirm each has a Pydantic model with appropriate field constraints (min/max length, regex patterns)
- [ ] Verify `log` endpoint `limit` parameter has a maximum (e.g. 1000)
- [ ] Verify pairing endpoint validates link format before passing to adapter
- [ ] Verify no endpoint uses `model.dict()` or `**kwargs` to pass raw input to subprocess calls
- [ ] Document any gaps found and fix them

### Acceptance Criteria

- [ ] All endpoints listed in FR-API-02 (Issue #18–#23, #31) have explicit Pydantic input models
- [ ] No `shell=True` subprocess calls anywhere in the codebase

---

## Issue #36 — Write backend unit tests (≥60% coverage)

**WBS:** 8.1  
**Labels:** `testing`  
**Milestone:** v0.1  
**Effort:** 2 days  
**Depends on:** #14, #15, #17, #21, #23

### Description

Write unit tests for all backend modules. Mocking strategy: mock `subprocess` calls (Conduit adapter), `psutil` (metrics), and filesystem reads (logs).

### Test modules to cover

- [ ] `tests/unit/test_auth.py` — login success/failure, bcrypt, lockout, session create/expire/delete
- [ ] `tests/unit/test_session.py` — session CRUD, expiry, purge
- [ ] `tests/unit/test_adapter.py` — `get_status()`, `start()`, `stop()`, `restart()` with mocked subprocess
- [ ] `tests/unit/test_metrics.py` — system metrics with mocked psutil; null handling for unavailable values
- [ ] `tests/unit/test_logs.py` — last N lines, redaction of pairing link patterns
- [ ] `tests/unit/test_pairing.py` — verify pairing link is never written to any log or file

### Acceptance Criteria

- [ ] `pytest tests/unit/ --cov=backend` reports ≥60% coverage
- [ ] All tests pass with no warnings
- [ ] Coverage report is uploaded as a CI artefact

---

## Issue #37 — Write API integration tests

**WBS:** 8.2  
**Labels:** `testing`  
**Milestone:** v0.1  
**Effort:** 1.5 days  
**Depends on:** #36

### Description

End-to-end tests against the running FastAPI app using `httpx` and `pytest`.

### Test scenarios

- [ ] Unauthenticated access to protected endpoints returns 401
- [ ] Login with correct credentials sets session cookie
- [ ] Login with wrong credentials returns 401 (does not leak which field was wrong)
- [ ] 5 failed logins trigger lockout; 6th returns 429
- [ ] Logout invalidates session; subsequent request returns 401
- [ ] `GET /api/status` returns correct schema
- [ ] `POST /api/conduit/start` returns correct schema (adapter mocked)
- [ ] `GET /api/metrics/system` returns all required fields
- [ ] `GET /api/logs` with and without `limit` parameter
- [ ] CSRF: POST without token returns 403
- [ ] `PUT /api/settings/password` — success and failure cases

### Acceptance Criteria

- [ ] All integration tests pass in CI with mocked Conduit adapter
- [ ] No test depends on a running Conduit service or real hardware

---

## Issue #38 — Test install.sh on Raspberry Pi 4 hardware

**WBS:** 8.3  
**Labels:** `testing`, `infrastructure`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #9

### Description

Manual validation of the install script on real Raspberry Pi 4 hardware. Automated tests cannot fully replace this step.

### Test procedure

- [ ] Flash a fresh Ubuntu 22.04 ARM64 image to SD card
- [ ] Boot the Pi; run `install.sh` and record the full output
- [ ] Verify: `systemctl status conduit-cc` shows `active (running)`
- [ ] Verify: HTTPS dashboard is accessible at the configured domain
- [ ] Verify: Login works with the password set during install
- [ ] Verify: Node status panel shows correct state
- [ ] Verify: System health panel shows real CPU temperature
- [ ] Verify: Re-running `install.sh` is idempotent (no errors, no data loss)
- [ ] Document any issues found; fix them; repeat until clean run

### Acceptance Criteria

- [ ] Install script runs to completion on a fresh image with no manual intervention
- [ ] All 10 v0.1 in-scope features from SRS Section 3.2 pass their acceptance criteria

---

## Issue #39 — Write README.md

**WBS:** 9.1  
**Labels:** `docs`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #9

### Description

Write the primary project README that serves as the homepage on GitHub.

### Contents (SRS OSS-DOC-01)

- [ ] Project description (1–2 paragraphs, plain English)
- [ ] Feature list for v0.1
- [ ] Screenshot placeholder (or actual screenshot if available)
- [ ] Prerequisites section
- [ ] Quick install instructions (3-step: clone, run install.sh, open browser)
- [ ] Link to full TLS setup guide
- [ ] Badges: CI status, licence (MIT), GitHub Issues
- [ ] Link to CONTRIBUTING.md
- [ ] Link to SECURITY.md
- [ ] Known limitations section (what v0.1 does not do yet)

### Acceptance Criteria

- [ ] A new user can install the dashboard by following only the README
- [ ] All badge links resolve correctly

---

## Issue #40 — Write CONTRIBUTING.md, SECURITY.md, and CODE_OF_CONDUCT.md

**WBS:** 9.2–9.3  
**Labels:** `docs`  
**Milestone:** v0.1  
**Effort:** 1 day  
**Depends on:** #4

### Description

Write the community health files required for a public open-source release.

### CONTRIBUTING.md (SRS OSS-DOC-02)

- [ ] Development environment setup (link to `docs/dev-setup.md`)
- [ ] Coding standards: PEP 8, ruff, no build tools in frontend
- [ ] Commit message format: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`)
- [ ] Branch naming: `feature/`, `fix/`, `docs/`
- [ ] Pull request checklist
- [ ] How to run tests
- [ ] How to report a bug

### SECURITY.md (SRS OSS-DOC-03)

- [ ] Supported versions table
- [ ] Vulnerability disclosure email address
- [ ] Expected response SLA (acknowledge within 7 days, fix within 30 days for critical)
- [ ] What NOT to do (do not open a public GitHub issue for security vulnerabilities)

### CODE_OF_CONDUCT.md

- [ ] Contributor Covenant v2.1 (full text)
- [ ] Enforcement contact email

### CHANGELOG.md

- [ ] v0.1.0 entry with all implemented features listed
- [ ] "Known Limitations" subsection listing deferred features

### Acceptance Criteria

- [ ] All four files exist and are linked from README.md
- [ ] CONTRIBUTING.md allows a new contributor to set up a dev environment and open a PR without asking questions

---

*End of GitHub Issues — Conduit Control Center v0.1*

---

## Issue #41 — Integrate Cloudflare DDNS script and cron job

**WBS:** 2.7  
**Labels:** `infrastructure`, `security`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #9

### Description

The Raspberry Pi 4 is on a home internet connection with a dynamic public IP. `cloudflare-ddns.sh` is the mechanism that keeps the Cloudflare A record for the dashboard hostname pointing at the current IP. Without it working correctly, the dashboard becomes unreachable whenever the ISP reassigns the IP — silently and without warning.

**This script uses Script B behaviour**: it reads the current proxy status from the Cloudflare API and preserves it unchanged when updating the IP. The user controls the orange/grey cloud from the Cloudflare dashboard; the script never overrides that choice.

### Script behaviour

```
1. Read CF_API_TOKEN, CF_ZONE_NAME, CF_RECORD_NAME from /etc/conduit-cc/.env
2. Get current public IP from https://api.ipify.org
3. Get Zone ID via Cloudflare API (GET /zones?name={CF_ZONE_NAME})
4. Get current DNS record: ID, current IP, proxy status (GET /dns_records?name={CF_RECORD_NAME})
5. If current IP == API IP → log {result: "no_change"} and exit 0
6. If current IP != API IP → PUT /dns_records/{RECORD_ID} with new IP, preserving proxy status
7. Log {result: "updated" or "error"} as JSON to /var/log/conduit-cc/ddns.log
```

### Tasks

- [ ] Save script as `scripts/cloudflare-ddns.sh`
- [ ] Read all configuration from `/etc/conduit-cc/.env` — no hardcoded values
- [ ] Structured JSON log output appended to `/var/log/conduit-cc/ddns.log`:
  ```json
  {"timestamp":"2026-05-31T14:30:00Z","ip":"1.2.3.4","result":"updated","message":"A record updated"}
  {"timestamp":"2026-05-31T14:35:00Z","ip":"1.2.3.4","result":"no_change","message":"IP unchanged"}
  {"timestamp":"2026-05-31T14:40:00Z","ip":null,"result":"error","message":"Unable to retrieve public IP"}
  ```
- [ ] Add to `.env.example`:
  ```bash
  # Cloudflare DDNS — requires Zone:DNS:Edit + Zone:Zone:Read permissions
  CF_API_TOKEN=your_cloudflare_api_token_here
  CF_ZONE_NAME=yourdomain.com
  CF_RECORD_NAME=conduit.yourdomain.com
  ```
- [ ] `install.sh` Phase 2j: install cron job as `conduit-cc` user: `*/5 * * * *`
- [ ] `install.sh` Phase 2j: run script once immediately after cron install; if it fails print a warning and the log path, but do not abort the install
- [ ] Add logrotate config: `/var/log/conduit-cc/ddns.log` weekly, 4 rotations, compress
- [ ] Verify `curl` and `jq` are in the `apt install` list in `install.sh` (Phase 1c)
- [ ] Script passes `shellcheck` with no errors

### Security requirements

- [ ] `CF_API_TOKEN` is never printed, echoed, or logged — not even partially
- [ ] Script sources `.env` with `set -a; source /etc/conduit-cc/.env; set +a` so the token stays in environment, not command arguments
- [ ] API token should have **Zone:DNS:Edit + Zone:Zone:Read** on the specific zone only — document this in `.env.example` comments

### Acceptance Criteria

- [ ] `grep -r "CF_API_TOKEN=" /opt/conduit-cc/scripts/` returns only the variable reference, never a real value
- [ ] `crontab -l -u conduit-cc` shows the `*/5 * * * *` entry after install
- [ ] `/var/log/conduit-cc/ddns.log` contains a valid JSON line after the first cron run
- [ ] The Cloudflare A record for `CF_RECORD_NAME` shows the Pi's current public IP
- [ ] Re-running the script with the same IP produces `result: "no_change"` — no unnecessary API calls
- [ ] Script preserves proxy status: if the record has `proxied: true`, it stays `true` after an IP update
- [ ] `shellcheck scripts/cloudflare-ddns.sh` returns exit code 0

---

## Issue #42---

## Issue #42 — Implement GET /api/ddns/status endpoint

**WBS:** 5.4  
**Labels:** `backend`, `metrics`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #12, #41

### Description

Parse the DDNS log file and expose the last known DDNS state to the frontend. This lets the dashboard surface a DDNS failure before the user notices the dashboard is unreachable (e.g. after a brief ISP reconnect while they were away).

### Response schema

```json
{
  "hostname": "conduit.rockysystem.net",
  "current_ip": "1.2.3.4",
  "last_updated": "2026-05-31T14:30:00Z",
  "last_result": "updated",
  "last_message": "A record updated successfully",
  "consecutive_errors": 0
}
```

### Tasks

- [ ] Read the last 50 lines of `/var/log/conduit-cc/ddns.log`
- [ ] Parse each line as a JSON object; skip malformed lines gracefully
- [ ] Extract the most recent entry's `timestamp`, `ip`, `result`, `message`
- [ ] Count consecutive `error` results from the end of the log (resets on `updated` or `no_change`)
- [ ] Return `CF_RECORD_NAME` from config as `hostname`
- [ ] If log file does not exist or is empty, return `last_result: "unknown"` with nulls for other fields — do not error
- [ ] Cache response for 30 seconds

### Acceptance Criteria

- [ ] Returns correct `current_ip` after a successful DDNS update
- [ ] `consecutive_errors` increments correctly when multiple error entries are at the end of the log
- [ ] Returns 200 with `last_result: "unknown"` on a fresh install before the first cron run
- [ ] Returns 401 without a valid session

---

## Issue #43 — Build DDNS status panel on dashboard

**WBS:** 6.9  
**Labels:** `frontend`, `metrics`  
**Milestone:** v0.1  
**Effort:** 0.5 day  
**Depends on:** #26, #42

### Description

A compact status panel on the dashboard Overview page that shows the health of the DDNS update mechanism. For a home-hosted Pi, a broken DDNS silently makes the dashboard unreachable — surfacing this in the UI prevents confusion.

### Tasks

- [ ] Display: hostname, current public IP, last update time (relative: "2 minutes ago"), last result badge
- [ ] Result badge colours:
  - `updated` → green ("Updated")
  - `no_change` → blue ("No change needed")
  - `error` → red ("Update failed")
  - `unknown` → grey ("Not yet run")
- [ ] If `consecutive_errors >= 3`: show a prominent warning banner: "DDNS has failed 3+ consecutive times. Your dashboard may become unreachable. Check `/var/log/conduit-cc/ddns.log`."
- [ ] Polls `GET /api/ddns/status` every 60 seconds (much slower than system metrics — DDNS only updates every 5 min)
- [ ] Tooltip on the IP address showing: "This is the public IP Cloudflare is currently pointing to. If your ISP changes your IP and DDNS fails, this value will be stale."

### Acceptance Criteria

- [ ] Panel renders correctly with each of the four `last_result` states
- [ ] Warning banner appears when `consecutive_errors >= 3`
- [ ] Panel shows "Not yet run" gracefully on a fresh install
- [ ] IP address and hostname are displayed without any JavaScript errors when the API returns nulls

---

*End of GitHub Issues — Conduit Control Center v0.1 (Cloudflare DDNS additions)*
