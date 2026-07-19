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
the shared `release/oci_manifest.py` gate with the STORE-AGNOSTIC runtime-identity binding
(`runtime_image_id` == manifest digest on the containerd store / == config digest on the legacy
store; exactly one relationship, both/neither → fail closed; mode recorded + reused by Phase B).
Manifest capture uses `docker save` → detected archive transport → `skopeo inspect --raw` (no
`docker-daemon:` transport), with a fail-fast interop smoke test before the build (the digest-pinned
base is a multi-arch OCI index, accepted under `--allow-index` and bound to its index digest) and
atomic evidence writes. Empirically confirmed on the RPi2 (Docker 29 containerd store): built-image
`.Id` == manifest digest. See `release/builder/README.md`.
The produced `provenance/wheelhouse-armv7.json` binds recipe/base/manifest/environment and is
validated by the producer before signing.

**5b. Commit the build-independent locks PRE-TAG.** Generate with `release/gen_locks.py`:
`requirements-aarch64.lock` (PyPI aarch64 wheels: `pip download --only-binary=:all: -r requirements.txt`)
and `requirements-armv7-solution.lock` (PyPI sdists: `pip download --no-binary=:all: -r requirements.txt`)
— the **durable 30-pin armv7 solution**, which is the generator INPUT, not the build lock. Commit both
at the repo root. Do NOT hand-write or `gen_locks`-generate `requirements-armv7-build.lock`: under the
dual-origin model (5b-lifecycle below) it is a DERIVED six-entry output of `gen_active_inputs.py` and
must not exist until that generator has been run. Also commit the **three active builder inputs** —
`release/builder/{apt-packages.list,rustup-init.sha256,requirements-build-backends.lock}` — with
real validated values (never the `.example` placeholders); `validate_builder_inputs` and CI must
accept them. Also commit `release/builder/requirements-extractor-tools.{in,lock}` (the pinned tomli
parser) before running extraction. Also commit `release/builder/requirements-build-backends.source-allowlist` (backends with no official target wheel, e.g. `cffi`); the generation gate must prove no compatible wheel exists (drift fails closed) and the image installs backends in two ordered `--require-hashes` passes. Let CI go green, THEN tag `vX.Y.Z` (two-commit sequence). The build-DEPENDENT `requirements-armv7.lock` (resulting wheel hashes) is NOT
committed; it is produced with the wheelhouse and passed at build time via `--armv7-runtime-lock`
(injected + digest-bound). Do NOT commit placeholder/0.0.0 locks (release-input gate).

**5b-lifecycle (v0.3.17 reuse-first, ADR-0003 Amendment A5) — the THREE lock roles, in order.**
The armv7 wheelhouse is DUAL-ORIGIN, and exactly three files carry distinct, non-interchangeable roles:

| file | role | arity | origin |
|---|---|---|---|
| `requirements-armv7-solution.lock` | durable authoritative solution (generator INPUT) | 30 pins | committed by Owner (`gen_locks.py`) |
| `requirements-armv7-build.lock` | DERIVED source-build partition | exactly 6 | output of `gen_active_inputs.py` |
| `release/builder/armv7-reuse-authz.json` | DERIVED reuse partition | exactly 24 | output of `gen_active_inputs.py` |

The two derived files are DISJOINT and their union must equal the 30-package runtime closure
(partition invariant, enforced in `produce_release` and `release_preflight`). They are co-produced and
committed ATOMICALLY, which yields exactly two legal repository states — enforced as a state machine:

1. **pre-generation** — BOTH derived files ABSENT. The only valid state before the Owner runs the
   generator. `python -m release.preflight` (dev mode) is green here; release mode is red.
2. **generated** — BOTH present, validated as the exact disjoint 6+24=30 partition. Green in both modes.
3. **half-state** — exactly one present. INVALID IN EVERY MODE; the preflight fails closed with
   "derived active inputs are not atomic". This is what a partial commit looks like: commit both or neither.

During the
CONNECTED pre-tag phase, acquire the 24 wheels with `release/builder/acquire_reuse_wheels.py --reuse-authz
release/builder/armv7-reuse-authz.json --target-tags release/builder/target-supported-tags.txt --bundle
<off-tree-bundle>` (official PyPI only, cache-isolated, exactly-24, live `requires_python` re-checked,
final-redirect origin re-checked; publishes ONE atomic bundle `<bundle>/{wheels/,acquisition-record.json}`,
off-tree, never committed, never a standalone asset). Transfer the verified bundle to the RPi2; Phase B
re-verifies it OFFLINE and merges 24 reused + 6 built into the exact 30-wheel wheelhouse
(`build-wheelhouse-offline.sh --reuse-authz release/builder/armv7-reuse-authz.json --reuse-store
<bundle>/wheels`).

**5b-generate (co-producer, atomic Owner commit).** Do NOT hand-write the two active inputs. Generate
them together with `release/builder/gen_active_inputs.py` from hash-gated evidence and commit both
ATOMICALLY after reviewing `generation-record.json`:

    python3 release/builder/gen_active_inputs.py \
      --pypi-metadata <pypi-metadata.json>   --pypi-metadata-sha256 <sha> \
      --target-tags   release/builder/target-supported-tags.txt --target-tags-sha256 <sha> \
      --six-acquisition-record <six.json>    --six-acquisition-record-sha256 <sha> \
      --solution-lock requirements-armv7-solution.lock --solution-lock-sha256 <sha> \
      --out-bundle <staging-bundle>

The committed sanitized `release/builder/target-supported-tags.txt` (exactly 495 ordered tags, no
host header) is the single mandatory target-compat source at ALL boundaries; its sha256 is bound in
provenance (`authorizers.target_tags_sha256`) and recomputed by the producer. The connected acquisition
publishes ONE atomic bundle `<bundle>/{wheels/,acquisition-record.json}` (filename-addressed,
hash-verified). Phase B publishes ONE atomic bundle `<out>/bundle/{wheelhouse-armhf/,wheelhouse-armv7.json,
requirements-armv7.lock,build-evidence.json}`, enforcing the 6/24/30 approved-six policy and generating
`requirements-armv7.lock` from the final validated wheelhouse (exact 30-way bijection) BEFORE publication.
Pass the bundle's `wheelhouse-armhf/`, `wheelhouse-armv7.json`, and `requirements-armv7.lock` to
`produce_release` on the Owner PC (signing occurs only there; never on the RPi2).

**5b-solution (durable 30-pin solution — the generator input).** The authoritative dual-origin input
is `requirements-armv7-solution.lock` at the repo root: the full **30-package** runtime closure pinned
with hashes. It is DURABLE (changes only when the closure changes), and is the single source the
co-producer partitions into the derived six-entry `requirements-armv7-build.lock` (source-built) and the
24-entry `release/builder/armv7-reuse-authz.json` (reused). Do NOT confuse the two: the solution lock is
an *input* (30 pins); the build lock is a *derived output* (6 pins). `validate_armv7_solution` (in
`produce_release` and the preflight) proves the solution is a valid 30-pin closure and that it partitions
exactly into the approved six source builds + the 24 reused wheels with agreeing versions.

**5b-tags (target-tag derivation lifecycle).** The committed `release/builder/target-supported-tags.txt`
(exactly 495 ordered tags) is DERIVED once from the raw RPi2 supported-tag evidence with
`release/builder/sanitize_target_tags.py --raw-evidence <raw> --expected-raw-sha256 <sha> --out-bundle
<off-tree>`. The trust-boundary order is strict: it first verifies `sha256(raw_bytes)` **byte-exactly**
against `--expected-raw-sha256` — that digest identifies the EXACT acquired artifact, not an equivalence
class, so a CRLF-mutated copy is a DIFFERENT artifact and fails even though its normalized content is
identical — and only then parses the now-trusted content, validates the target identity (CPython 3.10.12 /
armv7l / glibc 2.35), extracts ONLY the content between the `SUPPORTED_TAGS_BEGIN/END` markers, and records
the three distinct identities in `derivation-record.json`: `raw_evidence_sha256` (exact bytes as acquired),
`raw_evidence_lf_sha256` (audit reference), and `sanitized_sha256` (the derived artifact).
This lifecycle is SEPARATE from the per-release co-producer (tags change only when the target ABI changes).
Hashing of the COMMITTED artifact is **LF-canonical** (`reuse_authz.canonical_lf`) so a Windows CRLF
checkout produces the identical digest as a Unix checkout; a narrow `.gitattributes` rule keeps it LF on
disk. Note the deliberate asymmetry: off-tree raw evidence is byte-exact, the committed artifact is
LF-canonical (git normalizes line endings on checkout, raw evidence is never in git).

**5b-preflight (authoritative readiness gate — CI + pre-tag).** Before tagging, run
`python -m release.preflight --repo . --require-present`. This is the SAME validation `produce_release`
performs (builder inputs + durable solution + 495-tag artifact + six-entry build lock + 24-entry reuse
authorization + full partition + authz-vs-target-tags), so CI cannot report ready while the producer
would reject. Target tags go through the ONE canonical validator
(`ccc_release.validate_target_tags` → `reuse_authz.parse_target_tags`) that `produce_release` also calls.

**The authoritative PRE-TAG gate is the Owner's exact-commit local run**, because it is the only check
that happens before the tag exists:

    git checkout <candidate-sha> && git rev-parse HEAD      # confirm the exact candidate
    python -m release.preflight --repo . --require-present  # must exit 0 BEFORE tagging

Only after that is green does the Owner create the tag. CI plays three clearly-bounded roles:

* `lint` job, every branch/PR — **dev mode** (`python -m release.preflight`), which permits the legitimate
  pre-generation state and catches drift in the committed durable inputs early.
* `release-preflight` job, `workflow_dispatch` **with `candidate_sha`** — the CI form of the pre-tag gate:
  it checks out that exact commit, asserts `HEAD == candidate_sha`, and runs release mode. This is the
  only CI run that constitutes evidence bound to a specific release candidate.
* `release-preflight` job, version-tag push — **post-tag secondary defense only.** It runs after the tag
  already exists and therefore CANNOT prevent an invalid tag from being created; it can only detect one so
  the Owner can retract it. Do not treat a green tag-triggered run as the pre-tag gate.

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
