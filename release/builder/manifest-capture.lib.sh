#!/usr/bin/env bash
# manifest-capture.lib.sh -- ONE shared, fail-closed image-manifest capture contract for the
# CCC builder ceremony. Phase A and Phase B source this and use the SAME implementation, so a
# single manifest representation is captured and validated in both phases.
#
# The incompatible `skopeo inspect --raw docker-daemon:` transport (rejected by modern Docker's
# minimum API version) is replaced by a LOCAL-ARCHIVE flow that needs no daemon-API negotiation:
#   docker save <tag> (streamed to an unprivileged user-owned fd) -> detect archive transport
#   (oci-archive | docker-archive) -> skopeo
#   inspect --raw <transport>:<tar> -> shared oci_manifest gate (config.digest == image_id).
#
# Load-bearing invariant PRESERVED: manifest.config.digest == image_id (the config blob is the
# image's identity, invariant across representations). image_manifest_digest is treated as a
# mechanism-consistent EVIDENCE value; Phase A records the transport and Phase B MUST reuse it,
# so the digest comparison is exact. There is NO docker-daemon fallback.
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

# capture_manifest <tag> <image_id> <manifest_out> [<expected_transport>]
#
# docker save -> detect transport (must equal <expected_transport> when given) -> skopeo inspect
# --raw -> non-empty check -> shared oci_manifest gate (config.digest == image_id + single-image
# schema-2/OCI shape + self-consistent digest) -> ATOMIC publish to <manifest_out>.
#
# On success prints exactly two lines to stdout (nothing else goes to stdout):
#   TRANSPORT=<oci-archive|docker-archive>
#   MANIFEST_DIGEST=sha256:<hex>
# All temporaries (the save tarball + the pre-publish manifest) are removed on EVERY return.
capture_manifest() {
  local tag="$1" image_id="$2" out="$3" want_transport="${4:-}"
  local tmpd tar manifest transport digest out_tmp
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

  digest="sha256:$(sha256sum "${manifest}" | cut -d' ' -f1)"
  # Shared gate: valid single-image schema-2/OCI manifest AND config.digest == image_id.
  python3 "${_MC_LIB_DIR}/../oci_manifest.py" \
      --manifest "${manifest}" --image-id "${image_id}" --expect-manifest-digest "${digest}" >/dev/null \
    || { echo "ERROR: manifest failed shared validation (config.digest != image_id or bad shape)" >&2; return 1; }

  # ATOMIC publish: the final path appears only after a successful, non-empty, validated capture.
  out_tmp="${out}.tmp.$$"
  cp "${manifest}" "${out_tmp}" && mv -f "${out_tmp}" "${out}" \
    || { rm -f "${out_tmp}"; echo "ERROR: could not publish manifest to ${out}" >&2; return 1; }

  echo "TRANSPORT=${transport}"
  echo "MANIFEST_DIGEST=${digest}"
  return 0
}
