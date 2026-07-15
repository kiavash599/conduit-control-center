# Changelog

All notable changes to Conduit Control Center are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.3.17] — 2026-07-14

**Declared, auditable armv7 wheelhouse builder (pinned/verified inputs) + bound builder provenance.**

### Added

- Canonical, committed OCI builder recipe (`release/builder/Containerfile`) targeting
  **Ubuntu 22.04 (Jammy) armhf** (glibc baseline no newer than the RPi2 target) with a
  digest-pinned base image and pinned/verified connected inputs: apt packages by exact
  `name=version` (unpinned fails the build), a sha256-verified `rustup-init`, and hash-pinned
  PEP 517 backends from a non-empty `requirements-build-backends.lock` (empty/comment-only
  fails closed). The armv7 wheel build no longer inherits undeclared RPi2 host state.
  Full bit-for-bit image reproducibility is not claimed; the environment is content-bound and
  the connected inputs are pinned/verified.
- Extended fail-closed builder provenance: the wheelhouse provenance binds the committed
  recipe (`recipe_sha256`), the pinned `base_image_digest`, the OCI `image_manifest_digest`
  (explicitly distinct from Docker's local image/config `image_id`, which is evidence-only),
  the committed build-backends lock (`build_backends_lock_sha256`), and an `environment`
  manifest (captured from the executing image) with `environment_sha256`; the declared
  build backends are cross-checked against the authorized lock and the glibc baseline is
  enforced <= the target.
- Controlled build tooling + runbook: `release/builder/build-builder-image.sh` (connected,
  pinned image construction), `release/builder/build-wheelhouse-offline.sh` (offline
  `--network=none` build; non-root, dropped capabilities, read-only inputs, bounded output,
  RPi2 resource policy), `release/builder/extract_build_backends.py`, `release/builder/README.md`.

### Changed

- `release/build_wheelhouse.py` and `release/ccc_release.py` require and validate the
  extended builder block; malformed, missing, unbound, local-image-ID-only, or legacy
  `image_digest` provenance fails closed.
- The builder provenance now also binds `apt_packages_sha256` and `rustup_init_file_sha256`
  (the two remaining committed inputs), captures structured OS identity
  (`os_id`/`os_version_id`/`arch`) and **structurally enforces** the Ubuntu 22.04 armhf/armv7l
  (Jammy) target — not merely a non-empty OS string.
- The raw OCI image manifest is embedded into the signed armv7 artifact
  (`provenance/image-manifest.json`); the producer independently **recomputes**
  `sha256(manifest) == image_manifest_digest` before signing (a local image ID cannot
  masquerade as the manifest digest).
- `release/builder/extract_build_backends.py` is hardened: it consumes the hash-pinned sdist
  lock, verifies every sdist's sha256 before reading it, requires an exact bijection with the
  lock (missing/extra/duplicate/unrecognized/mismatch fail closed), supports `.tar.*` and
  `.zip`, and models the PEP 517 legacy default backend.
- Phase B enforces an **explicit RAM/swap contract** (`--ram`, `--swap`; `--memory-swap` =
  RAM+swap; swap on a swap-less host rejected before Docker). `skopeo` is an **explicit,
  preflight-verified** prerequisite in both scripts (never auto-installed).
- Lifecycle-aware validation of the three committed builder inputs: absent is allowed
  pre-gate, present must pass strict semantic validation, `.example` templates are never
  active, and release/tag production requires all three.
- One shared, stdlib-only OCI/Docker image-manifest validator (`release/oci_manifest.py`)
  used at all three trust boundaries (Phase A, wheelhouse self-check, producer). It parses the
  manifest, enforces the single-image schema-2/OCI shape + descriptors, and BINDS
  `manifest.config.digest == image_id` (the config id Phase B executes) -- a local id can no
  longer masquerade as an unrelated manifest.
- APT provenance is now execution-bound and architecture-aware: `${binary:Package}` identity,
  installed-only status, `apt_architecture == armhf`, and every authorized pin proven present
  at the byte-exact version (epoch/revision included) with strict Debian arch semantics.
- The offline build enforces a mandatory, host-validated RAM/swap/host-reserve contract
  (`--ram`/`--swap`/`--host-reserve`): reserve-protecting math (`RAM + reserve <= MemTotal`),
  `RAM <= MemAvailable`, swap bounded by active `SwapTotal`/`SwapFree` with cgroup swap-limit
  capability, all validated before any container starts; point-in-time evidence is external
  only (never in signed artifact bytes).
- The build-backend extractor uses a real TOML parser (stdlib `tomllib` on 3.11+, else the
  hash-pinned `tomli` bootstrapped into an isolated venv) -- the regex fallback is gone,
  malformed/unreadable TOML and invalid UTF-8 fail closed, and the sdist layout must be
  unambiguous (one root, <=1 pyproject, no duplicate/unsafe members, regular-file only).
- Independent-review corrections: the release producer now REQUIRES the extractor-tools
  `.in` and `.lock` at the tag gate (no longer optional) and BINDS `extractor_tools_lock_sha256`
  into the builder provenance (rejecting a missing/malformed/mismatched/substituted binding);
  the `.in`<->lock relationship is a CLOSED authorization (exactly the hash-pinned `tomli`
  closure, extra packages rejected); the extractor CLI wires the isolated-venv bootstrap with a
  non-recursive re-exec; the shared manifest validator now parses JSON strictly (duplicate keys
  at any depth and NaN/Infinity rejected); ZIP `pyproject.toml` candidates must be regular files
  (symlink/special modes rejected) and `build-system.requires` must be non-empty strings (no
  `str()` coercion); and swap-limit capability requires positive, attributable evidence (readable
  cgroup control file or explicit override) — an unavailable/empty `docker info` can no longer
  fall through as capable — with the decision recorded in external evidence, guarded against
  input/output path collisions.

### Security

- The armv7 builder environment is auditable and content-addressed; a locally-built image
  ID alone can no longer satisfy provenance. The connected image-construction / offline
  network-isolated wheel-build trust boundary is enforced.

---

## [0.3.16] — 2026-07-14

**Signed V2 platform release artifacts and dependency supply-chain hardening.**

### Added

- A V2 release model with one product version, two mandatory deterministic
  platform artifacts (`aarch64` and `armv7l`), and one signed canonical manifest.
  The aarch64 artifact contains the application/runtime tree without a
  wheelhouse; the armv7l artifact embeds its verified offline wheelhouse,
  provenance record, and post-tag runtime lock.
- Controlled, build-independent pre-tag dependency locks at the repository root:
  `requirements-aarch64.lock` and `requirements-armv7-build.lock`. CI validates
  both as complete, pinned, hashed solutions of `requirements.txt` before a tag
  may be created.
- V2 release-builder, lock-generator, lock-validator, wheelhouse-builder, and
  platform-verifier coverage, including negative tests for cross-platform,
  malformed-manifest, digest, provenance, extraction, disk-space, and secret
  failures.

### Changed

- The update backend selects the signed artifact entry for the detected platform;
  the privileged helper independently detects the real host platform and binds
  the received bytes to that exact signed entry. Unknown platforms, missing
  entries, and cross-platform artifacts fail closed with no fallback.
- New releases are V2-only. The V1 single-artifact format is rejected, so legacy
  v0.3.14 installations migrate once by the documented manual SSH procedure;
  subsequent V2 releases use the dashboard One-Click Update path.
- armv7l install/update dependency provisioning is strictly offline and
  hash-locked. aarch64 remains wheelhouse-free and installs only the signed,
  hash-locked aarch64 dependency solution.
- Documentation builds now run as a pull-request gate. Deployment and GitHub
  Pages permissions remain restricted to pushes, and internal release runbooks
  are excluded from the public documentation site.

### Fixed

- Installer firewall planning now discovers and preserves the evidenced local
  SSH administration port instead of assuming port 22 (ADR-0004), including
  robust parsing of both observed `ss` output layouts. HTTP remains port 80,
  HTTPS remains the installer-selected port, and no Conduit UDP rule is added.

### Security

- The signed V2 manifest binds source tag/commit, both platform artifacts,
  dependency-lock digests, and the armv7l wheelhouse/runtime-lock/provenance
  chain. Signature, canonical form, exact member allowlists, path safety,
  digest-to-platform binding, disk-space checks, secret exclusion, and NUL-free
  text checks are enforced before privileged apply.
- The privileged update helper does not trust the backend's platform decision;
  it derives the host architecture independently and authorizes only the matching
  signed artifact.

### Notes

- This section is stamped for release-candidate preparation. No `v0.3.16` tag or
  GitHub Release exists yet; the two final artifacts, manifest, and signature must
  be built from the committed tag in one SRT ceremony and qualified before
  publication.
- Clean-image installation passed on real Raspberry Pi 2 armv7l (SSH 1222,
  HTTPS 2053) and Raspberry Pi 4 aarch64 (SSH 22, HTTPS 443) at source commit
  `6dbcc89`, reporting version 0.3.15. That validates the installer, platform
  mapping, firewall transaction, services, and external health path; it does not
  substitute for post-tag qualification of the final signed v0.3.16 artifacts.
- The committed pre-tag lock SHA-256 values are
  `9019cead48bf6c4fc25c27f41a7cd3d669969ef8101fe9e78493c031ecfb17f9`
  (`requirements-aarch64.lock`) and
  `8d31fad50e747c5d9a33782b49568cb63d186b29134d30ea3a8b5ed71455b95f`
  (`requirements-armv7-build.lock`).

---

## [0.3.15] — 2026-07-13

**Raspberry Pi 2 / 32-bit (armhf) support (BL-0002).**

### Added

- Architecture detection in `install.sh` and `update.sh` mapping the machine
  architecture to the Conduit asset (`aarch64` -> `conduit-linux-arm64`,
  `armv7l` -> `conduit-linux-armv7`), preserving the pinned `CONDUIT_VERSION`,
  checksum, and `--version` verification, with install/update parity and
  fail-closed behaviour on unsupported architectures. The arm64 / Raspberry Pi 4
  path is unchanged.
- On `armv7l`, native Python dependencies install offline from an official,
  verifiable `wheelhouse-armhf` asset (full `requirements.txt` closure,
  `--no-index --only-binary=:all:`), failing closed if the wheelhouse is missing,
  unverified, or incomplete.
- Systemd unit files are LF-normalised on install (CRLF-safe), so token/default
  validation cannot fail on CR-terminated lines.

### Fixed

- **Installer firewall now discovers the real local SSH port (ADR-0004).** A
  pre-release clean-image review found `install.sh` hardcoded `ufw allow 22/tcp`
  and then enabled UFW — on a host whose sshd listens on a non-22 port (e.g.
  1222) this could lock out administrative SSH after reconnect/reboot. The
  installer now resolves a purpose-aware firewall plan BEFORE any UFW write:
  it derives the SSH admin port(s) from the active session (procfs ancestry ->
  the responsible sshd's established socket, local endpoint only) and the
  effective sshd configuration (`sshd -T`, or an active/enabled `ssh.socket`),
  opens ONLY the evidenced local port(s) with no conventional 22 fallback and
  no union of conflicting evidence, and fails closed (leaving UFW untouched) on
  ambiguity/conflict. A bounded override `CCC_SSH_PORTS` is supported
  (`sudo env CCC_SSH_PORTS=1222 bash install.sh`); an override that omits the
  active session port, or any invalid value, is fatal. HTTP stays fixed at 80,
  HTTPS is the installer-selected port, and no inbound Conduit UDP rule is added.
  `ccc-apply-https-port` gains a backward-compatible `--skip-ufw` mode so all
  installer UFW writes are consolidated in one add-before-enable transaction;
  `update.sh` is unchanged.

### Changed

- Dev tooling: bumped `pytest` (development dependency) to clear a Dependabot
  alert. No runtime dependency changed; `requirements.txt` is unchanged.

### Notes

- Not yet released: a `wheelhouse-armhf` candidate asset has been built and
  locally verified on RPi2 (commit ef7cf78, 30 wheels; archive SHA256
  34191a2379f3236aa87e74e7be27454e5a358df89bf4092dd35ab80c55597443) but is not yet
  published as an official GitHub Release asset; clean-image install validation
  and CI also remain pending. The
  install and update paths are field-validated on a real Raspberry Pi 2 (armv7l,
  Ubuntu Server 22.04.5, Python 3.10.12); the update test covered a same-version
  `update.sh --ccc-only` clean reinstall (cross-version upgrade not yet exercised).

---

## [0.3.14] — 2026-07-07

**Trusted Update Signing — Phase-B audit + deploy-integrity fix.** Completes the ADR-0003
signed-release audit model and fixes a deterministic-artifact deploy defect found on hardware.

### Added

- Non-authorizing update **audit trail** (`/var/log/conduit-cc-audit/update-audit.jsonl`,
  `root:conduit-cc 0640`): `accepted` / `applied` / `reverted` outcomes with an allowlist
  redaction (no trust material), plus the reject/failure taxonomy.
- `signing_principal` metadata recorded on successful verification.
- Deployment purge of stale Python bytecode, and regression tests for content-based transfer.

### Changed

- Deploy and rollback-restore now transfer by **content** (`rsync --checksum`) so a same-length
  change to a deterministic (`mtime=0`) artifact can no longer be skipped.

### Fixed

- **Deploy-integrity defect:** a same-length version change (e.g. `0.3.13` → `0.3.14`) was
  silently skipped by rsync's size+mtime quick-check, leaving the runtime on the old version and
  triggering an auto-rollback. Fixed by content-based transfer; Raspberry Pi validated.

### Security

- Update apply writes an append-only audit record without ever altering the fail-closed
  verification decision, exit code, deployment, or rollback (ADR-0003, Invariant I5).

---

## [0.3.13] — 2026-07-04

**Security hardening candidate.** Adds ADR-0003 artifact-signing groundwork:
publisher-side signed release production, SSH-signature manifest verification,
signed-asset update ingestion, and fail-closed verification before privileged
update execution.

### Added

- Release tooling for canonical signed update artifacts.
- Device-side verification core for signed manifests and content digests.
- Signed-asset update framing between the Update API and update helper.
- Unit coverage for signed release production and verification failure modes.

### Changed

- One-Click Update now consumes publisher-produced signed assets instead of
  GitHub auto-generated source archives.

---

## [0.3.12] — 2026-07-02

**Frontend polish release.** Ships UI polish for the Software Updates and Restore
status surfaces and drives one real dashboard One-Click Update on Raspberry Pi
(**v0.3.11 → v0.3.12**) to validate the polished UI end to end. **No backend
changes, no update-engine/logic changes, no capability-subsystem changes.**

### Fixed

- Software Updates: the **Install Update** button now correctly hides when the
  system is already up to date. `.btn` set an explicit `display`, which overrode
  the user-agent `[hidden]` rule, so `button.hidden = true` had no visual effect;
  a scoped `.btn[hidden] { display: none }` rule restores the intended behaviour.
- Software Updates: stale update progress/rollback messages no longer survive page
  reloads — the progress area is reset on load.
- Restore: restore success now uses a transient **Toast** notification (auto-dismiss
  + de-duplication via the shared Toast) instead of a persistent global banner, so
  a past "Restore complete" no longer re-appears on unrelated pages after a
  refresh. Failure states (`rolled_back`, `rollback_failed`) remain persistent,
  dismissible banners.

---

## [0.3.11] — 2026-07-02

**Validation release for the One-Click Update end-to-end path.** This release
exists only to drive one real dashboard One-Click Update on Raspberry Pi
(**v0.3.10 → v0.3.11**) on the B1 transient-unit engine, confirming the update
completes after the deployment-drift recovery. There are **no application feature
changes** and **no update-logic changes**.

---

## [0.3.10] — 2026-07-01

**Validation release for the Update Engine Test & CI Hardening milestone.** This
release exists only to drive one real dashboard One-Click Update on Raspberry Pi
(**v0.3.9 → v0.3.10**), validating the already-merged test/CI hardening and the
Phase-3 deploy `rsync --exclude '/bin/'` fix. There are **no application feature
changes** and **no update-logic changes**.

---

## [0.3.9] — 2026-06-29

**Validation release for the Trusted Update Engine.** This release exists only to
validate the dashboard One-Click Update path end to end on Raspberry Pi, updating
a real device from **v0.3.8 → v0.3.9**. There are **no application feature
changes**.

Expected validation path: dashboard check → install → `ccc-update-apply` →
`update.sh --ccc-only --non-interactive` → restart → health → success.

Expected observability after the run: `/var/lib/conduit-cc/update-worker.log`
(captured updater output) and `/var/lib/conduit-cc/update-status.json` (terminal
state).

---

## [0.3.8] — 2026-06-29

**One-Click Update non-interactive fix release.** Restores the dashboard-driven
One-Click Update path, which failed during the v0.3.6 → v0.3.7 validation.

### Fixed

- `update.sh` now skips the manual confirmation prompt for CCC-only /
  non-interactive updates (`--ccc-only`, or when stdin is not a TTY); normal
  interactive `update.sh` runs still prompt. The one-click helper runs the
  updater with stdin redirected to `/dev/null`, so the prompt previously hit EOF
  and aborted with `rc=1`.
- `ccc-update-apply` now captures the worker's `update.sh` stdout/stderr to
  `/var/lib/conduit-cc/update-worker.log` (root-only `0600`) instead of
  discarding them to `/dev/null`, so update failures are diagnosable.

This fixes the silent `rc=1` rollback discovered during the v0.3.6 → v0.3.7
One-Click Update validation.

---

## [0.3.7] — 2026-06-29

**Validation release for the complete One-Click Update pipeline.** Cut to perform
the first full end-to-end Raspberry Pi validation of dashboard-driven One-Click
Update (discover → install → `ccc-update-apply` → `update.sh --ccc-only` → restart
→ reconnect → success) updating a real device from v0.3.6. No application logic
changed in this release; the user-visible content is the branding migration below.

> **Known issue.** One-Click Update from v0.3.6 → v0.3.7 may roll back with `rc=1`
> because `update.sh` still prompts for confirmation in the non-interactive helper
> path (stdin is `/dev/null`, so the prompt aborts). **Fixed in v0.3.8.**

### Changed

- **Branding: adopted the CCC Logo System v1.0** as the official identity
  (commit `d7c3823`). Replaces the historical B1 branding throughout:
  - **Dashboard** favicon + app/maskable icons migrated to the new system
    (`frontend/static/favicon/`, `frontend/static/icons/`).
  - **MkDocs** logo and favicon migrated
    (`website/overrides/assets/branding/{logo.svg,favicon.svg}`).
  - The new asset system and Brand Usage Guide live under `branding/logo/`.

### Removed

- Retired the legacy **B1 branding assets** under `docs/brand/` (superseded by
  `branding/logo/`; `docs/brand/README.md` now points to the Brand Usage Guide).

---

## [0.3.6] — 2026-06-28

### Fixed

- Dashboard One-Click Update install now sends `Content-Type: application/json`,
  preventing `POST /api/update/install` from returning HTTP 422 before route
  execution (the privileged helper never started and the update status stayed
  `idle`). Frontend-only (`frontend/static/js/updates.js`, `e0c7891`); discovered
  during v0.3.5 Raspberry Pi validation.
- Dashboard now renders structured FastAPI validation errors readably instead of
  displaying `[object Object]`.

---

## [0.3.5] — 2026-06-28

> **Known issue.** Dashboard One-Click Update install in v0.3.5 may fail with
> HTTP 422 / `[object Object]`. Fixed in v0.3.6. A manual `update.sh` deployment is
> required to reach v0.3.6 from v0.3.5 or earlier.

### Added

- **Storage Protection** (Log Management / SD-Card Protection; `a6b6bd4`) —
  long-term storage maintenance for SD-card-based Raspberry Pi installs:
  - **logrotate integration** for `/var/log/conduit-cc/*.log`
    (`deployment/conduit-cc.logrotate` — weekly, 4 rotations, compressed,
    `su root conduit-cc`, no `copytruncate`; provisioned by `install.sh`,
    re-provisioned by `update.sh`, removed by `uninstall.sh`);
  - **automatic cleanup of `ccc-update-*` work directories** in `ccc-update-apply`
    (a flock-guarded sweep of orphaned directories; the current work directory is
    removed only after the terminal `update-status.json` is written);
  - **improved long-term Raspberry Pi storage maintenance** overall.

  Linux-native; no new privileged helper, sudoers rule, systemd timer, dashboard
  action, or journald change.

> **Release objective (not a feature of this release).** v0.3.5 is also the
> milestone for the **first complete end-to-end Raspberry Pi validation of the
> existing One-Click Update feature** (introduced in v0.3.2; lock-path fix in
> v0.3.4). This release does **not** introduce or change One-Click Update
> functionality — the validation is a release goal, not a changelog entry.

---

## [0.3.4] — 2026-06-28

### Fixed

- One-click update (Feature 2) failed at the lock step with
  `OSError: [Errno 30] Read-only file system: '/run/lock/ccc-update-apply.lock'`,
  so `POST /api/update/install` returned HTTP 500 and no update ever started. The
  privileged helper `ccc-update-apply` runs via `sudo` inside the `conduit-cc`
  service's mount namespace, where `ProtectSystem=strict` makes `/run/lock`
  read-only (sudo does not escape the namespace). The lock file is moved into the
  service's writable StateDirectory — `/var/lib/conduit-cc/.update.lock` — and
  opened with `O_NOFOLLOW`, matching the proven `ccc-restore-apply` pattern. Fix
  is confined to the helper; no changes to the API, frontend, installer, updater,
  systemd unit, or sudoers. (Exposed by the v0.3.3 validation release.)

---

## [0.3.3] — 2026-06-28

**Validation-only release — no functional changes.**

This release exists solely to validate the Feature 2 one-click update path end to
end (Dashboard → `ccc-update-apply` → `update.sh --ccc-only` → restart → reconnect)
against a real, stable, higher-version GitHub Release. It bumps the version stamp
only; there are no changes to application logic, the installer, the updater, the
privileged helpers, configuration, or documentation. A Pi running 0.3.2 detects
0.3.3 as available and can perform a real dashboard-driven update to confirm the
mechanism.

---

## [0.3.2] — 2026-06-28

### Added

- Cloudflare-compatible HTTPS port selection (Feature 1): the installer offers a
  user-selectable, Cloudflare-supported HTTPS port (`443`, `8443`, `2053`, `2083`,
  `2087`, `2096`; default `443`), skips occupied ports, and `update.sh` preserves
  the chosen port across updates. The dashboard shows a read-only
  "HTTPS port (configured)".
- One-click CCC update system (Feature 2): a dashboard "Software Updates" section
  checks GitHub Releases (stable only), previews release notes, and installs CCC
  updates via a privileged helper (`ccc-update-apply`) with detached execution,
  async status, reconnect-after-restart, and automatic rollback. Operator-initiated
  only (no auto-update); Conduit Core is out of scope. The check is cached for 24h
  and degrades gracefully when GitHub is unreachable.
- Raspberry Pi 3 Model B (1 GB) validated and documented as a supported platform
  (Chapter 2 + README; platform badge updated to Raspberry Pi 3/4).
- TLS onboarding (Epic C / D3): the user guide now routes operators to the
  Cloudflare Origin Certificate workflow — EN Chapter 5 §5.15 + Chapter 6 §6.4,
  mirrored in the Persian guide. (`83d2ed0`, `652f028`)
- Bilingual TLS guide (Epic D): added the Persian TLS guide `docs/fa/tls-setup.md`
  as a translation of `docs/tls-setup.md` (`05366fe`), and converted the chapter
  references to language-routed markdown links — English chapters → `docs/tls-setup.md`,
  Persian chapters → `docs/fa/tls-setup.md` (`957d497`). The 8 redacted Cloudflare
  TLS screenshots are shared by both guides (single image set, no duplicates).
- FA diagram parity completed (Epic D / D4): all 19 diagrams (DGM-01–19) are now
  integrated into the Persian user guide — DGM-01–12 in ch04/04a/05 and DGM-13–19
  in ch10/11/12/14 — replacing the legacy Persian ASCII-art diagram blocks they
  superseded (EN-paralleled navigation/micro-flows preserved). Full EN↔FA diagram parity.

### Fixed

- ShellCheck SC2034: removed unused `NGINX_AVAILABLE`/`NGINX_ENABLED` from
  `install.sh` (orphaned by the HTTPS-port provisioning refactor); unblocks CI.
- Backup key-exclusion guard (BCA-1): the fail-closed path guard now matches
  excluded locations cross-platform; it previously failed open under Windows-style
  path resolution (Linux production unaffected). (`043cb6a`)

### Internal

- Backup permission test (BCA-2): the POSIX file-mode invariant is asserted only
  where the filesystem honours POSIX permission bits (Linux CI / Raspberry Pi),
  preventing false failures on non-POSIX developer filesystems. No production
  change. (`043cb6a`)

---

## [0.3.1] - 2026-06-24

### Fixed

- Root URL onboarding (D1): `/` now redirects to `/dashboard` (`f5233ff`)
- Cloudflare onboarding screenshot fix (D2): corrected zone representation (`b88ab33`)

---

## [0.3.0] - 2026-06-21

> First public release. Bundles the Personal Mode, Ryve Claim, and Backup &
> Restore milestones on top of the v0.2.0 Smart Conduit Control base; all
> production-validated on a Raspberry Pi 4 (Ubuntu 22.04 ARM64). `APP_VERSION`
> is bumped to match this heading; the `test_version` guard keeps the two in
> lock-step.

### Added — Backup & Restore (Epic #4)

- **Encrypted backups** — create a single-file, password-protected backup of CCC
  state (`ccc.db`, a redacted `.env` subset, `config.json`, and the applied
  Conduit settings) from **Settings → Backup**. The archive is sealed with
  **AES-256-GCM** using a key derived from the operator passphrase via **scrypt**;
  the cleartext header is authenticated as AES-GCM associated data.
- **Fail-closed key exclusion** — the collector refuses to include key-grade or
  secret material (private keys, session secret, Cloudflare token, TLS paths);
  the same scan runs again when a backup is opened (defense in depth).
- **Inspect before restore** — upload a backup to preview its manifest
  (app version, contents, compatibility) without writing anything.
- **Guided restore** — a destructive, confirmation-gated restore runs through a
  privileged worker (`ccc-restore-apply`) with a pre-apply checkpoint and
  automatic rollback on failure; the dashboard restarts and is verified healthy,
  and the applied Conduit settings are re-applied through the validated config
  helper only after the core restore commits.
- **API** — `POST /api/backup/create`, `POST /api/backup/inspect`,
  `POST /api/backup/restore`, `GET /api/backup/restore/status` (auth + CSRF;
  `Cache-Control: no-store`). Passphrases are never logged, placed in argv/env,
  or echoed in responses.

### Added — Ryve Claim QR (Epic #3)

- **Ryve Claim** — generate a Ryve claim QR to adopt the station in the Ryve
  mobile app. The QR is treated as **private-key-grade**: produced by the
  `ccc-ryve-claim` helper (runs as `conduit`, never root), written only to a
  unique `0600` tmpfs path that is unlinked immediately after being read into
  memory, and never persisted or logged.
- **API** — `POST /ryve/claim`, `GET /ryve/claim/image/{claim_id}` (PNG,
  `no-store`), `DELETE /ryve/claim/{claim_id}` (auth + CSRF); claims live in an
  in-memory store and are not written to disk.

### Added — Personal Mode (C4 / C5 / C6)

- **Personal Mode** — create a personal Conduit identity for trusted contacts and
  manage it entirely from **Settings → Personal mode**: a three-state status card
  (Not set up / Created — inactive / Active · N personal clients), **create
  identity** (display name, 1–32 chars), **View / share token** with a
  **client-side QR**, and a **Max personal clients** control that enables /
  adjusts / disables the personal-client limit. Setting the limit to **0 disables
  Personal Mode but keeps the identity**.
- **Backend** — helper (C4, runs as `conduit`, flock-serialised, single-depth
  `.bak`, never opens the private key), adapter (C5), API (C6a:
  `GET /personal/status`, `POST /personal/compartment`, `GET /personal/token`,
  `PUT /personal/max-clients`), max-clients apply with restart → health-as-truth
  verify → rollback (C6b), and regenerate / restore endpoints (C6c).
- **QR** — vendored **Nayuki `qrcodegen` v1.8.0** (MIT), SHA-256-pinned and served
  same-origin; the QR is drawn to a `<canvas>` (no `eval` / `Function` /
  `document.write`, no DOM injection). **CSP unchanged.**
- **Security / privacy** — the pairing token is never logged, stored, persisted,
  placed in a URL, or written to `localStorage` / `sessionStorage` / cookies; the
  token endpoint responds `Cache-Control: no-store`; token text + QR are cleared
  from the DOM on close and on navigation. Aggregate-only is preserved (no IPs,
  identities, or per-user data).
- **Production fix (EROFS)** — Personal Mode status failed with `503` on the
  Raspberry Pi because `ProtectSystem=strict` made the helper's lock path
  read-only inside the CCC service namespace. Fixed in `deployment/conduit-cc.service`
  by granting `ReadWritePaths=/var/lib/conduit/data` (narrow data dir), carving the
  private key back to read-only via `ReadOnlyPaths=/var/lib/conduit/data/conduit_key.json`,
  and ordering `After=conduit.service` (no `Wants=` pull-in). Commit `39ba3eb`.
- **Deferred** — Regenerate / Restore **UI** (Slice 5; backend retained); a **live
  connected personal-client count** (requires upstream Conduit metrics — not
  exposed today). Production-validated on a Raspberry Pi 4 (C6e); see
  `docs/closure/PERSONAL_MODE_CLOSURE.md`.

---

## [0.2.0] — 2026-06-17

> Smart Conduit Control milestone — CLOSED and production-validated on a
> Raspberry Pi 4 (Ubuntu 22.04 ARM64). `APP_VERSION` is bumped to match this
> heading; the `test_version` guard keeps the two in lock-step.

### Added — Theme Support (Light / Dark / System)

- **Light / Dark / System** themes selectable from a new **Appearance** card in
  Settings (native radio group). Default dark; System follows the OS via
  `prefers-color-scheme`. Light is a WCAG-AA palette.
- **Flash-free first paint** — the active theme is **server-rendered** into
  `<html data-theme="…">` from a `theme` cookie (HttpOnly, Secure, SameSite=Strict,
  Path=/, 1-year). **No localStorage.** Instant apply on toggle via
  `document.documentElement.dataset.theme`, with UI + dataset **revert on a failed
  save**.
- API — `POST /api/settings/theme` (auth + CSRF), validating against
  `light` / `dark` / `system` (422 + no cookie on an invalid value). Theme injected
  into the Dashboard, Settings, and Login page contexts. `textContent`/DOM-only —
  no `innerHTML`.
- CSS tokenised for theming — populated `[data-theme]` blocks and four shared
  tokens (`--color-on-accent`, `--color-spinner-track`, `--color-spinner-head`,
  `--color-chart-down`); five hard-coded colour leaks removed. Validated end-to-end
  on a Raspberry Pi 4 (TS4); see `docs/closure/theme-support-closure.md`.

### Added — Live Operations (Node Status broker badge + live signals)

- Node Status card extended with a four-state **broker badge** (Live / Starting /
  Disconnected / Not running, plus "Unknown" when metrics are unreadable),
  **connecting clients**, **idle**, and the Conduit **build revision** (appended
  to the version line). Read-only and aggregate-only.
- API — `GET /api/status` gains a nested `live` block
  `{broker_state, connecting_clients, idle_seconds, build_rev}`, computed
  server-side and **non-fatal**: a metrics failure never changes the HTTP code
  and never nulls `node_status` / `conduit_version` / `uptime_seconds`.
- No duplication of Advisor/Traffic/Lifetime values; `conduit_uptime_seconds`
  intentionally deferred (Node Status shows service uptime only). Validated on a
  Raspberry Pi 4; see `docs/closure/live-operations-closure.md`.

### Added — Bandwidth Scheduling (reduced-mode window)

- Operator-configurable **daily reduced-mode window** for Conduit, set through the
  existing Settings → Conduit Configuration workflow (validate → confirm →
  restart → verify, with rollback).
- **UTC reduced window** — Start and End as `HH:MM` (24-hour, UTC), with a
  browser-local preview in the UI.
- **Reduced max common clients** and **reduced bandwidth (Mbps)** applied during
  the window; normal limits apply outside it.
- **Restart-on-apply, no boundary restarts** — Conduit restarts once when the
  schedule values change; psiphon-tunnel-core then performs the daily
  normal⇄reduced transition internally with no restart at the start/end times and
  no disconnect of already-connected clients. CCC runs no scheduler (no cron, no
  APScheduler, no systemd timers).
- API — `GET /api/conduit/config` reports the configured reduced window
  (configured-only; no effective metric exists); `POST /config/validate` and
  `POST /config/apply` accept the reduced fields. Aggregate-only: no per-client,
  session, IP, or identity data. The privilege boundary stays integer-only (the
  root helper formats `HH:MM` from validated minutes), preserving the M2 security
  model. `update.sh`/`install.sh` migrate and guard the reduced-capable helper +
  unit. Validated end-to-end on a Raspberry Pi 4 (Conduit 2.0.0); see
  `docs/closure/bandwidth-scheduling-closure.md`.

### Added — Regional Analytics

- Regions dashboard card — aggregate-only, top 10 active regions by traffic,
  `scope="common"`, sorted Traffic DESC. Columns: No., Country (flag + name),
  Traffic, Clients. Dashboard-aware 60s polling; mobile responsive.
- Regions API — `GET /api/conduit/regions` (auth-required, aggregate-only).
  Per region returns `{region, traffic_bytes, clients}` where
  `traffic_bytes = conduit_region_bytes_uploaded + conduit_region_bytes_downloaded`
  and `clients = conduit_region_connected_clients`. No IP, session, or
  per-client data; degrades to an empty list when metrics are unavailable.
- "Clients" terminology enforced in the UI (never "Users"); frontend guard
  tests added (`tests/unit/test_regions_frontend_guard.py`).
- Known limitation: Unicode flag emoji depend on platform font support; some
  desktop environments display the ISO letters instead of a flag (cosmetic;
  accepted, not a defect).

### Added — Contribution Advisor

- Contribution Advisor — read-only, aggregate-only guidance card at the top of
  the Dashboard. Surfaces Health, Capacity, and Reduced-mode recommendations
  plus a contribution health summary (Live/Offline status chip + headline).
- Advisor API — `GET /api/advisor` (auth-required, `Cache-Control: no-store`);
  deterministic engine with cooldown and a growth warm-up gate; degrades
  gracefully (never 5xx) when inputs are unavailable.
- Configurable via the `advisor` block in `config.json` (sampling/warm-up
  knobs; see `config.example.json`). Defaults are safe; aggregate-only — no
  per-client or per-region data.

### Added — Traffic history and dashboard information architecture

- Persistent traffic collector — aggregate-only byte ledger in SQLite with
  hourly/daily rollups, lifetime checkpoints, and configurable retention
  (default 30 days). Ship-dark: disabled by default (`traffic_collector_enabled`).
- Traffic Read API — `GET /api/traffic/summary` and `GET /api/traffic/series`
  (24h / 7d / 30d), read-only and aggregate-only.
- Dashboard information architecture (M-IA) — sections restructured to
  Dashboard / System / Settings with hash migration (`#overview`→`#dashboard`,
  `#logs`→`#system`) and additive pattern/state CSS conventions.
- "Lifetime & history" traffic card — persistent totals, recent-window figures,
  and a hand-built SVG grouped-bar time-series chart (CSP-safe; accessible
  data-table fallback).
- Static asset cache-busting — `static_url()` appends a per-file mtime query
  token so frontend deploys no longer require a manual CDN purge.

### Added — Psiphon Conduit end-to-end deployment (Issue #45)

**Psiphon Conduit end-to-end deployment**

- `install.sh` — Phase 1x: detect Conduit binary (PATH → `./conduit` →
  offer GitHub download with SHA-256 verification)
- `install.sh` — Phase 2x: create `conduit` system user; create
  `/opt/conduit/` (binary) and `/var/lib/conduit/` (data, keypair);
  pre-swap validation (4 steps); install binary; install + enable
  `conduit.service`; post-start verification; UFW reminder
- `deployment/conduit.service` — production systemd unit: `conduit` user,
  `/opt/conduit/conduit`, `--metrics-addr 127.0.0.1:9090`,
  `--max-common-clients 50`, `--bandwidth 40`, `ProtectSystem=strict`,
  `ReadWritePaths=/var/lib/conduit`, `PrivateTmp=yes`, `NoNewPrivileges=yes`
- `update.sh` — `phase2b_conduit_update`: detect new binary, 4-step
  pre-swap validation, `.bak` rollback copy, stop/swap/start, 3-check
  post-swap verification with automatic rollback
- `uninstall.sh` — `phase4b_conduit_remove`: stop/disable `conduit.service`,
  remove binary directory; preserve `conduit_key.json` and `conduit` user by
  default; `--purge` removes data directory and user
- `config.example.json` — added `_comment_metrics_port` to document the
  `metrics_port` ↔ `--metrics-addr` coupling
- `docs/pre-install.md` — Step 1a: Conduit binary options (download, local
  copy, PATH); post-install UFW firewall discovery procedure

### Fixed — deployment and access (post-0.1.1)

- Grant `journalctl` access so the Logs page can read the Conduit journal
- Drop `NoNewPrivileges`-implying hardening so sudo-based Conduit controls work
- Document the v0.1.1 control-hardening trade-off

---

## [0.1.1] — 2026-06-11

Maintenance release. Contains only the changes included in the `v0.1.1` tag.

### Security
- Upgrade Starlette to >= 1.0.1 (PYSEC-2026-161)

### Fixed
- Resolve shellcheck warnings in the install / update / uninstall scripts
- Render ANSI colours with `%b` after SC2059 cleanup

---

## [0.1.0] — MVP

> 0.1.0 was not separately tagged and first shipped within the v0.1.1 release.

All items listed below were committed to the repository and validated
end-to-end on a Raspberry Pi 4 (Ubuntu 22.04 ARM64) in Issue #38.

### Added

**Authentication**
- Password-protected web dashboard
- Login page with bcrypt password verification (cost factor 12)
- Server-side session management backed by SQLite (HttpOnly, Secure, SameSite=Strict cookies)
- Account lockout after 5 consecutive failed login attempts (15-minute lock)
- `ccc-unlock` CLI command to manually unlock a locked account
- Session idle timeout (default 60 minutes, configurable)
- Change password form — requires current password; invalidates all existing sessions on change

**Conduit Node Management**
- Node status display with colour-coded badge (Running / Stopped / Starting / Stopping / Error)
- Start, Stop, and Restart controls with confirmation dialog and loading spinner
- Pairing workflow guide — pairing link processed in memory only, never stored or logged
- Conduit configuration viewer (read-only; sensitive values masked)

**Monitoring**
- System health panel: CPU %, RAM MB/%, CPU temperature (°C), disk usage GB/% — updates every 10 seconds
- Traffic counter widget: bytes uploaded and downloaded since last service start
- Log viewer: last 200 lines of Conduit service log with auto-refresh every 30 seconds
- DDNS status panel: current public IP, hostname, last update time, last result (success/failure)

**Infrastructure**
- `install.sh` — automated installer for Ubuntu 22.04 ARM64
  - Interactive prompts for Cloudflare API token, zone name, hostname, and Origin Certificate
  - Validates all inputs via the Cloudflare API before making any system changes
  - Installs and configures Nginx, systemd, UFW, and DDNS cron job
- `uninstall.sh` — clean removal script
- `update.sh` — in-place upgrade script with automatic rollback on failure
- `cloudflare-ddns.sh` — Cloudflare DDNS update script (Script B: preserves proxy status); runs every 5 minutes via cron; structured JSON logging to `/var/log/conduit-cc/ddns.log`
- Nginx virtual host with Cloudflare Origin Certificate support, `ngx_http_realip_module` (restores real visitor IP from `CF-Connecting-IP`), security headers, and login rate limiting (10 req/s per IP)
- `conduit-cc.service` — systemd unit with sandboxing (`ProtectSystem=strict`, `PrivateTmp=yes`, `NoNewPrivileges=yes`) and automatic restart
- UFW firewall rules: allow ports 22, 80, and 443 only

**API**
- REST API with auto-generated OpenAPI documentation at `/api/docs`
- Unauthenticated health check: `GET /api/health`
- CSRF protection (double-submit cookie pattern) on all state-changing endpoints

**Documentation**
- `docs/pre-install.md` — Cloudflare dashboard setup checklist
- `docs/tls-setup.md` — Cloudflare Origin Certificate and Let's Encrypt setup
- `docs/dev-setup.md` — local development environment guide
- `docs/architecture.md` — system architecture overview
- API reference provided by the live OpenAPI docs at `/api/docs`, `/api/redoc`, and `/api/openapi.json` (no separate hand-maintained reference file)

### Security

- `CF_API_TOKEN` stored in `/etc/conduit-cc/.env` (permissions 640, owned by `conduit-cc` service user); never logged
- Primary TLS model: Cloudflare Proxy + Origin Certificate + Full (strict) SSL mode
- Nginx rate limiting on login endpoint (10 req/s per IP, using restored real IP)
- Security headers on all responses: HSTS, Content-Security-Policy, X-Frame-Options: DENY, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer
- Pairing links never appear in logs, database, or API responses
- FastAPI binds to `127.0.0.1` only — not exposed directly to the internet

### Known Limitations

- Historical traffic charts were not part of the original 0.1.0 MVP scope
- No email or webhook alerting (planned for v1.1)
- Conduit configuration is read-only — no editor (planned for v1.1)
- No two-factor authentication / TOTP (planned for v1.3)
- No multi-node support (planned for v2.0)
- Let's Encrypt certificate renewal is documented but not automated by the installer (planned for v1.0)
