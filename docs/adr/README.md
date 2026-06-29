# Architecture Decision Records (ADRs)

This directory holds CCC's **Architecture Decision Records** — durable records of
long-term architecture decisions and the reasoning behind them.

## Conventions

- ADRs record **enduring** architecture decisions (the *why*), not implementation
  detail, roadmap, or version-specific notes.
- ADRs are **append-only**: once accepted, an ADR is not casually rewritten. A
  decision is changed by adding a **new ADR that supersedes** the old one (and the
  old one's `Superseded by` field is updated to point to it).
- Files are numbered sequentially: `NNNN-short-title.md`.

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-0001](0001-trusted-update-engine.md) | Trusted Update Engine | Proposed |

## Planned / future

- **ADR-0002 — Update Payload Specification** *(future)*: the concrete payload
  manifest, schema, capability and migration declarations, integrity
  (signature/checksum) scheme, and compatibility fields. ADR-0001 owns the
  *invariants* the engine must uphold; ADR-0002 will own *how* the payload
  expresses them.
