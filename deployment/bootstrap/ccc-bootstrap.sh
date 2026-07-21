#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# ccc-bootstrap.sh -- Owner ceremony: the ONLY supported v0.3.18 -> v0.3.19
# transition. Run as root on the device:
#
#   sudo bash ccc-bootstrap.sh \
#       --candidate <owner-transferred source tree> \
#       --hashes <out-of-band per-file sha256 manifest> \
#       --trust-anchor <out-of-band allowed_signers file> \
#       --fingerprint SHA256:<independently-supplied-ed25519-fingerprint> \
#       --source-commit <40-lowercase-hex> --source-tag v0.3.19
#
# Contract (accepted alignment):
#   1. Creates a fresh ROOT-OWNED 0700 staging under /var/lib/ccc-update/.
#   2. SNAPSHOT-copies the candidate into staging/source, rejecting symlinks,
#      hardlinked duplicates, specials and any entry outside the source
#      contract DURING the copy (TOCTOU closed: verification happens on the
#      snapshot, and no privileged operation ever reads the Owner-writable
#      transfer tree again).
#   3. Verifies the snapshot: exact file set + per-file SHA-256 against the
#      Owner's out-of-band hash manifest.
#   4. Executes the STAGED engine (update.sh) with the staged
#      ccc-bootstrap-runtime as --runtime-tool and the trust-anchor ceremony
#      inputs. The engine performs: extended Phase-1 backup FIRST (no helper/
#      sudoers mutation happens before it), candidate build, downtime,
#      legacy-trust quarantine + anchor provisioning, conversion, activation,
#      deploy, helpers/sudoers/unit, validators, start, health.
#   5. The staging (engine + runner) remains as the ROLLBACK RESERVE until the
#      final acceptance marker; GC never removes it before then.
#
# The installed v0.3.18 updater is NEVER used for this transition.
set -euo pipefail

die() { echo "ccc-bootstrap: ERROR: $*" >&2; exit 1; }
info() { echo "ccc-bootstrap: $*"; }

CANDIDATE="" HASHES="" ANCHOR="" FPRINT="" SOURCE_COMMIT="" SOURCE_TAG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --candidate)    CANDIDATE="${2:?}"; shift 2;;
        --hashes)       HASHES="${2:?}"; shift 2;;
        --trust-anchor) ANCHOR="${2:?}"; shift 2;;
        --fingerprint)  FPRINT="${2:?}"; shift 2;;
        --source-commit) SOURCE_COMMIT="${2:?}"; shift 2;;
        --source-tag)    SOURCE_TAG="${2:?}"; shift 2;;
        *) die "unknown argument: $1";;
    esac
done
[[ -n "${CANDIDATE}" && -n "${HASHES}" && -n "${ANCHOR}" && -n "${FPRINT}" \
   && -n "${SOURCE_COMMIT}" && -n "${SOURCE_TAG}" ]] \
    || die "usage: --candidate <dir> --hashes <file> --trust-anchor <file> --fingerprint SHA256:... --source-commit <40hex> --source-tag vX.Y.Z"
[[ "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || die "source commit must be exactly 40 lowercase hex"
[[ "$(id -u)" == "0" ]] || die "must run as root"
[[ -d "${CANDIDATE}" && ! -L "${CANDIDATE}" ]] || die "candidate must be a real directory"
[[ -f "${HASHES}" && ! -L "${HASHES}" ]] || die "hash manifest must be a regular file"

# ---- 1. write-ahead record + root-owned staging ----------------------------- #
install -d -o root -g root -m 0700 /var/lib/ccc-update
install -d -o root -g root -m 0700 /var/lib/ccc-update/bootstrap-reserves
BOOT_ID="$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
STAGING="/var/lib/ccc-update/bootstrap-${BOOT_ID}"
RESERVE_RECORDS="/var/lib/ccc-update/bootstrap-reserves"
# This record is durable BEFORE the staging directory exists.  It is the only
# later deletion authority for the rollback reserve; no prefix sweep may remove
# the directory.  Inline stdlib Python is intentional here: the verified staged
# runtime module does not exist yet, while this already-running Owner ceremony
# is the trust root that is about to create the staging tree.
/usr/bin/python3 -I - "${RESERVE_RECORDS}" "${BOOT_ID}" "${STAGING}" \
    "${SOURCE_COMMIT}" "${SOURCE_TAG}" <<'PY'
import json
import os
import re
import stat
import sys
import tempfile

records, attempt, work, commit, tag = sys.argv[1:]
st = os.lstat(records)
if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
    raise SystemExit("reserve record root is not a real directory")
if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
    raise SystemExit("reserve record root must be root-owned mode 0700")
if not re.fullmatch(r"[0-9a-f]{12,32}", attempt):
    raise SystemExit("invalid bootstrap attempt id")
if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(
        r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
    raise SystemExit("invalid bootstrap source identity")
expected = os.path.join(os.path.realpath(os.path.dirname(records)), f"bootstrap-{attempt}")
if work != expected or os.path.lexists(work):
    raise SystemExit("bootstrap reserve path is not fresh and exact")
path = os.path.join(records, f"{attempt}.json")
if os.path.lexists(path):
    raise SystemExit("bootstrap reserve record collision")
doc = {
    "schema": 1,
    "attempt_id": attempt,
    "work": work,
    "source_commit": commit,
    "source_tag": tag,
    "target_version": tag[1:],
    "state": "staged",
    "history": ["staged"],
}
fd, tmp = tempfile.mkstemp(prefix=".reserve-", dir=records)
try:
    with os.fdopen(fd, "wb") as fh:
        fh.write((json.dumps(doc, sort_keys=True) + "\n").encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
        os.fchmod(fh.fileno(), 0o600)
    os.replace(tmp, path)
    dfd = os.open(records, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
PY
install -d -o root -g root -m 0700 "${STAGING}"
SNAP="${STAGING}/source"
install -d -o root -g root -m 0700 "${SNAP}"
info "staging: ${STAGING}"

# ---- 2. snapshot copy under the source object contract ---------------------- #
# Regular files and directories ONLY; symlinks/specials/hardlink-dups refused.
_bad="$(find "${CANDIDATE}" ! -type f ! -type d -print | head -5 || true)"
[[ -z "${_bad}" ]] || die "candidate contains non-regular entries (refused): ${_bad}"
_hl="$(find "${CANDIDATE}" -type f -links +1 -print | head -5 || true)"
[[ -z "${_hl}" ]] || die "candidate contains multi-link files (refused): ${_hl}"
# --chmod normalizes; root-owned by execution + explicit --chown.
rsync -a --chown=root:root --chmod=D0700,F0600 \
    --no-links --no-devices --no-specials \
    "${CANDIDATE}/" "${SNAP}/"
info "snapshot copied"

# ---- 3. verify THE SNAPSHOT (exact set + per-file sha256) ------------------- #
# Hash manifest format: "<sha256>  <relative/path>" (sha256sum -c compatible).
( cd "${SNAP}" && sha256sum -c --quiet --strict "${HASHES}" ) \
    || die "snapshot hash verification FAILED (aborting before any mutation)"
# exact set: every snapshot file must be listed, every listed file present.
_snap_set="$(cd "${SNAP}" && find . -type f | sed 's|^\./||' | sort)"
_list_set="$(awk '{ $1=""; sub(/^ +/,""); print }' "${HASHES}" | sort)"
[[ "${_snap_set}" == "${_list_set}" ]] \
    || die "snapshot file set differs from the authorized hash manifest (exact-set check)"
info "snapshot verified: exact set + per-file hashes"

SNAP_VERSION="$(awk '
    /^APP_VERSION = "[0-9]+\.[0-9]+\.[0-9]+"$/ {
        value=$0; sub(/^APP_VERSION = "/, "", value); sub(/"$/, "", value); count++
    }
    END { if (count != 1) exit 2; print value }
' "${SNAP}/backend/_version.py" 2>/dev/null || true)"
[[ -n "${SNAP_VERSION}" && "${SOURCE_TAG}" == "v${SNAP_VERSION}" ]] \
    || die "source tag does not match the verified snapshot APP_VERSION"

# executable bits for the engine + runner (snapshot was normalized to 0600)
chmod 0700 "${SNAP}/update.sh" "${SNAP}/deployment/bootstrap/ccc-bootstrap-runtime"

# v0.3.18 has no installed ccc-env. Build a minimal root-owned installed-style
# closure from the already hash-verified snapshot so the staged updater can
# perform its pre-downtime canonical .env read without trusting the old tree.
ENV_ROOT="${STAGING}/env-tool"
install -d -o root -g root -m 0700 "${ENV_ROOT}/bin" "${ENV_ROOT}/backend"
install -o root -g root -m 0700 \
    "${SNAP}/deployment/bin/ccc-env" "${ENV_ROOT}/bin/ccc-env"
install -o root -g root -m 0600 \
    "${SNAP}/backend/__init__.py" "${ENV_ROOT}/backend/__init__.py"
install -o root -g root -m 0600 \
    "${SNAP}/backend/env_file.py" "${ENV_ROOT}/backend/env_file.py"
cmp -s "${SNAP}/deployment/bin/ccc-env" "${ENV_ROOT}/bin/ccc-env" \
    || die "staged canonical env-tool copy failed byte-identity verification"
cmp -s "${SNAP}/backend/__init__.py" "${ENV_ROOT}/backend/__init__.py" \
    || die "staged canonical env-tool copy failed byte-identity verification"
cmp -s "${SNAP}/backend/env_file.py" "${ENV_ROOT}/backend/env_file.py" \
    || die "staged canonical env-tool copy failed byte-identity verification"
ENV_RUNNER="${ENV_ROOT}/bin/ccc-env"
info "canonical env-tool staged from verified source bytes"

# ---- 4. execute the STAGED engine ------------------------------------------- #
RUNNER="${SNAP}/deployment/bootstrap/ccc-bootstrap-runtime"
info "handing over to the staged engine (installed v0.3.18 updater is NOT used)"
bash "${SNAP}/update.sh" --ccc-only --non-interactive \
    --source "${SNAP}" \
    --update-attempt-id "${BOOT_ID}" \
    --runtime-tool "${RUNNER}" \
    --env-tool "${ENV_RUNNER}" \
    --authorized-source-commit "${SOURCE_COMMIT}" \
    --authorized-source-tag "${SOURCE_TAG}" \
    --trust-anchor-file "${ANCHOR}" \
    --trust-fingerprint "${FPRINT}" \
    || die "staged engine failed; staging preserved at ${STAGING} as the rollback reserve"

# The transaction itself must have reached success before the reserve can be
# accepted later.  Marking it ready does not delete anything.
/usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime reserve-ready "${BOOT_ID}" \
    || die "update succeeded but rollback reserve could not be marked ready"

info "bootstrap complete; staging retained at ${STAGING} until the acceptance marker"
info "BOOTSTRAP_ATTEMPT_ID=${BOOT_ID}"
info "BOOTSTRAP_SOURCE_COMMIT=${SOURCE_COMMIT}"
info "BOOTSTRAP_SOURCE_TAG=${SOURCE_TAG}"
info "next: complete device qualification, then run the exact reserve-accept command from the runbook"
