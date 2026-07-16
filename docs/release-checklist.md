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

5. **Build the signed, canonical artifact from the tag.** After committing and tagging
   (`vX.Y.Z`), build from the committed tag only (`--git-ref vX.Y.Z`) so the artifact is
   reproducible and provenance-linked. Sign the manifest with the publisher key.

6. **Qualify locally.** Verify: no NULL bytes in text/script members, `APP_VERSION` matches,
   digest ↔ manifest agree, and the signature verifies against the publisher identity. Record
   the artifact **SHA-256 digest**.

7. **Publish (irreversible — manual).** Push `main` and the tag, then replace the GitHub Release
   assets with the three `ccc-X.Y.Z.*` files. Verify the published digest equals the recorded
   digest and that the downloaded manifest signature verifies.

8. **Reconcile status.** Update `docs/PROJECT-STATUS.md` (release, resolved issues, timeline).

**Never publish:** `trusted_publishers`/`allowed_signers`, the signing private key, or any asset
other than the three `ccc-X.Y.Z.*` files. The device trust anchor is provisioned out-of-band.

## Why this is enforced, not just documented

Step 3 is the automated backstop for steps 1–2: the CHANGELOG release heading and
`APP_VERSION` are cross-checked in CI. The checklist is the human ritual; the test
is what makes "we forgot to bump the version" a red build instead of a production
surprise.


## V2 platform-artifact release (ADR-0003 Amendment A1)

Steps 5-7 above are replaced for V2 as follows.

**5a-builder. Construct the pinned builder image (Owner-gated ceremony).** Author/commit
`release/builder/Containerfile` (base digest-pinned; toolchain + PEP 517 backends pinned via
`requirements-build-backends.lock`). Prerequisites `docker`, `skopeo`, `sha256sum`, `python3` are **preflight-verified and never
auto-installed** (skopeo is an explicit, out-of-band dependency on RPi2). Run
`release/builder/build-builder-image.sh --base-image <ref@sha256:...>` to build it, capture the
OCI image MANIFEST digest (not the local image ID) and the environment manifest, then build the
wheelhouse offline with `release/builder/build-wheelhouse-offline.sh` (`--network=none`; the
RAM/swap contract is mandatory: `--ram <RAM> --swap <extra> --host-reserve <reserve>
--resource-evidence <path>`, host-validated — RAM+reserve ≤ MemTotal, RAM ≤ MemAvailable, swap
bounded by active SwapTotal/SwapFree + cgroup capability — before Docker runs). Phase A also runs
the shared `release/oci_manifest.py` gate (`config.digest == image_id`). Manifest capture uses
`docker save` → detected archive transport → `skopeo inspect --raw` (no `docker-daemon:`
transport), with a fail-fast interop smoke test before the build and atomic evidence writes; the
first corrected ceremony must confirm on-RPi2 that `config.digest == docker image .Id`. See
`release/builder/README.md`.
The produced `provenance/wheelhouse-armv7.json` binds recipe/base/manifest/environment and is
validated by the producer before signing.

**5b. Commit the TWO build-independent locks PRE-TAG.** Generate with `release/gen_locks.py`:
`requirements-aarch64.lock` (PyPI aarch64 wheels: `pip download --only-binary=:all: -r requirements.txt`)
and `requirements-armv7-build.lock` (PyPI sdists: `pip download --no-binary=:all: -r requirements.txt`).
Commit both at the repo root. Also commit the **three active builder inputs** —
`release/builder/{apt-packages.list,rustup-init.sha256,requirements-build-backends.lock}` — with
real validated values (never the `.example` placeholders); `validate_builder_inputs` and CI must
accept them. Also commit `release/builder/requirements-extractor-tools.{in,lock}` (the pinned tomli
parser) before running extraction. Also commit `release/builder/requirements-build-backends.source-allowlist` (backends with no official target wheel, e.g. `cffi`); the generation gate must prove no compatible wheel exists (drift fails closed) and the image installs backends in two ordered `--require-hashes` passes. Let CI go green, THEN tag `vX.Y.Z` (two-commit sequence). The build-DEPENDENT `requirements-armv7.lock` (resulting wheel hashes) is NOT
committed; it is produced with the wheelhouse and passed at build time via `--armv7-runtime-lock`
(injected + digest-bound). Do NOT commit placeholder/0.0.0 locks (release-input gate).

**5c. Build the two signed artifacts (one SRT ceremony).**
```
python3 release/ccc_release.py --version X.Y.Z --sign-key <key> \
    --git-ref vX.Y.Z --wheelhouse-armv7 <wheelhouse-dir> \
    --provenance-armv7 provenance/wheelhouse-armv7.json \
    --armv7-runtime-lock requirements-armv7.lock \
    --image-manifest release/builder/evidence/image-manifest.json \
    --recommended-core <core> --out dist
```
The producer computes requirements + the two committed lock sha256 from the canonical bytes, computes the
armv7 runtime-lock sha256 from the injected file, and binds all four. Pass `--expect-*-sha256` only for
optional cross-checks. Provenance is strictly validated against the embedded wheelhouse + SHA256SUMS AND
authorized against `requirements-armv7-build.lock`. The raw OCI manifest is embedded into the
armv7 artifact at `provenance/image-manifest.json` and the producer recomputes
`sha256(manifest) == image_manifest_digest` before signing.

Produces exactly: `ccc-X.Y.Z-aarch64.tar.gz`, `ccc-X.Y.Z-armv7l.tar.gz`,
`ccc-X.Y.Z.manifest.json`, `ccc-X.Y.Z.manifest.json.sig`. The producer runs the pre-sign
secret-exclusion + no-NUL-in-text scan and fails closed on any violation.

**6b. Qualify locally.** For BOTH platforms: `ssh-keygen -Y verify` the manifest; recompute each
artifact sha256 and confirm it matches its signed entry; confirm the armv7 wheelhouse `bundle_sha256`
and `provenance_sha256`; run `deployment/bin/ccc-verify-release` per platform (expect exit 0 for the
matching platform, exit 2 cross-platform). Record both artifact digests.

**7b. Publish EXACTLY these four assets:** the two platform artifacts + manifest + signature. **Never
publish** the wheelhouse standalone, the locks-as-assets (they are in Git), `trusted_publishers` /
`allowed_signers`, or the signing key. The device trust anchor is provisioned out-of-band.

**Both platform artifacts are mandatory** — a release missing either is incomplete and the verifier
rejects the manifest.
