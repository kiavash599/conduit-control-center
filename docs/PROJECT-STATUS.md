# Project Status — Conduit Control Center

> **Authoritative operational status.** Tracks current state, open/closed work, and
> known issues. The **roadmap** (`docs/roadmap/CCC_Product_Roadmap_v1.md`, Rev 1.23,
> Reconciled to v0.3.14) owns forward planning and feature catalogues; the
> **CHANGELOG** owns shipped history; **closure records** (`docs/closure/`) are
> optional per-epic decision deep-dives. This file is the canonical closed-epic
> index and links the three — it does not duplicate them.
>
> Last reconciled: 2026-07-07 · branch `main` · latest release `v0.3.14`.

## 1. Current Release

| Current Product Release | Current Documentation Release | Roadmap Revision | Status |
|---|---|---|---|
| **v0.3.14** (released 2026-07-07 — ADR-0003 Trusted Update Signing Phase-B audit + deploy-integrity fix `rsync --checksum`; Pi validated) · v0.3.13 (ADR-0003 signing groundwork) · v0.3.12 (frontend polish) · v0.3.11 (One-Click Update production-proven) · v0.3.5 (Log Management / SD-Card Protection) · v0.3.2 (Features 1 + 2) | docs-v0.3 (2026-06-22, documentation milestone) | 1.23 | ✅ Released · One-Click Update **Maintenance Only**; ADR-0003 signing (Phase-B) landed (see §10 and `docs/closure/one-click-update-closure.md`) |

Branch `main` · **v0.3.14 released** — the v0.3.2 → v0.3.12 line delivered HTTPS port selection + one-click CCC update (Features 1 + 2, v0.3.2), Log Management / SD-Card Protection (v0.3.5, commit `a6b6bd4`), and the One-Click Update hardening/validation series culminating in **production-proven** on Raspberry Pi (v0.3.10 → v0.3.11) and the final frontend-polish validation (v0.3.11 → v0.3.12). The v0.3.13 → v0.3.14 line then delivered **ADR-0003 Trusted Update Signing** (Phase-B audit trail) and a **deploy-integrity fix** (`rsync --checksum`), Raspberry Pi-validated. One-Click Update / Trusted Update Engine is now **Completed / Production-Proven / Maintenance Only**: future work is limited to bug fixes, security hardening, and maintenance, and functional expansion requires a new ADR (ADR-0001 Accepted; closure `docs/closure/one-click-update-closure.md`). See §10.

## 2. Closed Epics

| Epic / Workstream | Status | Evidence | Closure record |
|---|---|---|---|
| English User Guide | ✅ Closed | 17 chapters, `docs/user-guide/` | `775848b` |
| Persian User Guide (text) | ✅ Closed | 17 chapters; heading parity 17/17 | `fe86f80`, `c45fc48` |
| Diagram Program (EN) | ✅ Closed | DGM-01–19; 19 SVG/PNG integrated EN | `c898201`…`8a8379b` |
| Screenshot Program (K.5) | ✅ Closed | 21 assets; parity 22=22; 0 placeholders; Guard PASS | `c354993`; `INTEGRATION-SYNC.md` (19/19) |
| Documentation Governance | ✅ Closed | governance + `docs/release-checklist.md` | `971b1bf` |
| Documentation Platform (MkDocs Phase 1) | ✅ Completed | MkDocs Material site; Persian RTL; self-hosted Vazirmatn + Inter (no Google Fonts/CDN/analytics); bilingual landing page; RTL/LTR Authoring Style Guide v1.0; documentation governance foundation | `website/`, `mkdocs.yml`, `CONTRIBUTING.md` (Phase 1 close commit) |
| Roadmap Reconciliation (Epic A.1) | ✅ Closed | Rev 1.11 Reconciled | `2d42372` |
| Governance & Status (Epic A.2) | ✅ Closed | this file established as the operational source of truth | `docs/PROJECT-STATUS.md` |
| v0.3.1 Hotfixes (Epic B) | ✅ Released v0.3.1 | D1 root-URL fix (`f5233ff`; CI + Pi PASS) + D2 screenshot correction (Parity Guard PASS) | tag `v0.3.1` |
| HTTPS Port Selection (Feature 1) | ✅ Released v0.3.2 | Cloudflare-compatible HTTPS port chosen at install; preserved by `update.sh`; `ccc-apply-https-port`; dashboard read-only display; Pi 4 + Pi 3 B validated | `docs/closure/v0.3.2-closure.md`; tag `v0.3.2` |
| One-Click CCC Update (Feature 2) | ✅ Production-proven · **Maintenance Only** (v0.3.12) | Dashboard Software Updates → `/api/update` → `ccc-update-apply` → `update.sh --ccc-only`; GitHub Releases (stable); async status + reconnect + auto-rollback; no auto-update; Conduit Core out of scope. The v0.3.3 validation release exposed a `/run/lock` EROFS, fixed in v0.3.4 (lock → `/var/lib/conduit-cc/.update.lock`). **Validated end-to-end on a live Raspberry Pi: v0.3.10 → v0.3.11 (B1 transient-unit engine) and the final frontend-polish v0.3.11 → v0.3.12 (2026-07-02): `state=success`, `/api/health` ok at 0.3.12, `/opt/conduit-cc/bin` preserved, no "cannot delete non-empty directory", `ProtectSystem=strict` unchanged.** Now **Maintenance Only** — future work limited to bug fixes, security hardening, and maintenance. | `docs/closure/one-click-update-closure.md`; tags `v0.3.2`…`v0.3.12` |
| One-Click Update Lock-Path Fix (EROFS) | ✅ Released v0.3.4 | `ccc-update-apply` lock moved from `/run/lock` (read-only under `ProtectSystem=strict`) to `/var/lib/conduit-cc/.update.lock` (+ `O_NOFOLLOW`); exposed by the v0.3.3 validation release | CHANGELOG `[0.3.4]`; tag `v0.3.4` |
| Log Management / SD-Card Protection | ✅ Released v0.3.5 (`a6b6bd4`) | logrotate for `/var/log/conduit-cc/*.log` (`deployment/conduit-cc.logrotate`; provisioned by install.sh, re-provisioned by update.sh, removed by uninstall.sh) + automatic cleanup of stale `ccc-update-*` work directories and the current work directory **after** the terminal `update-status.json` is written, inside `ccc-update-apply` under the existing update flock. Linux-native; **no** new helper, sudoers rule, systemd timer, dashboard cleanup feature, or journald change | commit `a6b6bd4`; tag `v0.3.5` |
| Backup Contract Alignment (BCA-1 + BCA-2) | ✅ Complete | Backup Subsystem Contract v1 approved; BCA-1 (cross-platform fail-open exclusion guard) + BCA-2 (POSIX-only permission-test guard); CI green | commit `043cb6a` |
| TLS / Origin Certificate Onboarding (Epic C / D3) | ✅ Released v0.3.2 | EN ch05 §5.15 + ch06 §6.4 (`83d2ed0`); FA parity (`652f028`); **bilingual illustrated TLS guide** — `docs/tls-setup.md` (EN) + `docs/fa/tls-setup.md` (FA, `05366fe`), 8 shared redacted screenshots; chapters language-routed (`957d497`) | commits `83d2ed0`, `652f028`, `05366fe`, `957d497` |
| Smart Conduit Control (v0.2.0) | ✅ Closed | roadmap §6 CLOSED | tag `v0.2.0` |
| Regional Analytics | ✅ Closed | — | `docs/closure/regional-analytics-closure.md` |
| Live Operations | ✅ Closed | — | `docs/closure/live-operations-closure.md` |
| Bandwidth Scheduling | ✅ Closed | — | `docs/closure/bandwidth-scheduling-closure.md` |
| Theme Support | ✅ Closed | — | `docs/closure/theme-support-closure.md` |
| Contribution Advisor | ✅ Closed | delivered v0.2.0 (supersedes Smart Assistant) | roadmap §6.4 |
| Personal Mode | ✅ Closed | shipped v0.3.0; Pi-validated | `docs/closure/PERSONAL_MODE_CLOSURE.md` |
| Ryve Claim / Identity | ✅ Closed | shipped v0.3.0 | CHANGELOG `[0.3.0]` (no closure file — by policy) |
| Backup & Restore | ✅ Closed | shipped v0.3.0 | CHANGELOG `[0.3.0]` (no closure file — by policy) |
| Trusted Update Signing (ADR-0003, Phase-B) | ✅ Released v0.3.13 → v0.3.14 | Signed canonical artifacts + fail-closed on-device verification + non-authorizing audit trail; deploy-integrity fix (`rsync --checksum`) Pi-validated | ADR-0003; CHANGELOG `[0.3.13]`,`[0.3.14]` |

## 3. Active Epics

**None open.** The v0.3.2 → v0.3.12 line is released (see §1 and §8 Release Timeline). One-Click Update / Trusted Update Engine is **Completed / Production-Proven / Maintenance Only** (see §2 Closed Epics and §10). The project is currently **between implementation phases** — no implementation epic is open. See §9 Next Recommended Action.

## 4. Approved Next Epics

**None.** The two epics previously listed here — **HTTPS Port Selection (Feature 1)** and the **One-click update system (Feature 2)** — are delivered and now recorded under §2 Closed Epics (v0.3.2 → v0.3.12). No implementation epic is currently approved; the project is **between implementation phases** (see §9). Candidate drivers for a future phase — Conduit Core update design, Documentation Normalization — are defined in the reconciled roadmap (`docs/roadmap/CCC_Product_Roadmap_v1.md`, *Next Phase / Candidate Drivers*). Artifact signing is delivered (ADR-0003, v0.3.13–v0.3.14) and is no longer a candidate; the Architecture Atlas has been retired from repository planning and relocated as a frozen, historical Owner reference outside the repository (not a source of truth).

> Documentation work that was tracked here (D5 RTL/LTR formatting) is now split: **platform RTL/LTR support is complete** under Documentation Platform Phase 1; **content normalization of existing chapters is Deferred** — see §6 *Documentation Normalization*.

## 5. Known Issues

| ID | Issue | Severity | Status | Epic / Target |
|---|---|---|---|---|
| D1 | Root URL `/` returns 404; only `/login` works, though docs instruct opening the root URL | High (P0) | ✅ Resolved — `f5233ff` (`/`→`/dashboard` 307); CI PASS; Raspberry Pi runtime validation PASS | Epic B / v0.3.1 |
| D2 | `cloudflare-domain-active.png` shows a subdomain (`conduit.example.com`) as an active Cloudflare zone | Medium-High (P1) | ✅ Resolved — in-place relabel to root domain `example.com`; same filename/path/dimensions; Parity Guard PASS | Epic B / v0.3.1 |
| D3 | TLS/Origin-cert workflow not integrated into the guide flow | Medium (P1) | ✅ Resolved — EN ch05 §5.15 + ch06 §6.4 (`83d2ed0`); FA parity (`652f028`); bilingual TLS guides `docs/tls-setup.md` (EN) + `docs/fa/tls-setup.md` (FA, `05366fe`), language-routed chapter links (`957d497`), 8 shared redacted screenshots | Epic C / D |
| D4 | DGM-13–19 (7 feature diagrams) integrated in EN only; absent in FA | Medium (P2) | ✅ Resolved — DGM-13–19 integrated into FA ch10/11/12/14 (Epic D Batch 3); DGM-01–12 into FA ch04/04a/05 (Batches 1–2); full DGM-01–19 EN↔FA parity | Epic D |
| D5 | Persian RTL/LTR formatting & mixed-language readability — URLs, commands, paths, API names, inline code rendering incorrectly in RTL layout | Low-Medium (P3) | ✅ Platform support complete (MkDocs Phase 1 — per-page `dir=rtl`, code/command LTR protection, `.tech-list`); content normalization of existing chapters **Deferred** (see §6) | Docs Platform Phase 1 |
| D6 | Governance / release-narrative drift (roadmap vs shipped tags) | — | ✅ Resolved by Epic A.1 + Epic A.2 | Epic A |
| DI-1 | Deterministic-artifact deploy: a same-length file change was silently skipped by rsync's size+mtime quick-check → runtime kept the old version, auto-rolled-back | High (P0) | ✅ Resolved — content-based transfer (`rsync --checksum`, `8a10e4a`); regression tests + Pi validated | ADR-0003 / v0.3.14 |

## 6. Deferred Work

| Item | Reason / revisit trigger | Evidence |
|---|---|---|
| Deep Persian rewrite | Only if Epic D's light formatting pass proves insufficient | structural parity already strong (17/17 headings) |
| **Documentation Normalization** | Normalize existing documentation to the RTL/LTR Authoring Style Guide v1.0. Scope: identifier backticks · command fencing · `.tech-list` tagging · EN/FA parity review. **Status: Deferred** (do chapter-by-chapter with a read-only audit first). | Style Guide v1.0 in `CONTRIBUTING.md` |
| Parity Guard → CI integration | Guard is manual-only | `.github/workflows/ci.yml` has no parity step |
| `errors="replace"` in canonical guard | Mount-robustness; only the run-copy is patched | `scripts/check-screenshot-parity.py` |
| Roadmap §4 maintenance | `db-perms-600`, `root-crontab-cleanup` (ops/security); `pairing-neutralise` → v0.4 candidate | roadmap §4 |
| D7 — health.py OpenAPI example `0.1.0` | Cosmetic; runtime returns correct APP_VERSION; excluded from v0.3.1 | `backend/api/health.py` |
| Advanced log management (postponed) | Explicitly postponed after the minimal log feature (`a6b6bd4`): journald drop-ins (system-wide; out of scope for CCC), dashboard cleanup action, scheduled cleanup timer, installer retention prompt, and broader advanced log management. **Revisit only** if SD-card pressure shows the minimal logrotate + temp-cleanup is insufficient. | roadmap § *v0.4 Candidates* |

## 7. Documentation Status

| Area | State |
|---|---|
| English Guide | ✅ Complete (17 chapters) |
| Persian Guide | ✅ Complete (text; 17 chapters; heading parity 17/17) |
| Diagrams | 19 on disk; **integrated EN + FA (DGM-01–19)**; full EN↔FA diagram parity |
| Screenshots | 21 integrated EN+FA; 0 placeholders |
| Parity Guard | ✅ PASS (Existence · EN↔FA parity · Orphans · Hygiene) |
| Docs Website (MkDocs) | ✅ Phase 1 complete — Material, Persian RTL, self-hosted fonts, bilingual landing page |
| Authoring Style Guide | ✅ RTL/LTR v1.0 (`CONTRIBUTING.md`); existing chapters not yet normalized → Deferred (§6) |
| Hardware validation | ✅ v0.3.1 validated on Raspberry Pi 4 (2 GB) and Raspberry Pi 3 Model B (1 GB) — see `docs/health-checks/v0.3.1-hardware-validation.md` |

## 8. Release Timeline

| Version | Date | Note |
|---|---|---|
| v0.1.0 | 2026-06 | MVP |
| v0.1.1 | 2026-06-11 | Maintenance |
| v0.2.0 | 2026-06-17 | Smart Conduit Control |
| v0.3.0 | 2026-06-21 | First public release (Personal Mode, Ryve Claim, Backup & Restore) |
| docs-v0.3 | 2026-06-22 | Documentation milestone (not a product release) |
| v0.3.1 | released (2026-06-24) | Hotfixes (D1 + D2) |
| v0.3.2 | 2026-06-28 | HTTPS port selection (Feature 1) + one-click CCC update (Feature 2); Pi 4 + Pi 3 B validated |
| v0.3.3 | 2026-06-28 | Validation release — exercised the one-click path; exposed the `/run/lock` EROFS in `ccc-update-apply` |
| v0.3.4 | 2026-06-28 | One-click update lock-path fix (EROFS): lock → `/var/lib/conduit-cc/.update.lock` (+ `O_NOFOLLOW`) |
| v0.3.5 | 2026-06-28 | Storage Protection (Log Management / SD-Card Protection, `a6b6bd4`); also the first end-to-end One-Click Update validation milestone (a 422 install bug surfaced, fixed in v0.3.6) |
| v0.3.6 | 2026-06-28 | One-Click Update install fix — `Content-Type: application/json` on `POST /api/update/install` (was HTTP 422); readable validation errors |
| v0.3.7 | 2026-06-29 | Validation release for the full One-Click Update pipeline; CCC Logo System v1.0 branding (known issue: non-interactive prompt rollback, fixed in v0.3.8) |
| v0.3.8 | 2026-06-29 | One-Click Update non-interactive fix — `update.sh` skips the confirmation prompt for `--ccc-only` / non-TTY runs |
| v0.3.9 | 2026-06-29 | Validation release — Trusted Update Engine end-to-end on Raspberry Pi (v0.3.8 → v0.3.9) |
| v0.3.10 | 2026-07-01 | Validation release — Update Engine Test & CI Hardening; deploy `rsync --exclude '/bin/'` fix (v0.3.9 → v0.3.10) |
| v0.3.11 | 2026-07-02 | One-Click Update **production-proven** — first fully successful dashboard update on Raspberry Pi via the B1 transient-unit engine (v0.3.10 → v0.3.11) |
| v0.3.12 | 2026-07-02 | Frontend polish + final One-Click Update validation (v0.3.11 → v0.3.12); One-Click Update now Completed / Production-Proven / Maintenance Only |
| v0.3.13 | 2026-07-04 | ADR-0003 signed-release groundwork (publisher signing, on-device fail-closed verification) |
| v0.3.14 | 2026-07-07 | ADR-0003 Phase-B audit trail + deploy-integrity fix (`rsync --checksum`); Pi validated |

## 9. Next Recommended Action

**Between implementation phases.** v0.3.14 (Trusted Update Signing, Phase-B) is released. One accepted backlog item awaits scheduling — **Owner Operations Toolkit — Capability 1: Signed-Release Toolkit** (`docs/BACKLOG-REGISTER.md`, BL-0001, P2; implementation postponed). No new implementation phase is open. Candidate drivers are defined in the reconciled roadmap (`docs/roadmap/CCC_Product_Roadmap_v1.md`, *Next Phase / Candidate Drivers*). Being between implementation phases does not mean the project is inactive: architecture, documentation, ADR, research, discovery, and planning work may continue; only new-feature implementation requires an approved driver and a Value Gate.

## 10. Deployment Strategy

**Status: One-Click Update is delivered and production-proven (v0.3.12).** The one-time *validation milestone* (adopted/corrected 2026-06-28) is now **historical** — validation was completed on Raspberry Pi (v0.3.10 → v0.3.11 and v0.3.11 → v0.3.12; see `docs/closure/one-click-update-closure.md`).

- **One-Click Update** (dashboard Software Updates → `ccc-update-apply` → `update.sh --ccc-only`) is the delivered, production-proven update mechanism for end users.
- **Manual SSH `update.sh`** is retained for initial install, disaster recovery, and emergency maintenance.
- Future One-Click Update work is **Maintenance Only** (bug fixes, security hardening, maintenance); functional expansion requires a new ADR.

---

> **Maintenance:** update this file at every epic open/close. On **open**, move the epic
> from Approved Next to Active; on **close**, move it to Closed Epics (with evidence),
> flip the related Known-Issue rows to Resolved, and update Next Recommended Action. On
> every release/tag, append a row to the Release Timeline (append-only). Wire an "Update
> PROJECT-STATUS.md" step into `docs/release-checklist.md` so it cannot drift. Record
> state and pointers only — feature specs live in the roadmap, shipped detail in the
> CHANGELOG, decision rationale in closure records. Owner: project owner; changes
> proposed per epic.
