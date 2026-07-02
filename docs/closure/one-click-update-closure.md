# Closure Record — One-Click Update (Production Validation)

**Status:** Closed — production-proven · **Maintenance Only**
**Date:** 2026-07-02
**Feature:** Trusted Update Engine — dashboard-driven One-Click Update (Feature 2)
**Validated transitions:** v0.3.10 → v0.3.11 (engine) and v0.3.11 → v0.3.12 (frontend polish) on a live Raspberry Pi
**Related:** ADR-0001 (Trusted Update Engine); tags `v0.3.2`…`v0.3.12`

## Summary

The dashboard-driven One-Click Update was validated end-to-end on a live
Raspberry Pi, updating the device from **v0.3.10 to v0.3.11** via the B1
transient-unit execution model. A subsequent **v0.3.11 → v0.3.12** One-Click
Update on the same device validated the released frontend polish end-to-end. This
closes the One-Click Update feature as **production-proven** on the target
platform; the feature now moves to **Maintenance Only** (see *Status: Maintenance
Only* below).

## What was validated

The full production path on real hardware: dashboard → `/api/update/install` →
`ccc-update-apply` → `systemd-run` transient unit (host namespace) →
`update.sh --ccc-only --non-interactive` → backup → deploy → restart → terminal
status.

## Evidence (device)

- Device updated **v0.3.10 → v0.3.11**.
- `update-status.json`: `state = success`, `from_version = 0.3.10`, `to_version = 0.3.11`.
- `/api/health`: HTTP 200, version `0.3.11`.
- Services active (`conduit-cc`, `conduit`, `nginx`).
- `/opt/conduit-cc/bin` preserved with 6 helpers.
- No `cannot delete non-empty directory: bin` error.
- `ProtectSystem` remains **strict**.

## Final frontend-polish validation (v0.3.11 → v0.3.12)

The frontend polish release (**v0.3.12**) was validated by a real dashboard
One-Click Update on the same Raspberry Pi. This is the **final validation** for
the One-Click Update feature.

- Device updated **v0.3.11 → v0.3.12** via the dashboard One-Click Update.
- `update-status.json`: `state = success`.
- `APP_VERSION = 0.3.12`; `/api/health`: HTTP 200 (`ok`), version `0.3.12`.
- No `cannot delete non-empty directory: bin` error.
- Frontend polish confirmed in the released build: the **Install Update** button
  hides when the system is already up to date, stale update progress/rollback
  messages no longer survive a page reload, and restore success surfaces as a
  transient Toast rather than a persistent banner.

## What this confirms

- The B1 transient-unit namespace escape works end-to-end (resolving the pre-B1
  Phase-1 EROFS failure mode).
- The Phase-3 deploy `rsync --exclude '/bin/'` fix holds under its only
  triggering condition — the worker running from `/opt/conduit-cc/bin` during
  `rsync --delete` — a condition unit tests cannot reproduce.
- Status reconciliation lands a correct terminal `success` (no stale `in_progress`).
- Service hardening (`ProtectSystem=strict`) is preserved across a privileged update.
- Helper re-provisioning (step 3b2) is intact.
- Deployment-drift recovery works: an out-of-band manual update restored the B1
  engine, after which the dashboard one-click completed cleanly.

## Status: Maintenance Only

The One-Click Update feature is **complete and production-proven**. It now moves to
**Maintenance Only**: no further feature development is planned. Future work on
this feature is limited to:

- **bug fixes**,
- **security hardening**,
- **routine maintenance** (dependency and compatibility upkeep).

Artifact integrity / signing (ADR-0001 invariant 5) remains a **deferred
hardening item** (see *Scope / not covered* below). It is **not** part of this
closure and does not gate it; if undertaken later it falls under the
security-hardening maintenance category above.

## Architectural stability

The Trusted Update Engine and the One-Click Update subsystem are now considered
architecturally stable and complete. Future evolution must preserve the
established Trusted Update Engine architecture. Any functional expansion requires
an explicit ADR and shall be treated as a new architectural evolution rather than
an incremental extension of this completed subsystem.

## Scope / not covered

- **Artifact integrity / signing** (ADR-0001 invariant 5) remains deferred —
  integrity rests on TLS + host allow-list + gzip-magic + structural +
  version-increase checks; there is no cryptographic signature. Principal
  remaining production-hardening item.
- The success run did **not** exercise the rollback path (rollback is
  unit-tested and was observed during the earlier failed `0.3.9 → 0.3.10` attempt).
- `--ccc-only` path only; the Conduit Core update path is out of scope.
- The capability subsystem (ADR-0002, Batches A–D) is unwired and is not part of
  this feature.

## Deployment-drift lesson

A device running a version that predates the B1 engine cannot self-validate the
One-Click path; it must first be recovered to the B1 engine out-of-band. This
validation therefore used **v0.3.10 (B1) → v0.3.11**.

## References

- ADR-0001 — Trusted Update Engine.
- `CHANGELOG.md` entries `[0.3.12]`, `[0.3.11]`, `[0.3.10]`, `[0.3.9]`.
- `backend/api/update.py`, `deployment/bin/ccc-update-apply`, `update.sh`.
