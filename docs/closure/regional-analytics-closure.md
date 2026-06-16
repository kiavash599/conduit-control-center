# Closure Record — Regional Analytics (RA)

**Epic:** Regional Analytics (roadmap §6.3)
**Status:** ✅ CLOSED — delivered (MVP) and production-validated
**Closure date:** 2026-06-16
**Process:** Evidence → Analysis → Decision

---

## 1. Scope delivered

An aggregate-only "Regions" card on the Dashboard showing the top active
contribution regions, backed by a read-only API.

**Backend** — `GET /api/conduit/regions` (commit `6f96978`, CI #106 green)

- Reads the labelled Prometheus series `conduit_region_bytes_uploaded`,
  `conduit_region_bytes_downloaded`, and `conduit_region_connected_clients`
  at `{scope="common", region=...}`.
- Per region: `traffic_bytes = uploaded + downloaded`;
  `clients = connected_clients`.
- Excludes regions with zero traffic **and** zero clients. Sorts by
  `traffic_bytes` DESC (region code as a stable tiebreak). Caps to the top 10.
- Response shape is aggregate-only: `{region, traffic_bytes, clients}` per row.
  No IP, session, or per-client fields. Degrades to an empty list on any
  metrics error (never 5xx).

**Frontend** — Regions dashboard card (commit `a169089`, CI #107 green)

- Columns: **No. · Country (flag + name) · Traffic · Clients**.
- Traffic rendered in binary units (KiB/MiB/GiB); country name via the
  browser-native `Intl.DisplayNames` (canonical ISO 3166-1 region mapping),
  falling back to the raw ISO code when unavailable.
- Rendering uses `createElement` / `textContent` only (no `innerHTML`).
- Dashboard-aware polling at 60s (paused when the Dashboard section is hidden;
  immediate refresh on navigation into the Dashboard); registered for logout
  teardown.
- Loading / empty / error / populated states. Mobile responsive via a
  horizontal-scroll table.

**Tests**

- Unit: region-series parser + `get_regions` aggregation/sort/top-10/zero-exclude/degrade.
- Integration: `GET /api/conduit/regions` auth, aggregate-only shape, order
  passthrough, empty case.
- Frontend guard: `tests/unit/test_regions_frontend_guard.py` — asserts the
  Regions markup/JS never contain the literal "Users", the markup uses
  "Clients", and `regions.js` contains no `innerHTML`.

---

## 2. Validation evidence

**CI**

- Backend `6f96978` — CI #106 GREEN.
- Frontend `a169089` — CI #107 GREEN.

**Production validation (Raspberry Pi 4, Ubuntu 22.04 ARM64)**

- Deployment of `main` completed successfully.
- `GET /api/conduit/regions` returns `401` when unauthenticated (expected).
- Conduit metrics confirmed present and labelled:
  `conduit_region_bytes_uploaded`, `conduit_region_bytes_downloaded`,
  `conduit_region_connected_clients`.
- Regions card renders correctly; top-10 table visible.
- Sort order (Traffic DESC) verified.
- Mobile layout validated.
- Privacy validation passed: aggregate-only policy respected; no IP, session,
  or per-client information exposed anywhere in the API response or the rendered
  page.

---

## 3. Delivered vs. original specification (reconciliation)

The MVP was intentionally scoped narrower than the original §6.3 / §5 design.
Differences are recorded so the roadmap and the shipped product agree.

| Aspect | Original spec | Delivered (MVP) | Disposition |
|---|---|---|---|
| Region count | Top 15 | Top 10 | Accepted MVP scope |
| Traffic columns | Separate Uploaded + Downloaded | Single combined **Traffic** | Accepted MVP scope |
| Client columns | Connected + Connecting | **Clients** = connected only | Connecting (D14) deferred |
| Scope | All / Common / Personal filter | `scope="common"` only | Scope filter (D17) deferred to v0.4.0 |
| Country cell | Flag + name + ISO in parens | Flag + name | Accepted deviation (cosmetic) |
| Mobile | Row-expand for hidden Traffic cols (§5.9) | Horizontal-scroll table | Accepted deviation |

**Deferred items remain tracked:** the scope filter is already listed under
§8 (v0.4.0 — Personal Mode); the connecting-clients column and separate
upstream/downstream columns are available metrics (matrix D14, D12/D13) for a
future enhancement and are not lost.

---

## 4. Risks

- **Low.** The feature is read-only and aggregate-only. The backend never
  writes, never invokes the root helper, and degrades to an empty list on any
  metrics error, so a metrics outage cannot 5xx the Dashboard.
- **Privacy surface (managed).** Regional Analytics is the one deliberate
  reader of *labelled* `{scope,region}` series (all other readers take only
  unlabelled scalar gauges). It is bounded to region-level aggregates by
  construction: the API serialises only `{region, traffic_bytes, clients}`, and
  the guard tests + integration tests lock the shape and terminology. No IP,
  session, or per-client data is read or exposed.
- **No new attack surface.** Auth-required endpoint; no new privileges, no new
  systemd or sudo paths.

---

## 5. Known limitations

- **Unicode flag rendering depends on platform font support.** Flags render on
  Android / mobile; some Windows desktop environments display the country-code
  letters instead of the emoji flag. Cosmetic only — the country name is always
  shown alongside. **Accepted; not considered a defect.**
- **`scope="common"` only.** Personal-scope regional data is not shown until the
  v0.4.0 personal-mode work introduces the scope filter.
- **Connecting clients and separate upload/download** are not displayed in the
  MVP (metrics are available for a future enhancement).

---

## 6. Final verdict

**GO — Regional Analytics is formally CLOSED.**

All MVP acceptance criteria are met and production-validated on the target
Raspberry Pi. CI is green on both the backend and frontend commits. Privacy and
aggregate-only constraints are satisfied and test-locked. The single known
limitation (flag-font support) is cosmetic and accepted. Deferred enhancements
(scope filter, connecting-clients/per-direction columns) are tracked in the
roadmap and are not blockers.

---

## 7. Documentation updated at closure

- `docs/roadmap/CCC_Product_Roadmap_v1.md` — §6.3 marked DELIVERED (MVP) with
  reconciliation; §3.2 matrix D12/D13/D15 → delivered, D14/D17 annotated; §6
  v0.2.0 status updated; revision history → 1.5.
- `CHANGELOG.md` — `Added — Regional Analytics` under `[Unreleased]`.
- `docs/closure/regional-analytics-closure.md` — this record.

No open issue-tracking references to Regional Analytics exist
(`docs/CCC_v0.1_GitHub_Issues.md` contains none), so none required updating.
