# Closure Record — Theme Support (Light / Dark / System)

**Epic:** Theme Support (roadmap §5.10 design / §6.7 deliverable).
**Status:** ✅ CLOSED — DELIVERED and production-validated
**Closure date:** 2026-06-17
**Process:** Design Gate → TS1 (CSS palette) → TS2 (backend cookie + endpoint) → TS3 (Settings toggle) → TS4 (Raspberry Pi validation) → Closure

## 1. Objective
Give operators a Light / Dark / System theme choice that persists across reloads
and sessions, with a flash-free first paint and no client-side storage. The
preference is carried by a server-side cookie and rendered into the page on the
server, so the correct theme is present in the very first HTML response.

## 2. Scope delivered
- **Three themes** — Dark (default), Light, and System (follows the OS via
  `prefers-color-scheme`), driven entirely by the `[data-theme]` token blocks in
  `base.css`. Light is a WCAG-AA palette.
- **Flash-free first paint** — the active theme is server-rendered into the
  `<html data-theme="…">` attribute from a cookie; no localStorage, no
  on-load repaint.
- **Settings-only toggle** — a native radio group (Dark / Light / System) in a new
  Appearance card. Instant apply via `document.documentElement.dataset.theme`,
  then persisted with `POST /api/settings/theme`; the UI and dataset revert to the
  last saved value on failure.
- **Server-side persistence** — `theme` cookie (HttpOnly, Secure per settings,
  SameSite=Strict, Path=/, 1-year Max-Age); validated against
  `("light","dark","system")`, defaulting to dark and never raising.
- Applied across **Dashboard, Settings, and Login** pages (and the placeholder
  template). `textContent` / DOM-only; no `innerHTML`.

**Deliberately excluded:** no sidebar/header quick-toggle (Settings-only by
decision); no per-component palette overrides; no new colours beyond the
agreed Light/Dark/System token sets and the four shared tokens added in TS1
(`--color-on-accent`, `--color-spinner-track`, `--color-spinner-head`,
`--color-chart-down`).

## 3. Implementation summary
- **TS1 (CSS):** populated `[data-theme="light"]` (WCAG-AA), the shared
  `[data-theme="dark"], [data-theme="system"]` block, and the
  `@media (prefers-color-scheme: light)` override for System; tokenised five
  hard-coded colour leaks (primary/danger button text, spinner track/head,
  chart-down). Guard test `tests/unit/test_theme_css.py`.
- **TS2 (backend):** `THEME_COOKIE_NAME` / `VALID_THEMES` / `DEFAULT_THEME` plus
  `read_theme()` and `set_theme_cookie()` in `backend/auth/cookies.py`;
  `POST /api/settings/theme` (auth + CSRF; 422 on an invalid value, no cookie
  written); `read_theme(request)` injected into the login, login-error, and
  dashboard page contexts; `base.html` / `placeholder.html` render
  `data-theme="{{ theme | default('dark', true) }}"`. Tests
  `test_cookies_theme.py`, `test_api_settings_theme.py`.
- **TS3 (frontend):** the Appearance card (`#appearance-card` → `#theme-fieldset`
  radio group + `#theme-status` aria-live region) with the current theme
  server-rendered as `checked`; `settings.js` `wireThemeToggle()` /
  `setCheckedTheme()` / `setThemeStatus()` — instant apply, `apiFetch` persist,
  revert-on-failure. Layout-only `.theme-option` CSS class (no colours). Guard
  test `test_theme_toggle_markup.py`.

## 4. Commit references
| Commit | Description |
|---|---|
| `46547c0` | TS1 — Light/Dark/System CSS palette + tokenised colour leaks (CI #117) |
| `df49f42` | TS2 — theme cookie reader/validator + `POST /api/settings/theme` + template injection (CI #118) |
| _(TS3)_ | Appearance card radio toggle + `settings.js` wiring + `.theme-option` + markup guard test |

CI: **#117 / #118 GREEN**; TS3 committed and green on the same pipeline.

## 5. Validation evidence (TS4 — production, Raspberry Pi — PASS)
All checks passed with **no blocking defects**:
- Dark theme — render correct.
- Light theme — render correct.
- System theme — follows OS preference.
- Persistence across reloads — preference retained.
- Persistence across login / logout — cookie survives the session boundary.
- Dashboard, Settings, and Login pages — all render correctly in every theme.
- Mobile layout — correct.
- Theme toggle behaviour — instant apply confirmed.
- Error / revert behaviour — UI and dataset revert on a failed save.
- Visual review — passed.

## 6. Accepted deviations
- **Settings-only toggle** (no sidebar/header quick-switch) — by design.
- **Default dark** when no/invalid cookie is present — by design; `read_theme`
  never raises.

## 7. Final status
**CLOSED as DELIVERED.** Theme Support (§5.10 / §6.7) is delivered, CI-green, and
production-validated on the Raspberry Pi across all three themes, both persistence
paths, all three pages, mobile, and the error/revert path, with a flash-free
server-rendered first paint and no client-side storage.
