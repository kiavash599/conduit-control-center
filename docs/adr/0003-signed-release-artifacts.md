# ADR-0003: Signed Release Artifacts and On-Device Verification

**Status:** Accepted
**Date:** 2026-07-07
**Deciders:** CCC maintainers
**Supersedes:** —   **Superseded by:** —
**Relates to:** ADR-0001 (Trusted Update Engine — realizes its artifact-integrity expectation), ADR-0002 (Update Payload Specification — *planned*)

## Core Principle

> **A device installs only what a trusted publisher signed, verified fail-closed before any privileged action. The artifact is content-defined; every consumer trusts content, not timestamps.**

This is the orientation for the signing layer. Where it and the Architectural Invariants below appear to differ, the **invariants govern** — they are the enforceable contract.

## Context

ADR-0001 established that *policy authorizes, the engine executes, and the payload never commands privileged control flow.* It deliberately left **how a release proves its authenticity** to a later decision (artifact signing was deferred). CCC is a censorship-circumvention tool whose threat model includes **targeted supply-chain attacks**: a malicious or compromised release must never obtain privileged execution on a device. One-Click Update, being remote and unattended on constrained hardware, makes this concrete — the device must be able to reject an unauthentic update **before** any privileged step, and must do so identically every time regardless of the delivery channel (GitHub, mirror, or manual copy).

Forces: authenticity and integrity of releases; reproducibility (so a digest is a stable identity); a trust anchor that lives on the device and is not shippable with the payload; fail-closed behaviour; and non-authorizing auditability of the verification and deployment.

## Decision

1. **Canonical Release Artifact.** Releases are packed into a deterministic, content-fixed `.tar.gz`: members sorted, `mtime=0`, fixed mode/uid/gid and empty owner names, gzip header `mtime=0`. Identical content yields identical bytes, so the **content digest (SHA-256)** is a stable identity. Artifacts are built **only from a committed, tagged source** (`commit → tag → --git-ref`).
2. **Signed Object.** A manifest (`format_version = 1`) binds `{product, version, artifact name, digest{algorithm, value}, compatibility{platform, recommended_conduit_core}}` and is signed with an **SSH Ed25519** key using SSHSIG, namespace **`ccc-update-manifest`**, publisher identity **`conduit-control-center-publisher`**.
3. **On-Device Trust Store (M2).** The device holds an `allowed_signers` trust anchor at `/opt/conduit-cc/trust/allowed_signers` (root-owned, service-readable, `root:conduit-cc 0750`). It is **provisioned out-of-band** and is **never** shipped inside a release artifact.
4. **Fail-Closed Verification.** The privileged update helper verifies the manifest signature against the on-device trust store **before extraction and before the version gate**. Any verification, product-scope, or integrity failure aborts with **no** privileged action.
5. **Non-Authorizing Observability (Phase-B).** The helper writes append-only audit records for update outcomes (`accepted`, `applied`, `reverted`, and the reject/failure taxonomy) with **allowlist redaction** (no trust material) to a root-owned audit log (`/var/log/conduit-cc-audit/update-audit.jsonl`, `root:conduit-cc 0640`). Audit is best-effort and **never** alters the verifier result, exit code, status, deployment, or rollback.
6. **Deterministic-Artifact Consumer Invariant.** Because artifacts are `mtime=0`, every consumer that deploys or compares artifact content **must decide by content (hash), never by size+mtime** (realized by `rsync --checksum`; see CCC-CAMP-0001 / CP-001).

## Architectural Invariants (enforceable)

- **I1** — No privileged action occurs before a passing fail-closed signature verification against the on-device trust store.
- **I2** — Trust material (private keys, `allowed_signers`) is never embedded in a release artifact and never appears in audit output.
- **I3** — The artifact is deterministic and content-addressed; its SHA-256 digest is its identity.
- **I4** — Releases are built only from a committed, tagged source (provenance chain intact).
- **I5** — Observability is non-authorizing: audit or logging never alters a trust or control decision.
- **I6** — Consumers of the deterministic artifact compare by content, not timestamps.

## Normative constants

`PRODUCT = "conduit-control-center"` · `DIGEST_ALGORITHM = "sha256"` · `SSHSIG_NAMESPACE = "ccc-update-manifest"` · `PUBLISHER_IDENTITY = "conduit-control-center-publisher"` · manifest `format_version = 1`.

## Consequences

Positive: releases are authenticated end-to-end; a compromised channel cannot obtain privileged execution; digests give reproducible identity; deployment and rollback are auditable without trusting the audit for control. Costs: more release ceremony (build → sign → verify → publish → verify-published) — mitigated by the registered **Owner Operations Toolkit** backlog item; and the determinism/`mtime=0` property requires content-based consumers (**I6**), a cross-layer discipline surfaced during v0.3.14 (the same `mtime` tie affected both CPython `.pyc` validation and rsync's quick-check).

## Relationship to other ADRs

- **ADR-0001 (Trusted Update Engine):** owns the engine invariants (policy authorizes, engine executes). ADR-0003 **realizes** ADR-0001's artifact-integrity expectation, which ADR-0001 deferred.
- **ADR-0002 (Update Payload Specification, planned):** will formalize the full payload/manifest schema, capability and migration declarations, and compatibility fields. ADR-0003 fixes the **signing, verification, and trust** decision and the current manifest fields; the two are complementary.


---

## Amendment A1 — V2 Platform-Artifact Release Model (accepted)

**Status:** Accepted · **Supersedes:** the V1 single-artifact manifest for all NEW releases.

**Context.** armv7l (RPi2) requires an offline wheelhouse (no PyPI armv7 wheels for the Rust/C
closure); aarch64 does not. There are no external v0.3.14 clients, so no V1 One-Click compatibility
is required and the trusted surface can be narrowed.

**Decision.**
1. **One version, two deterministic platform artifacts, BOTH mandatory:** `ccc-X.Y.Z-aarch64.tar.gz`
   (source/runtime, no wheelhouse) and `ccc-X.Y.Z-armv7l.tar.gz` (same source + embedded
   `wheelhouse-armhf/` + `provenance/wheelhouse-armv7.json`).
2. **One canonical V2 manifest, one signature** (`format_version = 2`, existing SSHSIG namespace/
   identity) binding: product, version, `source{vcs,commit,tag}`, the per-platform artifact set
   (each `name` + sha256 `digest`; armv7l a strict wheelhouse block), and `dependency_locks`
   (requirements.txt + both platform-lock sha256). Canonical bytes = signed bytes; `artifacts[]`
   sorted by platform.
3. **Verifier is V2-only** (`SUPPORTED_MANIFEST_FORMATS = {2}`) — V1 is rejected (removes a
   platform-unbound / format-downgrade bypass).
4. **Helper is the platform authority.** It detects the real host `uname -m` independently and binds
   the received bytes to the SIGNED entry for THAT platform. **Digest-to-platform-entry binding is
   the ROOT authorization invariant; there is NO fallback and unknown platforms fail closed.** The
   3-record frame carries no independent filename, so there is **no received-name check**; the
   manifest's per-entry `name` is a signed self-consistency field (`ccc-<version>-<platform>.tar.gz`),
   and filename use by the backend is a non-authorizing discovery convenience.
5. **Canonical artifact = deterministic composition** of `{tagged source tree}` + a fixed,
   content-addressed wheelhouse (armv7l only). This refines Decision §1/I3/I4: reproducibility is
   defined GIVEN both inputs. **Lifecycle (no tag/provenance circularity):** the build-INDEPENDENT locks are
   COMMITTED PRE-TAG (PyPI sdist hashes for armv7 sources; PyPI wheel
   hashes for aarch64); the wheelhouse and `provenance/wheelhouse-armv7.json` (per-wheel
   sdist->builder->wheel chain) are POST-TAG content-addressed inputs, never committed, injected into
   the armv7 artifact and explicitly digest-bound (`bundle_sha256`, `provenance_sha256`).
6. **Two-lock dependency install (parity in install.sh + update.sh):** armv7l installs offline,
   `--no-index --only-binary=:all: --require-hashes -r requirements-armv7.lock --find-links <wheelhouse>`;
   aarch64 installs from the index but hash-locked, `--require-hashes --only-binary=:all: -r
   requirements-aarch64.lock`. `requirements.txt` stays bounds-based; CI asserts both locks remain
   valid solutions of it.
7. **Verify-before-extract bootstrap for clean installs:** `deployment/bin/ccc-verify-release`
   (reusing this verifier) authenticates the manifest + platform digest BEFORE extraction, using a
   publisher anchor obtained OUT-OF-BAND. install.sh and the embedded wheelhouse are consumed only
   from the verified tree. install.sh gains NO circular self-verification.
8. **Privileged wheelhouse isolation:** the update worker pins `CCC_WHEELHOUSE_DIR` to the verified
   tree on armv7l and strips any inherited value, so an injected env var cannot redirect privileged
   pip. The manual/test override remains available outside the One-Click worker.

**New invariants.** I7 both platform artifacts mandatory for a full release; I8 digest-to-platform is
the root platform authorization (no fallback, unknown fails closed); I9 dependency binding
(requirements.txt + both platform locks) is mandatory in every signed manifest; I10 no source build,
index, cache, or network fallback on armv7l; I11 secret exclusion + no-NUL-in-text enforced pre-sign.


### A1 refinements (post-review hardening)

- **Digests computed from canonical bytes.** The producer computes `requirements.txt` and every
  platform-lock sha256 from the CANONICAL (LF-normalized) committed bytes in the artifact tree, not
  from caller input, so binding is independent of checkout line endings. Caller values are optional
  expected-value cross-checks only.
- **Coherent, non-circular lock lifecycle.** Two locks are build-INDEPENDENT and **committed PRE-TAG**:
  `requirements-aarch64.lock` (PyPI aarch64 **wheel** hashes) and `requirements-armv7-build.lock`
  (PyPI **sdist** hashes = the pre-build authorization for `pip download --require-hashes` before
  building armv7 wheels). The build-DEPENDENT `requirements-armv7.lock` (resulting armv7 **wheel**
  hashes) **cannot** be committed pre-tag; it is a **POST-TAG content-addressed release input**,
  injected into the armv7 artifact (at the root, so update/install read it) alongside the wheelhouse and
  provenance, and digest-bound (`dependency_locks.armv7_lock_sha256`, computed by the producer from the
  injected bytes). All four digests (requirements + 3 locks) are mandatory in `dependency_locks`; the
  armv7 wheelhouse block additionally binds `build_lock_sha256`. Distinct build-input vs runtime-wheel
  locks remove hash-any-match ambiguity.
- **Build-input authorization is bound to provenance.** Every provenance source record (`sdist_name`,
  `sdist_sha256`) is cross-checked at produce time against the canonical `requirements-armv7-build.lock`:
  an sdist absent from the build lock, or with a name/version/hash mismatch, or a missing/extra/duplicate
  source record, fails closed. An unapproved sdist cannot be built and then legitimized in provenance.
- **Signed top-level allowlist.** Each artifact entry binds `top_level` (the exact set of top-level
  members); the helper rejects any member whose top-level is not in the signed set (so a non-armv7l
  payload cannot carry a wheelhouse and no arbitrary root is ever accepted).
- **Disk gate fails closed.** The pre-extraction free-space check fails closed on BOTH insufficient
  space AND an inability to query it; the extractall OSError handler is additional defense-in-depth.
- **Strict provenance.** `provenance/wheelhouse-armv7.json` must declare `builder{identity,image_digest}`,
  `bundle{sha256=embedded bundle digest}`, and a `wheels[]` list whose records (`sdist_name`,
  `sdist_sha256`, `wheel_filename`, `wheel_sha256`) match the embedded wheelhouse EXACTLY and its
  SHA256SUMS (no missing/extra/duplicate); it is cross-checked at produce time.
- **No platform override.** `ccc-verify-release` has no `--platform` flag; the real host platform is
  the only source (a test seam `_host_platform()` is monkeypatchable).
- **Extraction top-level isolation + honest disk handling.** A non-armv7l payload carrying any
  `wheelhouse-armhf/` member is rejected; disk exhaustion fails closed on the extraction call itself
  (the free-space pre-check is best-effort, not the guarantee).
- **Secret scan.** Only true binary payloads (by extension) are exempt from the marker + no-NUL-in-text
  scan; extensionless executables and textual wheelhouse metadata (e.g. SHA256SUMS) ARE scanned.
- **Release-input gate.** The active root `requirements-*.lock` are produced by the controlled build and
  committed pre-tag; until then they are absent and the hash-locked install fails closed. Do NOT merge
  with placeholder locks (schema fixtures live under `release/lock-schema/`).


### A1 refinement — declared, bound builder environment (v0.3.17)

The armv7 wheelhouse builder is now a committed, canonical OCI recipe
(`release/builder/Containerfile`, base pinned by digest, toolchain + PEP 517 backends
declared/pinned) and the provenance binds the environment fail-closed:
`recipe_sha256` (== the committed recipe, covered by `source.commit`), `base_image_digest`,
`image_manifest_digest` (the OCI image MANIFEST digest — NOT Docker's local image/config
`image_id`, which is recorded only as evidence and must differ), and an `environment`
manifest + `environment_sha256`. The legacy single `image_digest` field is rejected. The
build runs in two phases with a hard boundary: connected, pinned image construction, then
an offline (`--network=none`) wheel build consuming only authorized, hash-verified
sdists/locks as read-only inputs; Phase B selects the image by its immutable local id after
re-verifying the tag still maps to the captured id + manifest digest. A locally-built image
ID alone no longer satisfies provenance (the manifest digest is recomputed from the raw OCI
manifest bytes).

**Target-libc policy (finding 8):** the builder base MUST be Ubuntu 22.04 (Jammy) armhf so
the glibc baseline is no newer than the production RPi2 target; the provenance records
`environment.glibc` and the producer rejects a glibc newer than the target. Final native
wheels must additionally be import-tested in a clean Ubuntu 22.04 armhf environment before
release. Connected construction inputs are pinned/verified (base by digest, apt by
`name=version`, `rustup-init` by sha256, backends `--require-hashes`); full bit-for-bit
image reproducibility is not claimed — the environment is content-bound and auditable.

**Amendment A2 (refined builder-provenance binding).** Four boundaries are hardened:
(1) a single shared, stdlib-only validator (`release/oci_manifest.py`) parses the raw OCI/
Docker manifest at Phase A, the wheelhouse self-check, and the producer, enforcing the
single-image schema-2/OCI shape and binding `manifest.config.digest == image_id` (the config
id Phase B executes); (2) the recorded APT environment is architecture-aware and
execution-bound — `${binary:Package}` identity, installed-only status, `apt_architecture ==
armhf`, and every authorized pin proven present at the byte-exact version; (3) the offline
build enforces a mandatory, host-validated RAM/swap/host-reserve contract (reserve-protecting,
cgroup-capability-checked, evidence external only); (4) build-backend extraction uses a real
TOML parser (stdlib `tomllib` or the hash-pinned `tomli` bootstrapped into an isolated venv;
no regex fallback), strict-UTF-8 decoding, and an unambiguous sdist layout. The extractor-tools
lock is bound into the signed source chain.

**Amendment A3 (authorized backend-sdist allowlist).** Build backends with no official wheel for
the exact target (currently `cffi`) install from a hash-pinned sdist authorized by the committed,
minimal `requirements-build-backends.source-allowlist`. A generation gate proves, via pip's
complete effective compatibility-tag set, that each allowlisted package has no compatible official
wheel (drift fails closed) and records external evidence. The image installs in two ordered,
disjoint passes (wheels first `--only-binary --no-deps`, then the allowlisted sdists `--no-binary
--no-build-isolation --no-deps`, both `--require-hashes`) — no build isolation, no implicit
dependency resolution/fetch. The allowlist sha256 is bound in the builder provenance and required
+ validated by the producer. The SRT signing model and V2 platform architecture are unchanged.
