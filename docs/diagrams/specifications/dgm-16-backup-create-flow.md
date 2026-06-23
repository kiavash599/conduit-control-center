# DGM-16 — Backup creation flow

## Source
- Referenced filename: `backup-create-flow.svg`
- Source chapter: `docs/user-guide/12-backup-and-restore.md` (English — Source of Truth)
- Source section/context: §12.6 «Creating a Backup» — CCC performs Collect → Package → Encrypt → Download.

## Purpose
Visualise how CCC creates an encrypted backup: it collects the configuration, packages the data, encrypts it (AES-256-GCM with a scrypt-derived key), and sends the file to the browser for download.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes, in order: `Collect Configuration` → `Package Data` → `Encrypt Backup (AES-256-GCM + scrypt)` → `Download Backup File`.

## Visual direction
Left→right pipeline. `Encrypt Backup` uses `primary` (the key security step); `Download Backup File` uses `success` (the delivered artifact); `Collect`/`Package` are `neutral`.

## Text labels
English, short. The algorithm note appears inside a quoted node label; `+` and parentheses are quoted to stay parser-safe.

## Security/privacy constraints
Conceptual only — no real backup contents, passphrases, paths, or credentials.

## Validation checklist
- [ ] Matches chapter context (§12.6)
- [ ] Shows encryption (AES-256-GCM + scrypt) as a distinct step
- [ ] Backup is downloaded to the browser (not stored on the Pi)
- [ ] File name matches manifest (`backup-create-flow.svg`)
