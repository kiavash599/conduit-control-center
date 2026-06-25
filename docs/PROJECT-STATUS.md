# Project Status — Conduit Control Center

> **Authoritative operational status.** Tracks current state, open/closed work, and
> known issues. The **roadmap** (`docs/roadmap/CCC_Product_Roadmap_v1.md`, Rev 1.11,
> Reconciled) owns forward planning and feature catalogues; the **CHANGELOG** owns
> shipped history; **closure records** (`docs/closure/`) are optional per-epic
> decision deep-dives. This file is the canonical closed-epic index and links the
> three — it does not duplicate them.
>
> Last reconciled: 2026-06-24 · branch `main` · HEAD `652f028`.

## 1. Current Release

| Current Product Release | Current Documentation Release | Roadmap Revision | Status |
|---|---|---|---|
| v0.3.1 (2026-06-24, patch — D1 + D2) | docs-v0.3 (2026-06-22, documentation milestone) | 1.13 | Reconciled · clean baseline |

Branch `main` · HEAD `652f028` (Epic C — TLS onboarding) · v0.3.1 released; Epic C complete (unreleased).

## 2. Closed Epics

| Epic / Workstream | Status | Evidence | Closure record |
|---|---|---|---|
| English User Guide | ✅ Closed | 18 chapters, `docs/user-guide/` | `775848b` |
| Persian User Guide (text) | ✅ Closed | 18 chapters; heading parity 17/17 | `fe86f80`, `c45fc48` |
| Diagram Program (EN) | ✅ Closed | DGM-01–19; 19 SVG/PNG integrated EN | `c898201`…`8a8379b` |
| Screenshot Program (K.5) | ✅ Closed | 21 assets; parity 22=22; 0 placeholders; Guard PASS | `c354993`; `INTEGRATION-SYNC.md` (19/19) |
| Documentation Governance | ✅ Closed | governance + `docs/release-checklist.md` | `971b1bf` |
| Roadmap Reconciliation (Epic A.1) | ✅ Closed | Rev 1.11 Reconciled | `2d42372` |
| Governance & Status (Epic A.2) | ✅ Closed | this file established as the operational source of truth | `docs/PROJECT-STATUS.md` |
| v0.3.1 Hotfixes (Epic B) | ✅ Released v0.3.1 | D1 root-URL fix (`f5233ff`; CI + Pi PASS) + D2 screenshot correction (Parity Guard PASS) | tag `v0.3.1` |
| Backup Contract Alignment (BCA-1 + BCA-2) | ✅ Complete | Backup Subsystem Contract v1 approved; BCA-1 (cross-platform fail-open exclusion guard) + BCA-2 (POSIX-only permission-test guard); CI green | commit `043cb6a` |
| TLS / Origin Certificate Onboarding (Epic C / D3) | ✅ Complete (unreleased) | EN ch05 §5.15 + ch06 §6.4 (`83d2ed0`); FA parity (`652f028`); **bilingual illustrated TLS guide** — `docs/tls-setup.md` (EN) + `docs/fa/tls-setup.md` (FA, `05366fe`), 8 shared redacted screenshots; chapters language-routed (`957d497`) | commits `83d2ed0`, `652f028`, `05366fe`, `957d497` |
| Smart Conduit Control (v0.2.0) | ✅ Closed | roadmap §6 CLOSED | tag `v0.2.0` |
| Regional Analytics | ✅ Closed | — | `docs/closure/regional-analytics-closure.md` |
| Live Operations | ✅ Closed | — | `docs/closure/live-operations-closure.md` |
| Bandwidth Scheduling | ✅ Closed | — | `docs/closure/bandwidth-scheduling-closure.md` |
| Theme Support | ✅ Closed | — | `docs/closure/theme-support-closure.md` |
| Contribution Advisor | ✅ Closed | delivered v0.2.0 (supersedes Smart Assistant) | roadmap §6.4 |
| Personal Mode | ✅ Closed | shipped v0.3.0; Pi-validated | `docs/closure/PERSONAL_MODE_CLOSURE.md` |
| Ryve Claim / Identity | ✅ Closed | shipped v0.3.0 | CHANGELOG `[0.3.0]` (no closure file — by policy) |
| Backup & Restore | ✅ Closed | shipped v0.3.0 | CHANGELOG `[0.3.0]` (no closure file — by policy) |

## 3. Active Epics

**NONE.** Epic B (v0.3.1) released; Backup Contract Alignment complete (`043cb6a`); Epic C — TLS onboarding complete (`83d2ed0`, `652f028`, unreleased). See Closed Epics.

## 4. Approved Next Epics

| Epic | Description | Priority |
|---|---|---|
| **D — Diagram Parity + Persian RTL/LTR Formatting** | D4: integrate DGM-13–19 into the Persian guide · D5: fix Persian RTL/LTR formatting of mixed-language technical content (URLs, commands, paths, API names, inline code) | Next |

## 5. Known Issues

| ID | Issue | Severity | Status | Epic / Target |
|---|---|---|---|---|
| D1 | Root URL `/` returns 404; only `/login` works, though docs instruct opening the root URL | High (P0) | ✅ Resolved — `f5233ff` (`/`→`/dashboard` 307); CI PASS; Raspberry Pi runtime validation PASS | Epic B / v0.3.1 |
| D2 | `cloudflare-domain-active.png` shows a subdomain (`conduit.example.com`) as an active Cloudflare zone | Medium-High (P1) | ✅ Resolved — in-place relabel to root domain `example.com`; same filename/path/dimensions; Parity Guard PASS | Epic B / v0.3.1 |
| D3 | TLS/Origin-cert workflow not integrated into the guide flow | Medium (P1) | ✅ Resolved — EN ch05 §5.15 + ch06 §6.4 (`83d2ed0`); FA parity (`652f028`); bilingual TLS guides `docs/tls-setup.md` (EN) + `docs/fa/tls-setup.md` (FA, `05366fe`), language-routed chapter links (`957d497`), 8 shared redacted screenshots | Epic C / D |
| D4 | DGM-13–19 (7 feature diagrams) integrated in EN only; absent in FA | Medium (P2) | Open | Epic D |
| D5 | Persian RTL/LTR formatting & mixed-language readability — URLs, commands, paths, API names, inline code rendering incorrectly in RTL layout | Low-Medium (P3) | Open | Epic D |
| D6 | Governance / release-narrative drift (roadmap vs shipped tags) | — | ✅ Resolved by Epic A.1 + Epic A.2 | Epic A |

## 6. Deferred Work

| Item | Reason / revisit trigger | Evidence |
|---|---|---|
| Deep Persian rewrite | Only if Epic D's light formatting pass proves insufficient | structural parity already strong (17/17 headings) |
| Parity Guard → CI integration | Guard is manual-only | `.github/workflows/ci.yml` has no parity step |
| `errors="replace"` in canonical guard | Mount-robustness; only the run-copy is patched | `scripts/check-screenshot-parity.py` |
| Roadmap §4 maintenance | `db-perms-600`, `root-crontab-cleanup` (ops/security); `pairing-neutralise` → v0.4 candidate | roadmap §4 |
| D7 — health.py OpenAPI example `0.1.0` | Cosmetic; runtime returns correct APP_VERSION; excluded from v0.3.1 | `backend/api/health.py` |
| CHANGELOG `[Unreleased]` section | Missing after 0.3.0 stamp; add when v0.3.1 work begins | `CHANGELOG.md` |

## 7. Documentation Status

| Area | State |
|---|---|
| English Guide | ✅ Complete (18 chapters) |
| Persian Guide | ✅ Complete (text; 18 chapters; heading parity 17/17) |
| Diagrams | 19 on disk; EN integrated; **DGM-13–19 FA pending → Epic D** |
| Screenshots | 21 integrated EN+FA; 0 placeholders |
| Parity Guard | ✅ PASS (Existence · EN↔FA parity · Orphans · Hygiene) |

## 8. Release Timeline

| Version | Date | Note |
|---|---|---|
| v0.1.0 | 2026-06 | MVP |
| v0.1.1 | 2026-06-11 | Maintenance |
| v0.2.0 | 2026-06-17 | Smart Conduit Control |
| v0.3.0 | 2026-06-21 | First public release (Personal Mode, Ryve Claim, Backup & Restore) |
| docs-v0.3 | 2026-06-22 | Documentation milestone (not a product release) |
| v0.3.1 | released (2026-06-24) | Hotfixes (D1 + D2) |

## 9. Next Recommended Action

**Start Epic D — Diagram Parity + Persian RTL/LTR Formatting.** Epic C (TLS onboarding) is complete (unreleased).

---

> **Maintenance:** update this file at every epic open/close. On **open**, move the epic
> from Approved Next to Active; on **close**, move it to Closed Epics (with evidence),
> flip the related Known-Issue rows to Resolved, and update Next Recommended Action. On
> every release/tag, append a row to the Release Timeline (append-only). Wire an "Update
> PROJECT-STATUS.md" step into `docs/release-checklist.md` so it cannot drift. Record
> state and pointers only — feature specs live in the roadmap, shipped detail in the
> CHANGELOG, decision rationale in closure records. Owner: project owner; changes
> proposed per epic.
