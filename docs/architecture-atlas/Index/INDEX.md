# CCC Architecture Atlas — Index

**Release:** v1.0.0-rc1 · **Verified Revision:** v0.3.12 · **Date:** 2026-07-02
**Status:** Release Candidate 1 · **Type:** Engineering Knowledge Base (human + AI audiences)

The Architecture Atlas is the canonical, evidence-based description of the Conduit
Control Center (CCC) runtime architecture. It is derived exclusively from the
repository at `v0.3.12`. Every entity carries a permanent, opaque identifier and
is traceable to repository evidence.

## Document map

| Path | Purpose |
|---|---|
| `CHARTER.md` | Frozen governing charter (v1.0). Method, conventions, freeze rules. |
| `Index/INDEX.md` | This file — navigation entry point. |
| `ADS/ADS.md` | Architecture Design Specification — the structured engineering reference. |
| `Appendix/REGISTRIES.md` | Frozen registries: Capability, Subsystem, Component, Runtime Flow, Data Flow, Trust Boundary, External System. |
| `Appendix/TRACEABILITY.md` | Traceability Matrix, Cross-Reference Index, Engineering Query Index, Architecture Views, Diagram Mapping. |
| `Claude-Design/MASTER.md` | Single self-contained document for automatic diagram generation (Claude Design). |
| `RELEASE-NOTES.md` | Release notes, version, contents, deferrals. |

## Entry points by audience

- **New engineer:** `ADS/ADS.md` → Capability Model → Runtime Subsystems → Component View.
- **Security review:** `ADS/ADS.md` → Trust Boundaries; `Appendix/REGISTRIES.md` → TB registry.
- **Operations:** `ADS/ADS.md` → External Systems + Operational View.
- **Diagram generation:** `Claude-Design/MASTER.md` (self-contained).
- **Traceability / impact analysis:** `Appendix/TRACEABILITY.md`.

## Identifier scheme (frozen)

Identifiers are **permanent, opaque identities**. The numeric part is allocation
sequence only — no architectural, dependency, importance, lifecycle, discovery, or
alphabetical meaning. IDs are never renumbered or reused.

| Prefix | Entity | Count |
|---|---|---|
| `CAP-` | Capability | 21 |
| `SUB-` | Runtime Subsystem | 11 |
| `CMP-` | Runtime Component | 70 |
| `RF-` | Runtime Flow | 44 |
| `DF-` | Data Flow | 20 |
| `TB-` | Trust Boundary | 9 |
| `EXT-` | External System | 8 |
| `VIEW-` | Architecture View | 13 |
| `DIAG-` | Diagram specification | (per view) |

## Status & class legend

- **Status (primary):** Current · Not-Implemented · Deferred · Deprecated (reserved).
- **Status (qualifiers):** Pure · Unwired · Maintenance-Only · Experimental (reserved).
- **Capability Class:** Runtime · Background · Deployment · Administration · N/A (Unwired only).
- **Capability Scope:** Internal · External · Mixed.
- **Confidence:** Verified · Partially Verified · Engineering Reasoning · Hypothesis.

## By-design terminals (not gaps)

- **CAP-004** Pair Conduit node — *Not-Implemented* (declared 501 surface).
- **CAP-016** Select HTTPS port — *Deployment View* (excluded from runtime chains).
- **CAP-021** Evaluate required capabilities — *Unwired* (present, on no runtime path).
