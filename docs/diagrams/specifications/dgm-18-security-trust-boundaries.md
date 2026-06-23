# DGM-18 — Security trust boundaries

## Source
- Referenced filename: `security-trust-boundaries.svg`
- Source chapter: `docs/user-guide/14-security-model.md` (English — Source of Truth)
- Source section/context: §14.2 «Trust Boundaries» (with §14.9–14.11 helper / least-privilege design).

## Purpose
Show CCC's layered trust boundaries and least-privilege design: the web app runs non-root and reaches privileged actions only through validated helpers; direct root access is never exposed to the browser.

## Audience
Beginner operator / non-network specialist.

## Required elements
- Layers, in order: `Internet` → `Cloudflare` → `Nginx / TLS termination` → `CCC web app (conduit-cc, non-root)` → `Validated helpers` → `conduit service` and `Root-level operations (only via controlled helpers)`.
- A highlighted **privilege boundary** on the edge from the web app to the validated helpers ("validated requests only").

## Visual direction
Top-down layered flow. `CCC web app` uses `primary`; the privilege gateway (`Validated helpers`) and `Root-level operations` use `warning`; the outer layers and `conduit service` are `neutral`. The privilege boundary is conveyed by the labelled edge into the helpers.

## Text labels
English, short. Parentheses appear only inside quoted node labels.

## Security/privacy constraints
Conceptual architecture only — no real hostnames, IPs, tokens, or credentials.

## Validation checklist
- [ ] Matches chapter context (§14.2)
- [ ] Web app shown as non-root
- [ ] Privileged actions only via validated helpers (privilege boundary highlighted)
- [ ] File name matches manifest (`security-trust-boundaries.svg`)
