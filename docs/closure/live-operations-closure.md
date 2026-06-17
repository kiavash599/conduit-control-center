# Closure Record — Live Operations (Node Status broker badge + live signals)

**Epic:** Live Operations Panel (roadmap §6.2 + §6.6), delivered as **Option 1**
(extend the existing Node Status card; no standalone panel).
**Status:** ✅ CLOSED — DELIVERED and production-validated
**Closure date:** 2026-06-17
**Process:** Audit → Design Gate → Commit 1 (adapter) → Commit 2 (API) → Commit 3 (frontend) → Pi validation → Closure

## 1. Objective
Surface the four genuinely-missing live signals — the four-state broker badge,
connecting clients, idle time, and build revision — without duplicating values
already shown by the Advisor, Traffic, and Lifetime cards. Net-new operator
value: the broker badge that §6.2 calls "the topmost status signal."

## 2. Scope delivered
- **Four-state broker badge** in Node Status: Live / Starting / Disconnected /
  Not running, plus an honest **Unknown** fallback when metrics are unreadable.
- **Connecting clients** row (`conduit_connecting_clients`).
- **Idle** row (`conduit_idle_seconds`; "Active" at 0).
- **Build revision** appended to the version line (`build_rev` -> "2.0.0 · <rev>").
- Read-only, aggregate-only, no privilege/write path. Reuses the existing
  5-second `/api/status` poll and existing badge / status-meta CSS.

**Deliberately excluded (no duplication):** connected clients (Advisor), bytes
(Traffic/Lifetime), service uptime + version (Node Status). **`conduit_uptime_seconds`
intentionally deferred** — Node Status shows service uptime; a second
conduit-runtime uptime would confuse operators.

## 3. Implementation summary
- **Commit 1 (adapter):** `LiveStatus` model; parsers for
  `conduit_connecting_clients` + `conduit_announcing`; pure `broker_state()`
  state machine; forgiving `get_live_status()` (None on unreachable; per-field
  None on miss; unlabelled scalars only).
- **Commit 2 (API):** `GET /api/status` gains a nested `live` block
  `{broker_state, connecting_clients, idle_seconds, build_rev}`, computed
  server-side. `get_live_status()` is a **non-fatal** call (return_exceptions
  gather): a metrics failure never changes the HTTP code and never nulls
  `node_status` / `conduit_version` / `uptime_seconds`; `broker_state` degrades
  to unknown / not_running.
- **Commit 3 (frontend):** Node Status markup gains the Broker badge +
  Connecting/Idle rows + a build_rev span; `status.js` renders `data.live`
  (badge map reusing existing classes), degrading the badge to Unknown on poll
  failure. `textContent`/`createElement` only — no `innerHTML`; "Clients", never
  "Users". No new card, no new endpoint, no new poller.

## 4. Commit references
| Commit | Description |
|---|---|
| `3741b71` | Commit 1 — broker state + live-status reader (adapter) |
| `d61a478` | Commit 2 — expose live block on GET /api/status |
| `b4bc9c1` | Commit 3 — Node Status broker badge + connecting/idle/rev |

CI: **#115 GREEN.**

## 5. Validation evidence (production — PASS)
- Services active; `/api/status` `live` block present with all four fields.
- `build_rev` matches `conduit_build_info`.
- `connecting_clients` and `idle_seconds` exposed and plausible vs the metrics endpoint.
- `broker_state` exposed; Broker badge renders correctly.
- Restart transition validated: **Disconnected -> Live** observed.
- No dashboard duplication; Advisor / Traffic / Lifetime unaffected.

## 6. Accepted deviations
- **"Starting" not observed** during the restart transition. The four-state logic
  is implemented and unit-tested; the live "Starting" window (`announcing==1`,
  `is_live==0`) was shorter than the 5-second poll interval, so the observed path
  was Disconnected -> Live. **Not a defect** — the state is reachable and tested;
  a sub-poll-interval transition is expected to be skipped by a 5 s poller.
- **`conduit_uptime_seconds` deferred** (design decision, documented above).
- **Build info partial** — only `build_rev` is shown; the full
  `conduit_build_info` label set (repo / go_version / values_rev) is future.

## 7. Final status
**CLOSED as DELIVERED.** Live Operations (§6.2/§6.6) is delivered via the Node
Status extension, CI-green, and production-validated, with a read-only,
aggregate-only posture and no duplication of existing cards.
