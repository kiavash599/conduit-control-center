# CCC Architecture Atlas — Charter

**Type:** Charter (governing document) · **Status:** Frozen · **Version:** 1.0 · **Date:** 2026-07-02
**Applies to:** Conduit Control Center at v0.3.12 (between implementation phases)
**Companions:** `ENGINEERING-PROCESS-GUIDE.md`, `PROJECT-LIFECYCLE.md`, ADR-0001, ADR-0002/IDD

---

## 1. Purpose

The Architecture Atlas is CCC's long-term architectural knowledge infrastructure and the project's **Engineering Knowledge Base**: a single, navigable, evidence-based description of what the system *is* — its capabilities, subsystems, components, runtime and data flows, and trust boundaries — kept faithful to the actual repository. It is documentation and capability-discovery, not a feature, and it is built to *answer engineering questions*, not merely to narrate the design.

As an Engineering Knowledge Base, the Atlas must let an engineer — or an AI collaboration system (§12.4) — answer questions such as: **What implements this capability? What depends on this subsystem? What breaks if this component changes? Which runtime flows are affected? Which ADR introduced this? Which tests validate it?** Every entity is therefore recorded with the dependency and provenance needed to answer them (§12.1). The Atlas serves **two equal audiences — human engineers and AI collaboration systems (current and future, including AICF-compliant systems)** — so its structure is deliberately consistent and cross-referenceable (§12.4).

CCC needs it **now** for three concrete reasons. First, the architecture phase for the core update capability is complete and One-Click Update is Production-Proven / Maintenance-Only — the system is stable enough to describe authoritatively without chasing a moving target. Second, knowledge is currently scattered across ADRs, IDDs, closure records, the roadmap, PROJECT-STATUS, and source; there is no consolidated map, which makes onboarding, review, and future ADR decisions slower and more error-prone. Third, the project is deliberately between implementation phases — the correct time to consolidate understanding rather than build, and to give future candidate drivers (e.g. Conduit Core update design) a stable architectural baseline to reason against.

## 2. Scope

The Atlas will describe the **current, shipped** architecture of CCC across:

- **Capabilities** — what the system can do, independent of how it is exposed.
- **Subsystems** — Trusted Update Engine (One-Click Update), Backup/Restore, Personal Mode, Ryve Claim/Identity, DDNS, HTTPS Port Selection, Conduit Adapter, the (pure, unwired) Capability subsystem, Metrics/Traffic, Contribution Advisor, Regional Analytics, Auth/session.
- **Components & modules** — backend API modules, privileged helpers (`ccc-update-apply`, `ccc-apply-https-port`), the updater (`update.sh`), adapters, frontend JS modules, deployment scripts.
- **Processes** — privileged helper execution (e.g. `systemd-run` transient unit), background/scheduled work (DDNS, logrotate, cleanup sweeps), service lifecycle.
- **Runtime flows** — end-to-end sequences (update install, backup create, restore, pairing, DDNS refresh).
- **Data flows** — where data originates, transforms, persists (`/etc/conduit-cc`, `/var/lib/conduit-cc`, backups), and crosses boundaries.
- **Security / trust boundaries** — dashboard↔backend, backend↔privileged helper (sudoers), `ProtectSystem=strict` namespace boundaries, TLS/Cloudflare edge, secret-handling perimeters.
- **Deployment topology** — Raspberry Pi target, nginx + Cloudflare, systemd units (`conduit-cc.service`, `conduit.service`), filesystem layout, `/opt/conduit-cc`.
- **Explicit subsystem coverage** as required: update engine, backup/restore, personal mode, Ryve claim, DDNS, and the dashboard/backend/frontend boundaries.

## 3. Out of Scope

Explicitly excluded: new feature implementation; any code, config, or script changes; redesign or refactoring of existing architecture; speculative or future systems (including Conduit Core update design and other candidate drivers — the Atlas describes what exists, not what might); and **AICF implementation** (the Atlas is *structured to be consumable by* AI/AICF-compliant systems per §12.4, but implementing AICF itself is not part of this work). The Atlas records the present architecture only; it must not propose changes, and it must not reopen frozen decisions (ADR-0001, ADR-0002/IDD). Where the current design has a known deferred item (e.g. artifact signing), the Atlas *notes it as deferred*; it does not design it.

## 4. Method — Capability-first (not API-first)

Discovery proceeds strictly in this order:

**Capability → Subsystem → Component → Process → Interaction → Data Flow → API / Endpoint.**

We start from *what the system can do* and only reach APIs last, as one projection among several.

**Why API-first is rejected.** The HTTP API is an incomplete and biased projection of CCC's real architecture:

- **Capabilities without endpoints.** Core behavior lives outside the API surface — the update actually executes in a privileged helper, a `systemd-run` transient unit, and `update.sh`; DDNS refresh and logrotate/cleanup run as background/scheduled processes; the Capability subsystem (ADR-0002) is pure and *unwired* (no endpoint at all). An API-first sweep would miss or under-weight all of these.
- **Trust boundaries are invisible to an endpoint list.** The most security-relevant structure — sudoers-mediated privilege escalation, `ProtectSystem=strict` namespace escape, secret perimeters — is not represented as routes.
- **Endpoints fragment single capabilities.** One capability (e.g. "update the CCC application safely") maps to several endpoints plus non-API steps; ordering by route scatters a coherent capability across the map.
- **APIs drift and re-shape.** Endpoints are renamed and reshaped; anchoring the Atlas to them makes it brittle. Capabilities are the stable spine.

Capability-first yields a map organized around durable truths (what CCC does and guarantees), with endpoints attached at the leaves where they genuinely exist. This mirrors CCC's own capability-driven guiding principle.

**Principle — Evidence precedes architecture representation.** No diagram, no architecture description, and no dependency graph may be created before the corresponding evidence has been discovered and verified. Representations depict **verified** architecture (§12.7); they never invent it. This is a permanent Charter rule and governs every deliverable and diagram (§7).

## 5. Source of Truth

Authoritative inputs, in rough precedence for architectural claims:

- **Decision records:** ADRs (ADR-0001 Trusted Update Engine; ADR-0002 + the capability IDD), and an ADS if present.
- **Closure records:** `docs/closure/*` (e.g. `one-click-update-closure.md`) — production-proven evidence and scope notes.
- **Planning/state:** `docs/roadmap/CCC_Product_Roadmap_v1.md` (Rev 1.21) and `docs/PROJECT-STATUS.md` (v0.3.12) — for status and boundaries, not for architecture internals.
- **Source code:** `backend/` (API modules, adapters, `_version.py`), `frontend/static/js/` + `frontend/templates/`.
- **Deployment & privileged surface:** `deployment/` (helpers `ccc-update-apply`, `ccc-apply-https-port`, `conduit-cc.logrotate`), `update.sh`, `install.sh`, `uninstall.sh`, systemd unit files, sudoers rules, nginx config.
- **Tests:** `tests/` — used as executable evidence of contracts and boundaries.

Rule of evidence: **code, deployment scripts, and systemd/sudoers/nginx are authoritative for runtime behavior; ADRs/IDDs are authoritative for intent and frozen decisions.** Where docs and code disagree, the Atlas records the code reality and flags the discrepancy rather than silently trusting either.

## 6. Deliverables

The Atlas will produce (as Markdown, Design-Claude-ready):

- **Architecture Atlas index** — the navigable entry point.
- **Capability map** — the capability spine (§4 order).
- **Subsystem map** — subsystems and their relationships.
- **Component inventory** — components/modules/helpers/scripts with responsibilities and sources.
- **Runtime-flow documents** — per major flow (update, backup, restore, pairing, DDNS).
- **Data-flow documents** — origins, transforms, persistence, boundary crossings.
- **Trust-boundary documents** — privilege, namespace, secret, and edge boundaries.
- **Diagram specification files** — one `.md` spec per diagram (see §7).
- **Design-Claude-ready Markdown** — every deliverable authored so a renderer can produce diagrams from the specs without further interpretation.

Each deliverable is authored as an **Engineering-Knowledge-Base entry**: it carries a stable Architecture ID (§12.3) and the standard dependency section (§12.1), cites its source-of-truth files, marks each claim with a **Confidence** level (§12.7), and — so the Atlas can answer *what implements / depends on / breaks / validates* a thing — links the capabilities, components, runtime flows, tests, and ADRs it relates to.

## 7. Diagram Specification Rule

For **every** diagram, the Atlas produces a Markdown `.md` **specification** for the renderer ("Design Claude") — never a rendered image in this workstream. Each spec describes intent, nodes, relationships/edges, grouping, trust-boundary overlays, legend, and semantics. Per §4, a spec may only depict **verified** architecture — diagrams represent, they never invent.

**Sizing is content-driven and unconstrained:**

- Do **not** set a fixed canvas size.
- Do **not** target A4, slide, poster, card, or any fixed frame dimensions.
- Do **not** force an aspect ratio.
- The diagram **expands naturally with the complexity** of its content; the spec describes structure and meaning, and lets the renderer size to fit.

Specs express relationships and layering (e.g. "group these components inside trust boundary X"), not pixels.

**Every diagram spec begins with a metadata block.** It must capture, at minimum: **Diagram ID** (DIAG-xxx, §12.3), **Version**, **Last Updated**, **Sources**, **Verified Revision** (§12.6), **Related ADRs**, **Related Components**, and **Related Runtime Flows**. Exact formatting is not mandated; the required information is. This keeps every diagram traceable to its evidence and detectably stale when its sources move.

## 8. Proposed File Structure

```
docs/architecture-atlas/
  index.md                     # entry point / navigation
  CHARTER.md                   # this charter (frozen)
  capabilities/                # capability map + per-capability notes
  subsystems/                  # subsystem + component maps/inventory
  flows/                       # runtime-flow and data-flow documents
  trust-boundaries/            # trust/security boundary documents
  diagram-specs/               # one .md spec per diagram (content-driven)
```

`index.md` also states the conventions (capability-first order; the evidence-precedes-representation principle; Confidence marking; content-driven diagram rule). The component inventory lives under `subsystems/` unless it grows large enough to warrant its own folder — decided at Phase C, not now.

## 9. Work Phases

- **Phase A — Inventory & source discovery.** Read-only enumeration of every source-of-truth file (§5) and a raw list of candidate capabilities. Exit: a source register + capability candidate list, no analysis committed to structure.
- **Phase B — Capability map.** Consolidate candidates into the capability spine (§4). Exit: approved capability map.
- **Phase C — Subsystem / component maps.** Map capabilities to subsystems, then to components/modules/helpers/scripts; build the component inventory. Exit: subsystem map + inventory.
- **Phase D — Runtime & data flows.** Document end-to-end flows and data movement per major capability. Exit: flow documents.
- **Phase E — Trust boundaries.** Overlay privilege, namespace, secret, and edge boundaries onto the maps and flows. Exit: trust-boundary documents.
- **Phase F — Diagram specs.** Produce one content-driven `.md` spec per required diagram (§7). Exit: diagram-spec set, renderer-ready.
- **Phase G — Review & freeze.** Cross-check against sources, reconcile discrepancies, and freeze the Atlas v1 (with a revision/version note). Exit: frozen Atlas index.

Each phase is separately approved; none begins until the prior is accepted. Diagram renders are downstream of Phase F specs and are not part of this workstream.

## 10. First Recommended Work Item

**Phase A, discovery only — produce the Architecture Atlas Source Register + raw capability candidate list.** A single read-only Markdown inventory that (a) enumerates every authoritative source (ADRs, IDDs, ADS if present, closure docs, roadmap, PROJECT-STATUS, backend/frontend source areas, deployment/systemd/nginx/helper scripts, tests) with a one-line note on what each is authoritative for, and (b) extracts a *raw, unstructured* list of candidate capabilities the system exhibits. No maps, no diagrams, no structure decisions, no IDs, no code changes — evidence-gathering only. This becomes the input to Phase B and is small, reversible, and independently valuable.

## 11. Risks

- **Scope creep** — the Atlas drifts into redesign or into documenting future systems. *Mitigation:* §3 out-of-scope is enforced; every deliverable describes only shipped architecture; candidate drivers are named, never designed.
- **Duplicating existing docs** — re-writing what ADRs/closure/roadmap already say. *Mitigation:* the Atlas *links and consolidates*, citing sources; it adds the cross-cutting map, not restated prose.
- **API-first bias** — sliding back into organizing by endpoints. *Mitigation:* the §4 capability-first order is mandatory; endpoints appear only at the leaves.
- **Stale diagrams** — specs/diagrams diverge from code over time. *Mitigation:* specs cite sources and a Verified Revision (§12.6), are frozen with a version at Phase G, and are regenerated from specs, never hand-drifted; the Atlas Lifecycle (§12.9) triggers review on architecture-affecting change.
- **Mixing current and future architecture** — present state blended with aspirations. *Mitigation:* the Atlas is strictly present-tense; deferred items are labeled "deferred," future drivers are labeled "candidate" and kept out of current-architecture diagrams (§12.2); neither is drawn as if it exists.
- **Evidence ambiguity** — docs vs. code disagreements silently resolved, or assumptions presented as facts. *Mitigation:* the evidence-precedes-representation principle (§4) plus the Confidence classification (§12.7); on conflict, record code reality and flag it.

## 12. Atlas Conventions

Cross-cutting conventions that every Atlas deliverable follows. They are defined here and applied from Phase A onward; **none is populated in this charter.** These conventions do not change the capability-first method (§4), the work phases (§9), or the file structure (§8).

### 12.1 Dependency Section (standard)

Every architectural entity — Capability, Subsystem, Component, Runtime Flow, Data Flow, Trust Boundary — ends with a standard dependency section, so the Atlas answers the §1 questions consistently. Standard fields: **Status (§12.5) · Depends On · Used By · Related Components · Related Runtime Flows · Security Impact · Files · Tests · ADRs · IDDs · Verified Revision (§12.6) · Confidence (§12.7).** Fields that do not apply are marked "none," never omitted (predictability, §12.4). This is defined as a convention only and is not populated now.

### 12.2 Architecture Evolution references

The Atlas describes the **current** architecture only. Each entity may carry an optional, clearly separated reference block for historical context: **Previous Architecture · Deprecated Architecture · Future Candidate.** These are *references only* — they never enter the current-architecture body, and **never appear inside current-architecture diagrams.** Future-candidate material is always kept visually and structurally segregated from current architecture so the two can never be conflated.

### 12.3 Stable Architecture IDs

Every entity receives a permanent identifier for long-term traceability, using type prefixes: **CAP-** (capability), **SUB-** (subsystem), **CMP-** (component), **FLOW-** (runtime flow), **DATA-** (data flow), **TB-** (trust boundary), **DIAG-** (diagram). IDs are stable across renames; future documents reference the **ID rather than the name** wherever possible. The numbering scheme (width, allocation, sequencing) is **not defined here** — only the prefix convention and the traceability intent are established now. **ID allocation is intentionally deferred until IDs are first minted; the numbering scheme will be decided before Phase B begins.**

### 12.4 Dual-audience, machine-friendly structure

The Atlas has **two equal audiences: human engineers and AI collaboration systems** (current and future, including AICF-compliant systems). Its structure is therefore **human-readable, machine-friendly, consistent, predictable, and cross-referenceable** — consistent section ordering, the standard dependency section (§12.1), and stable IDs (§12.3) that let a program follow references deterministically. Serving AI/AICF-compliant systems is a matter of **structure and consumability, not of implementing AICF** (which is out of scope, §3), and the wording is deliberately vendor- and implementation-independent. This does **not** mean introducing YAML, JSON, XML, RDF, a graph database, or any machine serialization: **Markdown remains the single canonical format.** Machine-friendliness is achieved purely through disciplined, predictable Markdown structure.

### 12.5 Entity Status

Every entity carries a **Status** describing its current implementation state — distinct from the historical references of §12.2. Status must let a reader tell apart **exists** (present in the repo), **implemented** (has working logic), **wired** (actually on a runtime path), **production** (shipped and in use), and **deprecated / legacy** (superseded). Example values (illustrative, not an exhaustive vocabulary): *Current, Production, Maintenance Only, Deferred, Pure, Unwired, Experimental, Legacy, Deprecated.* The concept is established here; the controlled vocabulary is fixed later with the glossary (§12.8). It exists because CCC has entities that are implemented-but-unwired (the Capability subsystem) and shipped-but-frozen (One-Click Update, Maintenance Only) — Status keeps those states from being read as the same thing.

### 12.6 Verified Revision

Every entity and every diagram spec (§7) carries a **Verified Revision** — the revision its description was last checked against, for long-term traceability. A Verified Revision may reference a **release tag, a git commit, or an approved milestone.** Formatting is not defined here; the convention and its purpose are. An entity whose Verified Revision lags the current architecture is a candidate for re-review (§12.9).

### 12.7 Architectural Confidence

Every claim carries a **Confidence** classification, so the Atlas never presents assumptions as verified facts. Standard levels: **Verified · Partially Verified · Engineering Reasoning · Hypothesis** — aligned with the project's evidence discipline (**Verified Fact · Engineering Reasoning · Assumption · Open Question**). This is not about weakening confidence; it is intellectual honesty and evidence tracking, and it operationalizes the evidence-precedes-representation principle (§4).

### 12.8 Canonical Glossary

The Atlas maintains a **canonical engineering vocabulary**; terminology must remain consistent across every entry (a requirement of the machine-friendly goal, §12.4). Terms to be defined include, e.g.: *Capability, Subsystem, Component, Module, Runtime Flow, Data Flow, Trust Boundary, Service, Adapter, Provider, Engine.* The glossary is **not created now** — the Charter establishes only that terms are used identically wherever they appear, fixed once in the glossary rather than redefined per document.

### 12.9 Atlas Lifecycle

The Atlas must stay synchronized with the real architecture. A change that can alter architecture — a **merged ADR, a merged subsystem change, a merged architectural refactor, or an approved architecture decision** — triggers an **Atlas review**: the affected entities are re-checked and their Verified Revision (§12.6) updated before the Atlas is again considered *current*. Between such triggers the Atlas is stable. This defines the lifecycle **philosophy only** — no workflow automation or tooling is designed here.

## 13. Stop

This Charter governs how the Atlas is built; it is not itself Atlas content. No Phase A work, discovery, capability enumeration, IDs, or diagrams are performed under this document — those proceed only as separately approved phases (§9).

---

## Charter Status

This Charter is now **Frozen** (Version 1.0, 2026-07-02).

Future modifications require an explicit architectural decision and are expected to be exceptional. Routine Architecture Atlas work shall proceed **under** this Charter rather than continuously modifying it.

**Deferred to resolve before Phase B (not before Phase A):** the ID numbering scheme (§12.3), the controlled Entity-Status vocabulary (§12.5), and the initial Canonical Glossary (§12.8). Whether an ADS exists (§5) is confirmed during Phase A discovery.
