# Conduit Control Center — Product Roadmap

**Document:** CCC_Product_Roadmap_v1  
**Revision:** 1.0  
**Date:** 2026-06-10  
**Status:** Draft for Review  
**Author:** CCC Development Team

---

## Table of Contents

1. [Overview](#1-overview)
2. [Guiding Principles](#2-guiding-principles)
3. [R1 — Capability Audit (Research Milestone)](#3-r1--capability-audit-research-milestone)
   - 3.1 [Audit Scope](#31-audit-scope)
   - 3.2 [Conduit Linux CLI Capability Matrix](#32-conduit-linux-cli-capability-matrix)
   - 3.3 [Confirmed Findings Summary](#33-confirmed-findings-summary)
4. [v0.1.1 — Maintenance Release](#4-v011--maintenance-release)
5. [v0.2.0 — Smart Conduit Control](#5-v020--smart-conduit-control)
   - 5.1 [Conduit Configuration](#51-conduit-configuration)
   - 5.2 [Live Operations Panel](#52-live-operations-panel)
   - 5.3 [Regional Analytics](#53-regional-analytics)
   - 5.4 [Smart Assistant](#54-smart-assistant)
   - 5.5 [Bandwidth Scheduling](#55-bandwidth-scheduling)
   - 5.6 [Broker Live Status](#56-broker-live-status)
   - 5.7 [Theme Support (Lower Priority)](#57-theme-support-lower-priority)
6. [v0.3.0 — Historical Analytics & Operations](#6-v030--historical-analytics--operations)
7. [v0.4.0 — Personal Mode & Ryve](#7-v040--personal-mode--ryve)
8. [Out of Scope](#8-out-of-scope)
9. [Revision History](#9-revision-history)

---

## 1. Overview

Conduit Control Center (CCC) is an open-source web dashboard for managing Psiphon Conduit nodes on Linux servers and Raspberry Pi devices. Its goal is to give volunteer operators a secure, lightweight, user-friendly interface that eliminates the need for direct CLI access for day-to-day operations.

This roadmap documents the planned evolution of CCC from a monitoring dashboard (v0.1) to a full control centre capable of configuring Conduit, visualising live and historical traffic, intelligently assisting operators with resource decisions, and managing personal relay identities.

**Target platform:** Raspberry Pi 4 (4 GB), Ubuntu 22.04 ARM64, public IP, Cloudflare DNS, Nginx, FastAPI, Python 3.

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

| # | Capability | Android App | Linux CLI | CCC Current | CCC Candidate |
|---|---|:---:|:---:|:---:|---|
| **A — Personal Mode** |||||
| A1 | Enable personal clients | ✅ | ✅ `--max-personal-clients` | ❌ | Read + Write (v0.4.0) |
| A2 | Compartment ID generation | ✅ | ✅ `new-compartment-id` cmd | ❌ | Generate + persist (v0.4.0) |
| A3 | Personal pairing token (v1) | ✅ | ✅ `BuildPersonalPairingToken` | ❌ | Generate + display (v0.4.0) |
| A4 | Personal pairing QR code | ✅ | ✅ (token → QR) | ❌ | Display in UI (v0.4.0) |
| A5 | `--compartment-id` accepts raw ID or token | — | ✅ `NormalizePersonalCompartmentInput` | ❌ | Accepts both formats (v0.4.0) |
| A6 | Pairing name max length | 32 chars | ✅ 32 chars enforced | ❌ | Validated input (v0.4.0) |
| A7 | Compartment persisted to disk | — | ✅ `personal_compartment.json` | ❌ | Read persisted ID (v0.4.0) |
| A8 | Per-user invitation management | ✅ | ❌ Not in CLI | ❌ | Out of scope |
| **B — Client Limits** |||||
| B1 | Max common clients (0–1000) | ✅ | ✅ `--max-common-clients` (default 50) | ❌ read | Read + Write (v0.2.0) |
| B2 | Max personal clients (0–1000) | ✅ | ✅ `--max-personal-clients` (default 0) | ❌ | Read (v0.2.0), Write (v0.4.0) |
| B3 | At-least-one-client validation | — | ✅ error if both = 0 | — | Enforce in UI (v0.2.0) |
| B4 | Runtime client limit update | — | ✅ `SetConfig(common, personal, bw)` | ❌ | Live apply via systemd restart (v0.2.0) |
| B5 | `InproxyMaxCommonClients` via `--set` | — | ✅ in allowlist | — | Covered by B1 |
| B6 | `InproxyMaxPersonalClients` via `--set` | — | ❌ not in `--set` allowlist | — | Use `--max-personal-clients` flag (v0.4.0) |
| **C — Bandwidth Controls** |||||
| C1 | Global bandwidth limit (Mbps) | ✅ | ✅ `--bandwidth` (default 40, -1 unlimited) | ❌ read | Read + Write (v0.2.0) |
| C2 | Bandwidth stored as bytes/sec | — | ✅ `int(Mbps × 1,000,000 / 8)` | — | Display as Mbps in UI (v0.2.0) |
| C3 | Separate upstream/downstream limits | ✅ | ✅ via JSON config only (`InproxyLimit*BytesPerSecond`) | ❌ | Out of scope for v0.2.0; consider v0.3.0 |
| C4 | Unlimited bandwidth (`-1`) | ✅ | ✅ | ❌ | Toggle in UI (v0.2.0) |
| C5 | Runtime bandwidth update | — | ✅ `SetConfig(…, bandwidthBytesPerSecond)` | ❌ | Live apply via systemd restart (v0.2.0) |
| C6 | Reduced-mode bandwidth scheduling | — | ✅ `InproxyReducedStartTime / EndTime / Limit*` via `--set` | ❌ | Scheduling UI (v0.2.0) |
| C7 | Reduced common clients during schedule | — | ✅ `InproxyReducedMaxCommonClients` via `--set` | ❌ | Scheduling UI (v0.2.0) |
| C8 | Minimum throttle protection | — | ✅ 100 GB / 7 days enforced by `conduit-monitor` | — | Document in UI tooltip (v0.2.0) |
| **D — Metrics** |||||
| D1 | `conduit_announcing` | — | ✅ | ✅ | Already exposed |
| D2 | `conduit_connecting_clients` | — | ✅ | ✅ | Already exposed |
| D3 | `conduit_connected_clients` | — | ✅ | ✅ | Already exposed |
| D4 | `conduit_is_live` (broker connection) | — | ✅ | ❌ | Add to Live Ops (v0.2.0) |
| D5 | `conduit_max_common_clients` | — | ✅ | ✅ | Already exposed |
| D6 | `conduit_max_personal_clients` | — | ✅ | ❌ | Show in Config panel (v0.2.0) |
| D7 | `conduit_bandwidth_limit_bytes_per_second` | — | ✅ (0 = unlimited) | ❌ | Show in Config panel (v0.2.0) |
| D8 | `conduit_bytes_uploaded` | — | ✅ cumulative | ❌ | Show in Live Ops (v0.2.0) |
| D9 | `conduit_bytes_downloaded` | — | ✅ cumulative | ❌ | Show in Live Ops (v0.2.0) |
| D10 | `conduit_uptime_seconds` | — | ✅ GaugeFunc (computed at scrape) | ❌ | Show in Live Ops (v0.2.0) |
| D11 | `conduit_idle_seconds` | — | ✅ GaugeFunc (computed at scrape) | ❌ | Show in Live Ops (v0.2.0) |
| D12 | `conduit_region_bytes_uploaded{scope,region}` | — | ✅ | ❌ | Regional Analytics (v0.2.0) |
| D13 | `conduit_region_bytes_downloaded{scope,region}` | — | ✅ | ❌ | Regional Analytics (v0.2.0) |
| D14 | `conduit_region_connecting_clients{scope,region}` | — | ✅ | ❌ | Regional Analytics (v0.2.0) |
| D15 | `conduit_region_connected_clients{scope,region}` | — | ✅ | ❌ | Regional Analytics (v0.2.0) |
| D16 | `conduit_build_info{build_repo,build_rev,go_version,values_rev}` | — | ✅ | partial | Expose full build info (v0.2.0) |
| D17 | Scope labels: `common` / `personal` | — | ✅ | ❌ | Scope filter in Regional Analytics (v0.2.0) |
| **E — Ryve** |||||
| E1 | `conduit ryve-claim` command | — | ✅ | ❌ | Subprocess invocation (v0.4.0) |
| E2 | Claim URI (`network.ryve.app://…`) | — | ✅ | ❌ | Display claim QR (v0.4.0) |
| E3 | Claim QR PNG saved to `data/ryve-claim-qr.png` | — | ✅ | ❌ | Serve PNG from data dir (v0.4.0) |
| E4 | ProxyID (Curve25519 public key) | — | ✅ | ❌ | Display ProxyID (v0.4.0) |
| E5 | Private key exposure in claim | — | ✅ requires explicit confirmation | ❌ | **CCC must NEVER store or display private key** |
| E6 | Ryve rewards data | — | ❌ not in CLI source | ❌ | Out of scope until confirmed |
| E7 | Station name / identity label | — | ✅ `--name` flag on `ryve-claim` | ❌ | Pre-fill with hostname (v0.4.0) |

---

### 3.3 Confirmed Findings Summary

**Personal Mode** is fully supported in Linux CLI via `--max-personal-clients` and `--compartment-id`. A compartment ID must be generated first; the CLI errors if personal > 0 and no compartment ID is configured. Personal pairing tokens use a defined v1 format (base64url-encoded JSON `{v:"1", data:{id, name}}`). CCC v0.4.0 can implement the full personal-mode workflow.

**Client limits** are runtime-configurable via `SetConfig()`, which updates common clients, personal clients, and bandwidth in a single call. Since CCC manages Conduit via systemd, "live apply" in practice means writing new flags to the service unit and restarting — not an in-process update.

**Bandwidth** defaults to 40 Mbps (decimal, stored as bytes/sec). The `--bandwidth` flag controls a combined limit; separate upstream/downstream limits require JSON config overrides and are not exposed on the `--set` allowlist. Reduced-mode scheduling (`InproxyReduced*` via `--set`) is the correct mechanism for time-based throttling, including the v0.2.0 bandwidth scheduling feature.

**`conduit_is_live`** is a confirmed metric (1 = broker connected, 0 = disconnected) and is not yet surfaced in CCC. It should be added to the Live Operations panel in v0.2.0.

**Ryve** is a mobile app that claims a Conduit station by scanning a QR code encoding the station's private keypair. CCC must never store or display the private key itself. The safe workflow is: invoke `conduit ryve-claim --output <path>` as a subprocess (after explicit user confirmation), serve the resulting QR PNG temporarily, then delete it. The private key must never be logged, stored in the database, or transmitted over the API.

**`InproxyMaxPersonalClients` is not in the `--set` allowlist** (only `InproxyMaxCommonClients` is). Personal client limits must be set via the `--max-personal-clients` flag, not `--set`. This affects how CCC writes service unit overrides in v0.4.0.

---

## 4. v0.1.1 — Maintenance Release

v0.1.1 is a maintenance-only release. No new user-facing features. All items correspond to open GitHub issues.

| Issue | Title | Priority |
|---|---|:---:|
| #3 | Harden `pair()` subprocess PATH in `adapter.py` | High |
| #4 | Restrict `ccc.db` file permissions (644 → 600) | High |
| #5 | Remove duplicate DDNS cron entry from root crontab | Medium |
| #6 | Update UFW firewall rules in `pre-install.md` based on production validation | Low |

**Release criteria:** all four issues resolved, tests green, CHANGELOG updated.

---

## 5. v0.2.0 — Smart Conduit Control

v0.2.0 transforms CCC from a read-only monitoring dashboard into an interactive control centre. The central theme is: **display everything Conduit exposes and give operators control over the parameters they need most.**

### 5.1 Conduit Configuration

Display and allow editing of the three runtime-configurable parameters:

- **Max common clients** — current value from `conduit_max_common_clients`; editable (0–1000); enforces at-least-one-client rule.
- **Max personal clients** — current value from `conduit_max_personal_clients`; read-only in v0.2.0 (write deferred to v0.4.0 pending personal-mode setup flow).
- **Global bandwidth limit** — current value from `conduit_bandwidth_limit_bytes_per_second`; display as Mbps (0 = unlimited); editable with unlimited toggle.

**Apply mechanism:** write new flags to the systemd service unit drop-in (`/etc/systemd/system/conduit.service.d/ccc.conf`) and run `systemctl daemon-reload && systemctl restart conduit`. Confirm to the user that Conduit will restart briefly.

> **Note:** `conduit_bandwidth_limit_bytes_per_second` uses decimal megabits (1 Mbps = 125,000 bytes/sec, not 131,072). Display and input must use decimal Mbps to match CLI behaviour.

### 5.2 Live Operations Panel

Replace or extend the current metrics display with a dedicated Live Operations section showing:

| Metric | Source | Format |
|---|---|---|
| Broker status | `conduit_is_live` | Live / Offline badge |
| Connected clients | `conduit_connected_clients` | Integer |
| Connecting clients | `conduit_connecting_clients` | Integer |
| Uptime | `conduit_uptime_seconds` | `Xd Xh Xm Xs` |
| Idle time | `conduit_idle_seconds` | `Xd Xh Xm Xs` or "Active" |
| Bytes uploaded | `conduit_bytes_uploaded` | Human-readable (KiB/MiB/GiB/TiB) |
| Bytes downloaded | `conduit_bytes_downloaded` | Human-readable (KiB/MiB/GiB/TiB) |
| Build revision | `conduit_build_info` | Short rev hash + link to release |

**Human-readable units:** use binary prefixes (KiB, MiB, GiB, TiB) for byte values — these are more accurate for storage/transfer contexts. Display Mbps (decimal) only when describing the configured bandwidth limit, consistent with the CLI.

**`conduit_is_live` guidance:** if `conduit_is_live == 0` and `conduit_announcing == 1`, the station is trying to connect. Show a yellow "Connecting" state, not a red "Offline" state. Distinguish: not started → grey, connecting → yellow, live → green, disconnected after being live → red.

### 5.3 Regional Analytics

Display top 15 active regions with combined `common` + `personal` scope, sortable by connected clients or traffic.

| Column | Source |
|---|---|
| Flag + country name | ISO 3166-1 alpha-2 lookup |
| ISO code | `region` label from GaugeVec |
| Connected clients | `conduit_region_connected_clients{scope, region}` (sum across scopes) |
| Connecting clients | `conduit_region_connecting_clients{scope, region}` (sum) |
| Uploaded | `conduit_region_bytes_uploaded{scope, region}` (sum) |
| Downloaded | `conduit_region_bytes_downloaded{scope, region}` (sum) |

Rules:
- Regions with zero clients AND zero bytes in all columns are hidden.
- Country name mapping maintained in-process (no external API dependency).
- Scope filter (`all` / `common` / `personal`) shown only when personal clients > 0.

### 5.4 Smart Assistant

An analysis assistant that helps operators choose appropriate limits for their hardware.

Three modes, selectable in settings:

| Mode | Behaviour |
|---|---|
| **Manual** | Operator sets all limits directly; no suggestions. |
| **Assisted** | Operator sets limits; assistant analyses current CPU, RAM, and bandwidth utilisation and flags potential issues with an explanation. Suggestions are advisory. |
| **Automatic** | Assistant adjusts `max_common_clients` automatically within operator-defined bounds (`min`, `max`). Writes new values via the same drop-in mechanism as the Config panel. Requires explicit opt-in with a confirmation dialog. |

**Assisted analysis inputs:** CPU utilisation (from `/proc/stat` or `psutil`), RAM utilisation, `conduit_connected_clients`, `conduit_bandwidth_limit_bytes_per_second`, `conduit_bytes_uploaded`, `conduit_bytes_downloaded`.

**Automatic mode safety constraints:**
- Never reduce below operator-specified minimum.
- Never exceed operator-specified maximum.
- Minimum interval between automatic adjustments: 5 minutes.
- All changes are logged to the CCC audit log with reason.

> **GUIDE.md guidance:** Conduit supports approximately 150–350 concurrent users per CPU / 2 GB RAM pair. The assistant should reference this range when computing suggestions.

### 5.5 Bandwidth Scheduling

Allow operators to define time-based bandwidth profiles using Conduit's built-in reduced-mode mechanism (`InproxyReduced*`).

**Mechanism:** the `--set` flag accepts `InproxyReducedStartTime`, `InproxyReducedEndTime`, `InproxyReducedLimitUpstreamBytesPerSecond`, `InproxyReducedLimitDownstreamBytesPerSecond`, and `InproxyReducedMaxCommonClients`. CCC writes these as `--set` arguments in the service unit drop-in.

**UI:** define a "reduced" profile with:
- Start time (HH:MM, 24h)
- End time (HH:MM, 24h)
- Reduced bandwidth limit (Mbps)
- Reduced max common clients (optional)
- Schedule type: weekday / weekend / daily / custom weekday bitmask

**Minimum throttle protection:** display a tooltip that reduced values below 100 GB / 7 days are overridden by `conduit-monitor`. Do not allow values the operator cannot actually enforce.

**Example profile (from product direction):** 08:00–18:00 at 20 Mbps, restoring to full capacity outside those hours.

> Note: `InproxyReducedStartTime` and `InproxyReducedEndTime` are in the `--set` allowlist and therefore supported directly without JSON config file changes.

### 5.6 Broker Live Status

Surface `conduit_is_live` prominently in the dashboard header/status bar, distinct from `conduit_announcing`. Four states:

- **Not running** — Conduit service is stopped (no metrics endpoint reachable).
- **Starting** — Service running, `conduit_announcing == 1`, `conduit_is_live == 0`.
- **Live** — `conduit_is_live == 1`.
- **Disconnected** — Service running, previously was live, now `conduit_is_live == 0` and not announcing.

### 5.7 Theme Support (Lower Priority)

Light / Dark / System theme toggle, implemented in CSS custom properties. No JavaScript framework required. Persist preference in `localStorage`. This is a cosmetic feature and should not block other v0.2.0 items.

---

## 6. v0.3.0 — Historical Analytics & Operations

| Feature | Description |
|---|---|
| Historical charts | Time-series charts for connected clients, bytes transferred, bandwidth utilisation. Stored in SQLite with a configurable retention window (default 30 days). |
| Health score | Composite score derived from uptime, broker live percentage, average client count, and bandwidth headroom. Displayed on dashboard home. |
| Backup & restore | Export CCC configuration (not Conduit key) to encrypted archive. Restore from archive. |
| Update centre | Check for new CCC releases (GitHub releases API). Display release notes. One-click update (runs `update.sh`). No automatic updates — operator must confirm. |
| Per-direction bandwidth display | Show `InproxyLimitUpstreamBytesPerSecond` / `InproxyLimitDownstreamBytesPerSecond` separately if set in the Psiphon config. Read-only display; write deferred. |

---

## 7. v0.4.0 — Personal Mode & Ryve

| Feature | Description |
|---|---|
| Personal mode setup | Generate compartment ID (`conduit new-compartment-id`), persist to `personal_compartment.json`, display in Config panel. |
| Pairing token generation | Build v1 pairing tokens (`BuildPersonalPairingToken(id, name)`). Display as QR code and copyable string. Max name: 32 chars. |
| Personal client limit control | Expose `--max-personal-clients` write path in Config panel (requires compartment ID to be set first). |
| Scope filter in Regional Analytics | Show `common` / `personal` scope breakdown when personal clients > 0. |
| Ryve claim QR | Invoke `conduit ryve-claim --output <tmp_path>` as subprocess after explicit user confirmation. Serve QR PNG in dashboard. Delete from disk after display. **Never store or log the private key.** |
| ProxyID display | Show Curve25519-derived ProxyID from `conduit ryve-claim` output. Display-only. |
| Ryve rewards | Out of scope until Psiphon exposes rewards data via metrics or a documented API. |

> **Security constraint (Ryve):** `conduit ryve-claim` prints the private key to stdout. CCC must capture only the PNG output path and ProxyID field. The private-key field in the subprocess output must be discarded before any logging or storage. A prominent warning must be shown to the user before invoking the command.

---

## 8. Out of Scope

The following items are explicitly out of scope for all planned versions and require formal re-evaluation before being added:

- **Ryve rewards / points data** — not exposed in the Conduit binary; requires a documented Ryve API.
- **Per-user invitation management** — supported in the Android app but not in the Linux CLI. No CLI mechanism confirmed.
- **Separate upstream/downstream bandwidth write** — requires changes to the Psiphon JSON config file, not the `--set` allowlist. Deferred pending a cleaner abstraction.
- **Auto-update** — the update centre will support one-click update only. No unattended upgrade mechanism will be implemented.
- **Multi-node management** — CCC manages one Conduit instance per installation. Orchestration of multiple nodes is a separate product concern.
- **Psiphon config file editing** — the psiphon_config.json file is provided externally. CCC does not generate or modify it.

---

## 9. Revision History

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-06-10 | CCC Development Team | Initial draft. R1 capability audit complete. All capability matrix entries verified against psiphon-Inc/conduit source as of 2026-06-10. |

---

*For questions or contributions, open an issue at the CCC GitHub repository.*
