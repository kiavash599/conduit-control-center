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
7. [Commit Messages](#commit-messages)
8. [Pull Request Process](#pull-request-process)
9. [Reporting Bugs](#reporting-bugs)

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
