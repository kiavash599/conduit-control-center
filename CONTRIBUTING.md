# Contributing to Conduit Control Center

Thank you for your interest in contributing. This project is intentionally designed to be approachable — whether you are learning Linux and Python for the first time or are an experienced developer.

If you have a question that is not answered here, open a [Discussion](https://github.com/kiavash599/conduit-control-center/discussions) rather than an issue. Discussions are for questions and ideas; Issues are for bugs and concrete feature requests tied to the approved roadmap.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Before You Start](#before-you-start)
3. [Development Environment](#development-environment)
4. [Project Structure](#project-structure)
5. [Making Changes](#making-changes)
6. [Coding Standards](#coding-standards)
7. [Documentation Authoring (RTL/LTR Style Guide)](#documentation-authoring-rtlltr-style-guide-v10)
8. [Commit Messages](#commit-messages)
9. [Pull Request Process](#pull-request-process)
10. [Reporting Bugs](#reporting-bugs)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating you agree to uphold it.

---

## Before You Start

- Check the [open issues](https://github.com/kiavash599/conduit-control-center/issues) to see if someone is already working on the same thing.
- For new features, open an issue first and wait for acknowledgement before writing code. The project scope is defined in the approved SRS v1.1 — features outside that scope will not be merged in the current release cycle.
- For bug fixes, you can proceed directly to a pull request. Include a regression test.

---

## Development Environment

Full instructions are in [docs/dev-setup.md](docs/dev-setup.md). The short version:

### Prerequisites

- Python 3.10 or higher (`python3 --version`)
- Git
- Linux or macOS (WSL2 works on Windows)
- `curl` and `jq` (needed for the DDNS script)

### Setup

```bash
# 1. Fork the repository on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/conduit-control-center.git
cd conduit-control-center

# 2. Add the upstream remote
git remote add upstream https://github.com/kiavash599/conduit-control-center.git

# 3. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows (WSL2): source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 5. Copy the example environment file and fill in your values
cp .env.example .env
# Edit .env — the comments in the file explain each variable

# 6. Start the development server
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The dashboard will be available at `http://127.0.0.1:8000`.

### Running the Test Suite

```bash
# Run all tests
pytest

# Run unit tests only
pytest tests/unit/

# Run with coverage report (must stay at or above 60%)
pytest --cov=backend --cov-report=term-missing

# Lint (must pass before committing)
ruff check .

# Check shell scripts
shellcheck scripts/*.sh install.sh uninstall.sh update.sh
```

---

## Project Structure

```
conduit-control-center/
├── backend/        FastAPI application (Python) — authentication, API, Conduit adapter, metrics
├── frontend/       HTML, CSS, JavaScript — no build step, no frameworks
├── scripts/        Shell scripts — cloudflare-ddns.sh, ccc-unlock
├── deployment/     Nginx config, systemd unit, logrotate config
├── docs/           User and developer documentation
├── tests/
│   ├── unit/       Unit tests for backend modules
│   └── integration/ Integration tests for API endpoints
├── .env.example    Template for required environment variables
└── config.example.json  Template for application configuration
```

See [docs/architecture.md](docs/architecture.md) for a full description of how the components fit together.

---

## Making Changes

### Branch Strategy

Always branch from `develop`, never from `main`.

| Branch type | Naming pattern | Example |
|-------------|----------------|---------|
| New feature | `feature/<description>` | `feature/login-page` |
| Bug fix | `fix/<description>` | `fix/session-expiry-redirect` |
| Documentation | `docs/<description>` | `docs/pre-install-guide` |
| Urgent hotfix | `hotfix/<description>` | `hotfix/csrf-bypass` |

```bash
# Always start from an up-to-date develop branch
git fetch upstream
git checkout develop
git merge upstream/develop
git checkout -b feature/your-feature-name
```

### Keep Changes Focused

One pull request = one concern. A PR that implements a feature and refactors unrelated code is harder to review and more likely to be delayed. If you notice a bug while working on something else, fix it in a separate PR.

---

## Coding Standards

### Python (backend/)

- Follow [PEP 8](https://peps.python.org/pep-0008/)
- All code must pass `ruff check .` before committing
- Type hints are required on all function signatures
- Docstrings are required on all public functions and classes
- Never use `shell=True` in subprocess calls — this is a hard security rule
- Never hardcode secrets — all configuration comes through `backend/config.py` which reads from `.env`
- Pydantic models are required for all API request and response bodies

### HTML / CSS / JavaScript (frontend/)

- Plain HTML5, CSS3, and vanilla JavaScript only — no frameworks, no build step
- CSS custom properties (variables) for all colours and spacing — defined in `frontend/static/css/base.css`
- Progressive enhancement — core status display must work without JavaScript
- No inline event handlers (`onclick=...`) — use `addEventListener`

### Shell Scripts (scripts/, install.sh, etc.)

- All scripts must pass `shellcheck` with zero errors or warnings
- All scripts must be idempotent (safe to run more than once)
- Secrets must never be echoed, printed, or written to logs
- Every script prints a progress line for each major step
- Errors follow this format: `ERROR: <what went wrong>` / `FIX: <what to do>`

### Tests

- New backend features require unit tests
- Bug fixes require a regression test
- Minimum coverage: 60% for v0.1, 75% for v1.x

---

## Documentation Authoring (RTL/LTR Style Guide v1.0)

> **Status note**
> - Existing documentation chapters are **not yet fully normalized** to this guide.
> - **All new documentation and any future edits must follow this Style Guide immediately.**
> - Future normalization of existing chapters will follow this approved Style Guide (tracked in `docs/PROJECT-STATUS.md` → Deferred Work → *Documentation Normalization*).

### Purpose
Make bilingual (English / فارسی) documentation render **deterministically** in LTR and RTL by deciding direction at **authoring time**, not via the browser's per-token bidi heuristics. Authors mark intent with standard Markdown (backticks, fenced blocks, one list attribute); the site CSS then needs only a few stable rules.

### The model in one sentence
**Persian prose is RTL by default; anything that is a technical identifier or command is written as `code`/fenced/`{ .tech-list }`, which the renderer always shows left-to-right.** No per-token guessing.

### Core rules
1. **Persian prose paragraphs → RTL.** Write normally; `/fa/` pages are RTL. Do nothing special.
2. **Inline technical identifiers → wrap in backticks.** A backticked token always renders LTR and isolated, in both editions. This is the most important rule.
3. **Commands → fenced code blocks** (```` ```bash ````), one command per line. Never bare prose or plain bullets.
4. **Lists follow the list's semantic category, decided by the author (never auto-detected):**
   - **Rule A — Persian explanatory list →** plain bullets, no marking (inherits RTL).
   - **Rule B — technical identifier list →** backtick each item **and** tag `{ .tech-list }` (whole list LTR).
   - **Rule C — command list →** prefer a single fenced block; bullets + `{ .tech-list }` only if unavoidable.

### What to wrap in backticks (LTR identifiers)

| Category | Wrap as `code` | Example |
|---|---|---|
| Domains / hostnames | `example.com`, `conduit.example.com`, `*.example.com` | "دامنهٔ `conduit.example.com`" |
| URLs (shown literally) | `https://dash.cloudflare.com` | prefer a Markdown link; else `` `https://…` `` |
| Onboarding URLs (claim / pairing / invitation / onboarding) | `https://ryve.example.com/claim/<redacted>`, `https://oat.example.com/claim/<redacted>` | always LTR — see dedicated section |
| IPv4 addresses | `192.168.1.50`, `203.0.113.10` | "به آدرس `192.168.1.50`" |
| IPv6 addresses | `2001:db8::1`, `fe80::1`, `::1` | "به آدرس `2001:db8::1`" |
| Ports | `443`, `80`, `8000` | "روی پورت `443`" |
| Linux paths | `/etc/conduit-cc/config.json`, `/var/log/conduit-cc/` | `` `/etc/conduit-cc/tls/` `` |
| File names | `origin.pem`, `update.sh`, `config.json` | `` `origin.pem` `` |
| Container / deployment files | `docker-compose.yml`, `compose.yaml`, `Dockerfile` | same rules as file names |
| Container / image / service identifiers | container `conduit-cc`, image `ghcr.io/kiavash599/conduit-cc:latest`, compose service `web` | same rules as service identifiers |
| Environment variables | `CF_API_TOKEN`, `CF_ZONE_NAME` | `` `CF_API_TOKEN` `` |
| API endpoints | `/api/docs`, `/api/openapi.json` | `` `/api/docs` `` |
| Service / unit names | `conduit-cc.service`, `nginx` (the service) | `` `conduit-cc.service` `` |
| GitHub identifiers | `kiavash599/conduit-control-center`, branch `main`, commit `4079c45` | `` `kiavash599/conduit-control-center` `` |
| QR payload strings | claim / pairing payloads | always LTR — see dedicated section |
| Inline commands / flags | `crontab -l`, `--strict` | multi-token commands → **fenced block** |

Numbers that are **values** (`443`, `192.168.1.50`, `2001:db8::1`) are wrapped; ordinary prose numbers ("۱۵ سال", "۱۶ فصل") are not.

### Onboarding URLs — claim / pairing / invitation / onboarding links
Claim links, pairing links, invitation links, onboarding links, and any similar onboarding URL are **Technical Identifiers** and **must render LTR** (backtick, or use as a Markdown link target). They must never be split, reversed, or reflowed by RTL.

```markdown
لینک Claim را باز کنید: `https://ryve.example.com/claim/<redacted>`
```

**Security (mandatory):** never publish a real claim/pairing/invitation link or its token. Use example hosts (`ryve.example.com`, `oat.example.com`) and a redacted path segment (`/claim/<redacted>`). Pairing/claim links are never stored or published.

### QR payloads — Ryve Claim, Personal Pairing, and general
QR payload strings are **Technical Identifiers** and **must render LTR**.

- **Ryve Claim QR payloads**, **Personal Pairing QR payloads**, and **any QR payload string** are opaque machine strings, not prose.
- **Short** payloads → inline backticks. **Long** payloads → a fenced code block (so they don't wrap or reorder).

```markdown
نمونهٔ payload (نمونه/ویرایش‌شده):

​```text
ryve-claim:eyJ2IjoxLCJ0IjoiPHJlZGFjdGVkPiJ9
​```
```

**Security (mandatory):** never publish a real QR payload or an image encoding a real claim/pairing secret. Use clearly fake/redacted example strings.

### What NOT to wrap (prose terms — plain text)
Brand, product, protocol, and acronym names used **as words** stay unwrapped — they embed cleanly in bidi, and backticking them adds noise.

**Do not backtick:** Cloudflare, Conduit, Psiphon, Ubuntu, Raspberry Pi, Pi, GitHub, Docker, Personal Mode, Ryve, Backup — and acronyms as prose (see next).

### Acronym rule (explicit — to avoid subjective interpretation)
**Acronyms used as prose terms remain plain text:** DNS, TLS, SSH, API, HTTP, HTTPS, NAT, DDNS, SSL, IP.

Plain (term): `DNS چیست؟` · `TLS چیست؟` · `API چیست؟`

**However, an identifier that *contains* an acronym is still an identifier and must be backticked:** `` `API_TOKEN` ``, `` `TLS_CERT_PATH` ``, `` `DNS_RECORD_NAME` ``, `` `/api/docs` ``, `` `tls-setup.md` ``.

Decision test: **is the acronym the *name of a concept* being discussed (plain), or *part of a literal string* a user types/sees (backtick)?**

| Acronym as term (plain) | Identifier containing the acronym (backtick) |
|---|---|
| DNS | `DNS_RECORD_NAME`, a DNS value `conduit.example.com` |
| TLS | `TLS_CERT_PATH`, `tls-setup.md`, `/etc/conduit-cc/tls/` |
| SSH | the command `ssh ubuntu@host` (fenced) |
| API | `API_TOKEN`, `/api/docs` |
| HTTP/HTTPS | `https://ryve.example.com/claim/<redacted>` |
| NAT / DDNS | `cloudflare-ddns.sh`, `DDNS_INTERVAL` |

### Lists — before / after
**Rule A — Persian explanatory list (RTL, no marking):**
```markdown
در این فصل یاد می‌گیرید:

- دامنه چیست؟
- DNS چیست؟
- چرا Cloudflare؟
```

**Rule B — identifier list (backticked items + whole-list LTR):**
```markdown
متغیرهای محیطی مورد نیاز:

- `CF_API_TOKEN`
- `CF_ZONE_NAME`
- `CF_RECORD_NAME`
{ .tech-list }
```

**Rule C — command list (fenced block preferred):**
```markdown
​```bash
sudo bash update.sh
crontab -u conduit-cc -l
tail -n 20 /var/log/conduit-cc/ddns.log
​```
```
Only if bullets are unavoidable:
```markdown
- `sudo bash update.sh`
- `crontab -u conduit-cc -l`
{ .tech-list }
```

### Per-category quick rules
- **Domains/hostnames:** always backtick; redacted examples only (`example.com`, `*.example.com`).
- **URLs:** prefer `[text](https://…)`; if literal, backtick; never embed real tokens/secrets.
- **Onboarding URLs (claim/pairing/invitation/onboarding):** backtick/LTR; redact the secret path; never real.
- **IPv4 / IPv6:** backtick; documentation ranges only (`192.168.x.x`, `203.0.113.x`, `2001:db8::/32`); never a real public address.
- **Ports:** backtick the number; keep "پورت"/"port" as prose.
- **Paths/filenames (incl. `docker-compose.yml`, `compose.yaml`):** always backtick; standalone path lines belong in fenced blocks.
- **Container/image/service identifiers:** backtick (`conduit-cc`, `ghcr.io/...:tag`, `conduit-cc.service`); the product name in prose stays plain.
- **Env vars:** always backtick; lists use Rule B.
- **API endpoints:** backtick (`/api/docs`); "API" the word stays plain.
- **GitHub identifiers:** backtick `owner/repo`, branches, commit SHAs; link issues/PRs as `[#22](url)`.
- **QR payloads:** backtick (short) or fenced (long); redacted only.
- **Secrets:** never include real tokens, keys, pairing/claim links, or QR payloads — use placeholders.

### Validation checklist (per chapter, before merge)
- [ ] No bare IP (v4/v6) / domain / path / env var / endpoint / onboarding URL / QR payload in Persian prose — each is backticked or fenced.
- [ ] Every multi-token command is in a fenced block.
- [ ] Identifier and command bullet lists carry `{ .tech-list }`; explanatory Persian lists do not.
- [ ] Acronyms as terms (DNS, TLS, SSH, API, HTTP, HTTPS, NAT, DDNS) are plain; acronym-bearing identifiers (`API_TOKEN`, `TLS_CERT_PATH`) are backticked.
- [ ] Brand/product words (Cloudflare, Conduit, Ubuntu, Raspberry Pi, Docker, Ryve) are not backticked.
- [ ] No real domains/IPs/tokens/claim links/pairing links/QR payloads — only redacted/example values.
- [ ] EN and FA editions stay in sync (same identifiers wrapped the same way).
- [ ] Local render check on a FA page: identifiers/commands/onboarding URLs/QR payloads LTR & left-aligned; prose RTL; `{ .tech-list }` lists fully LTR.

---

## Commit Messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <short summary in imperative mood>

[optional body — explain the why, not the what]

[optional footer]
Closes #<issue-number>
```

**Types:**

| Type | Use for |
|------|---------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `test` | Adding or fixing tests |
| `chore` | Build, CI, dependencies, tooling |
| `refactor` | Code change that is not a fix or feature |
| `style` | Formatting only (no logic change) |
| `security` | Security fix or hardening |

**Examples:**

```
feat(auth): implement bcrypt password hashing on login

fix(ddns): preserve proxy status when updating A record

Closes #41

docs(pre-install): add Cloudflare token permission steps

test(adapter): add unit tests for get_status with mocked subprocess

Closes #36
```

**Rules:**
- Imperative mood: "add" not "added", "fix" not "fixed"
- Summary under 72 characters
- Reference the GitHub Issue in the footer: `Closes #N`

---

## Pull Request Process

1. Make sure all CI checks pass: lint (`ruff`), tests, coverage (≥60%), security audit (`pip-audit`), shellcheck
2. Fill in every section of the pull request template — do not delete sections
3. Link the issue your PR addresses: `Closes #N` in the PR description or commit footer
4. Request a review — at least one approving review is required before merging
5. Do not merge your own PR
6. If your branch has noisy WIP commits, squash them before requesting a review

### What reviewers check

- Does the implementation match the acceptance criteria in the linked GitHub Issue?
- Are there tests for the new behaviour?
- Are there any hardcoded values that should be environment variables?
- Does anything touch authentication, secrets, subprocess calls, or the Conduit adapter? (These get extra scrutiny.)
- Does any shell script pass `shellcheck`?

---

## Reporting Bugs

Use the [Bug Report template](https://github.com/kiavash599/conduit-control-center/issues/new?template=bug_report.md).

Include:
- Steps to reproduce (exact commands or clicks, in order)
- Expected behaviour
- Actual behaviour (include any error messages or log lines)
- Your OS, Python version (`python3 --version`), and CCC version (from `GET /api/health`)

**Do not open a public issue for security vulnerabilities.** See [SECURITY.md](SECURITY.md).
