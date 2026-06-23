# DGM-15 — Ryve claim flow

## Source
- Referenced filename: `ryve-claim-flow.svg`
- Source chapter: `docs/user-guide/11-ryve-integration.md` (English — Source of Truth)
- Source section/context: §11.4 «The Claim QR generation process» and §11.5 (the QR is temporary and expires).

## Purpose
Show that Conduit creates the Ryve claim while CCC only requests and displays a temporary QR, and that the QR is ephemeral (held briefly in memory, then cleared).

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes, in order: `User clicks Generate` → `CCC requests claim` → `Conduit creates claim` → `CCC receives QR (PNG)` → `CCC displays temporary QR` → `User scans with Ryve app` → `Claim expires and is cleared from memory`.
- `Conduit creates claim` is the focal node (the claim's true owner).

## Visual direction
Top-down process flow. `Conduit creates claim` uses `primary`; the ephemeral cleanup `Claim expires …` uses `success`; the rest are `neutral`.

## Text labels
English, short. Parentheses appear only inside quoted node labels.

## Security/privacy constraints
The Claim QR is a sensitive credential; the diagram is conceptual and shows no real claim, token, QR image, or credential.

## Validation checklist
- [ ] Matches chapter context (§11.4 / §11.5)
- [ ] Makes clear Conduit creates the claim, CCC only displays it
- [ ] Shows the QR is temporary / cleared
- [ ] File name matches manifest (`ryve-claim-flow.svg`)
