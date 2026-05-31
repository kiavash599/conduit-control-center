# Security Policy

## Supported Versions

This project is in early development. Only the latest release receives security fixes.

| Version | Supported |
|---------|-----------|
| Latest release | ✅ Yes |
| Older releases | ❌ No |

---

## Reporting a Vulnerability

**Please do not open a public GitHub Issue for security vulnerabilities.**

A public issue exposes the vulnerability to everyone before a fix is available, which puts all users at risk — including people relying on Psiphon to access the internet safely in censored regions.

### How to report

Send an email to: **[ADD YOUR SECURITY EMAIL HERE]**

Include in your report:
- A description of the vulnerability and its potential impact
- Steps to reproduce (the more specific, the better)
- Any proof-of-concept code, logs, or screenshots
- Your preferred contact method for follow-up

You will receive an acknowledgement within **7 days**. If you do not hear back within that time, please send a follow-up email.

### What to expect

| Step | Target timeline |
|------|----------------|
| Acknowledgement | Within 7 days |
| Initial severity assessment | Within 14 days |
| Fix for critical vulnerabilities | Within 30 days |
| Fix for moderate or low vulnerabilities | Within 90 days |
| Public disclosure | After a fix is released |

We will credit you in the release notes unless you prefer to remain anonymous. We will not take legal action against researchers who report vulnerabilities in good faith and follow this policy.

---

## Scope

### In scope

- Authentication bypass or session hijacking
- SQL injection, command injection, XSS, or CSRF vulnerabilities
- Sensitive data exposure (secrets, pairing links, or API tokens appearing in logs, responses, or error messages)
- Privilege escalation on the host system
- Vulnerabilities in the install script (`install.sh`) that could compromise the host
- Insecure handling of the Cloudflare API token (`CF_API_TOKEN`)
- Psiphon pairing link exposure in any form

### Out of scope

- Vulnerabilities in Psiphon Conduit itself — report those to the [Psiphon team](https://psiphon.ca/contact.html)
- Issues requiring physical access to the Raspberry Pi
- Social engineering attacks
- Theoretical vulnerabilities without a working proof of concept
- Issues in dependencies that have an upstream fix not yet integrated

---

## Security Design Principles

These principles are documented here so contributors understand the reasoning behind specific code rules. Deviating from these principles — even with good intentions — will be flagged in code review.

### 1. No secrets in source code

All secrets are loaded at runtime from `/etc/conduit-cc/.env`. This file is excluded from git via `.gitignore`. The repository includes `.env.example` with placeholder values and inline documentation.

**Why:** Secrets committed to a public repository cannot be unexposed — they live in git history forever.

### 2. Pairing links are never persisted

A Psiphon Conduit pairing link is processed transiently in memory only. It is never written to a log file, database, environment variable, or any other persistent store.

**Why:** A pairing link gives access to a Conduit node. If it were stored, a compromise of the host would also compromise the Conduit connection.

### 3. FastAPI binds to localhost only

The FastAPI application listens on `127.0.0.1:8000` only. Nginx is the only internet-facing process. FastAPI is never directly exposed to the network.

**Why:** Nginx handles TLS, rate limiting, security headers, and IP restoration. Bypassing it would remove all of those controls.

### 4. Cloudflare API token is scoped minimally

The `CF_API_TOKEN` used for DDNS must have only `Zone:DNS:Edit` and `Zone:Zone:Read` permissions on the specific zone — never a Global API key.

**Why:** Principle of least privilege. A compromised minimal token can update one DNS record. A compromised Global API key can take over the entire Cloudflare account.

### 5. Real visitor IP is always restored from CF-Connecting-IP

When the Cloudflare proxy is enabled, Nginx uses `ngx_http_realip_module` with `real_ip_header CF-Connecting-IP` to restore the actual visitor IP before rate limiting and logging.

**Why:** Without this, all requests appear to come from Cloudflare's edge IP ranges, making rate limiting and audit logs useless.

### 6. Session cookies are HttpOnly, Secure, and SameSite=Strict

**Why:** `HttpOnly` prevents JavaScript from reading the cookie (mitigates XSS session theft). `Secure` prevents transmission over HTTP. `SameSite=Strict` prevents CSRF attacks that rely on cookies being sent cross-site.

### 7. CSRF protection is applied to all state-changing endpoints

All `POST`, `PUT`, and `DELETE` requests must include the `X-CSRF-Token` header matching the CSRF cookie value (double-submit cookie pattern).

**Why:** Even with `SameSite=Strict` cookies, defence in depth requires CSRF tokens on all mutating endpoints.

### 8. No `shell=True` in subprocess calls

The Conduit adapter calls `systemctl` and other system commands using explicit argument lists only.

**Why:** `shell=True` passes the command to `/bin/sh`, which introduces shell injection vulnerabilities if any part of the command is derived from user input.
