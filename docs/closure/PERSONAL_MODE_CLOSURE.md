# Closure Record — Personal Mode (C4 / C5 / C6)

**Epic:** Personal Mode (roadmap §8 v0.4.0 — Personal Mode & Ryve; §3.2 matrix A1–A4, D6).
**Status:** ✅ CLOSED — DELIVERED and production-validated
**Closure date:** 2026-06-19
**Process:** Backend C4→C5→C6a→C6b→C6c → Frontend Slice 1→2→3→4 (gated design reviews) → C6e Raspberry Pi validation → EROFS production fix → Closure

## 1. Objective
Let an operator run a *personal* Conduit identity for trusted friends/family: create a personal compartment, share its pairing token (as text and QR), and enable / adjust / disable a personal-client limit — entirely from Settings, while preserving CCC's aggregate-only, security-first posture. The pairing token must never be logged, stored, persisted, placed in a URL, or written to web storage or cookies; the Conduit private key is never touched.

## 2. Scope delivered

**Backend**
- **C4 — Helper** (`ccc-personal-compartment`): runs as the `conduit` user (sudo), flock-serialised, single-depth `.bak`, divergence self-check; subcommands `create` / `status` / `restore-bak` / `show-token`. Never opens the private key; emits only the displayable pairing **token** (never a raw compartment ID).
- **C5 — Adapter** (`backend/conduit/personal.py`): async bridge from the API to the helper.
- **C6a — API** (`backend/api/personal.py`, mounted at `/api/conduit`): `GET /personal/status`, `POST /personal/compartment` (create), `GET /personal/token` (no-store), `PUT /personal/max-clients`, plus the C6c endpoints.
- **C6b — Max personal clients:** apply via the M2 path (restart → health-as-truth verify → rollback); symmetric full-set merge preserving common-clients / bandwidth / reduced-window; hardened to require the effective personal metric match when present.
- **C6c — Regenerate / Restore endpoints:** token returned only on a healthy, non-rolled-back regenerate; compartment `.bak` rollback domain distinct from the M2 drop-in rollback. (Backend only — see §3.)

**Frontend (Settings → Personal mode card)**
- **Slice 1 — Status card:** three-state badge (Not set up / Created — inactive / Active · N personal clients), read-only display name, personal-capacity display; refresh-on-view, `401 → /login`.
- **Slice 2 — Create identity:** display-name input (1–32), `POST …/compartment` with CSRF; full error matrix (401/403/409/422/503/network); the create response token is deliberately **not** read here.
- **Slice 3 — Token + QR:** explicit View / share panel fetching `GET …/token`; token rendered with `textContent`; client-side QR via the **vendored Nayuki `qrcodegen` v1.8.0** (MIT, SHA-pinned, same-origin, CSP-safe, theme-independent dark-on-light); token + QR cleared from the DOM on close and on navigation.
- **Slice 4 — Max personal clients apply:** one control enables (0→N) / adjusts (N→M) / disables (N→0); **mandatory confirm-restart** before every effective change; results routed on `body.status` (no-op / applied / rolled_back / rollback_failed); 0 disables Personal Mode while **keeping the identity**.

## 3. Deferred functionality
- **Slice 5 — Regenerate / Restore UI:** intentionally deferred. The C6c **backend endpoints exist and are retained**; only the UI is deferred. Rationale: risk > value for normal operation; regenerate is destructive (invalidates shared tokens) and is reachable via the API when genuinely needed.
- **Live connected personal-client count:** NO-GO. Conduit exposes only an aggregate `connected` / `connecting` count with no per-compartment (personal vs common) runtime breakdown; `conduit_max_personal_clients` is a configured *limit*, not a live count. A personal-vs-common usage display would require an upstream Conduit / psiphon-tunnel-core metric and is recorded as an upstream feature request (roadmap §8 "Scope filter").

## 4. Implementation summary
Backend C4–C6c were merged ahead of the frontend series (CI green through #133 at C6c). The frontend shipped as four reviewed slices, each behind a design-review gate, with static "presence/wiring" guard tests in `tests/unit/test_frontend_personal_wiring.py` and the vendored-library integrity guard in `tests/unit/test_vendor_qrcodegen.py` (SHA-256 pin + CSP-safety scan). The QR library is loaded as a classic same-origin script (`script-src 'self'`); CCC draws the QR onto its own `<canvas>` (no DOM injection, no `eval`/`Function`/`document.write`). The strict CSP was not changed at any point.

## 5. Commit references & CI
| Item | Commit | CI |
|---|---|---|
| Backend C4–C6c (helper/adapter/API/max-clients/regenerate-restore) | merged ahead of frontend; C6c anchor `1ded16d` | #133 GREEN |
| Slice 1 — read-only status scaffold | `b900d70` | GREEN |
| Slice 2 — create flow | `6990f1f` | #135 GREEN |
| Slice 3 — token + QR (vendored qrcodegen) | `ac50df7` | #136 GREEN |
| Slice 4 — max-personal-clients apply | `193a793` | GREEN |
| C6e — EROFS production fix (unit hardening) | `39ba3eb` | GREEN |

## 6. Production validation summary (C6e — Raspberry Pi 4, Ubuntu 22.04 ARM64, Conduit 2.0.0, Nginx + Cloudflare, conduit.rockysystem.net)
- **Deployment — PASS:** `personal.js` and `qrcodegen.js` deployed; vendored SHA verified; dashboard updated.
- **Functional — PASS:** status, create identity, token retrieval, QR generation, enable, disable, and max-personal-clients apply all verified working in the browser.
- **Security controls — enforced by CI guards (GREEN) and CSP-by-construction:** token endpoint sets `Cache-Control: no-store`; the module carries no `localStorage` / `sessionStorage` / cookie-write / `console` of the token; CSP string unchanged (`test_csp_unchanged`); vendored QR same-origin + SHA-pinned. (A dedicated manual production DevTools sweep is recommended as a periodic spot-check — see §8.)
- **Post-fix — PASS:** after `39ba3eb`, `GET /personal/status` → 200, `GET /personal/token` → 200, no EROFS in the journal, Personal Mode fully functional.

## 7. Production bug and fix
**Bug:** `GET /personal/status` returned 503; journal showed `OSError: [Errno 30] Read-only file system: '/var/lib/conduit/data/.ccc-personal-compartment.lock'`.
**Root cause:** the helper is launched by the CCC backend via `sudo -u conduit` and therefore inherits `conduit-cc.service`'s mount namespace, where `ProtectSystem=strict` remounts the filesystem read-only except for the listed `ReadWritePaths`. `/var/lib/conduit/data` was not granted, so the helper's flock creation hit `EROFS` (a read-only *mount*, not a permission denial — interactive `sudo -u conduit touch` succeeds in the host namespace).
**Fix (`39ba3eb`, `deployment/conduit-cc.service`):** `ReadWritePaths=/var/lib/conduit/data` (the narrow data dir, never the broad `/var/lib/conduit`); `ReadOnlyPaths=/var/lib/conduit/data/conduit_key.json` to keep the private key mount-protected as defense-in-depth; `After=…conduit.service` ordering (so the data dir exists at start) **without** a `Wants=conduit.service` pull-in (CCC must not auto-start the Conduit node); guard test `test_unit_has_only_narrow_readwritepaths` updated. `ProtectSystem=strict` and all other hardening retained.

## 8. Risks
- **Boot-time coupling (operational):** with `After=` ordering plus `ReadWritePaths=/var/lib/conduit/data`, if `conduit.service` is not running so the data dir is absent, `conduit-cc` can fail to start (missing `ReadWritePaths` target). This is the deliberate trade for not auto-starting Conduit; documented here so a future operator isn't surprised.
- **No UI token rotation until Slice 5:** a compromised pairing token can only be invalidated via the API/CLI (regenerate) until the deferred UI ships. Acceptable given the deferral; recorded as an operational note.
- **Security-evidence basis:** the token-handling controls are enforced by automated CI guards and CSP-by-construction rather than a separately archived manual production sweep. Recommend a periodic manual DevTools spot-check (no-store on the token response; no token in URL / `localStorage` / `sessionStorage` / cookies; no CSP console errors) as routine hygiene.
- **Upstream metric dependency:** the personal-vs-common connected count is not available; no CCC action until Conduit exposes it upstream.

## 9. Lessons learned
- **Systemd sandboxing crosses `sudo`.** A helper invoked via `sudo` stays inside the calling service's mount namespace; `ProtectSystem=strict` therefore governs it. `EROFS` vs `EACCES` was the decisive signal that separated this from an ownership problem — read the errno before theorising about permissions.
- **Grant the narrowest mount exception and carve the secret back out.** Widening to the data dir was necessary (the compartment file can't move), but re-imposing read-only on the private key preserved defense-in-depth.
- **Vendoring beats CDN under strict CSP.** A SHA-pinned, same-origin, canvas-rendering, dependency-free library satisfied `script-src 'self'` with no CSP change and a tamper-evident integrity test — and the official precompiled JS lived in the GitHub *release assets*, not the source tree.
- **Verify Conduit capability before promising a feature.** The "connected personal clients" display was correctly stopped at review: the runtime telemetry is aggregate-only, so it was marked upstream-dependent rather than faked from a config limit.
- **Small reviewed slices held up in production.** Each slice was independently reviewable and reversible; the only production defect was an environmental (systemd) one, not a feature regression.

## 10. Final decision
**CLOSED / PASS.** Personal Mode (C4 / C5 / C6a–c backend + Frontend Slices 1–4) is delivered, CI-green, and production-validated on the Raspberry Pi, with the single production defect (EROFS under `ProtectSystem=strict`) fixed in `39ba3eb` and revalidated. Regenerate/Restore UI (Slice 5) is deferred with backend support retained; the live personal-client count is an upstream-dependent future item. The epic is moved to completed work.
