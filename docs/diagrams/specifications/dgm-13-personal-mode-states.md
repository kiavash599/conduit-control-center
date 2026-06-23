# DGM-13 — Personal Mode states

## Source
- Referenced filename: `personal-mode-states.svg`
- Source chapter: `docs/user-guide/10-personal-mode.md` (English — Source of Truth)
- Source section/context: §10.5 (activation rule) and §10.6 «Visible states» — Personal Mode is active only when an Identity exists **and** `Max Personal Clients > 0` **and** the change is applied.

## Purpose
Correct the most common misconception: creating an Identity does not by itself activate Personal Mode. Show the three states and the transitions between them.

## Audience
Beginner operator / non-network specialist.

## Required elements
- States: `Not Set Up`, `Created / Inactive`, `Active`.
- Transition `Not Set Up → Created / Inactive` labelled "Create Identity".
- Transition `Created / Inactive → Active` labelled "Set Max Personal Clients > 0 + Apply".
- Transition `Active → Created / Inactive` labelled "Set Max Personal Clients = 0 + Apply".

## Visual direction
Left→right state flow with a return edge from Active back to Inactive. `Active` uses the `success` class; the other states are `neutral`.

## Text labels
English, short. `>` is encoded as `&gt;` in the Mermaid source so it renders correctly under htmlLabels.

## Security/privacy constraints
Conceptual only — no real domains, IPs, tokens, QR codes, usernames, or credentials.

## Validation checklist
- [ ] Matches chapter context (§10.5 / §10.6)
- [ ] Shows that Identity alone is not "Active"
- [ ] No secret or personal data
- [ ] File name matches manifest (`personal-mode-states.svg`)
