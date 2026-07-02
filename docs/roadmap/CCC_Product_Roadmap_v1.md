# Conduit Control Center — Product Roadmap

**Document:** CCC_Product_Roadmap_v1  
**Revision:** 1.21  
**Date:** 2026-07-02  
**Status:** Reconciled to v0.3.12  
**Author:** CCC Development Team

---

## Table of Contents

1. [Overview](#1-overview)
   - [Release History](#release-history)
2. [Guiding Principles](#2-guiding-principles)
3. [R1 — Capability Audit (Research Milestone)](#3-r1--capability-audit-research-milestone)
   - 3.1 [Audit Scope](#31-audit-scope)
   - 3.2 [Conduit Linux CLI Capability Matrix](#32-conduit-linux-cli-capability-matrix)
   - 3.3 [Confirmed Findings Summary](#33-confirmed-findings-summary)
4. [v0.1.0 Maintenance Items](#4-v010-maintenance-items)
5. [D1 — UX/UI Design Milestone (Gate for v0.2.0)](#5-d1--uxui-design-milestone-gate-for-v020)
   - 5.1 [Design Principles](#51-design-principles)
   - 5.2 [Information Architecture](#52-information-architecture)
   - 5.3 [First-Screen Requirements](#53-first-screen-requirements)
   - 5.4 [Navigation Model](#54-navigation-model)
   - 5.5 [Visual Hierarchy Rules](#55-visual-hierarchy-rules)
   - 5.6 [Human-Readable Values](#56-human-readable-values)
   - 5.7 [Settings UX](#57-settings-ux)
   - 5.8 [Warnings, Confirmations, and Errors](#58-warnings-confirmations-and-errors)
   - 5.9 [Mobile Behaviour](#59-mobile-behaviour)
   - 5.10 [Theme Support](#510-theme-support)
   - 5.11 [Pre-Implementation Deliverables](#511-pre-implementation-deliverables)
6. [v0.2.0 — Smart Conduit Control](#6-v020--smart-conduit-control)
   - 6.1 [Conduit Configuration](#61-conduit-configuration)
   - 6.2 [Live Operations Panel](#62-live-operations-panel)
   - 6.3 [Regional Analytics](#63-regional-analytics)
   - 6.4 [Smart Assistant](#64-smart-assistant)
   - 6.5 [Bandwidth Scheduling](#65-bandwidth-scheduling)
   - 6.6 [Broker Live Status](#66-broker-live-status)
   - 6.7 [Theme Support](#67-theme-support)
- [Approved Delivery Priority (USER-VALUE-FIRST)](#approved-delivery-priority-user-value-first)
7. [Operations & Analytics — Feature Catalogue](#7-operations--analytics--feature-catalogue)
8. [Personal Mode & Ryve — Feature Catalogue](#8-personal-mode--ryve--feature-catalogue)
9. [Out of Scope](#9-out-of-scope)
- [Documentation Workstream](#documentation-workstream)
- [Maintenance & Patch Releases](#maintenance--patch-releases)
- [v0.4 Candidates](#v04-candidates-not-yet-scheduled)
- [Next Phase / Candidate Drivers](#next-phase--candidate-drivers)
10. [Revision History](#10-revision-history)

---

## 1. Overview

Conduit Control Center (CCC) is an open-source web dashboard for managing Psiphon Conduit nodes on Linux servers and Raspberry Pi devices. Its goal is to give volunteer operators a secure, lightweight, user-friendly interface that eliminates the need for direct CLI access for day-to-day operations.

This roadmap documents the planned evolution of CCC from a monitoring dashboard (v0.1) to a full control centre capable of configuring Conduit, visualising live and historical traffic, intelligently assisting operators with resource decisions, and managing personal relay identities.

**Target platform:** Raspberry Pi 4 (4 GB), Ubuntu 22.04 ARM64, public IP, Cloudflare DNS, Nginx, FastAPI, Python 3.

---

## Release History

The authoritative record of **product releases** (tags). Documentation snapshots are
tracked separately under [Documentation Workstream → Documentation Milestones](#documentation-workstream).

| Version | Tag | Date | Delivered |
|---|---|---|---|
| 0.1.0 | (MVP) | 2026-06 | Initial MVP — monitoring dashboard, auth, Conduit status |
| 0.1.1 | `v0.1.1` | 2026-06-11 | Maintenance items |
| 0.2.0 | `v0.2.0` | 2026-06-17 | Smart Conduit Control — Conduit Configuration, Regional Analytics, Bandwidth Scheduling, Live Operations, Theme Support, Traffic/Historical charts, Contribution Advisor |
| 0.3.0 | `v0.3.0` | 2026-06-21 | **First public release** — Personal Mode, Ryve Claim / Identity, Backup & Restore |
| 0.3.1 | `v0.3.1` | 2026-06-24 | Hotfixes — D1 root-URL redirect, D2 Cloudflare screenshot correction |
| 0.3.2 | `v0.3.2` | 2026-06-28 | HTTPS port selection (Feature 1) + one-click CCC update (Feature 2); validated on Raspberry Pi 4 + Pi 3 B (1 GB) |
| 0.3.3 | `v0.3.3` | 2026-06-28 | Validation release — exercised the one-click update path; exposed the `/run/lock` EROFS in `ccc-update-apply` |
| 0.3.4 | `v0.3.4` | 2026-06-28 | One-click update lock-path fix (EROFS): lock moved to `/var/lib/conduit-cc/.update.lock` (+ `O_NOFOLLOW`) |
| 0.3.5 | `v0.3.5` | 2026-06-28 | Storage Protection (Log Management / SD-Card Protection, `a6b6bd4`) — logrotate for `/var/log/conduit-cc/*.log` + flock-guarded `ccc-update-*` work-dir cleanup. Also the milestone for the first end-to-end One-Click Update validation (surfaced a 422 install bug, fixed in v0.3.6) |
| 0.3.6 | `v0.3.6` | 2026-06-28 | One-Click Update install fix — `Content-Type: application/json` on `POST /api/update/install` (was HTTP 422); readable FastAPI validation errors. Frontend-only |
| 0.3.7 | `v0.3.7` | 2026-06-29 | Validation release for the full One-Click Update pipeline; adopted the CCC Logo System v1.0 branding. (Known issue: non-interactive prompt rollback `rc=1`, fixed in v0.3.8) |
| 0.3.8 | `v0.3.8` | 2026-06-29 | One-Click Update non-interactive fix — `update.sh` skips the confirmation prompt for `--ccc-only` / non-TTY runs, restoring the dashboard-driven path |
| 0.3.9 | `v0.3.9` | 2026-06-29 | Validation release — Trusted Update Engine end-to-end on Raspberry Pi (v0.3.8 → v0.3.9) |
| 0.3.10 | `v0.3.10` | 2026-07-01 | Validation release — Update Engine Test & CI Hardening; deploy `rsync --exclude '/bin/'` fix (v0.3.9 → v0.3.10) |
| 0.3.11 | `v0.3.11` | 2026-07-02 | **One-Click Update production-proven** — first fully successful end-to-end dashboard update on Raspberry Pi via the B1 transient-unit engine (v0.3.10 → v0.3.11) |
| 0.3.12 | `v0.3.12` | 2026-07-02 | **Frontend polish** + final One-Click Update validation (v0.3.11 → v0.3.12): Install button hides when up to date; stale update messages no longer survive a reload; restore success uses a transient Toast. One-Click Update now **Completed / Production-Proven / Maintenance Only** |

---

## 2. Guiding Principles

- **Open-source first.** No proprietary dependencies. No telemetry without consent.
- **Security by default.** Never store or expose private keys, pairing tokens, or API secrets. Principle of least privilege throughout.
- **Beginner-friendly installation.** `install.sh` should work for a first-time Linux user.
- **Lightweight.** Suitable for Raspberry Pi 4 (4 GB). Avoid heavy frameworks or background indexing.
- **Production-ready.** Systemd integration, Nginx TLS, proper logging, graceful restarts.
- **Capability-driven.** No feature is scoped until it is confirmed possible with the installed Conduit binary.

---

## 3. R1 — Capability Audit (Research Milestone)

> **Gate:** No v0.2.0 implementation begins until R1 is complete and the capability matrix is approved.

### 3.1 Audit Scope

Five areas were audited against the official Psiphon-Inc/conduit repository (commit history current as of 2026-06-10):

- **A.** Personal Mode (Personal Links, Pairing URLs, QR generation, identities, invitations)
- **B.** Client Limits (max common, max personal, scopes, runtime configurability)
- **C.** Bandwidth Controls (global, per-direction, reduced/scheduling, runtime configurability)
- **D.** Metrics Inventory (complete `conduit_*` Prometheus gauge catalogue)
- **E.** Ryve (claim workflow, QR, rewards metadata, mobile integration)

Sources examined: `cli/cmd/start.go`, `cli/cmd/ryve.go`, `cli/internal/config/config.go`, `cli/internal/config/compartment.go`, `cli/internal/metrics/metrics.go`, `cli/README.md`, `cli/GUIDE.md`.

---

### 3.2 Conduit Linux CLI Capability Matrix

The table below captures every capability assessed. "CCC Candidate" describes the proposed CCC surface for that capability.

> **Contribution Advisor (delivered).** CCC ships a read-only, aggregate-only
> *advisory* surface — Health / Capacity / Reduced-mode guidance at
> `GET /api/advisor`. This is distinct from the *control* capabilities in the
> matrix below: **B1** `--max-common-clients` and **C1** `--bandwidth`
> (Read+Write, planned v0.2.0). The Advisor *recommends*; it does not *set*.
> Recommended-bandwidth and recommended-max-clients *calculations* are deferred
> to a future planning milestone that builds on the v0.2.0 control work.
>
> Naming note: the engineering epic was internally labelled "A1 Contribution
> Advisor". This is unrelated to capability-matrix row **A1 (Enable personal
> clients)** below; the "A1" shorthand should not be used for the Advisor in
> roadmap docs.

| # | Capability | Android App | Linux CLI | CCC Current | CCC Candidate |
|---|---|:---:|:---:|:---:|---|
| **A — Personal Mode** |||||
| A1 | Enable personal clients | ✅ | ✅ `--max-personal-clients` | ✅ | **DELIVERED v0.3.0** — Max personal clients apply (Slice 4 / C6b) |
| A2 | Compartment ID generation | ✅ | ✅ `new-compartment-id` cmd | ✅ | **DELIVERED v0.3.0** — Create identity (Slice 2 / C4 helper) |
| A3 | Personal pairing token (v1) | ✅ | ✅ `BuildPersonalPairingToken` | ✅ | **DELIVERED v0.3.0** — token retrieval + display (Slice 3 / C6a, C6c) |
| A4 | Personal pairing QR code | ✅ | ✅ (token → QR) | ✅ | **DELIVERED v0.3.0** — client-side QR, vendored qrcodegen (Slice 3) |
| A5 | `--compartment-id` accepts raw ID or token | — | ✅ `NormalizePersonalCompartmentInput` | ❌ | Accepts both formats (v0.3.0) |
| A6 | Pairing name max length | 32 chars | ✅ 32 chars enforced | ❌ | Validated input (v0.3.0) |
| A7 | Compartment persisted to disk | — | ✅ `personal_compartment.json` | ❌ | Read persisted ID (v0.3.0) |
| A8 | Per-user invitation management | ✅ | ❌ Not in CLI | ❌ | Out of scope |
| **B — Client Limits** |||||
| B1 | Max common clients (0–1000) | ✅ | ✅ `--max-common-clients` (default 50) | ❌ read | Read + Write (v0.2.0) |
| B2 | Max personal clients (0–1000) | ✅ | ✅ `--max-personal-clients` (default 0) | ❌ | Read (v0.2.0), Write (v0.3.0) |
| B3 | At-least-one-client validation | — | ✅ error if both = 0 | — | Enforce in UI (v0.2.0) |
| B4 | Runtime client limit update | — | ✅ `SetConfig(common, personal, bw)` | ❌ | Apply via service restart (v0.2.0) |
| B5 | `InproxyMaxCommonClients` via `--set` | — | ✅ in allowlist | — | Covered by B1 |
| B6 | `InproxyMaxPersonalClients` via `--set` | — | ❌ not in `--set` allowlist | — | Use `--max-personal-clients` flag (v0.3.0) |
| **C — Bandwidth Controls** |||||
| C1 | Global bandwidth limit (Mbps) | ✅ | ✅ `--bandwidth` (default 40, -1 unlimited) | ❌ read | Read + Write (v0.2.0) |
| C2 | Bandwidth stored as bytes/sec | — | ✅ `int(Mbps × 1,000,000 / 8)` | — | Display as Mbps in UI (v0.2.0) |
| C3 | Separate upstream/downstream limits | ✅ | ✅ via JSON config only (`InproxyLimit*BytesPerSecond`) | ❌ | Out of scope for v0.2.0; consider v0.3.0 |
| C4 | Unlimited bandwidth (`-1`) | ✅ | ✅ | ❌ | Toggle in UI (v0.2.0) |
| C5 | Runtime bandwidth update | — | ✅ `SetConfig(…, bandwidthBytesPerSecond)` | ❌ | Apply via service restart (v0.2.0) |
| C6 | Reduced-mode bandwidth scheduling | — | ✅ `InproxyReducedStartTime / EndTime / Limit*` via `--set` — HH:MM UTC; runtime, no boundary restart (B0/B0.1) | ✅ | Bandwidth Scheduling — delivered 2026-06-16 (`f838ff4`) |
| C7 | Reduced common clients during schedule | — | ✅ `InproxyReducedMaxCommonClients` via `--set` | ✅ | Bandwidth Scheduling — delivered 2026-06-16 (`f838ff4`) |
| C8 | Minimum throttle protection (100 GB / 7 days) | — | ✅ enforced by `conduit-monitor` — a **separate** quota supervisor (restart-based), distinct from `InproxyReduced*` | — | Not deployed by CCC; unrelated to Bandwidth Scheduling |
| **D — Metrics** |||||
| D1 | `conduit_announcing` | — | ✅ | ✅ | Read for broker_state — Live Operations (delivered 2026-06-17) |
| D2 | `conduit_connecting_clients` | — | ✅ | ✅ | Live Operations — delivered 2026-06-17 (Node Status; first parsed in Commit 1) |
| D3 | `conduit_connected_clients` | — | ✅ | ✅ | Already exposed |
| D4 | `conduit_is_live` (broker connection) | — | ✅ | ✅ | Live Operations — delivered 2026-06-17 (broker badge) |
| D5 | `conduit_max_common_clients` | — | ✅ | ✅ | Already exposed |
| D6 | `conduit_max_personal_clients` | — | ✅ | ✅ | **DELIVERED v0.3.0** (read + write; Max personal clients apply). Reclassified to the **Personal Mode & Ryve** catalogue (§8) — read + write fold into "Personal client limit control". The value is meaningful only alongside the v0.3.0 compartment / pairing / `--max-personal-clients` flow; not a standalone v0.2 item |
| D7 | `conduit_bandwidth_limit_bytes_per_second` | — | ✅ (0 = unlimited) | ❌ | Show in Config panel (v0.2.0) |
| D8 | `conduit_bytes_uploaded` | — | ✅ cumulative | ✅ | Delivered via Traffic / Lifetime cards (not Live Ops — no duplication) |
| D9 | `conduit_bytes_downloaded` | — | ✅ cumulative | ✅ | Delivered via Traffic / Lifetime cards (not Live Ops — no duplication) |
| D10 | `conduit_uptime_seconds` | — | ✅ GaugeFunc (computed at scrape) | ❌ | Intentionally deferred — Node Status shows service uptime (avoid a second uptime figure) |
| D11 | `conduit_idle_seconds` | — | ✅ GaugeFunc (computed at scrape) | ✅ | Live Operations — delivered 2026-06-17 (Node Status Idle) |
| D12 | `conduit_region_bytes_uploaded{scope,region}` | — | ✅ | ✅ | Regional Analytics — delivered 2026-06-16 (Traffic) |
| D13 | `conduit_region_bytes_downloaded{scope,region}` | — | ✅ | ✅ | Regional Analytics — delivered 2026-06-16 (Traffic) |
| D14 | `conduit_region_connecting_clients{scope,region}` | — | ✅ | ❌ | Not used by Regional Analytics MVP (connected only); available (v0.4 candidate) |
| D15 | `conduit_region_connected_clients{scope,region}` | — | ✅ | ✅ | Regional Analytics — delivered 2026-06-16 (Clients) |
| D16 | `conduit_build_info{build_repo,build_rev,go_version,values_rev}` | — | ✅ | partial | build_rev delivered in Node Status (Live Operations 2026-06-17); full build info (repo/go/values_rev) future |
| D17 | Scope labels: `common` / `personal` | — | ✅ | ❌ | Scope filter deferred (v0.4 candidate); Regional Analytics MVP is `scope=common` only |
| **E — Ryve** |||||
| E1 | `conduit ryve-claim` command | — | ✅ | ❌ | Subprocess invocation (v0.3.0) |
| E2 | Claim URI (`network.ryve.app://…`) | — | ✅ | ❌ | Display claim QR (v0.3.0) |
| E3 | Claim QR PNG saved to `data/ryve-claim-qr.png` | — | ✅ | ❌ | Serve PNG from data dir (v0.3.0) |
| E4 | ProxyID (Curve25519 public key) | — | ✅ | ❌ | Display ProxyID (v0.3.0) |
| E5 | Private key exposure in claim | — | ✅ requires explicit confirmation | ❌ | **CCC must NEVER store or display private key** |
| E6 | Ryve rewards data | — | ❌ not in CLI source | ❌ | Out of scope until confirmed |
| E7 | Station name / identity label | — | ✅ `--name` flag on `ryve-claim` | ❌ | Pre-fill with hostname (v0.3.0) |

---

### 3.3 Confirmed Findings Summary

**Personal Mode** is fully supported in Linux CLI via `--max-personal-clients` and `--compartment-id`. A compartment ID must be generated first; the CLI errors if personal > 0 and no compartment ID is configured. Personal pairing tokens use a defined v1 format (base64url-encoded JSON `{v:"1", data:{id, name}}`). CCC v0.3.0 implements the full personal-mode workflow.

**Client limits** are runtime-configurable via `SetConfig()`, which updates common clients, personal clients, and bandwidth in a single call. Since CCC manages Conduit via systemd, "apply" in practice means writing new flags to the service unit drop-in and restarting — not an in-process hot-reload.

**Bandwidth** defaults to 40 Mbps (decimal, stored as bytes/sec). The `--bandwidth` flag controls a combined limit; separate upstream/downstream limits require JSON config overrides and are not exposed on the `--set` allowlist. Reduced-mode scheduling (`InproxyReduced*` via `--set`) is the correct mechanism for time-based throttling, including the v0.2.0 bandwidth scheduling feature.

**`conduit_is_live`** is a confirmed metric (1 = broker connected, 0 = disconnected) and is surfaced as the Node Status **broker badge** (Live Operations, delivered 2026-06-17) — the topmost broker signal on the dashboard.

**Ryve** is a mobile app that claims a Conduit station by scanning a QR code encoding the station's private keypair. CCC must never store or display the private key itself. The safe workflow is: invoke `conduit ryve-claim --output <path>` as a subprocess (after explicit user confirmation), serve the resulting QR PNG temporarily, then delete it. The private key must never be logged, stored in the database, or transmitted over the API.

**`InproxyMaxPersonalClients` is not in the `--set` allowlist.** Personal client limits must be set via the `--max-personal-clients` flag, not `--set`. The Config panel write path in v0.3.0 uses `--max-personal-clients N` as a service unit flag — not `--set InproxyMaxPersonalClients=N`, which silently does nothing.

---

## 4. v0.1.0 Maintenance Items

These maintenance items are included in the v0.1.0 first public release. No new user-facing features. They are tracked here by descriptive name; corresponding GitHub issues have not yet been created.

| Item | Description | Priority |
|---|---|:---:|
| `pairing-neutralise` | Review and correct the unsupported `conduit pair`-based pairing workflow in `adapter.pair()` / `/api/conduit/pair`. `conduit pair` is not a CLI subcommand in any Conduit release (confirmed on `main` and `release-cli-2.0.0`). Full pairing functionality is a v0.4 candidate (Section 8). | High |
| `db-perms-600` | Restrict `ccc.db` file permissions (644 → 600) | High |
| `root-crontab-cleanup` | Remove duplicate DDNS cron entry from root crontab | Medium |
| `ufw-doc-update` | Update UFW firewall rules in `pre-install.md` based on production validation | Low |

**Release criteria:** all four maintenance items resolved, tests green, CHANGELOG updated.

---

## 5. D1 — UX/UI Design Milestone (Gate for v0.2.0)

> **Gate:** No v0.2.0 implementation begins until the D1 design deliverables are approved. The dashboard must be designed by, or to the standard of, a professional frontend/UX designer before a single line of implementation code is written.

> **Status: ✅ Completed (2026-06-14).** D1.0–D1.3 accepted and implemented as the M-IA dashboard restructure (Dashboard / System / Settings).

The dashboard must not be a raw metrics dump. Every screen must be something a non-technical volunteer operator — someone who set up a Raspberry Pi for the first time — can understand and act on without reading documentation.

### 5.1 Design Principles

| Principle | Requirement |
|---|---|
| **User-friendly** | Operators must understand every value on screen without knowing Prometheus, systemd, or Go. |
| **WYSIWYG where possible** | Configuration changes must preview their effect before being applied. |
| **Non-technical clarity** | No raw metric names (`conduit_bytes_uploaded`), no internal identifiers, no backend jargon exposed to the user. |
| **Clean and modern** | Visual design consistent with contemporary web dashboards (e.g. Grafana, Vercel, Linear). |
| **Mobile-friendly** | Fully usable on a smartphone. Operators must be able to check station health and change limits from their phone. |
| **Lightweight** | No heavy JavaScript frameworks. Must load acceptably on a Raspberry Pi 4 over a slow mobile connection. |

### 5.2 Information Architecture

The dashboard is organised into eight named sections. These sections drive both the navigation structure and the grouping of all features across v0.2.0–v0.3.0 (plus v0.4 candidates).

| # | Section | Contents |
|---|---|---|
| 1 | **Overview** | Station status, health indicator, key metrics summary — the home screen |
| 2 | **Live Clients** | Connected + connecting clients, broker status, uptime, idle time |
| 3 | **Traffic** | Total upload/download, bandwidth limit display |
| 4 | **Regions** | Top active countries with flags, client counts, traffic volumes |
| 5 | **Configuration** | Max common clients, max personal clients, bandwidth limit, apply workflow |
| 6 | **Scheduling** | Bandwidth schedule profiles (reduced-mode) |
| 7 | **Smart Assistant** | Manual / Assisted / Automatic mode selector and analysis panel |
| 8 | **Personal & Ryve** | Personal mode setup, pairing tokens, Ryve claim QR (v0.3.0) |

Settings (theme, password, session) are accessible via a gear icon, separate from the operational sections above.

### 5.3 First-Screen Requirements

The Overview (home screen) is the first thing an operator sees after login. It must answer the operator's three questions in under three seconds:

1. **Is my station working?** → Station status badge (Live / Starting / Offline / Disconnected)
2. **Is anyone using it?** → Connected clients count (large, prominent)
3. **How much have I served?** → Total upload + download since last restart

The first screen must contain, above the fold on a standard desktop browser:

| Element | Value shown | Notes |
|---|---|---|
| Station status badge | Live / Starting / Offline / Disconnected | Colour-coded: green / yellow / grey / red |
| Health indicator | Good / Warning / Critical | Derived from status + idle time + client count |
| Connected clients | Integer | Large metric card |
| Connecting clients | Integer | Secondary metric card |
| Bandwidth limit | X Mbps or Unlimited | Secondary metric card |
| Upload total | Human-readable (KiB/MiB/GiB/TiB) | Secondary metric card |
| Download total | Human-readable (KiB/MiB/GiB/TiB) | Secondary metric card |
| Top active countries | Top 3–5 country names + client counts | Compact summary; links to full Regions section |
| Uptime | Xd Xh Xm | Secondary detail |

Nothing on the first screen should require the operator to already know what they are looking for.

### 5.4 Navigation Model

**Desktop:** persistent left sidebar with section icons and labels. Active section highlighted. Sidebar collapses to icon-only at narrower widths. No horizontal top navigation.

**Mobile:** bottom navigation bar with icons for the five most important sections (Overview, Live Clients, Traffic, Regions, Configuration). Remaining sections accessible via a "More" item. No hamburger menu.

**Navigation rules:**
- The current section title is always visible in the page header.
- Breadcrumbs are not required — sections are flat, not nested.
- Destructive or disruptive actions (apply config, restart service) are never reachable in one tap/click from the navigation. They require entering the Configuration section first.
- Deep-linking to sections is supported (`/dashboard#regions`, `/dashboard#config`).

### 5.5 Visual Hierarchy Rules

These rules apply to every screen in the dashboard:

| Rule | Detail |
|---|---|
| **Large metric cards** | Used for the 3–4 most critical values on a given section (e.g. connected clients, station status). Font size ≥ 32px for the number. |
| **Secondary cards** | Used for supporting values (e.g. connecting clients, uptime). Smaller but same card component. |
| **Tables** | Only used when a list of comparable rows is genuinely the clearest format (e.g. Regions table). Never used for single values or key metrics. |
| **Charts** | Only added when the shape of data over time improves a decision (v0.2.x onwards). |
| **No raw metric names** | Never display `conduit_bytes_uploaded`, `conduit_is_live`, or any Prometheus/internal identifier to the user. Use plain English labels. |
| **No raw ISO codes alone** | Country codes (e.g. `MM`, `IR`) must always be accompanied by the country name and flag emoji. |
| **Colour usage** | Green = good/live/healthy. Yellow/amber = warning/transitioning. Red = error/offline. Grey = unknown/stopped. Blue = informational/neutral. No other status colours. |
| **Empty states** | Every section that can have zero data must display a friendly empty-state message explaining what the data will show once available (e.g. "No clients connected yet. New stations can take a few hours to receive traffic."). |

### 5.6 Human-Readable Values

All values presented to the operator must use human-readable formats. No raw numbers without units; no internal representations.

| Value type | Display format | Example |
|---|---|---|
| Byte counts (transfer totals) | Binary prefixes: KiB, MiB, GiB, TiB | `14.3 GiB` |
| Bandwidth limit | Decimal Mbps (matches CLI) or "Unlimited" | `40 Mbps` / `Unlimited` |
| Duration (uptime, idle) | `Xd Xh Xm Xs` — omit leading zero units | `2d 4h` / `37m 12s` |
| Client counts | Plain integer | `127` |
| Country identifiers | Flag emoji + full country name + ISO code in parentheses | 🇲🇲 Myanmar (MM) |
| Conduit version | Semantic version string from build info | `v2.0.0` |
| Timestamps | Local time in `YYYY-MM-DD HH:mm` | `2026-06-10 14:23` |
| Bandwidth in/out rates (v0.4 candidate) | Human-readable binary + `/s` | `1.2 MiB/s` |

Never mix decimal and binary byte prefixes on the same panel. Mbps (decimal) is used exclusively for the configured bandwidth limit value, as this matches the CLI's `--bandwidth` flag. All other byte quantities use binary prefixes (KiB, MiB, GiB, TiB).

### 5.7 Settings UX

The Configuration section manages all parameters that affect Conduit's behaviour. Because changes here cause a service restart, every interaction in this section must be deliberate and reversible.

**Layout:** a single form with clearly labelled fields, grouped into logical sub-sections (Client Limits, Bandwidth, Scheduling). A persistent "Apply Changes" button is anchored to the bottom of the form. The button is disabled until the operator has actually changed at least one value from the current state.

**Field-level UX:**

| Field | Input type | Constraints shown |
|---|---|---|
| Max common clients | Number input + slider | Range: 0–1000. Live validation. Warning if set to 0 while personal clients also 0. |
| Max personal clients | Read-only display in v0.2.0 | "Personal mode not yet configured" placeholder. |
| Global bandwidth limit | Number input (Mbps) + "Unlimited" toggle | Minimum: 1 Mbps. Displays estimated impact (e.g. "~X clients at average usage"). |
| Smart Assistant mode | Three-way toggle: Manual / Assisted / Automatic | Automatic mode requires a separate confirmation dialog before enabling. |

**Save/apply workflow:**
1. Operator changes one or more values. Changed fields are visually highlighted (yellow left border).
2. A diff summary appears below the form: "You are changing: Max common clients 50 → 75".
3. Operator clicks "Apply Changes".
4. A confirmation modal appears with the diff and the warning: "Applying these changes will briefly restart Conduit. Connected clients will be disconnected for a few seconds."
5. Operator confirms. CCC writes the drop-in, restarts the service, and shows a progress indicator.
6. On success: a green toast notification. Fields reset to the new current values.
7. On failure: a red toast with the error. No changes are retained in the running service.

**Bandwidth schedule UX (delivered — see §6.5):**
- A single **daily** reduced-mode window. Fields: start time and end time
  (`HH:MM`, 24-hour, **UTC**, with a browser-local preview), reduced max common
  clients (required when enabled, ≤ max common clients), reduced bandwidth (Mbps).
- **No day-of-week selection** — Conduit's reduced window is a daily time-of-day
  window only; there is no weekday/weekend/per-day capability.
- The window is evaluated by psiphon-tunnel-core at runtime: the normal⇄reduced
  transition is automatic, with **no restart at the start/end times** and no
  disconnect of already-connected clients.
- Changes to the schedule *values* follow the same diff + confirm + restart
  workflow as other configuration changes (one restart when the values change).
- The 100 GB / 7-day floor is the separate `conduit-monitor` quota supervisor
  (not deployed by CCC) and is unrelated to this window.

### 5.8 Warnings, Confirmations, and Errors

The dashboard uses three levels of notification:

| Level | Trigger | Presentation |
|---|---|---|
| **Toast** (non-blocking) | Success actions, non-critical state changes | Bottom-right corner, auto-dismiss after 4s. Green (success) or red (error). |
| **Banner** (persistent) | Station offline, broker disconnected, health degraded | Top of the relevant section, stays until condition resolves. Yellow or red. Dismissible. |
| **Modal** (blocking) | Any action that restarts Conduit, enables Automatic mode, or invokes Ryve claim | Requires explicit confirmation click. Shows exactly what will change and what the consequence is. Cancel button always present. |

**Specific warning text requirements:**

- **Before applying configuration changes:** "Applying these changes will briefly restart Conduit. Clients currently connected will be disconnected for a few seconds. Do you want to continue?"
- **Before enabling Automatic mode:** "Automatic mode will adjust your client limits without asking for confirmation. You can set minimum and maximum bounds below. Are you sure you want to enable Automatic mode?"
- **Before Ryve claim (v0.3.0):** "Generating the Ryve claim QR code requires accessing your station's private key. The key will not be stored or transmitted — only the QR code image will be displayed. This should only be done in a private location. Continue?"
- **Station offline banner:** "Your Conduit station is not running. Check the service status or review the logs."
- **Broker disconnected banner:** "Conduit is running but not connected to the Psiphon broker. No clients can be served until the connection is restored."
- **New station empty state:** "No clients connected yet. New stations can take several hours to receive traffic while building reputation with the Psiphon network."

### 5.9 Mobile Behaviour

The dashboard must be fully functional on a 375px-wide smartphone screen.

| Component | Mobile behaviour |
|---|---|
| Sidebar navigation | Hidden; replaced by bottom navigation bar (5 primary sections + More) |
| Metric cards | Stack vertically, full width |
| Configuration form | Stacked single-column layout; sliders replaced by number inputs with +/− buttons |
| Regions table | Simplified: show flag, country name, clients only. Traffic columns hidden (accessible via row expand) |
| Modals | Full-screen on small viewports |
| Charts (v0.2.x) | Horizontally scrollable within their container |
| Toast notifications | Bottom-centre, full width |

**Touch targets:** all interactive elements (buttons, inputs, toggles, navigation items) must have a minimum touch target of 44×44px.

**Performance:** the dashboard must be usable on a 3G mobile connection. Total initial page payload (HTML + CSS + JS, excluding metrics API calls) must not exceed 300 KB uncompressed.

### 5.10 Theme Support

Three theme modes: **Light**, **Dark**, and **System** (follows OS preference).

Implementation requirements:
- Implemented using CSS custom properties (`--color-bg`, `--color-surface`, `--color-text`, etc.) — no JavaScript required to switch themes.
- Theme preference persisted via a server-side cookie (`theme=light|dark|system`) set on the settings endpoint — not `localStorage` (not available in the embedded environment).
- System theme uses `@media (prefers-color-scheme: dark)` as the CSS fallback.
- Theme toggle is accessible from all screens via a small icon in the header or sidebar footer.
- All status colours (green/yellow/red/grey) must meet WCAG AA contrast in both light and dark themes.
- Charts and tables must have theme-aware colour palettes.

### 5.11 Pre-Implementation Deliverables

Before any v0.2.0 implementation code is written, the following design artefacts must be produced and approved:

| Deliverable | Description |
|---|---|
| **Dashboard layout proposal** | Annotated wireframe or mockup showing the layout of each section on desktop and mobile. Must demonstrate the navigation model, card hierarchy, and form layout for Configuration. |
| **Section hierarchy document** | Written description of each section's content, the information priority within it, and how it relates to adjacent sections. |
| **Card/table structure specification** | For each metric card and table in the dashboard: the data source, the display format, the empty state, and the error state. |
| **Navigation model diagram** | Diagram showing how a user moves between sections, what is reachable from the Overview, and what requires deliberate navigation to reach. |
| **Mobile wireframe** | Wireframe of the bottom navigation bar and at least three sections in mobile layout. |
| **UX rule document** | Written enumeration of all warning messages, confirmation dialogs, and error states specified in Section 5.8, plus any additional cases identified during design. |

---

## 6. v0.2.0 — Smart Conduit Control

> **Gate:** D1 design deliverables must be approved before implementation begins.

> **Status: ✅ CLOSED — all v0.2.0 features delivered and production-validated (2026-06-17).** v0.2.0 was the active *feature* milestone (gated by D1, complete). Delivered and production-validated: **Conduit Configuration (§6.1, via M2 + Bandwidth Scheduling §6.5); Regional Analytics (§6.3); Bandwidth Scheduling (§6.5); Live Operations (§6.2/§6.6); Theme Support (§6.7).** **Smart Assistant (§6.4) is reconciled** — the Contribution Advisor supersedes the original Manual/Assisted concept (Automatic mode → v0.3.0). **D6 (`max_personal_clients`) is no longer a v0.2 gap** — reclassified to **v0.3.0 Personal Mode** (the read-only value is meaningful only alongside the compartment / pairing / `--max-personal-clients` flow; see §3.2 D6 and §8). **No open v0.2.0 work remains.**

v0.2.0 transforms CCC from a read-only monitoring dashboard into an interactive control centre. The central theme is: **display everything Conduit exposes and give operators control over the parameters they need most.**

### 6.1 Conduit Configuration

Display and allow editing of the three runtime-configurable parameters:

- **Max common clients** — current value from `conduit_max_common_clients`; editable (0–1000); enforces at-least-one-client rule.
- **Max personal clients** — `conduit_max_personal_clients`. **Reclassified to v0.3.0 Personal Mode** (not surfaced in v0.2.0): the value is only meaningful alongside the compartment-ID / pairing-token / `--max-personal-clients` flow, so its read-only display folds into the v0.3.0 "Personal client limit control" (§8) rather than shipping as an isolated v0.2 field.
- **Global bandwidth limit** — current value from `conduit_bandwidth_limit_bytes_per_second`; display as Mbps (0 = unlimited); editable with unlimited toggle.

**Apply mechanism:** write new flags to the systemd service unit drop-in (`/etc/systemd/system/conduit.service.d/ccc.conf`) and run `systemctl daemon-reload && systemctl restart conduit`. Confirm to the user that Conduit will restart briefly (see Section 5.8).

> **Note:** `conduit_bandwidth_limit_bytes_per_second` uses decimal megabits (1 Mbps = 125,000 bytes/sec). Display and input must use decimal Mbps to match CLI behaviour.

### 6.2 Live Operations Panel

> **Status: ✅ DELIVERED — 2026-06-17.** Shipped as **Option 1** — an extension
> of the existing **Node Status** card, not a standalone panel — to avoid
> duplicating the Advisor, Traffic, and Lifetime cards. Commits `3741b71` /
> `d61a478` / `b4bc9c1`; CI #115 green; production-validated. Closure record:
> `docs/closure/live-operations-closure.md`.
>
> **Delivered subset:** four-state **broker badge** (Live / Starting /
> Disconnected / Not running, + an "Unknown" degradation state), **connecting
> clients**, **idle**, and **build_rev** (appended to the version line).
> **Not re-shown (no duplication):** connected clients (Advisor), bytes
> (Traffic/Lifetime), service uptime + version (Node Status). **Deferred:**
> `conduit_uptime_seconds` (a second uptime figure would confuse operators).
> "Starting" is sub-poll-interval and may be skipped by the 5 s poller (accepted).
>
> The original metric table below is retained for historical context.

Live Operations section, aligned with the card structure defined in Sections 5.3 and 5.5:

| Metric | Source | Display Format |
|---|---|---|
| Broker status | `conduit_is_live` | Live / Starting / Offline / Disconnected badge |
| Connected clients | `conduit_connected_clients` | Large metric card |
| Connecting clients | `conduit_connecting_clients` | Secondary metric card |
| Uptime | `conduit_uptime_seconds` | `Xd Xh Xm` |
| Idle time | `conduit_idle_seconds` | `Xd Xh Xm` or "Active" |
| Bytes uploaded | `conduit_bytes_uploaded` | Human-readable binary (KiB/MiB/GiB/TiB) |
| Bytes downloaded | `conduit_bytes_downloaded` | Human-readable binary (KiB/MiB/GiB/TiB) |
| Build revision | `conduit_build_info` | `v2.0.0 · short-rev` |

**Four-state broker status logic:**
- `conduit_announcing == 0` and service not reachable → **Not running** (grey)
- `conduit_announcing == 1` and `conduit_is_live == 0` → **Starting** (yellow)
- `conduit_is_live == 1` → **Live** (green)
- Service reachable, `conduit_is_live == 0`, not announcing → **Disconnected** (red)

**`conduit_idle_seconds` assistant trigger:** if idle > 12h and connected_clients == 0, the Smart Assistant surfaces a contextual message: "Your station hasn't served any clients recently. New stations can take 24–48 hours to build reputation with the Psiphon network. If your station has been running for several days without traffic, check your network connectivity and public IP."

### 6.3 Regional Analytics

> **Status: ✅ DELIVERED (MVP) — 2026-06-16.** Shipped and production-validated
> on the Raspberry Pi. Backend `GET /api/conduit/regions` (commit `6f96978`,
> CI #106 green) + Regions dashboard card (commit `a169089`, CI #107 green).
> Full closure record: `docs/closure/regional-analytics-closure.md`.
>
> **Delivered MVP scope** (intentionally narrower than the original
> specification retained below):
> - Top **10** regions (not 15), `scope="common"` only, sorted by **Traffic DESC**.
> - Columns: **No. · Country (flag + name) · Traffic · Clients**.
> - **Traffic** = `conduit_region_bytes_uploaded` + `conduit_region_bytes_downloaded`
>   (combined, binary units). **Clients** = `conduit_region_connected_clients`.
> - Aggregate-only: no IP, session, or per-client data. "Clients" terminology
>   enforced (never "Users"). Dashboard-aware 60s polling; mobile responsive
>   (horizontal-scroll table). Frontend guard tests added.
>
> **Deferred (not in the MVP), tracked for future milestones:**
> - Separate Uploaded / Downloaded columns and a Connecting-clients column
>   (`conduit_region_connecting_clients`, matrix D14) — not consumed by the MVP.
> - Scope filter (All / Common / Personal, matrix D17) — **v0.4 candidate**
>   (already listed in §8); surfaces only when personal clients > 0.
> - Mobile row-expand for hidden Traffic columns (§5.9) — superseded by the
>   horizontal-scroll table actually shipped.
>
> **Accepted deviations from the design spec** (cosmetic, not defects): the
> delivered country cell shows flag + name without the ISO code in parentheses
> (cf. §5.6); Unicode flag emoji depend on platform font support and degrade to
> ISO letters on some desktop environments. Both accepted; see the closure record.
>
> The original specification below is retained for historical context.

Top 15 active regions displayed as a table (see Section 5.5 — table rule), aligned with the Regions section of the information architecture.

| Column | Source |
|---|---|
| Flag + country name + ISO code | ISO 3166-1 alpha-2 lookup (in-process, no external API) |
| Connected clients | `conduit_region_connected_clients{scope,region}` (sum across scopes) |
| Connecting clients | `conduit_region_connecting_clients{scope,region}` (sum) |
| Uploaded | `conduit_region_bytes_uploaded{scope,region}` (sum, binary format) |
| Downloaded | `conduit_region_bytes_downloaded{scope,region}` (sum, binary format) |

Rules: regions with zero clients AND zero bytes in all columns are hidden. Scope filter (All / Common / Personal) visible only when personal clients > 0.

### 6.4 Smart Assistant

> **Status: ✅ Reconciled — delivered in spirit (2026-06-17).** The shipped
> **Contribution Advisor** delivers the intended Assisted-mode value: its
> Capacity domain analyses CPU / RAM (psutil) plus connected clients and the
> max-common limit to emit back-off / grow guidance, alongside reduced-mode and
> contribution-health advice. It is always-on and advisory ("recommends; does
> not set"). **The Contribution Advisor supersedes the original Manual / Assisted
> concept** — "Manual" = ignore the advice; "Assisted" = the default. **Automatic
> mode remains a v0.4 candidate.** No open v0.2.0 work.

> **Superseded — retained for historical context.** The original three-mode
> model (Manual / Assisted / Automatic) below is superseded by the reconciliation
> above: the always-on **Contribution Advisor** delivers the Assisted-mode value
> ("Manual" = ignore the advice; "Assisted" = the default), and **Automatic mode
> remains a v0.4 candidate** (Raspberry Pi restart-cascade risk). v0.2.0 has **no open
> Smart Assistant work**.

Three modes as originally defined in Section 5.7. Automatic mode deferred (v0.4 candidate) (see review note — risk on Raspberry Pi restart cascades).

**Advisor (Assisted-mode) analysis inputs:** CPU utilisation (psutil), RAM utilisation, `conduit_connected_clients`, `conduit_bandwidth_limit_bytes_per_second`, `conduit_bytes_uploaded`, `conduit_bytes_downloaded`, `conduit_idle_seconds`.

**GUIDE.md baseline:** ~150–350 concurrent users per CPU / 2 GB RAM pair. The assistant references this range when computing suggestions.

### 6.5 Bandwidth Scheduling

> **Status: ✅ DELIVERED — 2026-06-16.** Shipped and production-validated on the
> Raspberry Pi (final commit `f838ff4`; CI #109–#113 green). Full closure record:
> `docs/closure/bandwidth-scheduling-closure.md`.
>
> **Reduced Mode is CONFIRMED** (B0/B0.1): the `InproxyReduced*` keys are allowlisted
> by Conduit and forwarded to psiphon-tunnel-core, which evaluates the window at
> runtime.
>
> **Delivered model:**
> - A single **daily** reduced-mode window. `InproxyReducedStartTime` /
>   `InproxyReducedEndTime` use **`HH:MM`, 24-hour, UTC**.
> - Fields: enable, start (UTC), end (UTC), reduced max common clients (required
>   when enabled, must be ≤ max common clients), reduced bandwidth (single Mbps,
>   written to both `InproxyReducedLimitUpstream/DownstreamBytesPerSecond`).
> - **tunnel-core performs the runtime switching internally.** CCC runs **no
>   scheduler** — no cron, no APScheduler, no systemd timers, **no boundary
>   restarts**. A restart occurs **only when the schedule values change**;
>   already-connected clients are not disconnected at the boundary.
> - Set via the existing config write path (`--set` from the drop-in; the privilege
>   boundary stays **integer-only** — the root helper formats `HH:MM` from validated
>   minutes), with the same diff + confirm + restart + rollback workflow.
>
> **Deferred (not in MVP):** separate upstream/downstream reduced limits; multiple
> windows; an ACTIVE/INACTIVE badge; an Advisor "use recommendation" pre-fill
> button. **No day-of-week** scheduling (unsupported by Conduit's reduced window).
> The `conduit-monitor` 100 GB/7-day quota throttle is a **separate** mechanism,
> not deployed by CCC and unrelated to this feature.
>
> The original specification below is retained for historical context.

Time-based bandwidth profiles using Conduit's built-in reduced-mode mechanism (`InproxyReduced*` via `--set`), presented as the visual timeline defined in Section 5.7.

Fields written to the service unit drop-in: `InproxyReducedStartTime`, `InproxyReducedEndTime`, `InproxyReducedLimitUpstreamBytesPerSecond`, `InproxyReducedLimitDownstreamBytesPerSecond`, `InproxyReducedMaxCommonClients` (optional).

Changes to the schedule follow the same diff + confirm + restart workflow as all other configuration changes.

### 6.6 Broker Live Status

> **Status: ✅ DELIVERED — 2026-06-17.** The four-state broker badge is rendered
> in the **Node Status** card (not a separate Overview element). Validated
> Disconnected → Live; "Starting" is sub-poll-interval (accepted). See §6.2.

`conduit_is_live` is the topmost element of the Overview screen and is also present in the Live Clients section. Four states as defined in Section 6.2. Broker disconnected banner displayed per Section 5.8.

### 6.7 Theme Support

> **Status: ✅ DELIVERED — 2026-06-17.** Light / Dark / System themes shipped and
> **production-validated on the Raspberry Pi (TS4)** across all three themes, both
> persistence paths (reload + login/logout), all three pages (Dashboard / Settings
> / Login), mobile layout, the toggle, and the error/revert path — no blocking
> defects. Commits `46547c0` (TS1 CSS, CI #117) / `df49f42` (TS2 backend, CI #118) /
> TS3 (Settings toggle). Flash-free **server-rendered** first paint from a `theme`
> cookie (HttpOnly, Secure, SameSite=Strict, 1-year); **no localStorage**.
> Settings-only native radio toggle with instant apply and revert-on-failure;
> default dark. Closure record: `docs/closure/theme-support-closure.md`.

Light / Dark / System themes implemented per Section 5.10. Theme preference persisted via server-side cookie, not localStorage.

---

## Approved Delivery Priority (USER-VALUE-FIRST)

> ⚠️ **Historical planning artifact (rev 1.9).** This section records the original
> post-v0.2.0 execution sequence. It is **superseded by the [Release History](#release-history)
> table (above) and the forthcoming `docs/PROJECT-STATUS.md`** as the authoritative
> release record. Items 1–4 (Branding, Personal Mode, Ryve Claim, Backup & Restore)
> were delivered in **v0.3.0**; items 5–7 (Update Centre, Automatic Mode, Health Score)
> are **v0.4 candidates**. Retained for historical context — do not read it as the
> current release status.

> **Authoritative execution order — approved 2026-06-17 (Product Owner).** This
> section records the **sequence** in which the post-v0.2.0 epics will be
> delivered under the USER-VALUE-FIRST direction. It is intentionally **decoupled
> from the milestone version labels** in §7 (Operations & Analytics) and §8 (Personal Mode & Ryve): those
> sections are **preserved as-is** for historical and feature-specification
> purposes. Where the priority order and the old milestone grouping disagree,
> **this order governs delivery**; §7/§8 remain the detailed feature catalogues.
> No feature is removed and no scope is reduced — every item below points back to
> its existing specification.

| # | Epic | Spec reference | Notes |
|---|---|---|---|
| **1** | **Branding & Identity** | _new — not previously sectioned_ | Favicon, logo, GitHub avatar, docs branding. Isolated static assets/docs; no Conduit interaction, no privilege surface, fully reversible. Prior branding review was conditional-GO (needs a simplified favicon variant + full-bleed/maskable; teal-vs-indigo accent) — resolve design before starting. If a PWA icon set is in scope, note no web manifest exists today. |
| **2** | **Personal Mode** | §8 (Personal mode setup, Pairing token generation, Personal client limit control) + §3.2 A1–A7/B2/B6, §3.3 | Compartment generation (`new-compartment-id`), pairing token (`BuildPersonalPairingToken`, CCC-rendered QR), `--max-personal-clients` write (flag, **not** `--set` — §3.3). **D6 (`conduit_max_personal_clients`) is part of this epic** (read + write fold into "Personal client limit control"). **Scope filter in Regional Analytics** (§8; §3.2 D14/D17) folds in here — it is only meaningful when personal clients > 0. Introduces the privileged-subprocess runner reused by epic 3. |
| **3** | **Ryve Claim / Identity** | §8 (Ryve claim QR, ProxyID display) + §3.2 E1–E5/E7, §3.3, §5.8 modal | `conduit ryve-claim` subprocess after the §5.8 modal; serve QR PNG, display ProxyID, delete PNG after display. **Highest security surface** — the private key is printed to stdout and must never be logged, stored, or returned (§3.2 E5). Reuses the runner from epic 2 (so it follows Personal Mode). **Renamed from "Ryve Rewards"** — see the out-of-scope note below. |
| **4** | **Backup & Restore** | §7 | Export CCC configuration (never the Conduit/Ryve key) to an encrypted archive; restore. Precedes Update Center to provide a pre-update rollback. |
| **5** | **Update Center** | §7 | GitHub-releases check + one-click `update.sh`. Depends on epic 4 for the rollback safety net on the production Pi. |
| **6** | **Automatic Mode** | §7 + §6.4, §5.7 | Adjust `max_common_clients` within operator bounds. Requires **audit-log infrastructure** (built within this epic) plus bounds/hysteresis/disable to avoid Pi restart cascades (§5.7). Backup is **not** a prerequisite. |
| **7** | **Health Score** | §7 | Composite of uptime %, broker-live %, avg clients, bandwidth headroom. Fully unblocked today (all inputs exist); placed last by product choice, no dependency forces it earlier. |

**Also preserved (not dropped):** **Per-direction bandwidth display** (§7) remains a
planned read-only item, delivered alongside the operations epics (4–7). **Ryve
rewards / points** remain **explicitly out-of-scope** (§3.2 E6, §8, §9) until
Psiphon exposes the data via a metric or a documented API — epic 3 delivers claim
+ ProxyID identity only, **not** rewards.

---

## 7. Operations & Analytics — Feature Catalogue

> **Status: ✅ Traffic UI CLOSED — shipped early (v0.1.x → v0.2 cycle, in production).**
> The persistent traffic collector (SQLite, configurable retention) and the
> "Lifetime & History" dashboard card — Current Run, Lifetime totals, 24h/7d
> windows, SVG time-series chart, and recording/empty/error states — backed by
> `GET /api/traffic/summary` and `GET /api/traffic/series`, are delivered and
> production-validated (delivered in the **v0.2.x** line). Recorded here for history;
> this is **not** a future candidate epic. In the table below, **Backup & Restore was
> delivered in v0.3.0**; the remaining rows (Health Score, Automatic Mode, Update
> Centre, Per-direction bandwidth) are **v0.4 candidates**.

| Feature | Description |
|---|---|
| Health score | Composite score derived from uptime %, broker live %, average client count, and bandwidth headroom. Displayed on Overview screen. |
| Smart Assistant — Automatic mode | Adjusts `max_common_clients` within operator-defined bounds. Requires audit log infrastructure from this release. |
| Backup & restore | **✅ Delivered in v0.3.0.** Export CCC configuration (not Conduit key) to encrypted archive. Restore from archive. |
| Update centre | Check for new CCC releases via GitHub releases API. Display release notes. One-click update (runs `update.sh`). No automatic updates — operator must confirm. |
| Per-direction bandwidth display | Show upstream / downstream limits separately if set in `psiphon_config.json`. Read-only; write deferred. |

---

## 8. Personal Mode & Ryve — Feature Catalogue

> **Personal Mode status (2026-06-19): ✅ CLOSED — DELIVERED in v0.3.0 and production-validated.**
> Personal Mode + Ryve shipped under tag **v0.3.0** (first public release), not v0.4.0.
> The Personal Mode rows below (setup, pairing token, personal client limit
> control) are delivered as the **C4–C6 backend + Frontend Slices 1–4** and
> validated on a Raspberry Pi 4 (C6e). **Regenerate / Restore UI** is deferred
> (the C6c backend is retained); the **Regional-Analytics scope filter** is
> upstream-blocked (Conduit exposes no personal-vs-common runtime breakdown). The
> **Ryve** rows remain a separate future epic. See
> `docs/closure/PERSONAL_MODE_CLOSURE.md`.

| Feature | Description |
|---|---|
| Personal mode setup | **✅ DELIVERED (v0.3.0).** Generate compartment ID (`conduit new-compartment-id`), persist to `personal_compartment.json`, display in Config panel. |
| Pairing token generation | **✅ DELIVERED (v0.3.0).** Build v1 pairing tokens (`BuildPersonalPairingToken(id, name)`). Display as QR code and copyable string. Max name: 32 chars. _(Regenerate / Restore UI deferred; C6c backend retained.)_ |
| Personal client limit control | **✅ DELIVERED (v0.3.0).** Expose `--max-personal-clients` write path in Config panel (requires compartment ID to be set first). Uses `--max-personal-clients N` flag — not `--set InproxyMaxPersonalClients=N`. |
| Scope filter in Regional Analytics | **⏸ DEFERRED — upstream-blocked.** Show common / personal scope breakdown when personal clients > 0. Conduit exposes only an aggregate connected-client count (no personal-vs-common runtime metric); requires upstream Conduit support. |
| Ryve claim QR | Invoke `conduit ryve-claim --output <tmp_path>` as subprocess after modal confirmation (see Section 5.8). Serve QR PNG. Delete from disk after display. **Never store or log the private key.** |
| ProxyID display | Show Curve25519-derived ProxyID from `conduit ryve-claim` output. Display-only. |
| Ryve rewards | Out of scope until Psiphon exposes rewards data via metrics or a documented API. |

> **Security constraint (Ryve):** `conduit ryve-claim` prints the private key to stdout. CCC must capture only the PNG output path and ProxyID field. The private key field must be discarded before any logging or storage. A full-screen modal warning (per Section 5.8) must be shown before invoking the command.

---

## 9. Out of Scope

The following items are explicitly out of scope for all planned versions and require formal re-evaluation before being added:

| Item | Reason |
|---|---|
| Ryve rewards / points data | Not exposed in the Conduit binary; requires a documented Ryve API. |
| Per-user invitation management | Supported in Android app but not in Linux CLI. No CLI mechanism confirmed. |
| Separate upstream/downstream bandwidth write | Requires changes to `psiphon_config.json`, not the `--set` allowlist. Deferred pending a cleaner abstraction. |
| Auto-update | Update centre supports one-click update only. No unattended upgrade mechanism. |
| Multi-node management | CCC manages one Conduit instance per installation. Orchestration of multiple nodes is a separate product concern. |
| Psiphon config file editing | `psiphon_config.json` is provided externally. CCC does not generate or modify it. |

---

## Documentation Workstream

The product roadmap above tracks application features. This section tracks the
**documentation programme** so that doc epics are visible and their status is
unambiguous.

### Documentation Delivery

Completed documentation milestones:

| Milestone | Status | Evidence |
|---|---|---|
| English User Guide | ✅ **CLOSED** | `775848b` |
| Persian User Guide (text) | ✅ **CLOSED** | `fe86f80`, `c45fc48` |
| Diagram Program | ✅ **CLOSED** — DGM-01–19 produced and integrated in **both editions**: English (ch04–14) and Persian parity completed under Epic D (FA ch04/04a/05/10/11/12/14). Full EN↔FA diagram parity. | `c898201` … `8a8379b`; Epic D (rev 1.15) |
| Screenshot Program (K.5) | ✅ **CLOSED** | `c354993`; Parity Guard PASS |
| Documentation Governance | ✅ **CLOSED** | `971b1bf`; `docs/release-checklist.md` |
| Documentation Platform (MkDocs Phase 1) | ✅ **CLOSED** | MkDocs Material site, Persian RTL, self-hosted fonts, bilingual landing page, RTL/LTR Authoring Style Guide v1.0; `website/`, `mkdocs.yml`, `CONTRIBUTING.md` (rev 1.16) |

> **Deferred — Documentation Normalization:** normalize existing chapters to the RTL/LTR Authoring Style Guide v1.0 (identifier backticks, command fencing, `.tech-list` tagging, EN/FA parity). Tracked in `docs/PROJECT-STATUS.md` → Deferred Work. This is **separate** from the completed Documentation Platform milestone.

### Documentation Accuracy & Onboarding

Documentation-quality and onboarding improvements (open):

| Item | Description | Status | Priority |
|---|---|---|---|
| D2 — Cloudflare screenshot correction | Replace `cloudflare-domain-active.png` so the activated zone is the root domain (`example.com`), not a subdomain | ✅ Released v0.3.1 (2026-06-24) | P1 |
| D3 — TLS onboarding | Origin-Certificate workflow integrated into the guide flow (EN ch05 §5.15 + ch06 §6.4; FA parity); **bilingual** detailed guides — `docs/tls-setup.md` (EN) + `docs/fa/tls-setup.md` (FA), language-routed chapter links, 8 shared redacted screenshots | ✅ Done (Epic C; FA guide via Epic D, unreleased) | P1 |
| _(future)_ | Reserved for onboarding / documentation-accuracy items surfaced after v0.3.0 | Open | — |

### Documentation Milestones

Documentation snapshots are **not** product releases and are tracked here:

| Milestone | Tag | Date | Note |
|---|---|---|---|
| docs-v0.3 | `docs-v0.3` | 2026-06-22 | Documentation snapshot — EN + FA user guide, diagram program, screenshot program (K.5). **Documentation Milestone, not a product release.** |

---

## Maintenance & Patch Releases

### v0.3.1 — Patch (released 2026-06-24)

| ID | Fix | Priority | Scope |
|---|---|---|---|
| D1 | Root URL `/` → `/dashboard` redirect (reuse existing auth flow) | **P0** | Backend route + test; no nginx / doc change |
| D2 | Replace `cloudflare-domain-active.png` (root domain Active) | P1 | Image recapture + redaction |

### Log Management / SD-Card Protection — ✅ Released in v0.3.5 (commit `a6b6bd4`)

Minimal, Linux-native log/disk hygiene for SD-card-based Raspberry Pi installs:

- **logrotate** config for `/var/log/conduit-cc/*.log` (`deployment/conduit-cc.logrotate`) — provisioned by `install.sh`, re-provisioned by `update.sh`, removed by `uninstall.sh`.
- **Temp-dir cleanup** in `ccc-update-apply`: sweeps stale `ccc-update-*` work directories (under the existing update flock) and removes the current work directory **only after** the terminal `update-status.json` is written.
- **No** new privileged helper, sudoers rule, systemd timer, dashboard cleanup feature, or journald change.

Postponed (see *v0.4 Candidates*): journald drop-ins, dashboard cleanup action, scheduled cleanup timer, installer retention prompt, advanced log management.

### One-Click Update — Completed / Production-Proven / Maintenance Only

**Status: Completed / Production-Proven / Maintenance Only (v0.3.12).** The
dashboard-driven One-Click Update (Feature 2, Trusted Update Engine) is delivered
and was validated end-to-end on real Raspberry Pi hardware twice — v0.3.10 →
v0.3.11 (B1 transient-unit engine) and the final frontend-polish v0.3.11 →
v0.3.12. See the closure record: `docs/closure/one-click-update-closure.md`.

- **Deployment.** One-Click Update is the delivered, production-proven update
  mechanism for end users; a manual SSH `update.sh` deployment is retained for
  initial install, disaster recovery, and emergency maintenance. The earlier
  one-time *validation milestone* (rev 1.19 / 1.20) is now **historical**.
- **Maintenance Only.** Future One-Click Update work is limited to **bug fixes,
  security hardening, and maintenance**. No further feature development is planned.
- **Architectural stability.** The Trusted Update Engine architecture is stable;
  any functional expansion requires an **explicit new ADR** rather than an
  incremental extension of this completed subsystem.
- **Deferred hardening.** Artifact integrity / signing (ADR-0001 invariant 5)
  remains deferred and is intentionally not part of the closure.

---

## v0.4 Candidates (not yet scheduled)

Genuinely future items, consolidated from §7/§8 and the maintenance list. None are
delivered; none are scheduled.

| Candidate | Source | Note |
|---|---|---|
| Automatic Mode | §7, §5.7 | Raspberry Pi restart-cascade risk; needs audit-log infrastructure |
| Health Score | §7 | All inputs exist; unblocked |
| Per-direction bandwidth display | §7, §5.6 | Read-only; not yet built |
| Personal Regenerate / Restore UI | §8 | C6c backend retained; UI deferred |
| Regional Analytics scope filter | §8 (D14 / D17) | Upstream-blocked (no personal-vs-common runtime metric) |
| Full pairing implementation | §4 `pairing-neutralise` | `/api/conduit/pair` currently returns 501 |
| Ryve rewards | §8 / §9 | Out of scope until Psiphon exposes the data via a metric or documented API |
| journald size drop-ins | Log Mgmt (postponed) | System-wide journald config; deliberately not changed by CCC; revisit only under disk pressure |
| Dashboard log-cleanup action | Log Mgmt (postponed) | Rejected for the minimal feature (attack surface, evidence loss, needs a privileged helper) |
| Scheduled cleanup timer | Log Mgmt (postponed) | Not needed; OS-run logrotate + the flock-guarded sweep cover it |
| Installer log-retention prompt | Log Mgmt (postponed) | Installer-managed retention sizing; future enhancement |
| Advanced log management | Log Mgmt (postponed) | Broader retention/observability; only if the minimal approach proves insufficient |

---

## Next Phase / Candidate Drivers

One-Click Update (Feature 2) is closed (Completed / Production-Proven /
Maintenance Only). With no feature increment currently approved, the project
**remains Idle between phases** until the Project Owner selects a driver. Per the
Project Lifecycle, a new phase opens only on a real driver — **none is chosen
here**.

**"Idle between phases" means only that no _Implementation Phase_ is currently
open — not that the project is inactive.** Consistent with
`docs/ENGINEERING-PROCESS-GUIDE.md` and `docs/PROJECT-LIFECYCLE.md`, architecture
work, documentation, ADR work, research, discovery, planning, and the
Architecture Atlas may all continue during this state. Only the **implementation
of a new feature** requires a new approved engineering driver and a Value Gate.

The following are **candidate drivers only** (not selected, not scheduled). Each
requires explicit Project-Owner approval and a Value-Gate pass before any work
begins; those marked *(new ADR)* also require a new architectural decision record:

- **Architecture Atlas** — the approved long-term architectural knowledge
  infrastructure for CCC: an architectural-documentation and capability-discovery
  initiative, **not** a product-feature implementation. It was intentionally
  deferred until the architecture phase was complete and One-Click Update reached
  Production-Proven status, both of which now hold.
- **Conduit Core update design** *(new ADR)* — the successor to CCC-only updates;
  a design/architecture effort, distinct from and building on the completed
  Feature 2. It is a new architectural evolution, not an extension of the closed
  subsystem.
- **Artifact signing** — cryptographic integrity for update artifacts
  (ADR-0001 invariant 5); the principal deferred security-hardening item.
- **Documentation Normalization** — RTL/LTR authoring-style normalization of the
  existing chapters (deferred; see *v0.4 Candidates* and PROJECT-STATUS §6).

Selecting any of these is a Project-Owner decision. Until one is chosen, no next
phase is active.

---

## 10. Revision History

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-06-10 | CCC Development Team | Initial draft. R1 capability audit complete. |
| 1.1 | 2026-06-10 | CCC Development Team | Added Section 5: D1 — UX/UI Design Milestone as formal gate for v0.2.0. Sections 5–9 renumbered to 6–10. Smart Assistant Automatic mode deferred to v0.3.0. Theme persistence changed from localStorage to server-side cookie. |
| 1.2 | 2026-06-10 | CCC Development Team | Section 4 corrected: retitled to v0.1.0 Maintenance Items (folded into first public release); replaced collision issue numbers #3–#6 with descriptive names; removed inaccurate "open GitHub issues" claim. |
| 1.3 | 2026-06-14 | CCC Development Team | Reconciliation: marked D1 complete; noted historical traffic charts delivered early (§7); added v0.2.0 status note; reconciled CHANGELOG with the v0.1.1 tag (0.1.0 MVP + separate 0.1.1 maintenance). No milestone renumbering. |
| 1.4 | 2026-06-16 | CCC Development Team | §7: marked Traffic UI CLOSED (persistent collector + Lifetime & History card with SVG chart, backed by `/api/traffic/summary` & `/api/traffic/series`) and removed the "Historical charts" row from the v0.3.0 candidate table — recorded as shipped, no longer a future candidate. No other sections changed. |
| 1.5 | 2026-06-16 | CCC Development Team | Regional Analytics closure: §6.3 marked ✅ DELIVERED (MVP) with delivered-vs-spec reconciliation and a deferred remainder; §3.2 matrix D12/D13/D15 → delivered, D14/D17 annotated (connecting-clients and scope filter deferred); §6 v0.2.0 status updated (RA removed from outstanding). Closure record added at `docs/closure/regional-analytics-closure.md`. No milestone renumbering. |
| 1.6 | 2026-06-16 | CCC Development Team | Bandwidth Scheduling closure: §6.5 marked ✅ DELIVERED (commit `f838ff4`; CI #109–#113) with the confirmed reduced-mode model (HH:MM UTC, runtime switching in tunnel-core, no CCC scheduler, no boundary restarts, restart only on value change); §3.2 C6/C7 → delivered and C8 de-conflated (`conduit-monitor` quota throttle separated from `InproxyReduced*`, noted not deployed by CCC); §5.7 day-of-week selector removed and the 100 GB/7-day tooltip corrected; §6 v0.2.0 status updated (scheduling removed from outstanding). Closure record at `docs/closure/bandwidth-scheduling-closure.md`. No milestone renumbering. |
| 1.7 | 2026-06-17 | CCC Development Team | Live Operations closure: §6.2/§6.6 marked ✅ DELIVERED (Option 1 — Node Status extension; commits `3741b71`/`d61a478`/`b4bc9c1`, CI #115); §3.2 D1/D2/D4/D11 → delivered, D8/D9 noted delivered-via-Traffic, D10 deferred (service uptime only), D16 build_rev partial, D6 noted as a minor remaining gap; §3.3 is_live note updated; §6.4 reconciled (Contribution Advisor supersedes the original Manual/Assisted concept; Automatic → v0.3.0); §6 v0.2.0 status updated. Closure record added at `docs/closure/live-operations-closure.md`. No milestone renumbering. |
| 1.9 | 2026-06-17 | CCC Development Team | **Approved Delivery Priority (USER-VALUE-FIRST) recorded** as a new authoritative section before §7, documenting the post-v0.2.0 execution order (1 Branding & Identity, 2 Personal Mode, 3 Ryve Claim / Identity, 4 Backup & Restore, 5 Update Center, 6 Automatic Mode, 7 Health Score) **without renumbering** §7/§8 — those sections are preserved as feature catalogues. D6 recorded as part of Personal Mode; **"Ryve Rewards" renamed to "Ryve Claim / Identity"** with rewards/points kept explicitly out-of-scope (§3.2 E6, §8, §9); Per-direction bandwidth display and Regional-Analytics scope filter explicitly preserved. Companion to the `APP_VERSION 0.1.0 → 0.2.0` consistency cleanup (CHANGELOG `[0.2.0]` stamp + version guard + `docs/release-checklist.md`). No milestone renumbering. |
| 1.10 | 2026-06-19 | CCC Development Team | **Personal Mode epic CLOSED — DELIVERED and production-validated.** §3.2 matrix A1–A4 and D6 → ✅ DELIVERED (v0.4.0); §8 Personal Mode rows annotated DELIVERED (C4–C6 backend + Frontend Slices 1–4), with Regenerate/Restore **UI** recorded as deferred (C6c backend retained) and the Regional-Analytics **scope filter** as upstream-blocked (no personal-vs-common runtime metric). Production-validated on a Raspberry Pi 4 (C6e); EROFS production bug fixed in `39ba3eb` (`ReadWritePaths=/var/lib/conduit/data` + private-key `ReadOnlyPaths` carve-out + `After=conduit.service`, no `Wants=` pull-in). Closure record `docs/closure/PERSONAL_MODE_CLOSURE.md`; CHANGELOG `[Unreleased]` entry added. No milestone renumbering. |
| 1.8 | 2026-06-17 | CCC Development Team | **Theme Support closure + v0.2.0 CLOSED.** §6.7 marked ✅ DELIVERED (TS4 Raspberry Pi validation; commits `46547c0`/`df49f42`/TS3, CI #117–#118) with server-rendered flash-free first paint and no localStorage; §6 v0.2.0 status updated to **CLOSED — all features delivered**. **D6 (`max_personal_clients`) reclassified** from a minor v0.2 gap to v0.4.0 Personal Mode in §3.2 and §6.1 (folds into "Personal client limit control", §8). §6.4 stale three-mode prose reconciled (superseded by the Contribution Advisor; Automatic → v0.3.0). Closure record added at `docs/closure/theme-support-closure.md`. No milestone renumbering. |
| 1.11 | 2026-06-24 | CCC Development Team | **Roadmap reconciliation (Epic A.1).** Re-anchored §7/§8 from version-numbered titles to feature catalogues ("Operations & Analytics"; "Personal Mode & Ryve"). Corrected all "DELIVERED v0.4.0" → "v0.3.0" (Personal Mode + Ryve Claim + Backup shipped under tag v0.3.0 — first public release). Relabelled genuinely-future items (Automatic Mode, Update Centre, Health Score, Per-direction bandwidth, Regional-Analytics scope filter, full pairing) as **v0.4 candidates**; chart/traffic references re-labelled v0.2.x. Added Release History (product releases only), Documentation Workstream (Delivery + Accuracy & Onboarding + Documentation Milestones, with docs-v0.3 classified as a documentation milestone, not a product release), Maintenance & Patch Releases (v0.3.1 = D1 + D2), and v0.4 Candidates sections. Diagram Program marked CLOSED (EN); DGM-13–19 Persian parity assigned to Epic D and does not reopen it. Bannered "Approved Delivery Priority" as a historical planning artifact. Historical revision rows (≤1.10) left intact and not reordered. Status → Reconciled. No feature scope changed. |
| 1.12 | 2026-06-24 | CCC Development Team | **Post-v0.3.1 state reconciliation.** Marked v0.3.1 as **released 2026-06-24** in the Release History table, the Documentation Accuracy D2 row, and the Maintenance/Patch section (previously "planned/TBD"). No feature scope changed; documentation-only. (Backup Contract Alignment is internal correctness, tracked in `docs/PROJECT-STATUS.md`, not the roadmap.) |
| 1.13 | 2026-06-24 | CCC Development Team | **Epic C (TLS / Origin Certificate Onboarding, D3) closed.** EN onboarding (ch05 §5.15 + ch06 §6.4 note, `83d2ed0`) and FA parity (`652f028`) integrate the Cloudflare Origin Certificate workflow into the guide flow; text-only (no screenshots); the detailed guide remains canonical in English (`docs/tls-setup.md`) — `docs/fa/tls-setup.md` intentionally not created. D3 row marked Done. Documentation-only; unreleased (next patch). |
| 1.14 | 2026-06-24 | CCC Development Team | **TLS onboarding made bilingual (Epic D Phases 1–3) — supersedes the rev-1.13 "English-only" decision.** `docs/fa/tls-setup.md` created (`05366fe`); EN/FA chapter references converted to language-routed markdown links — EN → `../tls-setup.md`, FA → `../tls-setup.md`→`docs/fa/tls-setup.md` (`957d497`); the 8 redacted TLS screenshots are **shared** by both guides (no duplicate FA images). D3 row updated to bilingual. Manifest/INTEGRATION-SYNC/PROJECT-STATUS reconciled. Historical rev-1.13 row left intact. Documentation-only; unreleased. |
| 1.15 | 2026-06-25 | CCC Development Team | **Epic D — D4 (FA diagram parity) completed.** All 19 diagrams (DGM-01–19) integrated into the Persian guide: DGM-01–12 in FA ch04/04a/05 (Batches 1–2) and DGM-13–19 in FA ch10/11/12/14 (Batch 3), with the legacy Persian ASCII-art diagram blocks they replace removed and EN-paralleled navigation/micro-flows preserved. Full EN↔FA diagram parity. Diagram Program note updated to CLOSED (both editions); D4 marked Resolved in `docs/PROJECT-STATUS.md`; Manifest reconciled; CHANGELOG `[Unreleased]` entry added. Historical rev-≤1.14 rows left intact. Documentation-only; unreleased. |
| 1.16 | 2026-06-27 | CCC Development Team | **Documentation Platform (MkDocs Phase 1) closed** as a platform/governance milestone. Added the MkDocs Material site (renders existing `docs/` in place, curated nav, `exclude_docs` for internal files), Persian RTL/LTR base support, self-hosted Vazirmatn + Inter (no Google Fonts/CDN/analytics), a bilingual landing page, and the **RTL/LTR Documentation Authoring Style Guide v1.0** (frozen into `CONTRIBUTING.md`). Added `.tech-list` CSS support. **Documentation Normalization** of existing chapters is explicitly **Deferred** (PROJECT-STATUS Deferred Work) — no chapter content changed. Documentation-only; unreleased. |
| 1.17 | 2026-06-28 | CCC Development Team | **v0.3.2 released.** Cloudflare-compatible HTTPS port selection (Feature 1: installer prompt + `ccc-apply-https-port` + dashboard read-only display; `update.sh` preserves the chosen port) and one-click CCC update (Feature 2: dashboard Software Updates → `/api/update` → `ccc-update-apply` → `update.sh --ccc-only`; GitHub Releases stable-only; async status + reconnect + automatic rollback; no auto-update; Conduit Core out of scope). Validated on Raspberry Pi 4 and Pi 3 B (1 GB). CHANGELOG stamped; `APP_VERSION` 0.3.2; closure record `docs/closure/v0.3.2-closure.md`. Historical rev-≤1.16 rows intact. |
| 1.18 | 2026-06-28 | CCC Development Team | **Log Management / SD-Card Protection recorded (documentation-only; feature commit `a6b6bd4`, unreleased).** Added a "Log Management / SD-Card Protection — Delivered" subsection under *Maintenance & Patch Releases* (logrotate for `/var/log/conduit-cc/*.log` provisioned via install/update/uninstall; `ccc-update-apply` stale-`ccc-update-*` sweep under the update flock + current-workdir removal after the terminal `update-status.json`; Linux-native; no new helper/sudoers/timer/dashboard/journald) and five **postponed** items under *v0.4 Candidates* (journald drop-ins, dashboard cleanup, cleanup timer, installer retention prompt, advanced log management). Historical rev-≤1.17 rows intact; no feature scope changed. |
| 1.19 | 2026-06-28 | CCC Development Team | **Release-status reconciliation + Deployment Strategy milestone.** Reconciled the Release History table (and `docs/PROJECT-STATUS.md`) to reflect **v0.3.2 released**, the **v0.3.3 validation release** (exposed the `/run/lock` EROFS), and **v0.3.4 released** (EROFS lock-path fix → `/var/lib/conduit-cc/.update.lock`); added planned **v0.3.5**. Marked Log Management / SD-Card Protection (`a6b6bd4`) **complete in code, validation scheduled for v0.3.5**. Added a **Deployment Strategy** milestone: v0.3.5 is the final *planned* manual Pi deployment to validate the full dashboard One-Click Update workflow; after success, dashboard One-Click Update becomes the standard deployment mechanism, with manual `update.sh` retained for initial install, disaster recovery, and emergency maintenance (not a removal). Postponed Log Management items and all historical rev-≤1.18 rows left intact; no feature scope changed. |
| 1.20 | 2026-06-28 | CCC Development Team | **Deployment Strategy corrected.** The rev-1.19 wording wrongly implied dashboard One-Click Update would become the project's permanent/standard deployment workflow. Restated as a **One-Click Update *validation* milestone**: v0.3.5 is installed **once** via manual SSH (`update.sh`); the **next** update is performed from the Dashboard **solely** to validate One-Click Update end-to-end; **after success, the normal development workflow returns to SSH-based `update.sh` deployment**; Dashboard update remains a supported **end-user** capability but does **not** replace the project's normal development workflow. Updated the *Deployment Strategy* subsection, the 0.3.5 Release-History row, and `docs/PROJECT-STATUS.md` (§1/§3/§8/§9/§10). Rev-1.19 row left intact (history preserved); no feature scope changed. |
| 1.21 | 2026-07-02 | CCC Development Team | **Reconciled to v0.3.12.** One-Click Update / Trusted Update Engine marked **Completed / Production-Proven / Maintenance Only**. Added shipped releases **v0.3.5–v0.3.12** to Release History and removed the obsolete *v0.3.5 (planned)* validation-milestone row. Replaced the *Deployment Strategy — validation milestone* subsection with a *One-Click Update — Completed / Production-Proven / Maintenance Only* status subsection referencing `docs/closure/one-click-update-closure.md` (future work limited to bug fixes / security hardening / maintenance; functional expansion requires a new ADR; artifact signing deferred). Marked **Log Management / SD-Card Protection released in v0.3.5**; removed **Update Centre** from *v0.4 Candidates* (delivered as Feature 2). Added a **Next Phase / Candidate Drivers** section (Conduit Core update design, Artifact signing, Documentation Normalization) as candidates only — the project remains **Idle between phases** until the Project Owner selects a driver. Roadmap Markdown only; PDF not regenerated; `docs/PROJECT-STATUS.md` unchanged (separate pass); historical rev-≤1.20 rows intact; no feature scope changed. |

---

*For questions or contributions, open an issue at the CCC GitHub repository.*
