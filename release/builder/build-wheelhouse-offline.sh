#!/usr/bin/env bash
# Phase B (OFFLINE) -- build the armv7 wheelhouse with NO network. Owner-gated.
# Selects the EXACT Phase-A image by IMMUTABLE image id, after re-verifying that the
# tag still maps to the captured id AND manifest digest (no mutable-tag substitution
# window). Only authorized, hash-verified sdists/locks are mounted read-only; a single
# bounded writable output is mounted rw; non-root, all caps dropped, no-new-privileges,
# read-only rootfs + tmpfs scratch, resource-limited. No secrets/keys present.
#
# Resource contract (finding 3): --ram, --swap and --host-reserve are MANDATORY explicit
# inputs (no silent defaults). RAM is the container working-set cap; --host-reserve is the
# physical RAM held back for the OS/SSH/Docker/monitoring; --swap is the ADDITIONAL swap
# allowance (Docker --memory-swap = RAM + swap). The full host-capacity + cgroup-capability
# preflight runs BEFORE the first container starts and fails closed. All point-in-time
# evidence (MemAvailable, SwapFree, timestamp, ...) is written to an EXTERNAL evidence file
# only -- it is never injected into deterministic artifact bytes or provenance. The script
# never creates, enables, disables, wipes or removes swap.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
SDIST_DIR=""; BUILD_LOCK="${REPO}/requirements-armv7-build.lock"; OUT_DIR=""; PROV_OUT=""
INPUTS="${HERE}/evidence/builder-inputs.kv"
RAM=""; SWAP=""; HOST_RESERVE=""; RES_EVIDENCE=""
REUSE_AUTHZ=""; REUSE_STORE=""   # dual-origin: committed reuse authorization + read-only reuse store
# Test/CI indirection ONLY (production defaults read the real host); tests point these at
# fixtures so behavioral tests never mutate or depend on the real host.
MEMINFO_PATH="${CCC_MEMINFO_PATH:-/proc/meminfo}"
SWAPS_PATH="${CCC_SWAPS_PATH:-/proc/swaps}"

die() { echo "ERROR: $*" >&2; exit 1; }
_to_bytes() {  # size[bkmg] -> bytes on stdout; nonzero return on malformed input
  local v="$1" num unit
  [[ "$v" =~ ^([0-9]+)([bkmgBKMG]?)$ ]] || return 1
  num="${BASH_REMATCH[1]}"; unit="${BASH_REMATCH[2],,}"
  case "$unit" in
    ""|b) echo "$num";;
    k) echo $(( num * 1024 ));;
    m) echo $(( num * 1024 * 1024 ));;
    g) echo $(( num * 1024 * 1024 * 1024 ));;
    *) return 1;;
  esac
}
_meminfo_kb() {  # field name (e.g. MemTotal) -> value in kB; nonzero if absent
  awk -v k="$1:" '$1==k { print $2; found=1 } END { if (!found) exit 3 }' "${MEMINFO_PATH}"
}
# Positive, attributable swap-limit-capability determination (finding 7). Capability requires
# EITHER an explicit operator override OR a READABLE cgroup swap-limit control file. An
# unavailable command, permission failure, unexpected output, or inability to determine
# capability yields "no" (fail closed). `docker info`, when obtainable, can only DOWNGRADE the
# result to "no"; it can never upgrade an otherwise-unproven capability to "yes".
CG2_SWAP="${CCC_CGROUP2_SWAP_MAX:-/sys/fs/cgroup/memory.swap.max}"
CG1_MEMSW="${CCC_CGROUP1_MEMSW:-/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes}"
SWAP_CAP="no"; SWAP_CAP_SOURCE=""; SWAP_CAP_EVIDENCE=""
_determine_swap_capability() {
  if [[ -n "${CCC_SWAP_LIMIT_CAPABLE:-}" ]]; then
    if [[ "${CCC_SWAP_LIMIT_CAPABLE}" == "1" ]]; then SWAP_CAP="yes"; else SWAP_CAP="no"; fi
    SWAP_CAP_SOURCE="explicit-override(CCC_SWAP_LIMIT_CAPABLE=${CCC_SWAP_LIMIT_CAPABLE})"
    SWAP_CAP_EVIDENCE="${SWAP_CAP_SOURCE}"
    return
  fi
  if [[ -r "${CG2_SWAP}" ]]; then
    SWAP_CAP="yes"; SWAP_CAP_SOURCE="cgroup-v2"; SWAP_CAP_EVIDENCE="${CG2_SWAP}"
  elif [[ -r "${CG1_MEMSW}" ]]; then
    SWAP_CAP="yes"; SWAP_CAP_SOURCE="cgroup-v1"; SWAP_CAP_EVIDENCE="${CG1_MEMSW}"
  else
    SWAP_CAP="no"; SWAP_CAP_SOURCE="no-readable-cgroup-swap-control-file"
    SWAP_CAP_EVIDENCE="checked:${CG2_SWAP},${CG1_MEMSW}"
  fi
  local dinfo
  if dinfo="$(sudo docker info 2>/dev/null)" && [[ -n "${dinfo}" ]]; then
    if grep -qi "No swap limit support" <<<"${dinfo}"; then
      SWAP_CAP="no"; SWAP_CAP_SOURCE="${SWAP_CAP_SOURCE}+docker-info:no-swap-limit-support"
    fi
  fi
}

while [[ $# -gt 0 ]]; do case "$1" in
  --sdist-dir) SDIST_DIR="$2"; shift 2;;
  --build-lock) BUILD_LOCK="$2"; shift 2;;
  --out-dir) OUT_DIR="$2"; shift 2;;
  --provenance-out) PROV_OUT="$2"; shift 2;;
  --inputs) INPUTS="$2"; shift 2;;
  --ram) RAM="$2"; shift 2;;
  --swap) SWAP="$2"; shift 2;;
  --host-reserve) HOST_RESERVE="$2"; shift 2;;
  --resource-evidence) RES_EVIDENCE="$2"; shift 2;;
  --reuse-authz) REUSE_AUTHZ="$2"; shift 2;;
  --reuse-store) REUSE_STORE="$2"; shift 2;;
  *) echo "unknown arg: $1" >&2; exit 2;; esac; done

# --- Finding 8: explicit preflight-verified prerequisites; never auto-installed ---
require_tool() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required tool '$1' not found on PATH. Install it out-of-band ($2); this" >&2
    echo "       script never installs prerequisites. See release/builder/README.md." >&2
    exit 3; }
}
require_tool docker    "sudo apt-get install docker.io  OR the official Docker engine"
require_tool skopeo    "sudo apt-get install skopeo (Jammy: skopeo 1.4.1-ubuntu)"
require_tool sha256sum "coreutils"
require_tool tar       "tar (coreutils)"
# shellcheck source=manifest-capture.lib.sh
source "${HERE}/manifest-capture.lib.sh"

# --- Finding 3: mandatory, host-validated RAM/swap/host-reserve contract (before Docker) ---
: "${RAM:?--ram is required (no default)}"
: "${SWAP:?--swap is required (no default; use 0 for no swap)}"
: "${HOST_RESERVE:?--host-reserve is required (physical RAM held back for the host)}"
: "${RES_EVIDENCE:?--resource-evidence <path> is required (external ceremony evidence)}"
RAM_B="$(_to_bytes "${RAM}")" || die "invalid --ram: ${RAM} (use e.g. 800m, 2g)"
SWAP_B="$(_to_bytes "${SWAP}")" || die "invalid --swap: ${SWAP} (use 0, 512m, 3g)"
HR_B="$(_to_bytes "${HOST_RESERVE}")" || die "invalid --host-reserve: ${HOST_RESERVE}"
(( RAM_B > 0 )) || die "--ram must be > 0"
(( HR_B > 0 )) || die "--host-reserve must be > 0 (host must keep RAM for OS/SSH/Docker)"

MT_KB="$(_meminfo_kb MemTotal)"     || die "cannot read MemTotal from ${MEMINFO_PATH}"
MA_KB="$(_meminfo_kb MemAvailable)" || die "cannot read MemAvailable from ${MEMINFO_PATH}"
ST_KB="$(_meminfo_kb SwapTotal)"    || die "cannot read SwapTotal from ${MEMINFO_PATH}"
SF_KB="$(_meminfo_kb SwapFree)"     || die "cannot read SwapFree from ${MEMINFO_PATH}"
MT_B=$(( MT_KB * 1024 )); MA_B=$(( MA_KB * 1024 ))
ST_B=$(( ST_KB * 1024 )); SF_B=$(( SF_KB * 1024 ))

# The container RAM cap plus the host reserve must fit in physical RAM (so a container limit
# can never consume the reserve), and the requested RAM must actually be available now.
(( RAM_B + HR_B <= MT_B )) || die "RAM(${RAM}) + host-reserve(${HOST_RESERVE}) exceeds MemTotal"
(( RAM_B <= MA_B ))        || die "RAM(${RAM}) exceeds MemAvailable at ceremony time"

CGROUP_MODE="v1"; [[ -f /sys/fs/cgroup/cgroup.controllers ]] && CGROUP_MODE="v2"
SWAP_DEVICES="$(awk 'NR>1{print $1}' "${SWAPS_PATH}" 2>/dev/null | paste -sd, - || true)"
_determine_swap_capability   # sets SWAP_CAP / SWAP_CAP_SOURCE / SWAP_CAP_EVIDENCE (always recorded)
if (( SWAP_B > 0 )); then
  (( SWAP_B <= ST_B )) || die "--swap(${SWAP}) exceeds active SwapTotal"
  (( SWAP_B <= SF_B )) || die "--swap(${SWAP}) exceeds currently free swap (SwapFree)"
  [[ -n "${SWAP_DEVICES}" ]] \
    || die "--swap requested but no active swap device in ${SWAPS_PATH}"
  [[ "${SWAP_CAP}" == "yes" ]] \
    || die "--swap requested but swap-limit capability unproven (source=${SWAP_CAP_SOURCE})"
  MEMORY_SWAP_B=$(( RAM_B + SWAP_B ))
  MEMORY_SWAP="${MEMORY_SWAP_B}"          # Docker --memory-swap = RAM + swap (bytes)
else
  MEMORY_SWAP="${RAM}"                     # total == RAM  => swap explicitly DISABLED
fi

# Guard against the external evidence path colliding with any input/output path (F7).
_abs() { readlink -m "$1"; }
EV_ABS="$(_abs "${RES_EVIDENCE}")"
for _p in "${OUT_DIR}" "${PROV_OUT}" "${SDIST_DIR}" "${BUILD_LOCK}" "${INPUTS}" "${REUSE_AUTHZ}" "${REUSE_STORE}"; do
  [[ -n "${_p}" && "$(_abs "${_p}")" == "${EV_ABS}" ]] \
    && die "--resource-evidence path collides with ${_p}"
done
[[ -n "${OUT_DIR}" && "${EV_ABS}" == "$(_abs "${OUT_DIR}")/"* ]] \
  && die "--resource-evidence must not live inside the writable output dir"

umask 077
cat > "${RES_EVIDENCE}" <<EV
# External resource-preflight evidence (ceremony only; NOT part of any signed artifact).
timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
requested_ram=${RAM}
requested_swap=${SWAP}
host_reserve=${HOST_RESERVE}
mem_total_bytes=${MT_B}
mem_available_bytes=${MA_B}
swap_total_bytes=${ST_B}
swap_free_bytes=${SF_B}
cgroup_mode=${CGROUP_MODE}
active_swap_devices=${SWAP_DEVICES}
swap_limit_capable=${SWAP_CAP}
swap_limit_capable_source=${SWAP_CAP_SOURCE}
swap_limit_capable_evidence=${SWAP_CAP_EVIDENCE}
docker_memory=${RAM}
docker_memory_swap=${MEMORY_SWAP}
EV

[[ -f "${INPUTS}" ]] || { echo "missing Phase-A inputs: ${INPUTS}" >&2; exit 2; }
# --- Consume Phase-A evidence strictly as DATA. builder-inputs.kv is NEVER source/./eval'd: the
# stdlib reader validates the exact schema + every field and emits validated NUL-delimited records.
# We require a CLEAN reader exit before reading the temp, so partial output can never populate state,
# then load records into an associative array with pure builtins (no command substitution). ---
umask 077
KV_TMP="$(mktemp "${TMPDIR:-/tmp}/ccc-binputs.XXXXXX")"
trap 'rm -f "${KV_TMP}"' EXIT
python3 "${HERE}/read_builder_inputs.py" --inputs "${INPUTS}" > "${KV_TMP}" \
  || die "Phase-A builder inputs failed strict validation (${INPUTS}); refusing to proceed"
declare -A CCC=()
while IFS= read -r -d '' _rec; do
  CCC["${_rec%%=*}"]="${_rec#*=}"
done < "${KV_TMP}"
# Re-verify the exact approved key set (defence in depth alongside the reader).
for _k in CCC_BUILDER_IDENTITY CCC_RECIPE CCC_RECIPE_SHA256 CCC_BUILD_BACKENDS_LOCK \
          CCC_APT_PACKAGES CCC_RUSTUP_SHA CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST \
          CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST_SHA256 CCC_BASE_IMAGE_DIGEST CCC_IMAGE_TAG \
          CCC_RUNTIME_IMAGE_ID CCC_IMAGE_MANIFEST CCC_IMAGE_MANIFEST_DIGEST \
          CCC_IMAGE_CONFIG_DIGEST CCC_IMAGE_IDENTITY_MODE CCC_MANIFEST_CAPTURE_TRANSPORT; do
  [[ -v "CCC[${_k}]" ]] || die "validated builder inputs missing key ${_k}"
done
(( ${#CCC[@]} == 16 )) || die "validated builder inputs key-count mismatch (${#CCC[@]} != 16)"
# Bind the fields consumed below (pure assignment; no source/eval/command-substitution).
CCC_IMAGE_TAG="${CCC[CCC_IMAGE_TAG]}"
CCC_RUNTIME_IMAGE_ID="${CCC[CCC_RUNTIME_IMAGE_ID]}"
CCC_MANIFEST_CAPTURE_TRANSPORT="${CCC[CCC_MANIFEST_CAPTURE_TRANSPORT]}"
CCC_IMAGE_IDENTITY_MODE="${CCC[CCC_IMAGE_IDENTITY_MODE]}"
CCC_IMAGE_MANIFEST_DIGEST="${CCC[CCC_IMAGE_MANIFEST_DIGEST]}"
CCC_IMAGE_MANIFEST="${CCC[CCC_IMAGE_MANIFEST]}"
CCC_BUILDER_IDENTITY="${CCC[CCC_BUILDER_IDENTITY]}"
CCC_BASE_IMAGE_DIGEST="${CCC[CCC_BASE_IMAGE_DIGEST]}"
: "${SDIST_DIR:?--sdist-dir required}"; : "${OUT_DIR:?--out-dir required}"
# OUT_DIR is the writable mount + host-side recap scratch; the FINAL bundle (/out/bundle) is created
# atomically by the Python builder and must NOT be pre-created here.
mkdir -p "${OUT_DIR}"

# --- re-verify the tag STILL maps to the captured image id (immutable execution target) ---
CUR_ID="$(sudo docker image inspect --format '{{.Id}}' "${CCC_IMAGE_TAG}")"
[[ "${CUR_ID}" == "${CCC_RUNTIME_IMAGE_ID}" ]] || {
  echo "ERROR: tag ${CCC_IMAGE_TAG} no longer maps to the Phase-A image id (got ${CUR_ID})" >&2; exit 1; }
# Re-capture through the SAME shared contract, REUSING the transport AND identity mode recorded
# by Phase A (a different representation/mode must not silently pass), then confirm no
# manifest-digest drift. The contract re-binds runtime_image_id under the recorded mode; the temp
# recapture is auto-cleaned. Single-image required (allow_index=0).
: "${CCC_MANIFEST_CAPTURE_TRANSPORT:?Phase-A evidence missing CCC_MANIFEST_CAPTURE_TRANSPORT}"
: "${CCC_IMAGE_IDENTITY_MODE:?Phase-A evidence missing CCC_IMAGE_IDENTITY_MODE}"
if ! RECAP_OUT="$(capture_manifest "${CCC_IMAGE_TAG}" "${CCC_RUNTIME_IMAGE_ID}" "${OUT_DIR}/.recap-manifest.json" 0 "${CCC_MANIFEST_CAPTURE_TRANSPORT}" "${CCC_IMAGE_IDENTITY_MODE}")"; then
  rm -f "${OUT_DIR}/.recap-manifest.json"
  echo "ERROR: Phase-B manifest re-capture/validation failed (transport ${CCC_MANIFEST_CAPTURE_TRANSPORT})" >&2; exit 1
fi
rm -f "${OUT_DIR}/.recap-manifest.json"
CUR_MANIFEST_DIGEST="$(sed -n 's/^MANIFEST_DIGEST=//p' <<<"${RECAP_OUT}")"
[[ "${CUR_MANIFEST_DIGEST}" == "${CCC_IMAGE_MANIFEST_DIGEST}" ]] || {
  echo "ERROR: manifest digest drift (Phase A ${CCC_IMAGE_MANIFEST_DIGEST}, now ${CUR_MANIFEST_DIGEST})" >&2; exit 1; }

# --- Dual-origin: mount the pre-acquired reuse store + committed reuse authorization READ-ONLY
# (re-verified OFFLINE inside the container against the authorization by exact filename + sha256).
REUSE_MOUNTS=(); REUSE_ARGS=()
if [[ -n "${REUSE_AUTHZ}" || -n "${REUSE_STORE}" ]]; then
  [[ -f "${REUSE_AUTHZ}" ]] || die "reuse authorization not found: ${REUSE_AUTHZ}"
  [[ -d "${REUSE_STORE}" ]] || die "reuse store dir not found: ${REUSE_STORE}"
  REUSE_MOUNTS=(-v "${REUSE_AUTHZ}:/in/reuse-authz.json:ro" -v "${REUSE_STORE}:/in/reuse:ro")
  REUSE_ARGS=(--reuse-authz /in/reuse-authz.json --reuse-wheels-dir /in/reuse)
fi

# --- Run the build by IMMUTABLE image id (never the mutable tag), offline. Executable scratch is
# the FIELD-PROVEN `/tmp:rw,exec,...` (native .so import + native configure scripts require exec);
# noexec is not a security boundary here because the authorized PEP 517 build code executes anyway,
# and every other mount stays read-only/noexec. ---
sudo docker run --rm \
  --network=none --cap-drop=ALL --security-opt=no-new-privileges \
  --user 1000:1000 --read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=512m \
  --memory "${RAM}" --memory-swap "${MEMORY_SWAP}" --pids-limit 512 \
  -v "${REPO}/release:/repo/release:ro" \
  -v "${REPO}/requirements.txt:/repo/requirements.txt:ro" \
  -v "${BUILD_LOCK}:/in/requirements-armv7-build.lock:ro" \
  -v "${SDIST_DIR}:/in/sdists:ro" \
  -v "${CCC_IMAGE_MANIFEST}:/in/image-manifest.json:ro" \
  "${REUSE_MOUNTS[@]}" \
  -v "${OUT_DIR}:/out:rw" \
  "${CCC_RUNTIME_IMAGE_ID}" \
  python3 /repo/release/build_wheelhouse.py \
    --build-lock /in/requirements-armv7-build.lock \
    --sdist-dir /in/sdists \
    --out-bundle /out/bundle \
    --recipe /repo/release/builder/Containerfile \
    --build-backends-lock /repo/release/builder/requirements-build-backends.lock \
    --apt-packages /repo/release/builder/apt-packages.list \
    --rustup-sha /repo/release/builder/rustup-init.sha256 \
    --extractor-tools-lock /repo/release/builder/requirements-extractor-tools.lock \
    --build-backends-source-allowlist /repo/release/builder/requirements-build-backends.source-allowlist \
    --builder-identity "${CCC_BUILDER_IDENTITY}" \
    --base-image-digest "${CCC_BASE_IMAGE_DIGEST}" \
    --image-manifest /in/image-manifest.json \
    --runtime-image-id "${CCC_RUNTIME_IMAGE_ID}" \
    --target-tags /repo/release/builder/target-supported-tags.txt \
    --requirements /repo/requirements.txt \
    "${REUSE_ARGS[@]}" \
    --enforce-partition-policy

# The Python builder published the complete bundle atomically at /out/bundle:
#   bundle/wheelhouse-armhf/{*.whl,SHA256SUMS}  bundle/wheelhouse-armv7.json
#   bundle/requirements-armv7.lock  bundle/build-evidence.json
# --- optionally surface the provenance to --provenance-out (bundle remains the source of truth) ---
SRC="${OUT_DIR}/bundle/wheelhouse-armv7.json"
[[ -s "${SRC}" ]] || { echo "ERROR: Phase-B bundle provenance missing/empty: ${SRC}" >&2; exit 1; }
[[ -s "${OUT_DIR}/bundle/requirements-armv7.lock" ]] \
  || { echo "ERROR: Phase-B bundle runtime lock missing/empty" >&2; exit 1; }
if [[ -n "${PROV_OUT}" && "$(readlink -f "${SRC}")" != "$(readlink -f "${PROV_OUT}")" ]]; then
  cp "${SRC}" "${PROV_OUT}"        # optional convenience copy; the bundle is the source of truth
fi
echo "Phase B complete. bundle=${OUT_DIR}/bundle (wheelhouse-armhf + provenance + requirements-armv7.lock)"
echo "limits: --memory ${RAM} --memory-swap ${MEMORY_SWAP} (swap=${SWAP}, reserve=${HOST_RESERVE}); evidence=${RES_EVIDENCE}"
