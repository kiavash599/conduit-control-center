# DGM-14 — Personal Mode pairing

## Source
- Referenced filename: `personal-mode-pairing.svg`
- Source chapter: `docs/user-guide/10-personal-mode.md` (English — Source of Truth)
- Source section/context: §10.7 «What is QR Pairing?» (and §10.8 — the QR is built from the Identity and is a credential).

## Purpose
Show how a trusted device pairs after Personal Mode is active: the Identity yields a pairing QR, the trusted device scans it, and a personal connection is established.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Nodes, in order: `Identity` → `CCC generates pairing QR` → `Trusted device scans QR` → `Personal connection established`.

## Visual direction
Left→right process flow. `Identity` uses `primary` (the basis of pairing); `Personal connection established` uses `success`; the intermediate steps are `neutral`.

## Text labels
English, short.

## Security/privacy constraints
The pairing QR is a credential; the diagram is conceptual and shows no real Identity, QR image, token, or credential.

## Validation checklist
- [ ] Matches chapter context (§10.7 / §10.8)
- [ ] Shows pairing happens by scanning the Identity-derived QR
- [ ] No secret or personal data
- [ ] File name matches manifest (`personal-mode-pairing.svg`)
