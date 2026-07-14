# Manual SSH migration to v0.3.15 (legacy RPi4, arm64)

The single legacy v0.3.14 RPi4 is migrated manually over SSH (no One-Click; V1 One-Click is retired).
arm64 needs no wheelhouse (index path, hash-locked), so this is simple.

## Steps (operator, over SSH, as a user who can sudo)

1. Back up first (the installed update.sh Phase-1 backup also runs, but take an out-of-band copy of
   `/etc/conduit-cc/` and note the current version).
2. Download to the Pi: `ccc-0.3.15-aarch64.tar.gz`, `ccc-0.3.15.manifest.json`,
   `ccc-0.3.15.manifest.json.sig`, and obtain the publisher `allowed_signers` out-of-band.
3. Verify BEFORE extracting (see clean-install-verify.md), platform `aarch64`.
4. Extract the verified artifact; run `sudo bash update.sh --source <verified-tree>`. The aarch64
   dependency install is hash-locked to `requirements-aarch64.lock` (index, `--require-hashes`,
   `--only-binary`); no wheelhouse is involved.
5. Confirm `/api/health` reports 0.3.15 and services are active. If Phase 3/4 fails after downtime,
   the existing automatic rollback restores the prior version + pinned packages.

Do NOT conflate this with One-Click Update; it is an out-of-band, operator-trusted migration.
