# DGM-19 — CSRF double-submit verification

## Source
- Referenced filename: `csrf-double-submit.svg`
- Source chapter: `docs/user-guide/14-security-model.md` (English — Source of Truth)
- Source section/context: §14.7 «CSRF Protection» — Double Submit Cookie: the `csrf_token` cookie is compared with the `X-CSRF-Token` header.

## Purpose
Visualise CCC's CSRF protection: each request carries both the `csrf_token` cookie and the `X-CSRF-Token` header; CCC compares them and allows the request only on a match, otherwise it returns 403.

## Audience
Beginner operator / non-network specialist.

## Required elements
- `Browser request` → `Cookie (csrf_token) + Header (X-CSRF-Token)` → decision `Match?`.
- Branch: **Yes →** `Allowed`; **No →** `403 Forbidden`.

## Visual direction
Top-down decision flow with one diamond. The decision `Match?` and the `403 Forbidden` reject path use `warning`; `Allowed` uses `success`; the request and token nodes are `neutral`. (Three semantic colours total, per STYLE.)

## Text labels
English, short. Parentheses and `+` appear only inside quoted node labels.

## Security/privacy constraints
Conceptual only — no real tokens, cookies, headers, or credentials.

## Validation checklist
- [ ] Matches chapter context (§14.7)
- [ ] Shows both cookie and header being compared
- [ ] Shows allow-on-match, 403-on-mismatch
- [ ] File name matches manifest (`csrf-double-submit.svg`)
