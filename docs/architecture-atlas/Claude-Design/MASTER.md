# CCC Architecture Atlas — MASTER (Claude Design Input)

**Release:** v1.0.0 · **Verified Revision:** v0.3.12 · **Self-contained:** yes
**Type:** Deterministic rendering specification (machine-oriented; single-file input)

> This document is **completely self-contained**. It requires no other files,
> includes, imports, references, or preprocessing. Every entity, relationship,
> mapping, and visual encoding needed to render the CCC architecture is inside this
> file. A conforming rendering engine must never invent visual semantics, infer
> boundary crossings, or look outside this document. All abbreviations are defined in
> §6 before first use.

---

## 1. Architecture Scope

Render the **current runtime architecture** of Conduit Control Center (CCC) at
`v0.3.12`: 21 Capabilities (CAP), 11 Subsystems (SUB), 70 Components (CMP), 44 Runtime
Flows (RF), 20 Data Flows (DF), 9 Trust Boundaries (TB), 8 External Systems (EXT),
across 13 Architecture Views. The Deployment view is reference-only and excluded from
runtime diagrams unless a Deployment diagram is explicitly requested.

## 2. Rendering Objectives

Optimize every diagram for: clarity, traceability, maintainability, minimal edge
crossings, architectural readability, engineering usefulness. Not for artistic
appearance.

## 3. Diagram Philosophy

The architecture determines the canvas — never the reverse. Diagrams may become
arbitrarily large. One diagram renders exactly one View; never merge views.

## 4. Critical Sizing Rules (content-driven — MUST FOLLOW)

Do not assume or set paper size, page size, canvas size, image size, PDF size, screen
resolution, A4/Letter, slide, poster, card, aspect ratio, or pagination. The diagram
expands with node/edge/grouping count. Large views use §16 (split, do not shrink).

## 5. Architecture Layers (rendering tiers)

Arrange flow/component diagrams along these tiers (top→bottom or left→right):
1. External actor — Operator (browser). 2. Edge — EXT-003 Cloudflare, EXT-008 nginx.
3. Presentation — frontend shell + per-domain UI components. 4. API — API router
components. 5. Domain — adapters, engines, backup/traffic modules. 6. Privileged
helpers — root/`conduit` helpers, updater. 7. External systems — EXT-001…EXT-007.
8. Stores — SQLite, filesystem, tmpfs/RAM.

## 6. Notation & Symbol Definitions (define-before-use)

- **`RF-nnn` / `DF-nnn` / `TB-nnn` / `EXT-nnn` / `CAP-nnn` / `SUB-nnn` / `CMP-nnn`** —
  entity identifiers; opaque, permanent.
- **Mode = `sync`** — synchronous request/response interaction.
- **Mode = `async`** — asynchronous, deferred, background, or fire-and-forget.
- **`caller → callee`** — directed interaction from caller to callee.
- **`crosses-TB`** — the exact Trust Boundary identifiers an edge crosses, or `none`.
- **`transports-DF`** — the exact Data Flow identifiers an edge carries, or `none`.
- **`transported-by-RF`** — the exact Runtime Flow identifiers that carry a Data Flow.
- **`run-as`** — the OS identity a helper executes as: `root` or `conduit`.
- No other abbreviations are used. Every token above is spelled explicitly in the
  tables; the engine never infers meaning from context.

## 7. Visual Legend (deterministic — MUST be used verbatim)

A conforming engine MUST use these exact encodings. Colors are hex; engines render
these values, not substitutes.

### 7.1 Node shapes
- Capability → **stadium** (fully rounded rectangle).
- Subsystem → **container rectangle**, 8px rounded corners, with a title bar.
- Component → **rectangle**, square corners.
- External System → **hexagon**.
- Persistent store (SQLite / filesystem) → **cylinder**.
- Transient store (tmpfs / RAM / memory) → **parallelogram**.
- Trust Boundary → **dashed rounded-rectangle zone** (may nest and may span subsystems).

### 7.2 Capability colors (by Capability Class) — fill / text
- Runtime → `#1F6FEB` / white.
- Background → `#8250DF` / white.
- Deployment → `#6E7781` / white.
- Administration → `#BC4C00` / white.
- N/A (Unwired) → `#57606A` / white.

### 7.3 Subsystem colors
- Container fill `#F6F8FA`, border `#57606A`, title bar `#EAEEF2`, title text `#24292F`.

### 7.4 Component colors (by kind) — fill / border, with type badge
- API router → `#DDF4FF` / `#54AEFF`, badge `API`.
- Adapter → `#DAFBE1` / `#4AC26B`, badge `ADP`.
- Pure logic → `#FFF8C5` / `#D4A72C`, badge `λ`.
- Helper (run-as root) → `#FFEBE9` / `#FF8182`, badge `root`.
- Helper (run-as conduit) → `#FFF1E5` / `#FB8F44`, badge `conduit`.
- Script / CLI → `#F6F8FA` / `#6E7781`, badge `CLI`.
- Frontend module → `#FBEFFF` / `#C297FF`, badge `UI`.
- Background task → `#DEF7F7` / `#3DB7BE`, badge `⏱`.
- Platform / core → `#EEF1F4` / `#57606A`, badge `core`.

### 7.5 Runtime Flow line styles (edges)
- `sync` → **solid** line, **filled triangle** arrowhead, color `#24292F`.
- `async` → **dashed** line (dash 4, gap 2), **open triangle** arrowhead, color `#57606A`.
- Label each edge `RF-nnn` + short verb.

### 7.6 Data Flow line styles (edges)
- Data Flow → **dotted** line (dash 1, gap 2), **thin open** arrowhead, color `#6639BA`.
- Label each edge `DF-nnn` + data object + storage glyph (§7.9).

### 7.7 Realization / containment
- Capability → Subsystem (implemented-by) → **dashed** line, **hollow triangle**
  arrowhead, color `#6E7781`.
- Subsystem → Component (contains) → **spatial nesting** (component drawn inside the
  subsystem container); no edge drawn.

### 7.8 Trust Boundary styles (dashed zone border color) + crossing marker
- TB-001 Edge → `#0969DA`. TB-002 Authentication → `#1A7F37`. TB-003 Privilege(sudo) →
  `#CF222E`. TB-004 Namespace → `#A40E26`. TB-005 CCC↔Conduit daemon → `#9A6700`.
  TB-006 Secret perimeter → `#8250DF`. TB-007 GitHub fetch → `#6639BA`. TB-008 DNS →
  `#BC4C00`. TB-009 Local persistence → `#57606A`.
- **Crossing marker:** a small filled circle in the TB color placed at the edge's
  crossing point, labelled with the `TB-nnn` id.

### 7.9 Storage glyphs (Data Flow label badge)
- runtime memory → `[MEM]`. SQLite → `[SQLITE]` (cylinder). filesystem → `[FS]`
  (cylinder). journal → `[JRNL]`. network → `[NET]`. external service → `[EXTSVC]`.
  temporary (tmpfs/RAM) → `[TMP]` (parallelogram).

### 7.10 External System glyph
- Hexagon, fill `#EAEEF2`, border `#24292F`, label `EXT-nnn Name`, protocol on the
  incident edge.

### 7.11 Status styles (node border/overlay)
- Current → normal solid border. Maintenance-Only → solid border + tag `M`.
- Unwired → **dashed border + 60% opacity + tag `UNWIRED`**.
- Not-Implemented → **dotted border + tag `501`**.
- Pure → tag `λ`. Deployment (excluded) → gray fill + tag `DEPLOY (excluded)`, drawn
  **outside** all runtime clusters.

### 7.12 Arrowheads (summary)
- filled triangle = sync call; open triangle = async; thin open = data; hollow
  triangle = realization.

### 7.13 Legend block (render on every diagram)
Include a legend enumerating: node shapes (§7.1), Class colors (§7.2), component-kind
colors+badges (§7.4), sync/async edges (§7.5), data-flow edge (§7.6), realization
edge (§7.7), the TB zone colors + crossing marker (§7.8), storage glyphs (§7.9), and
status styles (§7.11).

## 8. Architecture Views (one diagram each)

VIEW-01 Capability · VIEW-02 Subsystem · VIEW-03 Component (per-subsystem sheets) ·
VIEW-04 Runtime Flow · VIEW-05 Data Flow · VIEW-06 Trust Boundary · VIEW-07 External
Systems · VIEW-08 Deployment (reference) · VIEW-09 Security (composed) · VIEW-10
Operational (composed) · VIEW-11 Traceability (tabular, from §11) · VIEW-12
Capability-Class · VIEW-13 Status/Lifecycle.

## 9. Entity Definitions (inline, authoritative)

### 9.1 Capabilities (CAP) — id · name · class · status
CAP-001 Control Conduit lifecycle · Runtime · Current.
CAP-002 Read node status · Runtime · Current.
CAP-003 Manage Conduit configuration · Runtime · Current.
CAP-004 Pair node · Runtime · Not-Implemented.
CAP-005 Manage Personal compartment · Runtime · Current.
CAP-006 Provide Ryve claim QR · Runtime · Current.
CAP-007 Report Conduit traffic · Runtime · Current.
CAP-008 Report system metrics · Runtime · Current.
CAP-009 Provide contribution advice · Runtime · Current.
CAP-010 Tail Conduit log · Runtime · Current.
CAP-011 Report DDNS status · Runtime · Current.
CAP-012 Maintain public DNS record · Background · Current.
CAP-013 Create & inspect backup · Runtime · Current.
CAP-014 Restore from backup · Runtime · Current.
CAP-015 Self-update CCC · Runtime · Current · Maintenance-Only.
CAP-016 Select HTTPS port · Deployment · Current (excluded from runtime).
CAP-017 Authenticate & protect access · Runtime · Current.
CAP-018 Manage app settings · Runtime · Current.
CAP-019 Provide operator web interface · Runtime · Current.
CAP-020 Report health · Runtime · Current.
CAP-021 Evaluate required capabilities · N/A · Current · Pure · Unwired.

### 9.2 Subsystems (SUB) — id · name · primary CAP
SUB-001 Conduit Control [CAP-001,002,003,004,010] · SUB-002 Personal Mode [CAP-005] ·
SUB-003 Ryve Claim [CAP-006] · SUB-004 Traffic [CAP-007] · SUB-005 Contribution
Advisor [CAP-009] · SUB-006 Backup & Restore [CAP-013,014] · SUB-007 Trusted Update
Engine [CAP-015; Maintenance-Only] · SUB-008 Authentication & Access Control [CAP-017]
· SUB-009 Application Runtime Platform [CAP-008,018,019,020] · SUB-010 Capability
Evaluation [CAP-021; Unwired] · SUB-011 Dynamic DNS [CAP-011,012].

### 9.3 Components (CMP) — id · name · kind · run-as/status
- **SUB-001:** CMP-001 Conduit Control API (API) · CMP-002 Status API (API) · CMP-003
  Log API (API) · CMP-004 Conduit Adapter (adapter) · CMP-005 Config Validator (pure)
  · CMP-006 Config Helper (helper, root) · CMP-007 Status UI (frontend) · CMP-008
  Config UI (frontend) · CMP-009 Logs UI (frontend) · CMP-010 Regions UI (frontend).
- **SUB-002:** CMP-011 Personal API (API) · CMP-012 Personal Adapter (adapter) ·
  CMP-013 Compartment Helper (helper, conduit) · CMP-014 Personal UI (frontend).
- **SUB-003:** CMP-015 Ryve API (API) · CMP-016 Ryve Adapter (adapter) · CMP-017 Ryve
  Claim Helper (helper, conduit) · CMP-018 Ryve UI (frontend).
- **SUB-004:** CMP-019 Traffic Read API (API) · CMP-020 Traffic Collector (background)
  · CMP-021 Accounting (pure) · CMP-022 Retention (domain) · CMP-023 Reads (domain) ·
  CMP-024 Repository (domain) · CMP-025 Traffic UI (frontend) · CMP-026 Traffic History
  UI (frontend).
- **SUB-005:** CMP-027 Advisor API (API) · CMP-028 Advisor Engine (pure) · CMP-029
  Advisor UI (frontend).
- **SUB-006:** CMP-030 Backup API (API) · CMP-031 Archiver (domain) · CMP-032 Collector
  (domain) · CMP-033 Crypto (domain) · CMP-034 Key-Exclusion Guard (domain) · CMP-035
  Manifest (domain) · CMP-036 Restore Primitive (domain) · CMP-037 Archive Codec
  (domain) · CMP-038 Restore Helper (helper, root) · CMP-039 Backup UI (frontend).
- **SUB-007:** CMP-040 Update API (API) · CMP-041 Update Helper (helper, root) ·
  CMP-042 Updater Script (script) · CMP-043 Update UI (frontend). *(All Maintenance-Only.)*
- **SUB-008:** CMP-044 Auth API (API) · CMP-045 Login/Credential (domain) · CMP-046
  Session Store (domain) · CMP-047 Lockout (domain) · CMP-048 Cookie Helper (domain) ·
  CMP-049 Unlock CLI (script; Administration) · CMP-050 Login UI (frontend).
- **SUB-009:** CMP-051 Composition Root (core) · CMP-052 Configuration (core) · CMP-053
  Database (core) · CMP-054 Shared Dependencies (core) · CMP-055 Health Endpoint (API)
  · CMP-056 Web Shell Server (core) · CMP-057 System Metrics Endpoint (API) · CMP-058
  Settings Endpoint (API) · CMP-059 Client Runtime (frontend) · CMP-060 Client
  Bootstrap (frontend) · CMP-061 Dashboard Shell (frontend) · CMP-062 System Metrics UI
  (frontend) · CMP-063 Settings UI (frontend).
- **SUB-010:** CMP-064 Required Decoder (pure; Unwired) · CMP-065 Extraction (pure;
  Unwired) · CMP-066 Sufficiency (pure; Unwired) · CMP-067 Decision (pure; Unwired).
- **SUB-011:** CMP-068 DDNS Status API (API) · CMP-069 DDNS Updater (background;
  script) · CMP-070 DDNS UI (frontend).

Shared source files render as separate component nodes: `api/metrics.py` → {CMP-057,
CMP-019}; `api/settings.py` → {CMP-058, CMP-044}. Draw two component nodes; if a file
glyph is shown, draw one file glyph owned by both.

### 9.4 Runtime Flow table (RF) — id · caller · callee · verb · mode · crosses-TB · transports-DF
| RF | caller | callee | verb | mode | crosses-TB | transports-DF |
|---|---|---|---|---|---|---|
| RF-001 | CMP-007 | CMP-002 | read status | sync | TB-001, TB-002 | none |
| RF-002 | CMP-007 | CMP-001 | start/stop/restart | sync | TB-001, TB-002 | none |
| RF-003 | CMP-008 | CMP-001 | read/validate/apply config | sync | TB-001, TB-002 | none |
| RF-004 | CMP-010 | CMP-001 | top regions | sync | TB-001, TB-002 | none |
| RF-005 | CMP-009 | CMP-003 | fetch log tail | sync | TB-001, TB-002 | DF-016 |
| RF-006 | CMP-014 | CMP-011 | compartment ops | sync | TB-001, TB-002 | none |
| RF-007 | CMP-018 | CMP-015 | claim ops | sync | TB-001, TB-002 | DF-009 |
| RF-008 | CMP-025/CMP-026 | CMP-019 | read traffic | sync | TB-001, TB-002 | DF-003 |
| RF-009 | CMP-029 | CMP-027 | get advice | sync | TB-001, TB-002 | none |
| RF-010 | CMP-039 | CMP-030 | create/inspect/restore | sync | TB-001, TB-002, TB-006 | DF-010, DF-011 |
| RF-011 | CMP-043 | CMP-040 | check/install/poll | sync | TB-001, TB-002 | DF-005 |
| RF-012 | CMP-050 | CMP-044 | login | sync | TB-001, TB-002 | DF-001, DF-002 |
| RF-013 | CMP-061 | CMP-044 | logout | sync | TB-001, TB-002 | DF-001, DF-002 |
| RF-014 | CMP-062 | CMP-057 | host metrics | sync | TB-001, TB-002 | DF-015 |
| RF-015 | CMP-063 | CMP-058/CMP-044 | theme/config/password | sync | TB-001, TB-002 | DF-019 |
| RF-016 | CMP-070 | CMP-068 | DDNS status | sync | TB-001, TB-002 | DF-008 |
| RF-017 | CMP-060 | CMP-059 | drive pollers | async | none | none |
| RF-018 | CMP-051 | API cluster (all API/pages components) | register + dispatch | sync | none | DF-014, DF-017 |
| RF-019 | CMP-051 | CMP-020 | start/stop collector | async | none | none |
| RF-020 | CMP-051 | CMP-046 | purge sessions | async | TB-009 | DF-001 |
| RF-021 | CMP-054 | CMP-046 | validate session | sync | TB-002, TB-009 | DF-001, DF-002 |
| RF-022 | CMP-054 | CMP-053 | yield DB connection | sync | TB-009 | none |
| RF-023 | CMP-001/CMP-002 | CMP-004 | drive systemctl / read | async | none | none |
| RF-024 | CMP-001 | CMP-005 | validate config | sync | none | none |
| RF-025 | CMP-011 → CMP-012 | CMP-013 | compartment op via helper | async | TB-003, TB-005, TB-006 | DF-018 |
| RF-026 | CMP-015 → CMP-016 | CMP-017 | claim gen via helper | async | TB-003, TB-005, TB-006 | DF-009 |
| RF-027 | CMP-027 | CMP-028 | compute advice | sync | none | none |
| RF-028 | CMP-028 | CMP-004 + CMP-023 | gather advisor inputs | sync | none | none |
| RF-029 | CMP-030 | CMP-031/CMP-036 + CMP-038 | orchestrate backup/restore | async | TB-006 | DF-006, DF-010, DF-011 |
| RF-030 | CMP-040 | CMP-041 | stream tarball (sudo, STDIN) | async | TB-003, TB-007 | DF-012 |
| RF-031 | CMP-004 | EXT-002 | systemctl is-active/show | async | TB-005 | none |
| RF-032 | CMP-004 | EXT-002 | sudo systemctl start/stop/restart | async | TB-003, TB-005 | none |
| RF-033 | CMP-006 | EXT-002 | write drop-in + restart | sync | TB-003, TB-005, TB-009 | DF-013 |
| RF-034 | CMP-013 | EXT-001 | compartment write (as conduit) | sync | TB-005, TB-006, TB-009 | DF-018 |
| RF-035 | CMP-017 | EXT-001 | ryve-claim CLI (as conduit) | sync | TB-005, TB-006 | DF-009 |
| RF-036 | CMP-038 | EXT-002 | restore state + restart | async | TB-003, TB-005, TB-009 | DF-006, DF-011 |
| RF-037 | CMP-041 | EXT-002 | systemd-run transient unit | async | TB-003, TB-004 | DF-004, DF-005, DF-012 |
| RF-038 | CMP-041 | CMP-042 | run update.sh (deploy) | async | TB-004, TB-009 | DF-004, DF-005, DF-012 |
| RF-039 | CMP-020 → CMP-004 | EXT-001 | read counters | async | TB-005, TB-009 | DF-003 |
| RF-040 | CMP-069 | EXT-003 | update DNS record | async | TB-008, TB-009 | DF-007, DF-020 |
| RF-041 | CMP-040 | EXT-004 | check latest / fetch tarball | sync | TB-007 | DF-012 |
| RF-042 | CMP-003 | EXT-005 | read journal | async | TB-005 | DF-016 |
| RF-043 | CMP-057 | EXT-006 | read host metrics | sync | none | DF-015 |
| RF-044 | CMP-046 | CMP-053 | delete expired sessions | async | TB-009 | DF-001 |

SUB-010 (CMP-064–067) participates in no Runtime Flow (Unwired). CAP-004 renders as a
`501` stub with no outgoing flow.

### 9.5 Data Flow table (DF) — id · producer · consumer · data object · storage · lifetime · transported-by-RF
| DF | producer | consumer | data object | storage | lifetime | transported-by-RF |
|---|---|---|---|---|---|---|
| DF-001 | CMP-046 | SQLite | session record | [SQLITE] | until expiry | RF-012, RF-013, RF-020, RF-021, RF-044 |
| DF-002 | CMP-048 | browser | session + CSRF cookie | [NET] | session | RF-012, RF-013, RF-021 |
| DF-003 | EXT-001 → CMP-020 | SQLite / UI | traffic counters | [SQLITE] | persistent (pruned) | RF-008, RF-039 |
| DF-004 | CMP-041 | filesystem | update-status.json | [FS] | until next run | RF-037, RF-038 |
| DF-005 | filesystem | CMP-040 → UI | update status | [MEM] | poll | RF-011, RF-037, RF-038 |
| DF-006 | CMP-038 | filesystem | restore-status.json | [FS] | until next restore | RF-029, RF-036 |
| DF-007 | CMP-069 | filesystem | DDNS result JSON lines | [FS] | rotated weekly | RF-040 |
| DF-008 | filesystem | CMP-068 → UI | DDNS status | [MEM] | request | RF-016 |
| DF-009 | CMP-017 | CMP-015 → browser | Ryve claim PNG | [TMP] | never persisted | RF-007, RF-026, RF-035 |
| DF-010 | CMP-031 | browser | encrypted backup archive | [NET] | download | RF-010, RF-029 |
| DF-011 | browser | CMP-036/CMP-038 | uploaded backup bytes | [TMP] | restore duration | RF-010, RF-029, RF-036 |
| DF-012 | EXT-004 | CMP-041 → /opt | update payload tarball | [NET]→[TMP]→[FS] | temp discarded; /opt persistent | RF-030, RF-037, RF-038, RF-041 |
| DF-013 | CMP-006 | filesystem | Conduit env drop-in | [FS] | persistent | RF-033 |
| DF-014 | filesystem | CMP-052 | .env + config.json | [MEM] | process lifetime | RF-018 |
| DF-015 | EXT-006 | CMP-057 → UI | host metrics | [MEM] | request | RF-014, RF-043 |
| DF-016 | EXT-005 | CMP-003 → UI | log lines | [JRNL] | request | RF-005, RF-042 |
| DF-017 | CMP-055 | UI | app version | [MEM] | process lifetime | RF-018 |
| DF-018 | CMP-013 | filesystem | personal compartment identity | [FS] | persistent | RF-025, RF-034 |
| DF-019 | CMP-058 | browser | theme preference cookie | [NET] | persistent (client) | RF-015 |
| DF-020 | CMP-069 | EXT-003 | DNS record + public IP | [EXTSVC] | persistent (DNS) | RF-040 |

### 9.6 Trust Boundaries (TB) — id · name (zone color in §7.8)
TB-001 Edge (TLS/proxy) · TB-002 Authentication · TB-003 Privilege elevation (sudo) ·
TB-004 Namespace (ProtectSystem=strict) · TB-005 CCC↔Conduit daemon · TB-006 Secret
perimeter · TB-007 External update fetch (GitHub) · TB-008 External DNS (Cloudflare) ·
TB-009 Local persistence.

### 9.7 External Systems (EXT) — id · name · protocol
EXT-001 Conduit Core (systemctl/CLI/counters) · EXT-002 systemd (systemctl /
systemd-run) · EXT-003 Cloudflare (HTTPS API + edge) · EXT-004 GitHub Releases (HTTPS)
· EXT-005 journald (journalctl) · EXT-006 OS/psutil (syscalls) · EXT-007 cron (timer)
· EXT-008 nginx (HTTPS→HTTP 127.0.0.1).

## 10. Relationships (edge semantics)

CAP **implemented-by** SUB (realization edge §7.7). SUB **contains** CMP (spatial
nesting §7.7). CMP **calls/invokes** CMP or EXT (RF edges §7.5). CMP
**produces/consumes** data (DF edges §7.6). RF **crosses** TB (per §9.4 crosses-TB;
draw §7.8 marker). RF **transports** DF (per §9.4 transports-DF).

## 11. Traceability Matrix (inline)

`CAP · Primary SUB · Supporting SUB · Components · Runtime Flows · Data Flows · Trust
Boundaries · External Systems`.

| CAP | Prim | Supp | CMP | RF | DF | TB | EXT |
|---|---|---|---|---|---|---|---|
| CAP-001 | SUB-001 | — | 001,002,004,007 | 002,023,031,032 | — | 003,005 | 001,002 |
| CAP-002 | SUB-001 | — | 002,004,007 | 001,023,031 | — | 005 | 001,002 |
| CAP-003 | SUB-001 | — | 001,005,006,008 | 003,024,033 | 013 | 003,005,009 | 001,002 |
| CAP-004 | SUB-001 | — | 001 | 501 terminal | — | — | — |
| CAP-005 | SUB-002 | — | 011,012,013,014 | 006,025,034 | 018 | 003,005,006,009 | 001 |
| CAP-006 | SUB-003 | — | 015,016,017,018 | 007,026,035 | 009 | 003,005,006 | 001 |
| CAP-007 | SUB-004 | SUB-009 | 019,020,021,022,023,024,025,026 | 008,019,039 | 003 | 005,009 | 001 |
| CAP-008 | SUB-009 | — | 057,062 | 014,043 | 015 | 009 | 006 |
| CAP-009 | SUB-005 | SUB-001,004 | 027,028,029 | 009,027,028 | — | — | 001,006 |
| CAP-010 | SUB-001 | — | 003,009 | 005,042 | 016 | 005 | 005 |
| CAP-011 | SUB-011 | — | 068,070 | 016 | 008 | — | — |
| CAP-012 | SUB-011 | — | 069 | 040 | 007,020 | 008,009 | 003,007 |
| CAP-013 | SUB-006 | — | 030,031,032,033,034,035,037,039 | 010,029 | 010,011 | 006 | — |
| CAP-014 | SUB-006 | — | 030,036,038,039 | 010,029,036 | 006,011 | 003,005,006,009 | 002 |
| CAP-015 | SUB-007 | SUB-009 | 040,041,042,043 | 011,030,037,038,041 | 004,005,012 | 003,004,007,009 | 004,002 |
| CAP-016 | Deployment View (excluded) | — | — | — | — | — | 003,008 |
| CAP-017 | SUB-008 | SUB-009 | 044,045,046,047,048,049,050 | 012,013,021 | 001,002 | 002,009 | — |
| CAP-018 | SUB-009 | — | 058,063 | 015 | 019 | 002 | — |
| CAP-019 | SUB-009 | — | 051,056,059,060,061 | 017,018 | 014,017 | 001,002 | 008 |
| CAP-020 | SUB-009 | — | 055 | 018 (dispatch) | 017 | — | — |
| CAP-021 | SUB-010 | — | 064,065,066,067 | — (Unwired) | — | — | — |

By-design terminals: CAP-004 Not-Implemented; CAP-016 Deployment-excluded; CAP-021
Unwired. No orphans; all other chains complete.

## 12. Hierarchy & Grouping Rules

Top level: Subsystems as labelled containers (§7.3). Components nest inside their
subsystem (§7.7). Capabilities are not containers of components; show CAP→SUB via the
realization edge (§7.7) or the Capability/Traceability views. Frontend components draw
adjacent to their subsystem; the SUB-009 shell (CMP-059/060/061) is the shared
presentation cluster.

## 13. Layering, Layout, Cluster, Boundary, Flow, Security Rules

- **Layering:** apply §5 tiers; never place a helper above its API or an EXT inside a
  subsystem container; stores at the bottom tier; background tasks (CMP-020, CMP-069;
  RF-017,019,020,040,044) carry the `⏱` badge.
- **Layout:** directed, mostly acyclic left→right (or top→down) by tier; minimize edge
  crossings; route long edges around clusters; callers on one side, callees on the
  other.
- **Cluster:** one container per Subsystem (label `SUB-00x Name`); TB zones (§7.8) are
  nested dashed rectangles that may span subsystems; EXT form an outer zone.
- **Boundary:** draw each TB an edge lists in `crosses-TB` (§9.4) with the §7.8 marker;
  nesting outer→inner: TB-001 → TB-002 → application → TB-003 → TB-004 / TB-005;
  TB-006 wraps CMP-013, CMP-017, CMP-034 and DF-009.
- **Flow:** solid=sync, dashed=async (§7.5); label `RF-nnn` + verb; RF-011 renders one
  request edge + one dashed poll-back edge; CAP-004 renders a `501` stub.
- **Security (VIEW-09):** emphasize TB zones; mark sudo-crossing flows (any RF whose
  `crosses-TB` includes TB-003), the namespace exception (RF-037, TB-004), and secret
  data (DF-009, DF-010, DF-013, DF-018); show helper run-as (root: CMP-006,038,041;
  conduit: CMP-013,017); annotate TB-007 "artifact signing deferred (ADR-0001 inv.5)".

## 14. Per-view Scope & Recommended Entry Points

VIEW-01 CAP grouped by class (entry CAP-015) · VIEW-02 SUB + realization (entry
SUB-009) · VIEW-03 CMP per-subsystem sheets (entry SUB-007) · VIEW-04 RF between CMP,
tiered (entry RF-030) · VIEW-05 DF swimlanes by storage class (entry DF-012) · VIEW-06
TB nested zones + crossings (entry TB-003) · VIEW-07 EXT hub-and-spoke (entry EXT-004)
· VIEW-08 Deployment reference (entry install.sh) · VIEW-09 Security layers (entry
TB-002) · VIEW-10 Operational timeline (entry RF-040) · VIEW-11 Traceability table from
§11 (entry CAP-015) · VIEW-12 CAP by class · VIEW-13 CAP/SUB/CMP by status.

## 15. Diagram Metadata (required block per diagram)

Every diagram carries: Diagram ID (`DIAG-xxx`), Version, Last Updated, Sources,
Verified Revision (`v0.3.12`), Related ADRs, Related Components, Related Runtime Flows.
Formatting is free; the information is mandatory.

## 16. Navigation, Expansion, Collapse, Large-Diagram, Readability

- Views are navigable by shared IDs; a Subsystem in VIEW-02 expands to its Component
  sheet in VIEW-03; collapse a subsystem to a single node for overviews.
- If a view exceeds legibility, split by subsystem (VIEW-03 sheets) or by tier — never
  shrink (§4). Cross-sheet edges are labelled stubs referencing the target ID.
- Readability: legible labels; no overlapping text; no edges through node bodies;
  consistent flow direction; group related components.

## 17. Checklists

### 17.1 Validation (pre-render)
- [ ] Every node/edge maps to a frozen ID in §9 / §11.
- [ ] Every RF has caller and callee present in §9.3 or §9.7 (EXT).
- [ ] Every RF `crosses-TB` value is honored with a §7.8 marker (or `none`).
- [ ] Every RF `transports-DF` matches the DF `transported-by-RF` (bidirectional).
- [ ] Every DF has a storage glyph (§7.9).
- [ ] No capability rendered as a container of components.
- [ ] By-design terminals correct (CAP-004 `501`, CAP-016 excluded, CAP-021 Unwired).
- [ ] Shared files render two components (metrics.py, settings.py).

### 17.2 Rendering
- [ ] One view per diagram; tiers (§5) and clusters (§13) correct.
- [ ] Edge styles per §7.5/§7.6/§7.7; TB zones nested per §13.
- [ ] Legend (§7.13) + metadata block (§15) present.
- [ ] Content-driven size; no fixed canvas/aspect (§4).

### 17.3 Quality
- [ ] Minimal edge crossings; no edges through nodes; labels legible.
- [ ] IDs consistent across all views (cross-view navigation preserved).
- [ ] Security emphasis correct (run-as, sudo/namespace, secrets).

### 17.4 Determinism
- [ ] All colors/line-styles/shapes/badges/glyphs taken verbatim from §7.
- [ ] No boundary crossing inferred — only §9.4 `crosses-TB` used.
- [ ] No RF↔DF linkage inferred — only §9.4/§9.5 used.
- [ ] No external target inferred — all RF callees are CMP or EXT ids (§9.4).
- [ ] No abbreviation used without a §6 definition.

---

*End of MASTER.md — fully self-contained deterministic rendering specification. All
entities, mappings (RF↔TB, RF↔DF), the traceability matrix, and the complete visual
legend are contained above. Verified Revision v0.3.12.*
