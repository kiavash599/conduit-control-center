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
| [ADR-0001](0001-trusted-update-engine.md) | Trusted Update Engine | Accepted |
| [ADR-0003](0003-signed-release-artifacts.md) | Signed Release Artifacts and On-Device Verification | Accepted |

> **Numbering note:** ADR-0003 was authored ahead of ADR-0002 because the signing/verification
> decision it records has been referenced since v0.3.13. **ADR-0002 — Update Payload Specification**
> remains *planned* (below) and is complementary: ADR-0003 fixes signing/verification/trust;
> ADR-0002 will formalize the payload/manifest schema.

## Planned / future

- **ADR-0002 — Update Payload Specification** *(future)*: the concrete payload
  manifest, schema, capability and migration declarations, integrity
  (signature/checksum) scheme, and compatibility fields. ADR-0001 owns the
  *invariants* the engine must uphold; ADR-0002 will own *how* the payload
  expresses them.
