# armv7 wheelhouse builder — controlled ceremony (ADR-0003 A1)

Declares the armv7l wheel build environment and binds it into the signed release
provenance so it is auditable, not inherited from the RPi2 host. Connected inputs are
pinned/verified (below); full bit-for-bit image reproducibility is not claimed.

## Trust boundaries

- **Build host** (dedicated old-image RPi2): produces wheels + provenance. It **never
  holds the signing key**. Docker is transient (start → ceremony → stop/disable); the
  operator uses `sudo` (empty, unused `docker` group).
- **Signing / SRT**: a separate ceremony that consumes the wheelhouse + provenance +
  locks and produces the one signed manifest. The private key never touches the builder.
- **Device**: verifies the signed manifest; the builder provenance is pre-sign
  supply-chain **evidence** (bound by the artifact digest + signature), not device-enforced.

## Prerequisites (preflight-verified, never auto-installed)

Both scripts **fail closed** (exit 3) if a required tool is missing and print an
out-of-band install hint; **they never install anything themselves**. Required on the
build host: `docker` (engine), `skopeo` (Jammy: `skopeo 1.4.1-ubuntu`, `sudo apt-get
install skopeo`), `sha256sum` (coreutils), `python3`. `skopeo` is an **explicit**
dependency — it is *not* present by default on RPi2 — used only to read the raw OCI
image manifest; the operator installs it deliberately before the ceremony. Phase A
records `skopeo --version` in the evidence.

## Two phases

**Phase A — connected construction** (`build-builder-image.sh`, network on):
builds the image from `Containerfile` with a **digest-pinned** base
(`--base-image ...@sha256:<64hex>`; unpinned fails closed) and captures the raw image manifest
via the shared, fail-closed capture contract (`manifest-capture.lib.sh`):
`docker save` → **detected archive transport** (`oci-archive` or `docker-archive`, chosen
explicitly and recorded in evidence) → `skopeo inspect --raw <transport>:<tar>`. The obsolete
`skopeo … docker-daemon:` transport is **removed**: modern Docker rejects Jammy skopeo's old
client API version, and the local-archive flow needs no daemon-API negotiation. A **fail-fast
interoperability smoke test** runs this exact path against the digest-pinned base image *before*
the expensive build, so a tooling mismatch aborts in seconds. The captured manifest is validated
by the shared `release/oci_manifest.py` gate with a **store-agnostic runtime-identity model**.
Docker's `.Id` (`runtime_image_id`) differs by image store, so the gate binds whichever
relationship holds: **containerd** (Docker 29 default) — `runtime_image_id == image_manifest_digest`;
**legacy** graphdriver — `runtime_image_id == image_config_digest` and `!= image_manifest_digest`.
Exactly one may hold (mutually exclusive); neither → fail closed. The derived `image_identity_mode`
is recorded and re-derived at each boundary, and a declared mode that disagrees (mode confusion) is
rejected. Provenance carries `runtime_image_id`, `image_manifest_digest`, `image_config_digest`,
`image_identity_mode` (the ambiguous single `image_id` is gone). Phase B re-captures through the
**same** contract, reusing the recorded transport **and mode**, and runs the container by
`runtime_image_id`. `image-manifest.json` and `builder-inputs.kv` are written **atomically**
(temp → validate → rename). Phase A also captures the environment manifest.

**`builder-inputs.kv` is DATA, never code.** The Phase-A → Phase-B handoff is a strict `KEY=VALUE`
file (`.kv`, not `.env`) that is **never** `source`d/`.`-dotted/`eval`d. Both phases parse it only
through the stdlib reader `read_builder_inputs.py`, which enforces the exact 16-key schema and
per-field constraints (digests, hashes, identity mode, transport, image tag, builder identity,
controlled absolute paths), rejects NUL/CR/CRLF/missing-final-LF/blank/comment/continuation/
duplicate/foreign/missing records, and emits validated NUL-delimited records only when the whole
file is valid. Phase A validates its temp file with the same reader before atomic publication, so
invalid producer output is never published. This closes the shell command-execution class (`$()`,
backticks, quotes, newline-smuggled assignments) that sourcing an evidence file would expose.

**OCI-index base vs single-image builder / RPi2 empirical basis.** The digest-pinned base is a
multi-arch OCI **index**, so the pre-build interop smoke test runs with `--allow-index` and binds
only `runtime_image_id == index digest`; the full single-image + config-descriptor + identity gate
applies to the built builder image. Empirically confirmed on the RPi2 (Docker 29 containerd store):
base index `.Id` == index digest; built image `.Id` == manifest digest; both `!= config digest`.

**Phase B — offline wheel build** (`build-wheelhouse-offline.sh`, `--network=none`):
runs `release/build_wheelhouse.py` inside the Phase-A image with the authorized,
hash-verified sdists + `requirements-armv7-build.lock` mounted **read-only**, a single
bounded **writable** output, and no network. `pip wheel --no-deps --no-build-isolation
--no-index` cannot fetch anything; every build backend must already be in the image.

**Image-context proof (Phase A → Phase B, byte-level).** Exactly six committed files construct the
image. The `Containerfile` `COPY`s five of them to `/opt/ccc` (lines 33/44/61/62/63):
`apt-packages.list`, `rustup-init.sha256`, `requirements-build-backends.lock`,
`requirements-build-backends.source-allowlist`, `partition_backends.py`. Because those copies remain
inside the image, `build_wheelhouse.py` re-reads them from `/opt/ccc` **before** environment probing,
reuse ingestion, or any source build, and requires each to hash identically (LF-canonical) to the
committed file — a missing, unreadable, non-regular, symlinked, or differing entry fails closed and
names the exact canonical path. The sixth file, the recipe, is not copied in; the shell binds it by
comparing Phase A's recorded `CCC_RECIPE_SHA256` with the committed `Containerfile` before Docker
runs. All six hashes plus their aggregate are recorded in `provenance.builder` and recomputed by the
producer from the committed bytes.

Note the distinction: the installed-APT and effective-backend-version checks are **semantic** checks
of the resulting installed state (different source bytes can give the same effective state); the
image-context proof is the **exact byte-level** binding. `builder-inputs.kv` keeps its existing exact
16-key schema — the proof adds no evidence key, so an unchanged build context lets an already-attested
image be reused without rebuilding. A Phase A re-run is required only if the proof fails.

**Dual-origin wheelhouse (v0.3.17, ADR-0003 Amendment A5).** The armv7 wheelhouse is the exact
30-package closure from two release-time origins: **24 reused** official PyPI wheels + **6
source-built** wheels (`cffi, httptools, markupsafe, psutil, pyyaml, uvloop`). The six sdists are
authorized by the (six-entry) `requirements-armv7-build.lock`; the 24 wheels by
`armv7-reuse-authz.json` (exact identity: name/version/filename/sha256/tags/official origin —
kept out of pip grammar). The two active inputs (six-entry build lock + 24-entry `armv7-reuse-authz.json`) are generated
together by the controlled co-producer `gen_active_inputs.py` from hash-gated evidence and committed
atomically. Target compatibility is mandatory and independent at every boundary via the committed
sanitized 495-tag artifact `target-supported-tags.txt` (bound in provenance by
`authorizers.target_tags_sha256`). The reuse store is acquired in a connected pre-tag phase
(`acquire_reuse_wheels.py`, strict HTTPS official origins only, cache-isolated) and published as ONE
atomic **filename-addressed, hash-verified** bundle (`wheels/` + `acquisition-record.json`),
transferred, and re-verified **offline** (`--reuse-authz`/`--reuse-store`, mounted read-only,
exact-set) before merge. Phase B (the Python builder) enforces the 6/24/30 approved-six policy and
publishes ONE atomic bundle (`wheelhouse-armhf/` + provenance + generated `requirements-armv7.lock`
+ evidence) before returning. Provenance
records per-wheel `origin` and binds the reuse authorization; the partition (built ⊎ reused == the
30 runtime pins) is enforced fail-closed. The executable build scratch is the field-proven
`/tmp:rw,exec,...` (native `.so` import + native configure scripts need exec; noexec is not a
security boundary once the authorized build code runs). The environment recorder binds the
**effective** resolved backend version (not last-wins), recording shadows for audit. The device
install path is origin-agnostic and unchanged.

## Cybersecurity controls (Phase B container)

Non-root (`--user 1000:1000`); `--cap-drop=ALL`; `--security-opt=no-new-privileges`;
`--network=none`; no published ports; `--read-only` rootfs + a bounded `--tmpfs`;
read-only input mounts; one bounded writable output; **no secrets/keys/.env**; transient
daemon (stop + disable `docker`/`docker.socket`/`containerd` after). Capture evidence,
then tear down containers and prune the build cache.

## Target-libc compatibility (finding 8)

The base MUST be **Ubuntu 22.04 (Jammy) armhf** (glibc 2.35) — NOT Debian Bookworm (newer
glibc), whose wheels could import in the builder yet fail on the Jammy RPi2. `environment.glibc`
is recorded and the producer rejects a glibc newer than the target. Before release, import-test
the final native wheels in a clean Jammy armhf environment.

## Pinned/verified connected inputs (finding 7)

`apt-packages.list` pins exact `name=version` (unpinned fails the build); `rustup-init` is
sha256-verified against `rustup-init.sha256`; PEP 517 backends install `--require-hashes` from
`requirements-build-backends.lock` (empty/comment-only fails closed). All three input files
(`apt-packages.list`, `rustup-init.sha256`, `requirements-build-backends.lock`) are bound by
sha256 in provenance; the backend lock is additionally cross-checked against the recorded
`environment.build_backends`.

**Lifecycle (finding 6).** These active inputs may be legitimately **absent** before the
builder gate is run (pre-tag development) — `validate_builder_inputs(require_present=False)`
accepts that. When **present** they must pass strict semantic validation (no placeholder
tokens, `name=version`-pinned apt, a real non-zero rustup sha256, a non-empty non-`0.0.0`
backend lock). The `.example` templates under `release/lock-schema/` are **never** read as
active inputs. **Release/tag production requires all three** (`require_present=True`) and the
producer re-validates them at the signing boundary. Recommended **two-commit sequence**:
(1) commit the three real, validated inputs and let CI go green; (2) then tag `vX.Y.Z`. Do
not commit placeholder/example values as the active files.

## Resource policy (RPi2, 917 MiB, no permanent swap)

The Rust/C builds (pydantic-core, cryptography, bcrypt≥4.1, watchfiles) exceed 917 MiB;
a controlled resource envelope is required:
- add a **temporary** swapfile for the ceremony; `swapoff` + wipe (`fstrim`/`shred`) +
  delete afterward. It holds only open-source build memory (no secrets — the key is never
  present), so confidentiality risk is low; wipe for hygiene.
- container limits are **mandatory, explicit, host-validated** (no defaults): `--ram`
  (RAM hard cap, Docker `--memory`), `--swap` (additional swap allowance), `--host-reserve`
  (physical RAM held back for OS/SSH/Docker/monitoring), `--resource-evidence <path>`. Before
  any container starts the script reads MemTotal/MemAvailable/SwapTotal/SwapFree + active swap
  devices + cgroup mode and requires: `RAM + host-reserve <= MemTotal` (so a container limit
  can never consume the reserve), `RAM <= MemAvailable`, and for nonzero swap `swap <=
  SwapTotal`, `swap <= SwapFree`, and an enforceable cgroup/Docker swap limit. `--memory-swap`
  is RAM+swap (equal to `--memory` when `--swap 0`, i.e. swap explicitly disabled). All
  point-in-time evidence (timestamp, MemAvailable, SwapFree, chosen limits) is written to the
  **external** `--resource-evidence` file only -- never into signed artifact bytes. The script
  never creates/enables/disables/wipes/removes swap. Also `--pids-limit`; image env pins
  `CARGO_BUILD_JOBS=1`, `MAKEFLAGS=-j1`, `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1`.
- RAM/swap affect build *success*, not wheel *bytes*; OOM yields a clean `pip wheel`
  failure, never a corrupt wheel.

## Evidence captured

`Containerfile` (committed; `recipe_sha256` bound in provenance), base image digest, the
store-agnostic runtime identity (`runtime_image_id`, `image_manifest_digest`,
`image_config_digest`, `image_identity_mode`), the archive-capture transport, the environment
manifest + its sha256, the build-input lock + verified sdist hashes, the per-wheel sdist→wheel
mapping + logs, and `SHA256SUMS`. The **raw manifest bytes** are embedded into the signed armv7
artifact at `provenance/image-manifest.json`; the producer (and any later auditor) independently
re-derives the identity mode and recomputes the manifest/config digests, binding
`runtime_image_id` to the captured manifest under the recorded mode before signing.

## PEP 517 build backends

**TOML parser bootstrap.** The extractor parses `pyproject.toml` with a real parser --
stdlib `tomllib` on Python 3.11+, otherwise `tomli` pinned by
`requirements-extractor-tools.lock` (generated from `requirements-extractor-tools.in` with
`release/gen_locks.py`) and installed into an **isolated** venv with `pip install
--require-hashes --no-deps`; the installed version is verified against the pin and recorded in
extraction evidence, and the lock is validated at the producer boundary and **bound by sha256 in
the builder provenance** (`extractor_tools_lock_sha256`). The `.in`<->lock relationship is a
**closed authorization**: the lock must pin exactly the requested closure — one hash-locked
`tomli` (which has no runtime dependencies) — and any extra package is rejected. The extraction
command is executable as written on both Python 3.11+ (stdlib `tomllib`) and 3.10 (auto-bootstrap):

```
python3 release/builder/extract_build_backends.py \
  --sdist-dir <sdists> --build-lock requirements-armv7-build.lock \
  --extractor-tools-in  release/builder/requirements-extractor-tools.in \
  --extractor-tools-lock release/builder/requirements-extractor-tools.lock \
  --bootstrap-venv <venv-dir> --evidence <evidence-file>
```

On 3.10 it validates the closure, builds the isolated venv, installs only the authorized
hash-locked `tomli`, records evidence, and **re-executes** extraction with that interpreter (a
single `execv`, guarded by an internal `--isolated` sentinel so there is no recursion). There is
**no regex fallback**: no parser, malformed TOML, or invalid UTF-8 all
fail closed. `pyproject.toml` is decoded strict-UTF-8 and the sdist layout must be unambiguous
(exactly one root, at most one root-level `pyproject.toml`, no duplicate/unsafe members,
regular file only) -- never "first matching member wins".

`extract_build_backends.py --sdist-dir <sdists> --build-lock requirements-armv7-build.lock`
is driven by the committed, hash-pinned sdist lock (the authority for *which* sdists exist
and their sha256). Every sdist is hashed and matched to a lock pin **before** its archive is
opened; the tool fails closed on a malformed/empty lock, an unauthorized/extra file, a
missing pin, duplicate content, an unrecognized extension, or a hash mismatch — an exact
bijection with the lock. It reads both `.tar.*` and `.zip` sdists, and models the PEP 517
**legacy default** backend (`setuptools` + `wheel`) for any sdist that declares no
`[build-system].requires`. It prints the union of `[build-system] requires`; pin them (with
`release/gen_locks.py`) into `requirements-build-backends.lock`, which the image installs
`--require-hashes`. Until populated, the image build fails closed.

**Source-authorized backends (no official target wheel).** A backend with no official wheel for
the exact target (currently `cffi`, from cryptography's `[build-system].requires`) source-builds
from a **hash-pinned sdist** via the committed, minimal `requirements-build-backends.source-allowlist`
(strict PEP 503 names). Generation proves — using pip's **complete** effective compatibility-tag
set for the target, never a hand-enumerated list — that each allowlisted package has no compatible
official wheel, records the target tags + result to external evidence, and **fails on drift**. The
image installs in two ordered passes: `partition_backends.py` splits the lock into a disjoint WHEEL
partition (`--require-hashes --only-binary=:all: --no-deps`, installed FIRST so build deps exist)
and the SOURCE partition (`--require-hashes --no-binary=:all: --no-build-isolation --no-deps`) —
build isolation off, no implicit resolution/fetch, so only the authorized hash-pinned sdist is
fetched. The allowlist sha256 is bound in provenance and required by the producer.

## Not run here

No image pulled/built, no container run, no wheel built, no Docker started, no Pi
touched. These scripts are the Owner-gated ceremony; the real digests/hashes are supplied
during construction (never committed as placeholders).
