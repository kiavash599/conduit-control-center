# CCC Architecture Atlas — Release Notes

**Version:** v1.0.0-rc1 (Release Candidate 1)
**Verified Revision:** v0.3.12
**Date:** 2026-07-02
**Type:** Engineering Knowledge Base — human engineers + AI (Claude Design) input

## Summary

First complete release of the CCC Architecture Atlas: an evidence-based, permanently
identified, fully traceable model of the Conduit Control Center runtime architecture,
compiled exclusively from the repository at `v0.3.12`. The release serves two equal
purposes: a long-term engineering knowledge base, and a single self-contained input
(`Claude-Design/MASTER.md`) for automatic architecture-diagram generation.

## Contents

```
docs/architecture-atlas/
    CHARTER.md                     # governing charter (frozen v1.0)
    RELEASE-NOTES.md               # this file
    Index/
        INDEX.md                   # navigation entry point
    ADS/
        ADS.md                     # Architecture Design Specification
    Appendix/
        REGISTRIES.md              # Capability/Subsystem/Component/RF/DF/TB/EXT
        TRACEABILITY.md            # matrix, cross-refs, queries, views, diagram map
    Claude-Design/
        MASTER.md                  # self-contained diagram-generation specification
```

## What the release contains

- **21 Capabilities** (CAP-001…021) with Class, Scope, Status, Confidence.
- **11 Runtime Subsystems** (SUB-001…011) with responsibilities and primary capabilities.
- **70 Runtime Components** (CMP-001…070) with Owned Files (Component ≠ File).
- **44 Runtime Flows**, **20 Data Flows**, **9 Trust Boundaries**, **8 External Systems**.
- **13 Architecture Views**, a full **Traceability Matrix**, a bidirectional
  **Cross-Reference Index**, an **Engineering Query Index**, and a **Diagram Mapping
  Registry**.
- A **self-contained MASTER.md** with complete entity data and content-driven
  rendering rules (no fixed canvas; architecture determines the canvas).

## Frozen foundation

Charter v1.0; Capability, Subsystem, Component, Runtime Flow, Data Flow, Trust
Boundary, and External Systems registries; Architecture Views; Traceability Matrix;
Cross-Reference and Query indexes; Diagram Mapping. These are immutable except to
correct objective factual errors or under an approved ADR / repository change (Atlas
Lifecycle).

## By-design terminals (not defects)

- **CAP-004** Pair Conduit node — Not-Implemented (declared 501 surface).
- **CAP-016** Select HTTPS port — Deployment View (excluded from runtime chains).
- **CAP-021** Evaluate required capabilities — Unwired (present, on no runtime path;
  ADR-0002).

## Known deferrals (non-blocking; ADS-body detail)

- Per-table SQLite enumeration beyond `sessions` and traffic tables (contributed via
  `database.py` `_TABLE_DDL`).
- Exact HTTP status/error semantics per route (summarized, not enumerated).
- **Artifact signing** for the Trusted Update Engine (TB-007 / ADR-0001 invariant 5)
  remains a deferred hardening item.
- The **Deployment/Provisioning View** is referenced but not compiled (out of runtime
  scope).

## Governance

- Identifiers are permanent, opaque identities; no semantic numbering; never
  renumbered or reused.
- Functional expansion of any subsystem requires a new ADR, not incremental
  redefinition.
- The Atlas is revised under the Atlas Lifecycle: a merged ADR / subsystem change /
  refactor / approved decision triggers Atlas review before it is considered current.

## Release-candidate status

This is **RC1**. Promotion to `v1.0.0` is recommended after Project-Owner sign-off,
with no further architectural work expected — only presentation/organization.

## Recommended version

`architecture-atlas v1.0.0-rc1`

## Recommended commit message

```
docs(atlas): Architecture Atlas release v1.0.0-rc1 (final assembly)

Assemble the complete CCC Architecture Atlas from the frozen registries:
Index, ADS, Appendix (registries + traceability), and a self-contained
Claude-Design/MASTER.md for content-driven diagram generation.

- 21 capabilities, 11 subsystems, 70 components, 44 runtime flows,
  20 data flows, 9 trust boundaries, 8 external systems, 13 views.
- Full traceability matrix, cross-reference and query indexes, diagram map.
- MASTER.md is fully self-contained (no includes); architecture determines
  the canvas (no fixed page/size).

Derived exclusively from the frozen Atlas at Verified Revision v0.3.12.
Docs-only; no code, no ADR, no Charter changes.
```
