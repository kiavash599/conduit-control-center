# Release Closure Checklist

A short, mandatory ritual to run **whenever a milestone is closed** (a version's
feature set is complete, CI is green, and it is validated on the Raspberry Pi).
Its purpose is to keep the version the application *reports* in lock-step with
the version the project *documents* — the drift that left CCC reporting `0.1.0`
after v0.2 had already closed.

## Steps

1. **Stamp the CHANGELOG.** In `CHANGELOG.md`, rename the `## [Unreleased]`
   section to a dated release heading `## [X.Y.Z] — <YYYY-MM-DD>`, and open a
   fresh empty `## [Unreleased]` above it. Do not delete entries — only retitle.

2. **Bump the application version.** Set `APP_VERSION` in `backend/_version.py`
   to the same `X.Y.Z`. This is the single source of truth; every consumer
   (dashboard sidebar, `GET /api/health`, OpenAPI metadata, the startup log, and
   the static cache-bust fallback) reads from it — no other file needs editing.

3. **Prove they agree.** Run the version guard:

   ```
   pytest tests/unit/test_version.py
   ```

   `test_app_version_matches_latest_changelog_release` fails if `APP_VERSION` and
   the topmost dated CHANGELOG heading disagree — so a forgotten bump cannot ship
   silently. Run the full suite before tagging.

4. **Update the roadmap.** Bump the roadmap revision in
   `docs/roadmap/CCC_Product_Roadmap_v1.md` (header + a Revision History row), and
   add or update the milestone's closure record under `docs/closure/`.

## Why this is enforced, not just documented

Step 3 is the automated backstop for steps 1–2: the CHANGELOG release heading and
`APP_VERSION` are cross-checked in CI. The checklist is the human ritual; the test
is what makes "we forgot to bump the version" a red build instead of a production
surprise.
