# Closure Record — One-Click Update (Production Validation)

**Status:** Closed — production-proven
**Date:** 2026-07-02
**Feature:** Trusted Update Engine — dashboard-driven One-Click Update (Feature 2)
**Validated transition:** v0.3.10 → v0.3.11 on a live Raspberry Pi
**Related:** ADR-0001 (Trusted Update Engine); tags `v0.3.2`…`v0.3.11`

## Summary

The dashboard-driven One-Click Update was validated end-to-end on a live
Raspberry Pi, updating the device from **v0.3.10 to v0.3.11** via the B1
transient-unit execution model. This closes the One-Click Update validation as
**production-proven** on the target platform.

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
- `CHANGELOG.md` entries `[0.3.11]`, `[0.3.10]`, `[0.3.9]`.
- `backend/api/update.py`, `deployment/bin/ccc-update-apply`, `update.sh`.
