# Developer Setup — Conduit Control Center

> **Goal:** get the development server running on your machine in under 10 minutes.
>
> This guide covers local development only. For a production installation on a
> Raspberry Pi, see [docs/pre-install.md](pre-install.md) and run `install.sh`.

---

## Prerequisites

### Required

| Tool | Minimum version | Check |
|---|---|---|
| Python | 3.10 | `python3 --version` |
| git | any recent | `git --version` |
| curl | any | `curl --version` |
| shellcheck | any | `shellcheck --version` |

**Python 3.10, 3.11, and 3.12** are all tested in CI. Python 3.13 is not yet
validated.

Install on Ubuntu 22.04:

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv git curl shellcheck
```

> **macOS:** `brew install python@3.12 shellcheck` (WSL2 on Windows works the
> same as Ubuntu.)

### Not required for local development

- Nginx — the dev server runs directly on `http://127.0.0.1:8000`
- Cloudflare credentials — DDNS and DNS validation are only used by `install.sh`
- A Conduit binary — all Conduit adapter functions degrade gracefully when the
  binary is absent; the dashboard renders fully without it

---

## 1. Clone the repository

If you plan to contribute, fork the repository on GitHub first, then clone your
fork:

```bash
git clone https://github.com/YOUR_USERNAME/conduit-control-center.git
cd conduit-control-center

# Add the upstream remote so you can pull future changes
git remote add upstream https://github.com/kiavash599/conduit-control-center.git
```

If you only want to read the code or run the tests locally, clone directly:

```bash
git clone https://github.com/kiavash599/conduit-control-center.git
cd conduit-control-center
```

---

## 2. Create a virtual environment

A virtual environment keeps the project's Python packages isolated from the rest
of your system. Always activate it before working on the project.

```bash
# Create the environment (one time only)
python3 -m venv venv

# Activate it (every terminal session)
source venv/bin/activate
```

Your prompt will change to show `(venv)` when the environment is active. To
deactivate, run `deactivate`.

---

## 3. Install dependencies

```bash
# Production dependencies (FastAPI, uvicorn, pydantic, bcrypt, etc.)
pip install -r requirements.txt

# Development-only dependencies (pytest, ruff, pip-audit, httpx, etc.)
pip install -r requirements-dev.txt
```

---

## 4. Configure the environment

The application reads secrets and settings from a `.env` file at the project
root. **Three fields must be set before the server will start and accept logins.**

### Step 4a — Copy the example file

```bash
cp .env.example .env
```

### Step 4b — Set `SESSION_SECRET`

The example file contains a placeholder value that the application **explicitly
rejects at startup**. You must replace it before running the server.

Generate a real secret (run this once and paste the output into `.env`):

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Open `.env` and replace the `SESSION_SECRET` line:

```
SESSION_SECRET=<paste the output here>
```

### Step 4c — Set `ADMIN_PASSWORD_HASH`

Without this value the server starts, but every login attempt returns HTTP 503.
Generate a bcrypt hash for a local development password:

```bash
python3 -c "
import bcrypt
pw = b'devpassword123'
print(bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode())
"
```

Paste the output (`$2b$12$...`) into `.env`:

```
ADMIN_PASSWORD_HASH=<paste the hash here>
ADMIN_USERNAME=admin
```

The username defaults to `admin`. You can leave it as-is for local development.

### Step 4d — Disable secure cookies

Session cookies are marked `Secure` by default, which means a browser running
over plain HTTP refuses to store or send them. This causes every login to loop
back to `/login` even after a correct password is entered.

In `.env`, set:

```
SECURE_COOKIES=false
```

### Complete minimal `.env` for local development

```
SESSION_SECRET=<your generated secret>
ADMIN_PASSWORD_HASH=<your generated hash>
ADMIN_USERNAME=admin
SECURE_COOKIES=false
LOG_LEVEL=DEBUG
```

All other variables in `.env.example` are optional for local development. The
Cloudflare fields (`CF_API_TOKEN`, `CF_ZONE_NAME`, `CF_RECORD_NAME`) can be
left blank or set to placeholder values — they are only used by
`scripts/cloudflare-ddns.sh`, which does not run during normal development.

---

## 5. Run the development server

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

`--reload` restarts the server automatically whenever you save a Python file.

**Expected startup output:**

```
INFO:     Will watch for changes in these directories: ['/path/to/conduit-control-center']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [...]
INFO:     2026-...[INFO] backend.main: Conduit Control Center v0.1.0 starting up
INFO:     2026-...[INFO] backend.main: Startup session purge: 0 expired session(s) removed
INFO:     2026-...[INFO] backend.main: Startup complete -- listening on port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser and log in
with `admin` and the password you hashed in Step 4c.

**Database:** `ccc.db` is created automatically at the project root on first
startup. It is listed in `.gitignore` and does not affect other contributors.
There is no migration step — the schema is applied with `CREATE TABLE IF NOT
EXISTS` on every startup.

---

## 6. Run the test suite

The tests do **not** require the development server to be running. They use an
in-memory SQLite database and mock all external dependencies (systemctl,
psutil, Conduit binary).

```bash
# Run all tests (unit + integration)
pytest

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/

# Run with coverage report (shows which lines are not covered)
pytest tests/unit/ --cov=backend --cov-report=term-missing

# Enforce the minimum coverage threshold (same as CI)
pytest tests/unit/ --cov=backend --cov-report=term-missing --cov-fail-under=60
```

Coverage must stay at or above **60%** for the `tests/unit/` run. This is
enforced in CI and will cause a pipeline failure if it drops below threshold.

---

## 7. Lint and check code quality

All code must pass `ruff` before committing. CI runs `ruff check .` and will
fail if any violations are present.

```bash
# Check for linting violations
ruff check .

# Auto-fix violations that ruff can resolve automatically
ruff check . --fix

# Format code (applies ruff's formatter)
ruff format .

# Check formatting without making changes
ruff format . --check
```

---

## 8. Check shell scripts

All shell scripts must pass `shellcheck` with zero errors. CI checks
`install.sh`, `uninstall.sh`, `update.sh`, and all files in `scripts/`.

```bash
shellcheck install.sh uninstall.sh update.sh scripts/cloudflare-ddns.sh
```

Also verify syntax with bash's built-in parser:

```bash
bash -n install.sh
bash -n uninstall.sh
bash -n update.sh
```

If `shellcheck` is not installed:

```bash
sudo apt install shellcheck
```

---

## Repository structure

```
conduit-control-center/
│
├── backend/                   FastAPI application (Python)
│   ├── api/                   Route handlers — auth, conduit, ddns, health,
│   │                          logs, metrics, settings, status
│   ├── auth/                  Session store, cookies, lockout, login logic
│   ├── conduit/               Conduit adapter — systemctl wrapper
│   ├── _version.py            Single source of truth for APP_VERSION
│   ├── config.py              Settings (from .env) and AppConfig (from config.json)
│   ├── database.py            SQLite schema and async connection helpers
│   ├── dependencies.py        FastAPI dependencies — auth, CSRF, database
│   ├── main.py                Application factory — routers, lifespan, static files
│   └── pages.py               HTML page routes (Jinja2 templates)
│
├── frontend/
│   ├── static/
│   │   ├── css/base.css       All styles — CSS custom properties for theming
│   │   └── js/                One file per page (api.js, dashboard.js, etc.)
│   └── templates/             Jinja2 HTML templates (base.html + per-page)
│
├── scripts/
│   ├── cloudflare-ddns.sh     DDNS updater — run by cron every 5 minutes
│   └── ccc-unlock             Admin lockout reset utility (Python)
│
├── deployment/
│   ├── conduit-cc.nginx       Nginx virtual host template
│   ├── conduit-cc.service     systemd unit for the FastAPI app
│   └── conduit.service        Reference unit for the Psiphon Conduit binary
│
├── tests/
│   ├── unit/                  Unit tests — mock all external dependencies
│   └── integration/           Integration tests — real FastAPI TestClient
│
├── docs/                      User and developer documentation
├── .env.example               Template for required environment variables
├── config.example.json        Template for application configuration (config.json)
├── requirements.txt           Production Python dependencies
├── requirements-dev.txt       Development and CI Python dependencies
├── install.sh                 Automated installer for Ubuntu 22.04 ARM64
├── uninstall.sh               Clean removal script
└── update.sh                  In-place upgrade script with automatic rollback
```

**No build step.** The frontend is plain HTML5, CSS3, and vanilla JavaScript.
There is no npm, webpack, or transpilation step.

---

## Development workflow

### Branch strategy

Always branch from `develop`, never from `main`.

```bash
# Start from an up-to-date develop branch
git fetch upstream
git checkout develop
git merge upstream/develop

# Create your working branch
git checkout -b feature/your-feature-name   # new feature
git checkout -b fix/your-bug-description    # bug fix
git checkout -b docs/your-doc-description   # documentation
```

### Commit message format

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary in imperative mood>

[optional body explaining the why]

Closes #<issue-number>
```

Common types: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `security`.

Examples:

```
feat(auth): implement bcrypt password hashing on login

docs(dev-setup): add troubleshooting section for cookie errors

Closes #5
```

### Pre-commit checklist

Before pushing a branch, verify all of the following pass locally:

```bash
ruff check .                          # no linting violations
pytest                                # all tests pass
pytest tests/unit/ --cov=backend \
  --cov-fail-under=60                 # coverage above threshold
shellcheck install.sh uninstall.sh \
  update.sh scripts/cloudflare-ddns.sh  # no shellcheck warnings
```

CI runs the same checks and will fail if any of these are broken.

For full contribution guidelines — PR process, coding standards, review
checklist — see [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Troubleshooting

### Server won't start: `SESSION_SECRET` validation error

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
  Value error, SESSION_SECRET is still set to the placeholder value.
```

**Cause:** You copied `.env.example` to `.env` but did not replace
`SESSION_SECRET`.

**Fix:** Generate a real value and update `.env`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

### Login returns HTTP 503

The server starts successfully but submitting the login form returns a server
error.

**Cause:** `ADMIN_PASSWORD_HASH` is empty or not set in `.env`.

**Fix:** Generate a hash and add it to `.env` (see [Step 4c](#step-4c--set-admin_password_hash)).

---

### Login loops back to `/login` after correct password

You enter the correct username and password, the form submits, but the browser
immediately redirects to `/login` again with no error message.

**Cause:** `SECURE_COOKIES=true` (the default). Browsers reject `Secure`
cookies over plain HTTP.

**Fix:** Add `SECURE_COOKIES=false` to `.env`.

---

### "No such table" errors on startup

This should not happen because tables are created automatically. If it does:

```bash
# Delete the local database and let it be recreated on next startup
rm -f ccc.db
```

---

### Import errors after pulling new changes

A new dependency may have been added to `requirements.txt` or
`requirements-dev.txt`.

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

---

### Port 8000 is already in use

```
ERROR:    [Errno 98] Address already in use
```

Find and stop whatever is using the port:

```bash
lsof -i :8000
# then: kill <PID>
```

Or run on a different port:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
```

---

### `shellcheck` not found

```bash
sudo apt install shellcheck          # Ubuntu / Debian
brew install shellcheck              # macOS
```

---

### `ruff` not found

Make sure the virtual environment is activated (`source venv/bin/activate`) and
that you ran `pip install -r requirements-dev.txt`.

---

## Next steps

- **[CONTRIBUTING.md](../CONTRIBUTING.md)** — full coding standards, PR
  process, and review checklist
- **[docs/pre-install.md](pre-install.md)** — Cloudflare dashboard setup for a
  production deployment
- **[docs/tls-setup.md](tls-setup.md)** — TLS certificate configuration
- **Architecture overview** — planned for a future release; will be at
  `docs/architecture.md` when published
- **API reference** — planned for a future release; will be at
  `docs/api-reference.md` when published
- **[GitHub Discussions](https://github.com/kiavash599/conduit-control-center/discussions)**
  — questions and ideas welcome
