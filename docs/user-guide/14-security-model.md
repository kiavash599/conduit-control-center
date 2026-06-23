---
title: Chapter 14 — Security Model
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 14 — Security Model

## Purpose of this chapter

In the previous chapters we learned CCC's capabilities.

Now we want to know:

Why is CCC secure?

and:

How does CCC protect operators and their data?

## By the end of this chapter

✓ You will understand CCC's security philosophy.

✓ You will know the Trust Boundaries.

✓ You will know Authentication and Session Security.

✓ You will know CSRF Protection.

✓ You will know the Least Privilege Design.

✓ You will know Secret Management.

✓ You will know Personal Mode and Ryve security.

✓ You will know Backup & Restore security.

## 14.1 CCC's security philosophy

**Purpose**

Understanding the security design principles.

CCC is designed based on the principle of:

Least Privilege

That is, each part has only the minimum access required to perform its task.

**Purpose**

Reducing the risk of:

- Intrusion
- Software mistakes
- Damage caused by Bugs
- Disclosure of sensitive information

## 14.2 Trust Boundaries

**Overall architecture**

![Trust boundaries: Internet, Cloudflare, Nginx, the non-root CCC web app, validated helpers, and the conduit service / root operations](../diagrams/svg/security-trust-boundaries.svg)

*Requests pass through Cloudflare and Nginx to the non-root CCC web app, which reaches privileged actions only through validated helpers — direct root access is never exposed to the browser.*

**Important note**

CCC does not change system settings directly; for each sensitive operation, a dedicated Helper is used.

## 14.3 Authentication

**Purpose**

Preventing unauthorized access.

CCC uses:

Username
+
Password

**Password storage**

The password is not stored as Plain Text. It uses:

bcrypt

**Result**

Even if the configuration file is accessed, the real password cannot be seen.

## 14.4 Lockout Protection

**Purpose**

Countering Brute Force.

After:

5

failed attempts:

the account is locked for:

15 minutes

**Recovery**

Only via SSH:

sudo ccc-unlock <username>

## 14.5 Session security

**Purpose**

Protecting the user's session.

After Login, CCC creates a Session.

**Properties**

**HttpOnly**

JavaScript cannot read the Session Cookie.

**Secure**

It is sent only over HTTPS.

**SameSite=Strict**

Sending the Cookie in Cross-Site requests is restricted.

## 14.6 Sliding Expiration

**Purpose**

Reducing the risk of abandoned Sessions.

A Session has an expiration time, but:

Active User
↓
Expiration Extended

**Benefit**

An active user is not logged out, while abandoned Sessions are removed.

## 14.7 CSRF Protection

**Purpose**

Preventing forged requests.

CCC uses:

Double Submit Cookie

**Components**

**Cookie**

csrf_token

**Header**

X-CSRF-Token

**Process**

Cookie
↓
Header
↓
Comparison
↓
Allowed

In case of a mismatch, the request is rejected with:

403 Forbidden

## 14.8 Authorization

**Purpose**

Restricting access.

In version v0.3.0:

a Single Administrator

model is used.

**Important note**

Almost all APIs require Login.

**Exception**

/api/health

is used only to check the service's health.

## 14.9 Least Privilege design

**Purpose**

Preventing everything from running as root.

CCC runs as the user:

conduit-cc

Conduit runs as:

conduit

Neither is root.

## 14.10 Helper architecture

**Purpose**

Securely running sensitive operations.

CCC uses Helpers for sensitive operations.

Examples:

ccc-apply-conduit-config

ccc-restore-apply

ccc-personal-compartment

ccc-ryve-claim

**Benefit**

Each Helper:

- Validates the input
- Is independent
- Has a defined access level

## 14.11 Sudoers architecture

**Purpose**

Precise control of access.

CCC does not use Wildcards.

All permissions are:

Exact Path

**Example**

/opt/conduit-cc/bin/ccc-apply-conduit-config

not:

/opt/conduit-cc/bin/*

## 14.12 Secret management

**Purpose**

Protecting sensitive information.

Secrets are kept in:

/etc/conduit-cc/.env

**File permission**

0600

**Sample Secrets**

SESSION_SECRET

CF_API_TOKEN

ADMIN_PASSWORD_HASH

## 14.13 Cloudflare security

**Purpose**

Protecting the Cloudflare Token.

The Token:

CF_API_TOKEN

is stored in the .env file.

**Properties**

✓ It is not placed in a URL

✓ It is not stored in a Log

✓ It is not placed in a Backup

## 14.14 Personal Mode security

**Purpose**

Protecting the personal identity.

The Identity is kept in:

/var/lib/conduit/data

**Permissions**

0700 Directory
0600 File

**Important note**

The Identity is not placed in a Backup.

## 14.15 Personal QR security

**Purpose**

Understanding the sensitivity of the Token.

The personal QR is:

a Sensitive Credential

**Recommendation**

Share it only with trusted people.

## 14.16 Ryve security

**Purpose**

Protecting the Claim QR.

The Ryve QR has:

Private-Key-Grade Data

**System behavior**

✓ Temporary

✓ Only in RAM

✓ Automatic cleanup

✓ No permanent storage

## 14.17 Backup security

**Purpose**

Protecting Backups.

Algorithm:

AES-256-GCM

Key Derivation:

scrypt

**Important note**

The Passphrase is not stored.

## 14.18 Items excluded from Backup

**Purpose**

Preventing the transfer of Secrets.

These items are not backed up:

SESSION_SECRET

CF_API_TOKEN

tls_private_key

conduit_private_key

ryve_identity

## 14.19 Restore security

**Purpose**

Preventing the system from breaking.

A Restore is considered successful only based on:

Health Verification

not merely based on:

Process Exit Code

## 14.20 Security Rollback

**Purpose**

Protection against a broken Restore.

If a Restore makes the system unhealthy, an:

Automatic Rollback

is executed.

## 14.21 Security Headers

**Purpose**

Protecting the browser.

CCC uses the following Headers:

**HSTS**

Strict-Transport-Security

**CSP**

Content-Security-Policy

**Frame Protection**

X-Frame-Options: DENY

**MIME Protection**

X-Content-Type-Options: nosniff

**Referrer Protection**

Referrer-Policy: no-referrer

**Permissions Policy**

camera=()
microphone=()
geolocation=()

## 14.22 Security best practices

**Recommendation 1**

Keep the Backup Passphrase secure.

**Recommendation 2**

Restrict the Cloudflare Token.

**Recommendation 3**

Treat the Personal and Ryve QRs as Secret.

**Recommendation 4**

Keep the system up to date.

**Recommendation 5**

Use only HTTPS.

## 14.23 Conclusion of this chapter

Now you know:

✓ What CCC's security philosophy is.

✓ How Authentication works.

✓ How Sessions are protected.

✓ How CSRF works.

✓ How Secrets are kept.

✓ How the Backup is protected.

✓ How Personal Mode and Ryve are secured.

✓ How Restore protects the system.

✓ What role Security Headers play.

**Next chapter**

In Chapter 15 we will examine:

Advanced Administration

and cover advanced settings, service management, Update, Recovery, and professional administrative operations.
