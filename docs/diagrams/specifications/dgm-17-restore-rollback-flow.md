# DGM-17 — Restore and automatic rollback

## Source
- Referenced filename: `restore-rollback-flow.svg`
- Source chapter: `docs/user-guide/12-backup-and-restore.md` (English — Source of Truth)
- Source section/context: §12.10 «The Restore process» combined with §12.13 «Automatic Rollback» — restore is health-verified and rolls back automatically on failure.

## Purpose
Show that restore is not a blind overwrite: CCC inspects, checks compatibility, restores, verifies health, and rolls back automatically if health verification fails.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes: `Upload backup`, `Inspect backup`, `Compatibility check`, `User confirmation`, `Restore attempt`, decision `Health verification: healthy?`, `Success / Restored`, `Failure`, `Automatic rollback`, `Post-rollback health check`.
- Decision branch: healthy? **Yes →** `Success / Restored`; **No →** `Failure` → `Automatic rollback` → `Post-rollback health check`.

## Visual direction
Top-down decision flow with one diamond. The decision and the rollback path use `warning`; `Success / Restored` uses `success`; the linear pre-restore steps are `neutral`. (Three semantic colours total, per STYLE.)

## Text labels
English, short. This single diagram intentionally replaces the separate §12.10 restore ASCII and §12.13 rollback ASCII.

## Security/privacy constraints
Conceptual only — no real backup contents, passphrases, paths, or credentials.

## Validation checklist
- [ ] Matches chapter context (§12.10 / §12.13)
- [ ] Shows health verification gating the outcome
- [ ] Shows automatic rollback on failure
- [ ] File name matches manifest (`restore-rollback-flow.svg`)
