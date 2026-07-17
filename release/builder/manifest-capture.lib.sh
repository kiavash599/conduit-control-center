#!/usr/bin/env bash
# manifest-capture.lib.sh -- ONE shared, fail-closed image-manifest capture contract for the
# CCC builder ceremony. Phase A and Phase B source this and use the SAME implementation, so a
# single manifest representation is captured and validated in both phases.
#
# The incompatible `skopeo inspect --raw docker-daemon:` transport (rejected by modern Docker's
# minimum API version) is replaced by a LOCAL-ARCHIVE flow that needs no daemon-API negotiation:
#   docker save <tag> (streamed to an unprivileged user-owned fd) -> detect archive transport
#   (oci-archive | docker-archive) -> skopeo inspect --raw <transport>:<tar> -> shared,
#   store-agnostic oci_manifest gate (runtime-identity binding, below).
#
# Store-agnostic runtime identity: Docker's .Id (runtime_image_id) is the MANIFEST digest on the
# containerd image store (Docker 29 default) and the CONFIG digest on the legacy graphdriver. The
# gate binds whichever SINGLE relationship holds -- containerd: runtime_image_id ==
# image_manifest_digest; legacy: runtime_image_id == image_config_digest (and != manifest digest)
# -- rejecting both-match (ambiguous) and neither-match (unbound). The derived image_identity_mode
# is recorded by Phase A and REUSED by Phase B (a different transport/mode must not silently pass).
# An OCI index is accepted ONLY on the pre-build smoke path (allow_index), bound to its index
# digest. There is NO docker-daemon fallback.
#
# Requires on PATH: docker, skopeo, sha256sum, python3, tar, mktemp.

_MC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect the skopeo archive transport for a `docker save` tarball. Fail closed if the archive is
# neither an OCI layout nor a legacy docker-save archive.
mc_detect_transport() {   # <tar> -> echoes "oci-archive" | "docker-archive"
  local tar="$1" listing
  # tar's stderr is intentionally NOT discarded, so a permission/format failure keeps its cause.
  listing="$(tar -tf "${tar}")" || { echo "ERROR: cannot list archive: ${tar}" >&2; return 1; }
  if grep -qE '(^|/)oci-layout$' <<<"${listing}"; then echo "oci-archive"; return 0; fi
  if grep -qE '(^|/)manifest\.json$' <<<"${listing}"; then echo "docker-archive"; return 0; fi
  echo "ERROR: unrecognized 'docker save' archive (neither OCI layout nor docker archive): ${tar}" >&2
  return 1
}

# docker save -> detect transport (== <want_transport> when given) -> skopeo inspect --raw ->
# non-empty check -> shared, store-agnostic oci_manifest gate (single-image schema-2/OCI shape +
# runtime-identity binding under the derived/expected mode) -> ATOMIC publish to <manifest_out>.
#
# On success prints these lines to stdout (nothing else goes to stdout):
#   TRANSPORT=<oci-archive|docker-archive>
#   IDENTITY_MODE=<containerd|legacy|index>
#   MANIFEST_DIGEST=sha256:<hex>
#   CONFIG_DIGEST=sha256:<hex>       (empty for an index)
#   MEDIA_TYPE=<manifest/index media type>
#
# Positional args:
#   capture_manifest <tag> <runtime_image_id> <out> <allow_index:0|1> [<want_transport>] [<want_mode>]
# <allow_index>=1 permits a multi-image index (pre-build smoke ONLY). When <want_transport> or
# <want_mode> is given, the DETECTED transport / DERIVED identity mode must match (Phase B reuses
# the values Phase A recorded). All temporaries are removed on EVERY return.
capture_manifest() {
  local tag="$1" runtime_image_id="$2" out="$3" allow_index="${4:-0}"
  local want_transport="${5:-}" want_mode="${6:-}"
  local tmpd tar manifest transport out_tmp cli_out
  tmpd="$(mktemp -d "$(dirname "${out}")/.mc.XXXXXX")" \
    || { echo "ERROR: mktemp failed near ${out}" >&2; return 1; }
  # Clean the temp workdir on ANY return path (success or failure).
  trap 'rm -rf "${tmpd}"' RETURN
  tar="${tmpd}/image.tar"; manifest="${tmpd}/manifest.json"

  # The redirection is opened by THIS unprivileged shell, so the archive is owned by the
  # ceremony user; umask 077 makes it mode 0600. `docker save` (via sudo) streams the archive to
  # that fd, so the file is never root-created. The subshell's status is docker save's status.
  ( umask 077; sudo docker save "${tag}" > "${tar}" ) \
    || { echo "ERROR: 'docker save ${tag}' failed" >&2; return 1; }
  [[ -s "${tar}" ]] || { echo "ERROR: 'docker save' produced an empty archive" >&2; return 1; }

  transport="$(mc_detect_transport "${tar}")" || return 1
  if [[ -n "${want_transport}" && "${transport}" != "${want_transport}" ]]; then
    echo "ERROR: archive transport drift: recorded '${want_transport}', detected '${transport}'" >&2
    return 1
  fi

  sudo skopeo inspect --raw "${transport}:${tar}" > "${manifest}" \
    || { echo "ERROR: 'skopeo inspect --raw ${transport}:' failed" >&2; return 1; }
  [[ -s "${manifest}" ]] || { echo "ERROR: empty raw-manifest capture (${transport})" >&2; return 1; }

  # Shared, store-agnostic gate: bind runtime_image_id to the manifest via the derived identity
  # mode (containerd/legacy for a single image; index only when allow_index=1). --expect-mode
  # rejects mode confusion. The CLI prints IDENTITY_MODE/MANIFEST_DIGEST/CONFIG_DIGEST/MEDIA_TYPE.
  local -a gate=( --manifest "${manifest}" --runtime-image-id "${runtime_image_id}" )
  [[ "${allow_index}" == "1" ]] && gate+=( --allow-index )
  [[ -n "${want_mode}" ]] && gate+=( --expect-mode "${want_mode}" )
  cli_out="$(python3 "${_MC_LIB_DIR}/../oci_manifest.py" "${gate[@]}")" \
    || { echo "ERROR: manifest failed shared identity validation (runtime_image_id/mode/shape)" >&2; return 1; }

  # ATOMIC publish: the final path appears only after a successful, non-empty, validated capture.
  out_tmp="${out}.tmp.$$"
  cp "${manifest}" "${out_tmp}" && mv -f "${out_tmp}" "${out}" \
    || { rm -f "${out_tmp}"; echo "ERROR: could not publish manifest to ${out}" >&2; return 1; }

  echo "TRANSPORT=${transport}"
  printf '%s\n' "${cli_out}"
  return 0
}
