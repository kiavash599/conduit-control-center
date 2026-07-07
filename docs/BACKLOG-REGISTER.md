# CCC Backlog Register

**Purpose:** a single, contributor-facing register of **accepted** backlog items — work the project has agreed is worth doing but has deliberately postponed. It exposes what is coming, why, and its priority, without committing a schedule. Implementation of any item is separately gated (design → Change Proposal → validation).

**Scope:** accepted, not-yet-implemented items only. Delivered work lives in the CHANGELOG and `docs/PROJECT-STATUS.md`; forward feature planning lives in the roadmap; architecture decisions live in `docs/adr/`.

**Conventions:** items are `BL-NNNN`; status is one of *Accepted*, *In Design*, *In Progress*, *Done* (on completion the row is moved out to the CHANGELOG/closure). Priority: Critical / High / Medium / Low.

## Register

| ID | Item | Priority | Status | Summary | Origin |
|---|---|---|---|---|---|
| BL-0001 | **Owner Operations Toolkit (OOT) — Capability 1: Signed-Release Toolkit** | Medium (P2) | Accepted (implementation postponed) | Reduce the manual toil and error surface of the ADR-0003 signed-release workflow with an Owner Release Guide, a local build/sign/verify helper, a post-publish verification helper, and a manual GitHub publish checklist. **Boundary:** automate only local/read-only steps; all irreversible public actions (git push, tag push, GitHub Release asset changes) remain a manual, Owner-controlled checklist. First capability of the broader long-term **Owner Operations Toolkit** concept. | CCC-CAMP-0001 retrospective; accepted per DR-006 |

## Notes
- **BL-0001** implementation scope is unchanged by its placement under the "Owner Operations Toolkit" concept; the name reflects a long-term grouping, not a scope change.
- New backlog items are appended here when accepted; each carries its origin (campaign, review, or issue) for traceability.
