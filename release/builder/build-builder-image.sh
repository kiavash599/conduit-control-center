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
SKOPEO_VERSION="$(skopeo --version 2>/dev/null | head -n1 || true)"
[[ -n "${SKOPEO_VERSION}" ]] || { echo "ERROR: unable to determine skopeo version" >&2; exit 3; }

[[ "${BASE_IMAGE}" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "ERROR: --base-image must be digest-pinned, e.g. arm32v7/ubuntu:22.04@sha256:<64hex>" >&2; exit 2; }
BASE_DIGEST="${BASE_IMAGE##*@}"
mkdir -p "${EVID}"

sudo docker build --build-arg CCC_BASE_IMAGE="${BASE_IMAGE}" -t "${TAG}" -f "${HERE}/Containerfile" "${HERE}"

# Capture the RAW OCI image manifest (its sha256 IS the manifest digest) + the local id.
sudo skopeo inspect --raw "docker-daemon:${TAG}" > "${EVID}/image-manifest.json"
[[ -s "${EVID}/image-manifest.json" ]] || { echo "ERROR: empty OCI manifest capture" >&2; exit 1; }
MANIFEST_DIGEST="sha256:$(sha256sum "${EVID}/image-manifest.json" | cut -d' ' -f1)"
IMAGE_ID="$(sudo docker image inspect --format '{{.Id}}' "${TAG}")"        # local id: EVIDENCE ONLY
[[ "${IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "ERROR: unexpected image id ${IMAGE_ID}" >&2; exit 1; }

# --- SHARED manifest gate (release/oci_manifest): the raw OCI manifest must be a valid
# single-image schema-2/OCI manifest AND manifest.config.digest MUST equal the captured
# image_id. If this relationship fails, we exit nonzero and DO NOT write a successful
# builder-inputs record (finding: manifest bound to executed image_id). ---
python3 "${HERE}/../oci_manifest.py" \
  --manifest "${EVID}/image-manifest.json" \
  --image-id "${IMAGE_ID}" \
  --expect-manifest-digest "${MANIFEST_DIGEST}" >/dev/null || {
    echo "ERROR: OCI manifest failed shared validation (config.digest != image_id or bad shape);" >&2
    echo "       refusing to write builder-inputs evidence." >&2; exit 1; }

RECIPE_SHA="$(python3 -c "import hashlib;print(hashlib.sha256(open('${HERE}/Containerfile','rb').read().replace(b'\r\n',b'\n').replace(b'\r',b'\n')).hexdigest())")"
cat > "${EVID}/builder-inputs.env" <<ENV
CCC_BUILDER_IDENTITY=conduit-control-center-armv7-wheelhouse-builder
CCC_RECIPE=${HERE}/Containerfile
CCC_RECIPE_SHA256=${RECIPE_SHA}
CCC_BUILD_BACKENDS_LOCK=${HERE}/requirements-build-backends.lock
CCC_APT_PACKAGES=${HERE}/apt-packages.list
CCC_RUSTUP_SHA=${HERE}/rustup-init.sha256
CCC_BASE_IMAGE_DIGEST=${BASE_DIGEST}
CCC_IMAGE_TAG=${TAG}
CCC_IMAGE_ID=${IMAGE_ID}
CCC_IMAGE_MANIFEST=${EVID}/image-manifest.json
CCC_IMAGE_MANIFEST_DIGEST=${MANIFEST_DIGEST}
CCC_SKOPEO_VERSION=${SKOPEO_VERSION}
ENV
echo "Phase A complete. image_manifest_digest=${MANIFEST_DIGEST} image_id=${IMAGE_ID} (evidence)."
echo "skopeo: ${SKOPEO_VERSION}"
echo "The environment manifest is captured in Phase B, FROM the executing image."
