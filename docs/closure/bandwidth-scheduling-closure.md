# Closure Record — Bandwidth Scheduling (Reduced Mode)

**Epic:** Bandwidth Scheduling (roadmap §6.5)
**Status:** ✅ CLOSED — DELIVERED and production-validated
**Closure date:** 2026-06-16
**Process:** Evidence → Capability Verification → Design → Implementation → Migration → Pi Validation → Closure

---

## 1. Scope delivered

An operator-configurable **daily reduced-mode window** for Conduit, set through the
existing Settings → Conduit Configuration card. During the window, Conduit applies
a lower common-client cap and a lower per-client bandwidth limit; outside it, the
normal limits apply.

- **Configurable values:** enable/disable; **Start** and **End** time (`HH:MM`,
  24-hour, **UTC**); **Reduced max common clients**; **Reduced bandwidth (Mbps)**.
- **Normal mode** (unchanged): max common clients, global bandwidth.
- **Aggregate-only / configured-only:** no per-client data; the reduced window is
  reported as *configured* values (no invented runtime/effective figure — see §9).

## 2. Architecture summary

CCC **configures** the schedule; **tunnel-core runs it**. There is **no CCC
scheduler** — no cron, no APScheduler, no systemd timers, and no boundary
restarts. A restart occurs **only once, when the schedule values are changed**.

```
UI (Settings card)
  → POST /api/conduit/config/{validate,apply}   (HH:MM accepted; parsed to integer minutes)
    → adapter.apply_conduit_config(... integers only ...)
      → sudo /opt/conduit-cc/bin/ccc-apply-conduit-config apply --reduced-* (integers)
        → writes Environment-only drop-in (helper formats HH:MM from validated ints)
          → systemd ExecStart passes --set InproxyReduced*=${CCC_REDUCED_*}
            → psiphon-tunnel-core evaluates the UTC window at RUNTIME
               (automatic normal⇄reduced transition; no restart at boundaries;
                existing clients grandfathered)
```

- **Time format:** `InproxyReducedStartTime` / `InproxyReducedEndTime` are `HH:MM`,
  24-hour, **UTC** (verified in tunnel-core `psiphon/config.go`).
- **Integer-only privilege boundary:** the API accepts `HH:MM`, converts to integer
  minutes, and passes **only integers** to the adapter/helper; the helper formats
  the `HH:MM` string itself — no untrusted string is ever written to the unit.
- **Full-state apply:** the drop-in is monolithic, so every apply writes the
  complete normal+reduced state; omitting the window in a request preserves the
  current one.

## 3. Commits

| Commit | Description |
|---|---|
| `68d6f97` | feat(scheduling): add reduced-mode write surface (helper + unit) |
| `600606f` | feat(scheduling): wire reduced-mode backend config (validation/models/adapter/API) |
| `07198b4` | fix(deploy): add reduced-mode migration safety (update.sh guard + deferred start; install.sh guard) |
| `ce75817` | feat(scheduling): add reduced-mode config markup (BS3.1) |
| `f838ff4` | feat(scheduling): wire reduced-mode config UI (BS3.2) — final deployed commit |

Preceded by the research spikes **B0** (capability), **B0.1** (runtime behaviour),
the Roadmap Impact Analysis, the Design Gate, and **BS0** (design freeze + threat
model).

## 4. CI evidence

| CI run | Commit | Result |
|---|---|---|
| #109 | `68d6f97` | GREEN |
| #110 | `600606f` | GREEN |
| #111 | `07198b4` | GREEN |
| #112 | `ce75817` | GREEN |
| #113 | `f838ff4` | GREEN |

## 5. Pi validation evidence

**Environment:** Raspberry Pi 4, Ubuntu 22.04, Conduit 2.0.0, CCC at `/opt/conduit-cc`
(source at `~/conduit-control-center`), deployed commit `f838ff4`. **Result: PASS.**

- `update.sh` deployment — PASS.
- BS1 reduced-mode artifact guard — PASS.
- Helper supports the reduced arguments — PASS.
- `conduit.service` contains the 5 `CCC_REDUCED_*` defaults — PASS.
- `conduit.service` contains the 5 `InproxyReduced*` `--set` tokens — PASS.
- API unauthenticated → `401` — PASS.
- Authenticated `GET /api/conduit/config` returns the `reduced` block — PASS.
- UI: reduced "Off" state, edit form, **UTC→local preview**, confirm dialog with the
  required *"evaluated automatically … no restart at the configured start or end
  time"* sentence, apply flow — PASS.
- Conduit restart + health after apply — PASS.
- Runtime systemd environment correctly applied (window 14:00–22:00 UTC, max 7,
  up=down 1,250,000 B/s = 10 Mbps):
  `CCC_REDUCED_START=14:00`, `CCC_REDUCED_END=22:00`, `CCC_REDUCED_MAXCOMMON=7`,
  `CCC_REDUCED_UP=1250000`, `CCC_REDUCED_DOWN=1250000` — PASS.
- Disable path (empty `CCC_REDUCED_START=` + zeros; Conduit restarts healthy) —
  PASS (operator-reported).

This empirically closed the two residuals that source analysis alone left open: the
**real Conduit binary accepts the reduced `--set` values** (enable + disable, no
config error), and the **empty-string disable path works** on the device.

## 6. Security model

- **M2 boundary preserved intact.** The root helper remains argv-only (no shell),
  hardcoded path/unit, Environment-only writes, atomic write + `O_NOFOLLOW` + flock
  + `.bak`, independent re-validation. The sudoers grant is command-level (new args
  need no change).
- **No untrusted string reaches the privilege layer or the unit.** Times cross the
  API→adapter→helper boundary as **integers**; the helper synthesises the `HH:MM`
  string itself from validated minutes (`^\d\d:\d\d$` by construction).
- **No new privileged surface beyond five validated integers.** The five static
  `--set` tokens live in the shipped unit (root-controlled at install/update), not
  in helper output.
- **Validation parity** across frontend, backend (`config_validation.py`), and the
  helper, enforced by a parity test.
- **Health-as-truth + rollback** unchanged: a value that prevents Conduit starting
  triggers automatic rollback to the previous drop-in.
- **Migration safety:** `update.sh` installs the reduced-capable helper + unit,
  **guards** them, and defers the `conduit-cc` start until after the guard, so the
  new backend never serves against an old helper.

## 7. Aggregate-only / privacy statement

Bandwidth Scheduling introduces **no per-client, per-session, IP, or identity data**.
It reads and writes only aggregate configuration knobs (a time window and two
numeric limits). The reduced window is **configured-only** and never derives or
exposes runtime client identity. CCC's aggregate-only posture is unchanged.

## 8. Known limitations

- **Reduced mode is configured-only.** No Prometheus metric exposes the active
  reduced window or the runtime-active limit (`conduit_max_common_clients` is the
  static startup gauge), so the UI shows configured values, not an effective figure.
- **Local-time preview uses the browser's current UTC offset (today).** Across a DST
  boundary the displayed local time shifts by an hour; tunnel-core always evaluates
  the window in **UTC**, so the schedule itself is unaffected (display-only caveat).
- **Single daily window; reduced bandwidth is a single Mbps value** (written to both
  upstream and downstream). Separate up/down reduced limits and multiple windows are
  out of scope (native support exists for separate limits; deferred).
- **`conduit-monitor` (100 GB / 7-day quota throttle) is a separate mechanism** and
  is **not** part of CCC's systemd deployment; it is unrelated to `InproxyReduced*`.

## 9. Deferred items (non-blocking follow-ups)

- **Long-duration boundary observation.** The automatic normal⇄reduced transition at
  the *actual* configured start/end was not separately observed during the session.
  Recorded as a **non-blocking follow-up**, not a closure blocker, because: tunnel-core
  source verification (B0.1) confirmed runtime scheduling; the real Conduit binary
  accepted the configuration; Conduit restarted healthy; the runtime environment was
  correctly applied; and the UI/API/helper/systemd path was validated end-to-end.
  (A clean future check: confirm `ActiveEnterTimestamp` is unchanged across a window
  boundary — proves "no boundary restart" without synthetic traffic.)
- **ACTIVE / INACTIVE status badge** — deferred (feasible client-side; risks
  misleading vs tunnel-core truth; clearly out of MVP scope).
- **Advisor "Use recommendation" pre-fill button** — deferred (the Advisor already
  *displays* its quiet-window recommendation; a pre-fill button is a future commit).
- **Advisor copy fix** — the Advisor's "bounded by Conduit's 100 GB / 7-day minimum"
  line conflates `conduit-monitor` with `InproxyReduced*`; correct in a separate
  small commit.

## 10. Final verdict

**GO — Bandwidth Scheduling is formally CLOSED as DELIVERED.**

The capability is source-confirmed (B0/B0.1), implemented across the helper, unit,
backend, migration, and UI, CI-green on all five commits, and production-validated
on the target Raspberry Pi with the M2 security model preserved and the
aggregate-only posture intact. The single known gap — direct observation of a live
boundary transition — is a non-blocking follow-up, with the runtime behaviour already
proven at the source level and the configuration proven accepted by the real binary.

---

## 11. Documentation updated at closure

- `docs/roadmap/CCC_Product_Roadmap_v1.md` — §6.5 marked DELIVERED; §3.2 C6/C7
  delivered + C8 de-conflated; §5.7 day-of-week selector removed and the 100 GB/7-day
  tooltip corrected; §6 status updated; revision history → 1.6.
- `CHANGELOG.md` — `Added — Bandwidth Scheduling` under `[Unreleased]`.
- `docs/closure/bandwidth-scheduling-closure.md` — this record.
