#!/usr/bin/env bash
# Phase A (CONNECTED) -- construct the pinned builder image and capture immutable
# evidence. Owner-gated; run ONLY during the controlled ceremony on the dedicated build
# RPi2. Docker is transient (start -> build -> capture -> stop/disable). No push; sudo only.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_IMAGE=""; TAG="ccc-armv7-wheelhouse-builder:local"; EVID="${HERE}/evidence"
while [[ $# -gt 0 ]]; do case "$1" in
  --base-image) BASE_IMAGE="$2"; shift 2;;
  --tag) TAG="$2"; shift 2;;
  --evidence-dir) EVID="$2"; shift 2;;
  *) echo "unknown arg: $1" >&2; exit 2;; esac; done

# --- Finding 8: prerequisites are EXPLICIT and preflight-verified. We NEVER install
# anything from this script (skopeo in particular is an undeclared dep on RPi2). If a
# required tool is missing we fail closed and tell the operator to install it out-of-band. ---
require_tool() {  # name  human-hint
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required tool '$1' not found on PATH. Install it out-of-band ($2); this" >&2
    echo "       script never installs prerequisites. See release/builder/README.md." >&2
    exit 3; }
}
require_tool docker    "sudo apt-get install docker.io  OR the official Docker engine"
require_tool skopeo    "sudo apt-get install skopeo (Jammy: skopeo 1.4.1-ubuntu)"
require_tool sha256sum "coreutils"
require_tool python3   "python3"
require_tool tar       "tar (coreutils)"
# shellcheck source=manifest-capture.lib.sh
source "${HERE}/manifest-capture.lib.sh"
SKOPEO_VERSION="$(skopeo --version 2>/dev/null | head -n1 || true)"
[[ -n "${SKOPEO_VERSION}" ]] || { echo "ERROR: unable to determine skopeo version" >&2; exit 3; }

[[ "${BASE_IMAGE}" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "ERROR: --base-image must be digest-pinned, e.g. arm32v7/ubuntu:22.04@sha256:<64hex>" >&2; exit 2; }
BASE_DIGEST="${BASE_IMAGE##*@}"
mkdir -p "${EVID}"
# Remove capture/evidence temporaries on ANY exit (success or failure); final artifacts are
# published atomically and are never these temp names.
_ba_cleanup() { rm -rf "${EVID}/.smoke-manifest.json" "${EVID}/".mc.* "${EVID}/".builder-inputs.env.tmp.* 2>/dev/null || true; }
trap _ba_cleanup EXIT

# --- FAIL-FAST interoperability smoke test (BEFORE the expensive build): exercise the EXACT
# docker-save -> detected-archive-transport -> skopeo -> oci_manifest capture path against the
# already digest-pinned base image (which is a multi-arch OCI index, so allow_index=1, bound to
# its index digest). A capability mismatch aborts here in seconds, not after a Rust + cffi build. ---
sudo docker pull "${BASE_IMAGE}" >/dev/null \
  || { echo "ERROR: cannot pull digest-pinned base image ${BASE_IMAGE}" >&2; exit 1; }
BASE_LOCAL_ID="$(sudo docker image inspect --format '{{.Id}}' "${BASE_IMAGE}")"
[[ "${BASE_LOCAL_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || { echo "ERROR: unexpected base image id ${BASE_LOCAL_ID}" >&2; exit 1; }
if ! SMOKE_OUT="$(capture_manifest "${BASE_IMAGE}" "${BASE_LOCAL_ID}" "${EVID}/.smoke-manifest.json" 1)"; then
  rm -f "${EVID}/.smoke-manifest.json"
  echo "ERROR: manifest-capture interoperability smoke test FAILED (docker-save/archive/skopeo/" >&2
  echo "       oci_manifest). Aborting BEFORE the expensive image build." >&2; exit 1
fi
rm -f "${EVID}/.smoke-manifest.json"
echo "Preflight OK: capture path works via $(sed -n 's/^TRANSPORT=//p' <<<"${SMOKE_OUT}") ($(sed -n 's/^IDENTITY_MODE=//p' <<<"${SMOKE_OUT}"))."

sudo docker build --build-arg CCC_BASE_IMAGE="${BASE_IMAGE}" -t "${TAG}" -f "${HERE}/Containerfile" "${HERE}"

IMAGE_ID="$(sudo docker image inspect --format '{{.Id}}' "${TAG}")"        # local id: EVIDENCE ONLY
[[ "${IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "ERROR: unexpected image id ${IMAGE_ID}" >&2; exit 1; }

# --- Capture + validate the built image manifest via the SHARED contract (docker save ->
# detected archive transport -> skopeo --raw -> oci_manifest gate: single-image shape AND the
# store-agnostic runtime-identity binding, recording runtime_image_id + image_manifest_digest +
# image_config_digest + image_identity_mode). image-manifest.json is published ATOMICALLY, only
# after a successful validated capture; the daemon transport is NOT used anywhere. ---
if ! CAP_OUT="$(capture_manifest "${TAG}" "${IMAGE_ID}" "${EVID}/image-manifest.json" 0)"; then
  echo "ERROR: refusing to write builder-inputs evidence (manifest capture/validation failed)." >&2
  exit 1
fi
MANIFEST_TRANSPORT="$(sed -n 's/^TRANSPORT=//p' <<<"${CAP_OUT}")"
MANIFEST_DIGEST="$(sed -n 's/^MANIFEST_DIGEST=//p' <<<"${CAP_OUT}")"
MANIFEST_IDENTITY_MODE="$(sed -n 's/^IDENTITY_MODE=//p' <<<"${CAP_OUT}")"
MANIFEST_CONFIG_DIGEST="$(sed -n 's/^CONFIG_DIGEST=//p' <<<"${CAP_OUT}")"
[[ -n "${MANIFEST_TRANSPORT}" && "${MANIFEST_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || { echo "ERROR: capture returned malformed transport/digest" >&2; exit 1; }
[[ "${MANIFEST_IDENTITY_MODE}" == "containerd" || "${MANIFEST_IDENTITY_MODE}" == "legacy" ]] \
  || { echo "ERROR: built image identity mode must be containerd|legacy (got '${MANIFEST_IDENTITY_MODE}')" >&2; exit 1; }
[[ "${MANIFEST_CONFIG_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || { echo "ERROR: single-image capture must carry a config digest" >&2; exit 1; }

RECIPE_SHA="$(python3 -c "import hashlib;print(hashlib.sha256(open('${HERE}/Containerfile','rb').read().replace(b'\r\n',b'\n').replace(b'\r',b'\n')).hexdigest())")"
ALLOWLIST_FILE="${HERE}/requirements-build-backends.source-allowlist"
ALLOWLIST_SHA="$(python3 -c "import hashlib;print(hashlib.sha256(open('${ALLOWLIST_FILE}','rb').read().replace(b'\r\n',b'\n').replace(b'\r',b'\n')).hexdigest())")"
BUILDER_INPUTS_TMP="${EVID}/.builder-inputs.env.tmp.$$"
cat > "${BUILDER_INPUTS_TMP}" <<ENV
CCC_BUILDER_IDENTITY=conduit-control-center-armv7-wheelhouse-builder
CCC_RECIPE=${HERE}/Containerfile
CCC_RECIPE_SHA256=${RECIPE_SHA}
CCC_BUILD_BACKENDS_LOCK=${HERE}/requirements-build-backends.lock
CCC_APT_PACKAGES=${HERE}/apt-packages.list
CCC_RUSTUP_SHA=${HERE}/rustup-init.sha256
CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST=${ALLOWLIST_FILE}
CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST_SHA256=${ALLOWLIST_SHA}
CCC_BASE_IMAGE_DIGEST=${BASE_DIGEST}
CCC_IMAGE_TAG=${TAG}
CCC_RUNTIME_IMAGE_ID=${IMAGE_ID}
CCC_IMAGE_MANIFEST=${EVID}/image-manifest.json
CCC_IMAGE_MANIFEST_DIGEST=${MANIFEST_DIGEST}
CCC_IMAGE_CONFIG_DIGEST=${MANIFEST_CONFIG_DIGEST}
CCC_IMAGE_IDENTITY_MODE=${MANIFEST_IDENTITY_MODE}
CCC_MANIFEST_CAPTURE_TRANSPORT=${MANIFEST_TRANSPORT}
CCC_SKOPEO_VERSION=${SKOPEO_VERSION}
ENV
# Publish builder-inputs.env ATOMICALLY: it appears at its final path only after everything
# above (incl. the validated manifest capture) succeeded.
mv -f "${BUILDER_INPUTS_TMP}" "${EVID}/builder-inputs.env"
echo "Phase A complete. runtime_image_id=${IMAGE_ID} image_manifest_digest=${MANIFEST_DIGEST} identity_mode=${MANIFEST_IDENTITY_MODE} (evidence)."
echo "skopeo: ${SKOPEO_VERSION}"
echo "The environment manifest is captured in Phase B, FROM the executing image."
