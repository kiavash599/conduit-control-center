# CCC — Architecture Design Specification (ADS)

**Release:** v1.0.0-rc1 · **Verified Revision:** v0.3.12 · **Status:** Release Candidate 1
**Companion registries:** `../Appendix/REGISTRIES.md`, `../Appendix/TRACEABILITY.md`
**Governing document:** `../CHARTER.md`

This ADS is a structured engineering reference assembled from the frozen
Architecture Atlas. It introduces no new architecture; every statement derives
from the frozen registries. Detailed entity tables live in the Appendix; this
document organizes and explains them and prefers references over repetition.

---

## 1. Introduction

Conduit Control Center (CCC) is an open-source, lightweight web dashboard that lets
volunteer operators manage a Psiphon Conduit node on a Raspberry Pi / Ubuntu 22.04
host without direct CLI access. The runtime is a FastAPI application (`conduit-cc`
service) fronted by nginx + Cloudflare, controlling a separate Conduit node
(`conduit` service) through hardened, argv-only privileged helpers.

This ADS describes the **current, shipped** runtime architecture at `v0.3.12`. It is
the authoritative engineering reference and the basis for automatic diagram
generation (`../Claude-Design/MASTER.md`).

## 2. Scope

In scope: the runtime architecture — capabilities, subsystems, components, runtime
flows, data flows, trust boundaries, and external systems that participate at run
time. Out of scope: new feature design, the Deployment/Provisioning view
(referenced only), speculative future systems, and per-line implementation detail.
By-design terminals (CAP-004 Not-Implemented, CAP-016 Deployment-excluded, CAP-021
Unwired) are recorded, not treated as gaps.

## 3. Architectural Principles

- **Capability-first.** Architecture is organized around what the system can do,
  independent of how it is exposed; APIs are the last projection, not the spine.
- **Evidence precedes representation.** No entity, flow, or boundary exists in the
  Atlas without repository evidence; representations depict verified architecture.
- **Component ≠ Source File.** Components are architectural concepts identified by
  responsibility; a component may span files and a file may host multiple
  components; ownership is conceptual, never file-based.
- **Least privilege.** The dashboard runs unprivileged (`conduit-cc`); privileged
  actions occur only through exact-path, argv-only helpers via sudoers, and helpers
  run as `root` or `conduit` strictly as required.
- **Hardened runtime.** `ProtectSystem=strict`; writes to protected paths occur only
  through a fixed-name `systemd-run` transient unit.
- **Secrets never persisted or logged.** Key-grade material (Ryve claim, pairing
  token, backup passphrase, private keys) is RAM/tmpfs-only or excluded by technical
  guard.
- **Identity-only identifiers.** All IDs are permanent and opaque (no semantic
  numbering); meaning lives in entity bodies.

## 4. Capability Model

CCC exposes **21 capabilities** (CAP-001…CAP-021), each with a Class
(Runtime/Background/Deployment/Administration), a Scope (Internal/External/Mixed),
and a Status. Full definitions: `../Appendix/REGISTRIES.md#capability-registry`.

Summary by class: 18 Runtime; 1 Background (CAP-012 DDNS update); 1 Deployment
(CAP-016 HTTPS port, excluded); CAP-021 N/A (Unwired). Notable statuses: CAP-015
Maintenance-Only (Trusted Update Engine, production-proven, ADR-0001); CAP-021
Pure+Unwired (ADR-0002); CAP-004 Not-Implemented.

## 5. Runtime Subsystems

**11 subsystems** (SUB-001…SUB-011), each the Primary Implementer of one or more
capabilities. Full entries: `../Appendix/REGISTRIES.md#subsystem-registry`.

| SUB | Name | Primary capabilities |
|---|---|---|
| SUB-001 | Conduit Control | CAP-001,002,003,004,010 |
| SUB-002 | Personal Mode | CAP-005 |
| SUB-003 | Ryve Claim | CAP-006 |
| SUB-004 | Traffic | CAP-007 |
| SUB-005 | Contribution Advisor | CAP-009 |
| SUB-006 | Backup & Restore | CAP-013,014 |
| SUB-007 | Trusted Update Engine | CAP-015 (Maintenance-Only) |
| SUB-008 | Authentication & Access Control | CAP-017 |
| SUB-009 | Application Runtime Platform | CAP-008,018,019,020 |
| SUB-010 | Capability Evaluation | CAP-021 (Unwired) |
| SUB-011 | Dynamic DNS | CAP-011,012 |

SUB-009 is the composition root and cross-cutting host; it is the most
depended-upon subsystem (evidence that SUB numbering is meaningless).

## 6. Runtime Components

**70 components** (CMP-001…CMP-070), each homed in exactly one subsystem, each
owning ≥1 implementation file. Two source files intentionally host two components
each (per Component ≠ File): `api/metrics.py` = {CMP-057 system facet, CMP-019
counters facet}; `api/settings.py` = {CMP-058 theme/config facet, CMP-044 password
facet}. Full registry with Owned Files: `../Appendix/REGISTRIES.md#component-registry`.

Component kinds present: API routers, domain adapters, pure engines/state machines,
privileged helpers (`root`/`conduit`), the updater script, operator CLIs, background
tasks, and frontend modules (shell + per-domain). Declarative type/model/error/schema
modules, templates, CSS, `_version.py`, vendored libraries, and `__init__.py` are
classified as Utilities/artifacts/mechanisms and are **not** components.

## 7. Runtime Flows

**44 runtime flows** (RF-001…RF-044) capture caller→callee interactions with
purpose, trigger, and sync/async. Full registry:
`../Appendix/REGISTRIES.md#runtime-flow-registry`. Flow tiers: UI→Backend (HTTP),
composition/lifecycle, API→domain (in-process), privilege/namespace/external egress,
and background/scheduled. SUB-010 has no flows (Unwired); CAP-004 is a 501 terminal.

## 8. Data Flows

**20 data flows** (DF-001…DF-020) classify each movement by storage class — runtime
memory, SQLite, filesystem, journal, network, external service, temporary state —
with lifetime. Full registry: `../Appendix/REGISTRIES.md#data-flow-registry`.
Persistent stores: SQLite (sessions, traffic), filesystem (`/var/lib/conduit-cc`
status files, `/etc/systemd/.../conduit.service.d`, `/var/log/conduit-cc/ddns.log`,
`/var/lib/conduit/data`). Never persisted: DF-009 (Ryve claim, tmpfs/RAM only).

## 9. Trust Boundaries

**9 trust boundaries** (TB-001…TB-009): Edge/TLS, Authentication, Privilege
elevation (sudo), Namespace (`ProtectSystem=strict`), CCC↔Conduit daemon, Secret
perimeter, External update fetch (GitHub), External DNS (Cloudflare), Local
persistence. Full registry with crossings and implications:
`../Appendix/REGISTRIES.md#trust-boundary-registry`. TB-007 carries a deferred
hardening item (artifact signing, ADR-0001 invariant 5).

## 10. External Systems

**8 external systems** (EXT-001…EXT-008): Conduit Core, systemd, Cloudflare, GitHub
Releases, journald, OS/psutil, cron, nginx. Full registry with protocol, auth, trust
assumptions, and failure impact: `../Appendix/REGISTRIES.md#external-systems-registry`.

## 11. Architecture Views

**13 views** (VIEW-01…VIEW-13) provide engineering lenses over the same frozen data
(no duplication): Capability, Subsystem, Component, Runtime Flow, Data Flow, Trust
Boundary, External Systems, Deployment (reference-only), Security (composed),
Operational (composed), Traceability, Capability-Class, Status/Lifecycle. Full
definitions: `../Appendix/TRACEABILITY.md#architecture-view-registry`.

## 12. Traceability

Every capability traces `CAP → Primary SUB → Supporting SUB → Components → Runtime
Flows → Data Flows → Trust Boundaries → External Systems → Evidence`. The complete
21-row matrix: `../Appendix/TRACEABILITY.md#architecture-traceability-matrix`. No
orphans; the only terminals are the three by-design cases.

## 13. Engineering Query Model

The Atlas answers structured engineering questions directly from the registries
(e.g. "what implements CAP-015?", "which flows cross TB-003?", "which capabilities
require sudo?"). The query set and answers:
`../Appendix/TRACEABILITY.md#engineering-query-index`.

## 14. Cross References

Bidirectional navigation (CAP↔SUB↔CMP↔RF↔DF↔TB↔EXT):
`../Appendix/TRACEABILITY.md#cross-reference-index`.

## 15. Architecture Rules

- No capability is owned by a subsystem; capabilities are **implemented**, with one
  **Primary Implementer** per capability.
- Each component has exactly one home subsystem; cross-use is a **Used-By**
  relationship, not co-ownership.
- Trust boundaries may lie within or between subsystems and never define subsystem
  boundaries.
- Runtime flows may cross subsystems; data flows may cross trust boundaries.
- Deployment-class capabilities belong to the Deployment View, not the runtime
  subsystem set.
- Functional expansion of a frozen subsystem requires a **new ADR**, not incremental
  redefinition.

## 16. Atlas Conventions

- **Component ≠ Source File** (conceptual ownership).
- **Identity-only identifiers** (opaque, permanent, no semantic numbering).
- **Entity Status** = one primary lifecycle value + orthogonal qualifiers.
- **Capability Class** = closed set {Runtime, Background, Deployment, Administration}.
- **Verified Revision** on every entity (currently `v0.3.12`).
- **Confidence** on every claim (Verified / Partially Verified / Engineering
  Reasoning / Hypothesis).
- **Dual-audience, machine-friendly Markdown** (no serialization formats).
- **Content-driven diagrams** (no fixed canvas; see MASTER.md).
- **Atlas Lifecycle:** a merged ADR/subsystem change/refactor/approved decision
  triggers Atlas review before the Atlas is considered current again.

## 17. Appendix

- `../Appendix/REGISTRIES.md` — full frozen registries.
- `../Appendix/TRACEABILITY.md` — matrix, cross-refs, query index, views, diagram mapping.
- `../Claude-Design/MASTER.md` — self-contained diagram-generation specification.
- `../CHARTER.md` — governing charter (frozen).

---

*This ADS is a projection of the frozen Atlas. It is revised only to correct
objective factual errors or under an approved ADR / repository change, per the
Atlas Lifecycle.*
