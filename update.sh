#!/usr/bin/env bash
# update.sh - Conduit Control Center transactional immutable-runtime updater
# ===========================================================================
# Upgrades CCC to a new version with automatic, selector-based rollback.
#
# Usage boundary:
#   Normal operators use CCC's signed One-Click Update flow. This file is the
#   privileged, source-identity-bound ENGINE invoked by the trusted installed
#   helper; it is not a signature-verification entry point.
#
#   Advanced/bootstrap callers must supply BOTH identity arguments from the
#   already verified signed manifest:
#     sudo bash update.sh --source DIR \
#       --authorized-source-commit <40-lowercase-hex> \
#       --authorized-source-tag vX.Y.Z [--ccc-only] [--non-interactive]
#   First-transition bootstrap callers must additionally supply:
#       --expected-installed-version 0.3.14|0.3.15|0.3.18
#
#   sudo bash update.sh --help       Show this help
#
# The source directory must be an already verified unpacked artifact tree. The
# downloaded payload never supplies privileged control flow: the installed
# helper runs this installed engine and passes the signed commit/tag identity.
#
# What this script does (in order):
#   Phase 0: Validate existing installation and source directory
#   Phase 1: Backup /etc/conduit-cc/ and /opt/conduit-cc/ (code, not venv)
#   Phase 2: Build and validate an immutable candidate runtime (service running)
#   Phase 3: Stop service; deploy new code; update nginx, systemd, DDNS
#   Phase 4: Start service; verify health and version
#   Phase 5: (on failure) Restore backup; restart old version
#   Phase 6: Print summary
#
# Downtime window: Phase 3 (systemctl stop) through Phase 4 (health check).
# Candidate dependencies are installed before the stop (Phase 2) to minimise
# downtime. Typical downtime: 15-30 seconds; package installation is outside it.
#
# What this script NEVER modifies:
#   /etc/conduit-cc/.env         SESSION_SECRET, CF_API_TOKEN, password hash
#   /etc/conduit-cc/tls/         TLS certificate and private key
#   /etc/conduit-cc/config.json  operator-customised settings
#   /etc/conduit-cc/ccc.db       runtime database (sessions, lockout, audit)
#   /var/log/conduit-cc/         DDNS and application logs
#   UFW firewall rules           never modified
#
# Rollback:
#   If any step in Phase 3 or 4 fails, this script automatically restores
#   /etc/conduit-cc/ and /opt/conduit-cc/ from the backup taken in Phase 1,
#   atomically selects the preserved immutable previous runtime (never pip),
#   and restarts/health-checks the exact old version.
#
# Backup location: /var/backups/conduit-cc/<timestamp>-<attempt-id>/
# The last 3 backups are kept. Older backups are deleted only after a new
# update reaches durable success.
#

set -euo pipefail
# Deterministic candidate/deploy modes on both armv7 and aarch64. Never let an
# ambient sudo/user umask turn identical signed inputs into different trust
# closure results.
umask 022

# --------------------------------------------------------------------------- #
#  Script source directory - same SCRIPT_DIR pattern as install.sh            #
# --------------------------------------------------------------------------- #

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# --------------------------------------------------------------------------- #
#  Constants - must match install.sh                                          #
# --------------------------------------------------------------------------- #

readonly APP_USER="conduit-cc"
readonly APP_DIR="/opt/conduit-cc"
readonly CONF_DIR="/etc/conduit-cc"
readonly LOG_DIR="/var/log/conduit-cc"
readonly SERVICE_NAME="conduit-cc"
readonly NGINX_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}"
readonly NGINX_RATELIMIT="/etc/nginx/conf.d/${SERVICE_NAME}-ratelimit.conf"
readonly SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
readonly DDNS_BIN="/usr/local/bin/cloudflare-ddns.sh"
readonly BACKUP_ROOT="/var/backups/conduit-cc"
readonly BACKUP_KEEP=3
readonly HEALTH_TIMEOUT=60
readonly HEALTH_INTERVAL=5

# Psiphon Conduit — must match install.sh constants (Issue #45)
# Bump CONDUIT_VERSION only after the new release has been validated with CCC.
readonly CONDUIT_VERSION="2.0.0"

# --- BL-0002: architecture support (aarch64 + armv7l; fail closed otherwise) --- #
# Map the host architecture to the pinned Psiphon Conduit release asset:
#   aarch64 (arm64, Raspberry Pi 3/4) -> conduit-linux-arm64
#   armv7l  (armhf, Raspberry Pi 2)   -> conduit-linux-armv7
# Unknown/unsupported architectures return non-zero so callers fail closed.
# armv6 (Pi Zero / Pi 1) is intentionally NOT mapped in v1.
conduit_asset_for_arch() {
    case "$1" in
        aarch64) printf 'conduit-linux-arm64' ;;
        armv7l)  printf 'conduit-linux-armv7' ;;
        *)       return 1 ;;
    esac
}

# Install Python dependencies with architecture-appropriate provisioning:
#   aarch64: install from the configured package index (existing arm64/RPi4 path).
#   armv7l : install ONLY from the official, verified wheelhouse-armhf asset and
#            fail closed if it is absent or unverified. Native source builds are
#            NOT used during a normal armhf install (BL-0002 / decision D-12).
#            The wheelhouse MUST contain the FULL requirements.txt dependency
#            closure (not only native-risk deps); a missing required wheel
#            (e.g. fastapi) makes pip fail -> the install fails closed.
# Args: <python_interpreter> <requirements_file> <wheelhouse_dir>
# --------------------------------------------------------------------------- #
#  Epic-1 ownership invariants (F1/F6) -- MIRRORS install.sh                   #
# --------------------------------------------------------------------------- #
_verify_app_dir_ownership() {
    # venv/.venvs are pruned here (verified by _verify_venv_ownership /
    # _verify_store_ownership); everything else in the executable closure must
    # be root:root, NOT group/other-WRITABLE (/022), no setuid/setgid (/6000),
    # and contain no symlinks (the selector at /venv is the only sanctioned
    # link and is pruned/validated separately).
    local _bad
    _bad="$(find "${APP_DIR}" \( -path "${APP_DIR}/venv" -o -path "${APP_DIR}/.venvs" \
                 -o -path "${APP_DIR}/ccc.db" \) -prune -o \
                 \( -not -user root -o -not -group root -o -perm /6000 -o -perm /022 \
                    -o -type l -o \( ! -type f ! -type d \) \
                    -o \( -type f -links +1 \) \) \
                 -print 2>/dev/null | head -20 || true)"
    if [[ -n "${_bad}" ]]; then
        echo "ERROR: APP_DIR ownership invariant violated (non-root/writable/setuid/symlink):" >&2
        echo "${_bad}" >&2
        exit 1
    fi
}

# Enforce the trust-anchor boundary: dir 0700 root:root, anchor (if present)
# regular root:root 0600.
_verify_trust_dir() {
    # lstat-level gates: a SYMLINKED trust dir or anchor is an attack, never a
    # configuration (the old -d/stat checks followed links).
    [[ -e "${APP_DIR}/trust" || -L "${APP_DIR}/trust" ]] || return 0
    if [[ -L "${APP_DIR}/trust" ]]; then
        echo "ERROR: ${APP_DIR}/trust is a symlink (refusing)" >&2; exit 1
    fi
    [[ -d "${APP_DIR}/trust" ]] || { echo "ERROR: ${APP_DIR}/trust is not a directory" >&2; exit 1; }
    local _m _foreign
    _m="$(stat -c '%U:%a' "${APP_DIR}/trust")"
    [[ "${_m}" == "root:700" ]] || { echo "ERROR: trust dir must be root:700 (got ${_m})" >&2; exit 1; }
    _foreign="$(find "${APP_DIR}/trust" -mindepth 1 -maxdepth 1 \
        ! -name allowed_signers -print -quit 2>/dev/null || true)"
    [[ -z "${_foreign}" ]] \
        || { echo "ERROR: foreign object in trust dir: ${_foreign}" >&2; exit 1; }
    if [[ -e "${APP_DIR}/trust/allowed_signers" || -L "${APP_DIR}/trust/allowed_signers" ]]; then
        [[ -L "${APP_DIR}/trust/allowed_signers" ]] && { echo "ERROR: trust anchor is a symlink" >&2; exit 1; }
        [[ -f "${APP_DIR}/trust/allowed_signers" ]] || { echo "ERROR: trust anchor is not a regular file" >&2; exit 1; }
        _m="$(stat -c '%U:%a' "${APP_DIR}/trust/allowed_signers")"
        [[ "${_m}" == "root:600" ]] || { echo "ERROR: trust anchor must be root:600 (got ${_m})" >&2; exit 1; }
    fi
}

# Enforce the helper-dir boundary: root-owned, no group/other write, no symlink.
_verify_bin_dir() {
    [[ -d /opt/conduit-cc/bin ]] || return 0
    local _bad
    _bad="$(find /opt/conduit-cc/bin \
                 \( -not -user root -o -not -group root -o -perm /6022 \
                    -o -type l -o \( ! -type f ! -type d \) \
                    -o \( -type f -links +1 \) \) \
                 -print 2>/dev/null | head -10 || true)"
    if [[ -n "${_bad}" ]]; then
        echo "ERROR: helper dir invariant violated:" >&2
        echo "${_bad}" >&2
        exit 1
    fi
}

# Enforce the runtime-store boundary (post Epic-2): root-owned, no g/o write.
_verify_store_ownership() {
    [[ -d "${APP_DIR}/.venvs" ]] || return 0
    # The shared validator understands every candidate's narrowly allowed
    # interpreter symlinks (bin/python, bin/python3, bin/python3.<N>) and
    # validates every other entry -- including candidate manifests -- without
    # following links. A raw `find -perm` scan over the whole store cannot
    # make that distinction: those symlinks always report as mode 0777 under
    # lstat regardless of umask, so it false-fails every candidate that has
    # one (see _verify_venv_ownership's identical rationale for the selector
    # and legacy-venv case).
    _rt validate-store-shape >/dev/null \
        || { echo "ERROR: runtime store failed the runtime-store shape gate" >&2; exit 1; }
}

_verify_venv_ownership() {
    [[ -e "${APP_DIR}/venv" || -L "${APP_DIR}/venv" ]] || return 0
    if [[ -L "${APP_DIR}/venv" ]]; then
        # Selector layout: GNU find with default -P would evaluate the SYMLINK
        # itself (mode 777) and false-fail /6022. The selector is validated by
        # the FULL runtime-store gate; the recursive ownership scan applies to
        # the store via _verify_store_ownership.
        _rt validate-selector >/dev/null \
            || { echo "ERROR: selector failed the runtime-store gate" >&2; exit 1; }
        return 0
    fi
    # The shared validator understands the venv's narrowly allowed interpreter
    # symlinks and validates every other entry without following links.  A raw
    # `find -perm` scan would false-fail standard symlinks (mode 0777) and would
    # not enforce the hardlink/type boundary.
    _rt validate-legacy >/dev/null \
        || { echo "ERROR: legacy venv failed the runtime-store gate" >&2; exit 1; }
}

_verify_runtime_pre_downtime() {
    [[ -e "${APP_DIR}/venv" || -L "${APP_DIR}/venv" ]] || return 0
    if [[ -L "${APP_DIR}/venv" ]]; then
        _rt validate-selector >/dev/null \
            || { echo "ERROR: selector failed the runtime-store gate" >&2; exit 1; }
        return 0
    fi
    # The legacy service may still be running here. This gate is deliberately
    # read-only: reject a malformed/hardlinked/escaping tree now, but defer its
    # ownership mutation until after systemd has stopped the service.
    _rt validate-legacy-shape >/dev/null \
        || { echo "ERROR: legacy venv failed the pre-downtime shape gate" >&2; exit 1; }
}

_secure_legacy_venv() {
    local _venv="${APP_DIR}/venv" _real
    [[ -e "${_venv}" || -L "${_venv}" ]] || return 0
    if [[ -L "${_venv}" ]]; then
        # Post-conversion: the selector symlink is validated by the full
        # runtime-store gate instead of the legacy ownership transition.
        _rt validate-selector \
            || { echo "ERROR: runtime selector failed validation" >&2; exit 1; }
        return 0
    fi
    if [[ ! -d "${_venv}" ]]; then
        echo "ERROR: ${_venv} must be a real directory (found non-directory)" >&2
        exit 1
    fi
    _real="$(readlink -f "${_venv}")"
    case "${_real}" in
        "${APP_DIR}"/*) ;;
        *) echo "ERROR: venv resolves outside ${APP_DIR}: ${_real}" >&2; exit 1;;
    esac
    # Pre-mutation shape gate rejects hardlinks, special files and every
    # external symlink except the exact base-interpreter links created by venv.
    # Thus recursive ownership cannot mutate an outside inode/target.
    _rt validate-legacy-shape \
        || { echo "ERROR: legacy venv failed its pre-mutation shape gate" >&2; exit 1; }
    chown -hR root:root "${_venv}"
    chmod -R go-w "${_venv}"
    _rt validate-legacy \
        || { echo "ERROR: legacy venv failed its post-transition trust gate" >&2; exit 1; }
    info "legacy venv secured: root-owned, non-service-writable (${_venv})"
}

_secure_legacy_app_root() {
    # Functional first-transition seam: the qualified legacy baselines made
    # APP_DIR itself owned by the service account. The immutable store cannot
    # safely be created below such a parent. Tighten only the real root here
    # before the candidate build; the staged bootstrap runner imports no old APP_DIR code.
    # Full recursive root normalization and verification happens at deploy (or
    # rollback restore), while the still-running old service keeps read access.
    if [[ -L "${APP_DIR}" || ! -d "${APP_DIR}" ]]; then
        echo "ERROR: ${APP_DIR} must be a real directory for ownership transition" >&2
        exit 1
    fi
    chown root:root "${APP_DIR}"
    chmod 0755 "${APP_DIR}"
    [[ "$(stat -c '%U:%G:%a' "${APP_DIR}" 2>/dev/null || true)" == "root:root:755" ]] \
        || { echo "ERROR: APP_DIR ownership transition failed" >&2; exit 1; }
}

_provision_priv_state_dirs() {
    install -d -o root -g root -m 0700 /var/lib/ccc-update
    install -d -o root -g root -m 0700 /var/lib/ccc-update/attempts
    install -d -o root -g root -m 0755 /var/lib/ccc-status
    # Trust is deliberately NOT created, chmodded or chowned here. The stopped-
    # service transaction classifies/quarantines every legacy object first, then
    # provisions the Owner-authorized anchor. Normalizing it here would silently
    # promote untrusted legacy state before the write-ahead trust_intent record.
}

# --------------------------------------------------------------------------- #
#  Epic-1/2 shared lifecycle path-filter contract (A4/B2).                     #
#  ONE authoritative anchored protect-set for every lifecycle rsync/find:      #
#    /venv    -- runtime selector (real dir pre-conversion, symlink after;     #
#                NO trailing slash so BOTH object types are protected)         #
#    /.venvs  -- immutable versioned runtime store                             #
#    /trust   -- publisher trust anchor (NEVER in ordinary backups, NEVER      #
#                deleted by deploy/rollback, NEVER restored from backup)       #
#    /bin     -- installed privileged helpers (provisioned explicitly)        #
#  Do NOT inline-copy these; consume the array.                                #
# --------------------------------------------------------------------------- #
readonly -a CCC_LIFECYCLE_EXCLUDES=(
    --exclude=/venv
    --exclude=/.venvs
    --exclude=/trust
    --exclude=/bin
)

install_python_deps() {
    # B5: $1 is the selected runtime's PYTHON interpreter; pip runs as -m pip.
    local _py="$1" _req="$2" _wh="$3" _arch _reqdir _lock
    local _pip="${_py} -m pip"
    # Accepted pip policy: never consult the live index for pip itself; no
    # version-check chatter, no interactive prompts.
    export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
    _arch="$(uname -m)"
    _reqdir="$(dirname "${_req}")"
    case "${_arch}" in
        aarch64)
            # aarch64: install from the package index but ONLY through the signed,
            # hash-locked aarch64 lock (--require-hashes pins every wheel; no source
            # build, no drift). requirements.txt stays bounds-based (policy unchanged).
            _lock="${_reqdir}/requirements-aarch64.lock"
            if [[ ! -f "${_lock}" ]]; then
                warn "aarch64 dependency lock missing: ${_lock}. The signed requirements-aarch64.lock is required."
                return 1
            fi
            ${_pip} install --quiet --require-hashes --only-binary=:all: -r "${_lock}" || return 1
            ;;
        armv7l)
            # armv7l: install ONLY from the official verified wheelhouse, OFFLINE and
            # hash-locked to requirements-armv7.lock. No index, no source build, no
            # network fallback; a missing/altered wheel fails closed.
            if [[ ! -d "${_wh}" ]]; then
                warn "armhf wheelhouse not found at ${_wh}. The official 'wheelhouse-armhf' release asset is required on armv7l; native source builds are not used at install time."
                return 1
            fi
            if [[ ! -f "${_wh}/SHA256SUMS" ]]; then
                warn "armhf wheelhouse integrity file missing: ${_wh}/SHA256SUMS. A verifiable wheelhouse-armhf asset is required."
                return 1
            fi
            if ! ( cd "${_wh}" && sha256sum -c --quiet SHA256SUMS >/dev/null ); then
                warn "armhf wheelhouse checksum verification failed: ${_wh}."
                return 1
            fi
            _lock="${_reqdir}/requirements-armv7.lock"
            if [[ ! -f "${_lock}" ]]; then
                warn "armv7 dependency lock missing: ${_lock}. The signed requirements-armv7.lock is required."
                return 1
            fi
            ${_pip} install --quiet --no-index --only-binary=:all: --require-hashes --find-links "${_wh}" -r "${_lock}" || return 1
            ;;
        *)
            warn "Unsupported architecture '${_arch}' for dependency provisioning."
            return 1
            ;;
    esac
}

# shellcheck disable=SC2034  # mirrors install.sh constants (Issue #45); unused in update.sh
readonly CONDUIT_USER="conduit"
readonly CONDUIT_BIN_DIR="/opt/conduit"
readonly CONDUIT_DATA_DIR="/var/lib/conduit"

# --------------------------------------------------------------------------- #
#  Script state                                                                #
# --------------------------------------------------------------------------- #

# Populated by _parse_args; defaults to SCRIPT_DIR.
SOURCE_DIR=""

# Runtime-store tool (Epic 2). Default: the INSTALLED helper. The bootstrap
# ceremony passes its root-owned staging runner via --runtime-tool (argv only,
# never environment). Validated by _validate_runtime_tool before first use;
# a missing tool FAILS CLOSED directing to the bootstrap ceremony -- the first
# legacy->v0.3.19 transition must never silently fall back.
CCC_RUNTIME_TOOL="/opt/conduit-cc/bin/ccc-runtime"
# Canonical .env tool. The first-transition bootstrap supplies a root-owned,
# byte-verified staging copy because the qualified legacy baselines do not have
# the installed helper. Ordinary updates use only the installed helper.
CCC_ENV_TOOL="/opt/conduit-cc/bin/ccc-env"
# Bootstrap-only trust-ceremony inputs (refinement 2): when BOTH are supplied,
# anchor provisioning happens INSIDE the stopped-service transaction, before
# service start / health / success. Ordinary updates supply neither.
CCC_TRUST_ANCHOR_FILE=""
CCC_TRUST_FINGERPRINT=""
# Authoritative release identity. Ordinary one-click updates receive these only
# from ccc-update-apply's already-verified signed manifest metadata; the
# first-transition bootstrap receives them as explicit Owner ceremony inputs.
CCC_AUTHORIZED_SOURCE_COMMIT=""
CCC_AUTHORIZED_SOURCE_TAG=""
# Required for a staged first-transition bootstrap. It is supplied by the
# Owner ceremony, allowlisted, and compared with independently parsed installed
# bytes before any update transaction can begin.
CCC_EXPECTED_INSTALLED_VERSION=""
CCC_UPDATE_ATTEMPT_ID=""
_TRANSACTION_BEGUN=false
_ROLLBACK_ACTIVE=false
_ROLLBACK_ATTEMPTED=false
_BACKUP_CREATED_BY_ATTEMPT=false

_validate_runtime_tool() {
    local _meta _bootstrap_root _bad
    if [[ -L "${CCC_RUNTIME_TOOL}" || ! -f "${CCC_RUNTIME_TOOL}" ]]; then
        die "runtime tool missing/not a regular file: ${CCC_RUNTIME_TOOL}.
This installation has not completed the v0.3.19 bootstrap ceremony. Run the
Owner bootstrap (deployment/bootstrap/ccc-bootstrap.sh) first."
    fi
    case "${CCC_RUNTIME_TOOL}" in
        /opt/conduit-cc/bin/ccc-runtime)
            # This helper imports backend.runtime_store from APP_DIR. Prove the
            # complete installed import closure before Python executes it.
            _verify_app_dir_ownership
            _verify_bin_dir
            _meta="$(stat -c '%u:%g:%a:%h' "${CCC_RUNTIME_TOOL}" 2>/dev/null || true)"
            [[ "${_meta}" == "0:0:755:1" ]] \
                || die "installed runtime tool must be root:root 0755 nlink=1 (got ${_meta})"
            ;;
        *)
            [[ "${CCC_RUNTIME_TOOL}" =~ ^/var/lib/ccc-update/bootstrap-([0-9a-f]{12,32})/source/deployment/bootstrap/ccc-bootstrap-runtime$ ]] \
                || die "invalid --runtime-tool path: ${CCC_RUNTIME_TOOL}"
            [[ -n "${CCC_EXPECTED_INSTALLED_VERSION}" ]] \
                || die "a staged bootstrap runtime requires --expected-installed-version"
            _bootstrap_root="${CCC_RUNTIME_TOOL%%/source/deployment/bootstrap/ccc-bootstrap-runtime}"
            [[ ! -L "${_bootstrap_root}" && -d "${_bootstrap_root}" \
               && "$(readlink -f "${CCC_RUNTIME_TOOL}" 2>/dev/null || true)" == "${CCC_RUNTIME_TOOL}" \
               && "$(stat -c '%u:%g:%a' "${_bootstrap_root}" 2>/dev/null || true)" == "0:0:700" ]] \
                || die "bootstrap runtime root/path is not exact root:root 0700 staging"
            _bad="$(find "${_bootstrap_root}/source" \
                \( -not -user root -o -not -group root -o -perm /6022 \
                   -o -type l -o \( ! -type f ! -type d \) \
                   -o \( -type f -links +1 \) \) \
                -print -quit 2>/dev/null || true)"
            [[ -z "${_bad}" ]] || die "bootstrap runtime closure is unsafe: ${_bad}"
            _meta="$(stat -c '%u:%g:%a:%h' "${CCC_RUNTIME_TOOL}" 2>/dev/null || true)"
            [[ "${_meta}" == "0:0:700:1" ]] \
                || die "bootstrap runtime tool must be root:root 0700 nlink=1 (got ${_meta})"
            ;;
    esac
    if [[ "$(stat -c '%u' "${CCC_RUNTIME_TOOL}")" != "0" ]]; then
        die "runtime tool is not root-owned: ${CCC_RUNTIME_TOOL}"
    fi
}

_validate_env_tool() {
    local _bad="" _bootstrap_root=""
    case "${CCC_ENV_TOOL}" in
        /opt/conduit-cc/bin/ccc-env) ;;
        /var/lib/ccc-update/bootstrap-*/env-tool/bin/ccc-env)
            _bootstrap_root="${CCC_ENV_TOOL%/env-tool/bin/ccc-env}"
            [[ "$(readlink -f "${CCC_ENV_TOOL}" 2>/dev/null || true)" == "${CCC_ENV_TOOL}" ]] \
                || die "bootstrap env tool path does not resolve to itself"
            _bad="$(find "${_bootstrap_root}/env-tool" \
                \( ! -type f ! -type d -o ! -user root -o ! -group root \
                   -o -perm /6022 -o -type l -o \( -type f -links +1 \) \) \
                -print -quit 2>/dev/null || true)"
            [[ -z "${_bad}" ]] || die "bootstrap env tool closure is unsafe: ${_bad}"
            ;;
        *) die "invalid --env-tool path: ${CCC_ENV_TOOL}";;
    esac
    if [[ -L "${CCC_ENV_TOOL}" || ! -f "${CCC_ENV_TOOL}" ]]; then
        die "canonical env tool missing/not a regular file: ${CCC_ENV_TOOL}"
    fi
    _bad="$(find "${CCC_ENV_TOOL}" \
        \( ! -user root -o ! -group root -o -perm /6022 -o -links +1 \) \
        -print -quit 2>/dev/null || true)"
    if [[ -n "${_bad}" ]]; then
        die "canonical env tool is not root-owned/non-writable: ${CCC_ENV_TOOL}"
    fi
}

# THE single invocation form for runtime-store operations.
_rt() {
    /usr/bin/python3 -I "${CCC_RUNTIME_TOOL}" "$@"
}

# Per-attempt, atomically-written transaction record. Every shared-state
# mutation is bracketed by an intent/completion mark; no global phase file is
# shared between runs.
_tx_mark() {
    _rt attempt-mark "${CCC_UPDATE_ATTEMPT_ID}" "$@" >/dev/null
}
_tx_fact() {
    local _key="$1"
    _rt attempt-show "${CCC_UPDATE_ATTEMPT_ID}" \
        | sed 's/^UPDATE_ATTEMPT=//' \
        | /usr/bin/python3 -I -c \
            'import json,sys; print(json.load(sys.stdin).get("facts",{}).get(sys.argv[1], ""))' \
            "${_key}"
}
_tx_phase() {
    _rt attempt-show "${CCC_UPDATE_ATTEMPT_ID}" \
        | sed 's/^UPDATE_ATTEMPT=//' \
        | /usr/bin/python3 -I -c \
            'import json,sys; print(json.load(sys.stdin)["phase"])'
}

# Populated by _parse_args; when true, skip the Conduit Core binary update
# (Phase 2b). SCOPE ONLY — it does not affect interaction mode.
CCC_ONLY=false

# Populated by _parse_args; when true, skip the Phase 0g manual confirmation.
# INTERACTION MODE ONLY — explicit, environment-independent. Set by automation
# (the one-click helper passes --non-interactive). Decoupled from CCC_ONLY so the
# update's behaviour is governed by the CLI contract, not by TTY availability.
NONINTERACTIVE=false

# Populated by phase1_backup; used by phase5_rollback.
BACKUP_DIR=""

# Populated by phase0_preflight.
CURRENT_VERSION=""
NEW_VERSION=""
CF_RECORD_NAME=""

# Set by phase3_deploy after systemctl stop succeeds.
# Triggers auto-rollback via the EXIT trap on any subsequent failure.
_DOWNTIME_STARTED=false

# Set by phase4_verify on success.
# Suppresses rollback when the EXIT trap fires on a normal successful exit.
_UPDATE_SUCCEEDED=false

# --------------------------------------------------------------------------- #
#  Terminal colours (disabled when not writing to a TTY)                      #
# --------------------------------------------------------------------------- #

if [[ -t 1 ]]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; RESET=''
fi

# --------------------------------------------------------------------------- #
#  Helper functions                                                            #
# --------------------------------------------------------------------------- #

info()    { printf "${GREEN}  OK${RESET}  %s\n"  "$*"; }
step()    { printf "${CYAN}[CCC]${RESET} %s\n"   "$*"; }
warn()    { printf "${YELLOW}  !${RESET}  %s\n"  "$*" >&2; }
error()   { printf "${RED}ERR${RESET}   %s\n"    "$*" >&2; }
section() {
    printf "\n${BOLD}%s${RESET}\n%s\n" "$*" "$(printf '=%.0s' {1..60})"
}

die() {
    printf "\n${RED}FATAL:${RESET} %s\n" "$1" >&2
    [[ -n "${2:-}" ]] && printf "${YELLOW}  FIX:${RESET} %s\n" "$2" >&2
    exit 1
}

# Read one explicitly allowlisted, non-secret value through the canonical
# symlink-refusing/mode-checking CLI. Generic shell reads are forbidden.
_env_val() {
    /usr/bin/python3 -I "${CCC_ENV_TOOL}" get-key "${CONF_DIR}/.env" "$1"
}

# Extract APP_VERSION from a backend/_version.py file.
# Usage: _read_version /path/to/source-or-app-dir
_read_version() {
    local _f="${1}/backend/_version.py"
    [[ ! -L "${_f}" && -f "${_f}" ]] || { printf "unknown"; return; }
    awk '
        /^APP_VERSION = "[0-9]+\.[0-9]+\.[0-9]+"$/ {
            value=$0
            sub(/^APP_VERSION = "/, "", value)
            sub(/"$/, "", value)
            count++
        }
        END { if (count != 1) exit 2; print value }
    ' "${_f}" 2>/dev/null || printf "unknown"
}

_assert_expected_installed_version() {
    # Ordinary post-bootstrap updates do not use this first-transition input.
    [[ -n "${CCC_EXPECTED_INSTALLED_VERSION}" ]] || return 0
    case "${CCC_EXPECTED_INSTALLED_VERSION}" in
        0.3.14|0.3.15|0.3.18) ;;
        *) die "expected installed version is not an explicitly qualified legacy baseline";;
    esac
    [[ "${CURRENT_VERSION}" == "${CCC_EXPECTED_INSTALLED_VERSION}" ]] \
        || die "installed version ${CURRENT_VERSION} does not match the Owner-authorized baseline ${CCC_EXPECTED_INSTALLED_VERSION}"
    info "Owner-authorized legacy baseline confirmed: ${CURRENT_VERSION}"
}

# Purge stale Python bytecode under APP_DIR so the runtime never loads a cached
# module after a same-size / mtime=0 source change (the deterministic-artifact +
# timestamp-based .pyc collision that made a deployed 0.3.14 report 0.3.13).
# STRICTLY scoped to APP_DIR; the venv subtree AND its children are pruned and
# never traversed, so dependency bytecode is untouched. Removes ONLY __pycache__
# directories and *.pyc files. Best-effort / non-fatal (uses -exec/-delete, no
# pipe, so it is pipefail-safe); any residual staleness is caught downstream by
# the Phase-4 version gate.
_purge_bytecode() {
    find "${APP_DIR}" \( -path "${APP_DIR}/venv" -o -path "${APP_DIR}/venv/*" \
                       -o -path "${APP_DIR}/.venvs" -o -path "${APP_DIR}/.venvs/*" \) -prune \
        -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find "${APP_DIR}" \( -path "${APP_DIR}/venv" -o -path "${APP_DIR}/venv/*" \
                       -o -path "${APP_DIR}/.venvs" -o -path "${APP_DIR}/.venvs/*" \) -prune \
        -o -type f -name '*.pyc' -delete 2>/dev/null || true
}

# Minimal JSON field reader - mirrors install.sh pattern.
# Usage: echo "$json_string" | json_get "d.get('key','')"
json_get() {
    python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null \
        || true
}

# --------------------------------------------------------------------------- #
#  Automatic rollback on unexpected failure                                   #
#                                                                             #
#  The EXIT trap fires on every exit, including set -e failures.              #
#  It initiates rollback only when all three conditions hold:                  #
#    1. _DOWNTIME_STARTED=true  (service was stopped; outage is in progress)  #
#    2. _UPDATE_SUCCEEDED=false (health check did not pass)                   #
#    3. BACKUP_DIR is set       (there is something to restore from)          #
# --------------------------------------------------------------------------- #

_on_exit() {
    local _rc=$?
    # The durable transaction is authoritative over this process-local flag.
    # A signal/crash can land after the success record is fsync'd but before
    # `_UPDATE_SUCCEEDED=true`.  Never attempt to roll back a terminally
    # successful transaction in that narrow window.
    if "${_TRANSACTION_BEGUN}" && ! "${_UPDATE_SUCCEEDED}"; then
        local _durable_phase
        _durable_phase="$(_tx_phase 2>/dev/null || true)"
        if [[ "${_durable_phase}" == "success" ]]; then
            _UPDATE_SUCCEEDED=true
        fi
    fi
    if "${_DOWNTIME_STARTED}" && ! "${_UPDATE_SUCCEEDED}" \
            && ! "${_ROLLBACK_ACTIVE}" && ! "${_ROLLBACK_ATTEMPTED}"; then
        set +e
        section "AUTOMATIC ROLLBACK"
        warn "Update failed (exit ${_rc}) while service was stopped."
        warn "Rolling back to version ${CURRENT_VERSION}..."
        phase5_rollback
        local _rb_rc=$?
        if [[ "${_rb_rc}" -eq 0 ]]; then
            printf '\n%bRollback succeeded.%b ' "${GREEN}" "${RESET}" >&2
            printf "Service is running version %s.\n" "${CURRENT_VERSION}" >&2
        else
            printf '\n%bRollback failed.%b Manual intervention required.\n' "${RED}" "${RESET}" >&2
            _print_manual_recovery
        fi
        exit 1
    elif "${_TRANSACTION_BEGUN}" && ! "${_UPDATE_SUCCEEDED}"; then
        # A pre-downtime failure needs a durable terminal classification too.
        # If backup construction failed after its WAL intent, first remove ONLY
        # that attempt-bound partial directory. If safe cleanup fails, preserve
        # the nonterminal record so startup recovery can retry/diagnose it.
        set +e
        if [[ "${_durable_phase:-}" == "backup_intent" \
                && "${_BACKUP_CREATED_BY_ATTEMPT}" == true \
                && -n "${BACKUP_DIR}" \
                && ( -e "${BACKUP_DIR}" || -L "${BACKUP_DIR}" ) ]]; then
            if _validate_recorded_backup_dir \
                    "${BACKUP_DIR}" "${CCC_UPDATE_ATTEMPT_ID}" \
                    && rm -rf --one-file-system -- "${BACKUP_DIR}" \
                    && [[ ! -e "${BACKUP_DIR}" && ! -L "${BACKUP_DIR}" ]]; then
                info "record-authorized partial backup removed after pre-downtime failure"
            else
                warn "partial backup cleanup failed; transaction remains nonterminal for recovery"
                exit "${_rc}"
            fi
        fi
        # Candidate staging/final publication has its own exact attempt-bound
        # recovery contract. Reconcile it BEFORE making the transaction
        # terminal; otherwise a dependency-install failure could strand an
        # unlisted .staging-<attempt> forever because incomplete discovery no
        # longer sees diagnostic_failure records. A valid final candidate is
        # retained for deterministic reuse, while exact partial output is
        # removed. On any ambiguity preserve both evidence and nonterminal WAL.
        if [[ "${_durable_phase:-}" == "candidate_intent" \
                || "${_durable_phase:-}" == "candidate_ready" ]]; then
            local _failed_candidate
            _failed_candidate="$(_tx_fact candidate_id 2>/dev/null || true)"
            if [[ ! "${_failed_candidate}" =~ ^[0-9a-f]{64}$ ]] \
                    || ! _rt reconcile-candidate "${_failed_candidate}" \
                        "${CCC_UPDATE_ATTEMPT_ID}" >/dev/null; then
                warn "candidate cleanup/revalidation failed; transaction remains nonterminal for recovery"
                exit "${_rc}"
            fi
        fi
        _tx_mark diagnostic_failure >/dev/null 2>&1
    fi
    exit "${_rc}"
}
trap '_on_exit' EXIT

_print_manual_recovery() {
    printf '\n%bManual recovery steps:%b\n' "${BOLD}" "${RESET}" >&2
    if [[ -n "${BACKUP_DIR}" ]]; then
        printf "  1. tar --exclude='etc/conduit-cc/.env' -xzf %s/conf.tar.gz -C /\n" \
            "${BACKUP_DIR}" >&2
        printf "  2. rsync -a --checksum --delete --exclude=/venv --exclude=/.venvs --exclude=/trust --exclude=/bin %s/app/ %s/\n" \
            "${BACKUP_DIR}" "${APP_DIR}" >&2
        printf "  3. /usr/bin/python3 -I %s rollback-activation   # selector-based; NEVER pip\n" \
            "${CCC_RUNTIME_TOOL}" >&2
        printf "  4. restore the recorded systemd units from %s (including conduit-service-present)\n" \
            "${BACKUP_DIR}" >&2
    fi
    printf "  5. systemctl daemon-reload && systemctl restart %s\n" \
        "${SERVICE_NAME}" >&2
}

# --------------------------------------------------------------------------- #
#  Argument parsing                                                            #
# --------------------------------------------------------------------------- #

_parse_args() {
    local _i=1
    while [[ "${_i}" -le "$#" ]]; do
        local _arg="${!_i}"
        case "${_arg}" in
            --source)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || \
                    die "--source requires a directory path argument."
                SOURCE_DIR="${!_i}"
                ;;
            --source=*)
                SOURCE_DIR="${_arg#--source=}"
                ;;
            --ccc-only)
                CCC_ONLY=true
                ;;
            --runtime-tool)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--runtime-tool requires a path argument."
                CCC_RUNTIME_TOOL="${!_i}"
                ;;
            --runtime-tool=*)
                CCC_RUNTIME_TOOL="${_arg#--runtime-tool=}"
                ;;
            --env-tool)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--env-tool requires a path argument."
                CCC_ENV_TOOL="${!_i}"
                ;;
            --env-tool=*)
                CCC_ENV_TOOL="${_arg#--env-tool=}"
                ;;
            --trust-anchor-file)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--trust-anchor-file requires a path."
                CCC_TRUST_ANCHOR_FILE="${!_i}"
                ;;
            --trust-fingerprint)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--trust-fingerprint requires a value."
                CCC_TRUST_FINGERPRINT="${!_i}"
                ;;
            --authorized-source-commit)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--authorized-source-commit requires a value."
                CCC_AUTHORIZED_SOURCE_COMMIT="${!_i}"
                ;;
            --authorized-source-tag)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--authorized-source-tag requires a value."
                CCC_AUTHORIZED_SOURCE_TAG="${!_i}"
                ;;
            --expected-installed-version)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--expected-installed-version requires a value."
                CCC_EXPECTED_INSTALLED_VERSION="${!_i}"
                ;;
            --expected-installed-version=*)
                CCC_EXPECTED_INSTALLED_VERSION="${_arg#--expected-installed-version=}"
                ;;
            --update-attempt-id)
                _i=$(( _i + 1 ))
                [[ "${_i}" -le "$#" ]] || die "--update-attempt-id requires a value."
                CCC_UPDATE_ATTEMPT_ID="${!_i}"
                ;;
            --non-interactive|--yes|-y)
                NONINTERACTIVE=true
                ;;
            --help|-h)
                sed -n '2,/^set -euo pipefail/p' "$0" \
                    | grep '^#' | sed 's/^#[[:space:]]\{0,1\}//'
                exit 0
                ;;
            *)
                printf "Unknown option: %s\n" "${_arg}" >&2
                printf "Usage: sudo bash %s --source DIR --authorized-source-commit 40HEX --authorized-source-tag vX.Y.Z [--expected-installed-version VERSION] [--ccc-only] [--non-interactive]\n" "$0" >&2
                exit 1
                ;;
        esac
        _i=$(( _i + 1 ))
    done

    # Default: use the directory containing this script (same as install.sh).
    if [[ -z "${SOURCE_DIR}" ]]; then
        SOURCE_DIR="${SCRIPT_DIR}"
    fi

    # Resolve to absolute path before any directory changes.
    SOURCE_DIR="$(cd "${SOURCE_DIR}" 2>/dev/null && pwd)" || \
        die "Source directory not accessible: ${SOURCE_DIR}"
}

_prepare_backup_root() {
    if [[ -e "${BACKUP_ROOT}" || -L "${BACKUP_ROOT}" ]]; then
        [[ ! -L "${BACKUP_ROOT}" && -d "${BACKUP_ROOT}" ]] \
            || die "backup root is not a real directory: ${BACKUP_ROOT}"
    fi
    install -d -o root -g root -m 0700 "${BACKUP_ROOT}"
    [[ "$(stat -c '%u:%g:%a' "${BACKUP_ROOT}" 2>/dev/null || true)" == "0:0:700" ]] \
        || die "backup root must be root:root mode 0700"
}

_install_unit_atomic() {
    local _src="$1" _dst="$2" _tmp
    [[ ! -L "${_src}" && -f "${_src}" ]] || return 1
    _tmp="$(mktemp "${_dst}.tmp.XXXXXX")" || return 1
    if sed 's/\r$//' "${_src}" > "${_tmp}" \
            && chown root:root "${_tmp}" \
            && chmod 0644 "${_tmp}" \
            && mv -f "${_tmp}" "${_dst}"; then
        return 0
    fi
    rm -f "${_tmp}"
    return 1
}

_validate_recorded_backup_dir() {
    local _path="$1" _attempt="$2" _meta
    [[ "${_attempt}" =~ ^[0-9a-f]{12,32}$ ]] || return 1
    [[ "${_path}" =~ ^${BACKUP_ROOT}/[0-9]{8}-[0-9]{6}-${_attempt}$ ]] || return 1
    [[ ! -L "${_path}" && -d "${_path}" ]] || return 1
    [[ "$(readlink -f "${_path}" 2>/dev/null || true)" == "${_path}" ]] || return 1
    _meta="$(stat -c '%u:%g:%a' "${_path}" 2>/dev/null || true)"
    [[ "${_meta}" == "0:0:700" ]]
}

_recover_incomplete_transaction() {
    local _raw _json _count _old_id _old_phase _old_backup _old_version _old_candidate
    local _requested_id="${CCC_UPDATE_ATTEMPT_ID}" _rc
    _raw="$(_rt attempt-incomplete)" \
        || die "cannot inspect durable update transactions"
    _json="${_raw#UPDATE_INCOMPLETE=}"
    _count="$(printf '%s' "${_json}" | /usr/bin/python3 -I -c \
        'import json,sys; print(len(json.load(sys.stdin)))')" \
        || die "cannot parse incomplete update transactions"
    [[ "${_count}" =~ ^[0-9]+$ ]] || die "invalid incomplete transaction count"
    [[ "${_count}" -le 1 ]] \
        || die "multiple incomplete update transactions require Owner diagnosis"
    [[ "${_count}" == "1" ]] || return 0

    _old_id="$(printf '%s' "${_json}" | /usr/bin/python3 -I -c \
        'import json,sys; print(json.load(sys.stdin)[0]["attempt_id"])')"
    _old_phase="$(printf '%s' "${_json}" | /usr/bin/python3 -I -c \
        'import json,sys; print(json.load(sys.stdin)[0]["phase"])')"
    _old_backup="$(printf '%s' "${_json}" | /usr/bin/python3 -I -c \
        'import json,sys; print(json.load(sys.stdin)[0]["facts"].get("backup_dir", ""))')"
    _old_version="$(printf '%s' "${_json}" | /usr/bin/python3 -I -c \
        'import json,sys; print(json.load(sys.stdin)[0]["facts"].get("previous_version", ""))')"
    _old_candidate="$(printf '%s' "${_json}" | /usr/bin/python3 -I -c \
        'import json,sys; print(json.load(sys.stdin)[0]["facts"].get("candidate_id", ""))')"
    [[ "${_old_id}" =~ ^[0-9a-f]{12,32}$ ]] \
        || die "incomplete transaction has an invalid attempt id"
    [[ "${_requested_id}" != "${_old_id}" ]] || _requested_id=""
    warn "incomplete update transaction ${_old_id} at ${_old_phase}; reconciling before a new update"

    CCC_UPDATE_ATTEMPT_ID="${_old_id}"
    _TRANSACTION_BEGUN=true
    case "${_old_phase}" in
        begun|ownership_intent|ownership_complete|backup_intent|backup_complete|candidate_intent|candidate_ready|downtime_intent)
            if [[ "${_old_phase}" == "downtime_intent" ]] \
                    && ! systemctl is-active --quiet "${SERVICE_NAME}"; then
                : # stop may have completed just before the completion mark
            else
                if [[ "${_old_phase}" == "backup_intent" && -n "${_old_backup}" \
                        && ( -e "${_old_backup}" || -L "${_old_backup}" ) ]]; then
                    _validate_recorded_backup_dir "${_old_backup}" "${_old_id}" \
                        || die "interrupted backup path failed its transaction binding"
                    rm -rf --one-file-system -- "${_old_backup}" \
                        || die "cannot remove the record-authorized partial backup"
                fi
                if [[ -n "${_old_candidate}" ]]; then
                    [[ "${_old_candidate}" =~ ^[0-9a-f]{64}$ ]] \
                        || die "incomplete transaction has an invalid candidate id"
                    _rt reconcile-candidate "${_old_candidate}" "${_old_id}" >/dev/null \
                        || die "cannot reconcile interrupted pre-downtime candidate publication"
                fi
                _tx_mark diagnostic_failure \
                    || die "cannot terminally classify interrupted pre-downtime update"
                info "pre-downtime transaction classified safely; active selector/runtime bytes and service process were untouched"
                CCC_UPDATE_ATTEMPT_ID="${_requested_id}"
                _TRANSACTION_BEGUN=false
                return 0
            fi
            ;;
    esac

    _validate_recorded_backup_dir "${_old_backup}" "${_old_id}" \
        || die "incomplete transaction backup path failed its exact binding"
    [[ "${_old_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "incomplete transaction lacks a valid previous version"
    BACKUP_DIR="${_old_backup}"
    CURRENT_VERSION="${_old_version}"
    _DOWNTIME_STARTED=true
    set +e
    phase5_rollback
    _rc=$?
    set -e
    _DOWNTIME_STARTED=false
    CCC_UPDATE_ATTEMPT_ID="${_requested_id}"
    _TRANSACTION_BEGUN=false
    _ROLLBACK_ACTIVE=false
    _ROLLBACK_ATTEMPTED=false
    if [[ "${_rc}" -ne 0 ]]; then
        die "automatic reconciliation of transaction ${_old_id} failed; manual recovery required"
    fi
    CURRENT_VERSION="$(_read_version "${APP_DIR}")"
    info "interrupted transaction rolled back and terminally recorded; continuing from v${CURRENT_VERSION}"
}

# --------------------------------------------------------------------------- #
#  Phase 0 - Pre-flight                                                       #
# --------------------------------------------------------------------------- #

phase0_preflight() {
    _validate_runtime_tool
    _validate_env_tool
    section "Phase 0 - Pre-flight"

    step "0a - Checking privileges"
    [[ "${EUID}" -eq 0 ]] || die \
        "This script must be run as root." \
        "Run: sudo bash update.sh"

    step "0b - Verifying existing installation"
    [[ -d "${APP_DIR}" ]] || die \
        "${APP_DIR} not found." \
        "Run install.sh first."
    [[ -f "${APP_DIR}/venv/bin/python3" ]] || die \
        "Python venv not found at ${APP_DIR}/venv." \
        "The installation may be incomplete - re-run install.sh."
    [[ -f "${CONF_DIR}/.env" ]] || die \
        "${CONF_DIR}/.env not found." \
        "The installation may be incomplete - re-run install.sh."
    [[ -f "${SYSTEMD_UNIT}" ]] || die \
        "${SYSTEMD_UNIT} not found." \
        "The installation may be incomplete - re-run install.sh."
    info "Existing installation found"

    step "0c - Reading installed version"
    CURRENT_VERSION="$(_read_version "${APP_DIR}")"
    info "Installed version: ${CURRENT_VERSION}"
    _assert_expected_installed_version

    step "0d - Verifying source directory: ${SOURCE_DIR}"
    [[ -f "${SOURCE_DIR}/backend/_version.py" ]] || die \
        "backend/_version.py not found in ${SOURCE_DIR}." \
        "Is this a valid conduit-control-center source directory?"
    [[ -f "${SOURCE_DIR}/requirements.txt" ]] || die \
        "requirements.txt not found in ${SOURCE_DIR}."
    [[ -f "${SOURCE_DIR}/deployment/conduit-cc.nginx" ]] || die \
        "deployment/conduit-cc.nginx not found in ${SOURCE_DIR}."
    [[ -f "${SOURCE_DIR}/deployment/conduit-cc.service" ]] || die \
        "deployment/conduit-cc.service not found in ${SOURCE_DIR}."
    [[ -f "${SOURCE_DIR}/scripts/cloudflare-ddns.sh" ]] || die \
        "scripts/cloudflare-ddns.sh not found in ${SOURCE_DIR}."
    info "Source directory looks valid"

    step "0e - Reading new version"
    NEW_VERSION="$(_read_version "${SOURCE_DIR}")"
    info "New version: ${NEW_VERSION}"

    step "0e1 - Validating the authorized candidate identity"
    [[ "${CCC_AUTHORIZED_SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] \
        || die "authorized source commit must be exactly 40 lowercase hex"
    [[ "${CCC_AUTHORIZED_SOURCE_TAG}" == "v${NEW_VERSION}" ]] \
        || die "authorized source tag must equal v${NEW_VERSION}"

    step "0f - Reading CF_RECORD_NAME from ${CONF_DIR}/.env"
    CF_RECORD_NAME="$(_env_val CF_RECORD_NAME)"
    [[ -n "${CF_RECORD_NAME}" ]] || die \
        "CF_RECORD_NAME is empty in ${CONF_DIR}/.env." \
        "Check ${CONF_DIR}/.env - the file may be corrupted."
    info "CF_RECORD_NAME: ${CF_RECORD_NAME}"

    step "0f1 - Reconciling any interrupted update transaction"
    _recover_incomplete_transaction
    # Re-bind after recovery: a rollback may have restored installed bytes.
    _assert_expected_installed_version

    step "0g - Confirming update"
    printf "\n"
    if [[ "${CURRENT_VERSION}" == "${NEW_VERSION}" ]]; then
        printf "  Source and installed versions are both ${BOLD}%s${RESET}.\n" \
            "${CURRENT_VERSION}"
        printf "  Proceeding will re-deploy the same version (clean reinstall).\n"
    else
        printf "  Upgrading: ${BOLD}%s${RESET}  ->  ${BOLD}%s${RESET}\n" \
            "${CURRENT_VERSION}" "${NEW_VERSION}"
    fi
    printf "  Source:  %s\n" "${SOURCE_DIR}"
    printf "  Backup:  %s/<timestamp>-<attempt-id>/\n" "${BACKUP_ROOT}"
    printf "\n"
    # Interaction mode is governed by the explicit CLI contract, NOT by the
    # environment. Automation must pass --non-interactive. Absence of a TTY is a
    # fail-closed safety check (no way to confirm), never a silent proceed.
    if [[ "${NONINTERACTIVE}" == true ]]; then
        info "Non-interactive mode (--non-interactive): skipping manual confirmation."
    elif [[ ! -t 0 ]]; then
        die "No terminal available for confirmation; pass --non-interactive for automation."
    else
        local _confirm
        read -r -p "  Continue? [y/N]: " _confirm
        [[ "${_confirm,,}" == "y" ]] || die "Update cancelled. No changes made."
    fi

    step "0h - Binding the new per-attempt write-ahead transaction"
    if [[ -z "${CCC_UPDATE_ATTEMPT_ID}" ]]; then
        CCC_UPDATE_ATTEMPT_ID="$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    [[ "${CCC_UPDATE_ATTEMPT_ID}" =~ ^[0-9a-f]{12,32}$ ]] \
        || die "update attempt id must be 12-32 lowercase hex"
    _rt attempt-begin "${CCC_UPDATE_ATTEMPT_ID}" "${NEW_VERSION}" \
        "${CCC_AUTHORIZED_SOURCE_COMMIT}" "${CCC_AUTHORIZED_SOURCE_TAG}" >/dev/null \
        || die "cannot create the per-attempt write-ahead transaction"
    _TRANSACTION_BEGUN=true
    info "transaction: ${CCC_UPDATE_ATTEMPT_ID}"
}

# --------------------------------------------------------------------------- #
#  Phase 1 - Backup (service still running; no downtime)                     #
#                                                                             #
#  Three items are backed up:                                                 #
#    conf.tar.gz  - full /etc/conduit-cc/ including TLS keys and ccc.db      #
#    app/         - /opt/conduit-cc/ code, excluding venv and caches         #
#    conduit-cc.service + conduit.service presence/bytes + drop-in-dir state #
# --------------------------------------------------------------------------- #

# A4: capture / assert the trust anchor's byte identity across lifecycle
# operations. The anchor may legitimately be ABSENT (pre-ceremony); it must
# never CHANGE or DISAPPEAR across deploy/rollback.
_TRUST_ANCHOR="${APP_DIR}/trust/allowed_signers"
_TRUST_DIGEST_BEFORE=""
_capture_trust_digest() {
    local _invalid=false _meta _foreign=""
    if [[ -e "${APP_DIR}/trust" || -L "${APP_DIR}/trust" ]]; then
        if [[ -L "${APP_DIR}/trust" || ! -d "${APP_DIR}/trust" ]]; then
            _invalid=true
        else
            _meta="$(stat -c '%U:%a' "${APP_DIR}/trust" 2>/dev/null || true)"
            [[ "${_meta}" == "root:700" ]] || _invalid=true
            _foreign="$(find "${APP_DIR}/trust" -mindepth 1 -maxdepth 1 \
                ! -name allowed_signers -print -quit 2>/dev/null || true)"
            [[ -z "${_foreign}" ]] || _invalid=true
        fi
    fi
    if [[ -e "${_TRUST_ANCHOR}" || -L "${_TRUST_ANCHOR}" ]]; then
        if [[ -L "${_TRUST_ANCHOR}" || ! -f "${_TRUST_ANCHOR}" ]]; then
            _invalid=true
        else
            _meta="$(stat -c '%U:%a' "${_TRUST_ANCHOR}" 2>/dev/null || true)"
            [[ "${_meta}" == "root:600" ]] || _invalid=true
        fi
    fi
    if "${_invalid}"; then
        if [[ -n "${CCC_TRUST_ANCHOR_FILE}" && -n "${CCC_TRUST_FINGERPRINT}" ]]; then
            _TRUST_DIGEST_BEFORE="UNTRUSTED_LEGACY"
            return 0
        fi
        die "installed trust boundary is malformed; an explicit Owner bootstrap ceremony is required"
    elif [[ -f "${_TRUST_ANCHOR}" ]]; then
        _TRUST_DIGEST_BEFORE="$(sha256sum "${_TRUST_ANCHOR}" | awk '{print $1}')"
    else
        _TRUST_DIGEST_BEFORE="ABSENT"
    fi
}
# Finding 4/7: rollback-context validators must NOT exit the process (that
# aborts rollback before service restart). These wrappers run the check in a
# subshell and set _failed instead of terminating.
_rollback_check() {
    # $1 = validator function name
    if ! ( "$1" ) >/dev/null 2>&1; then
        error "rollback validator $1 reported a problem (continuing rollback)"
        _failed=true
    fi
}

_assert_trust_anchor_unchanged() {
    local _now
    _verify_trust_dir
    if [[ -f "${_TRUST_ANCHOR}" && ! -L "${_TRUST_ANCHOR}" ]]; then
        _now="$(sha256sum "${_TRUST_ANCHOR}" | awk '{print $1}')"
    else
        _now="ABSENT"
    fi
    if [[ "${_now}" != "${_TRUST_DIGEST_BEFORE}" ]]; then
        echo "ERROR: trust anchor changed across a lifecycle operation" >&2
        echo "  before: ${_TRUST_DIGEST_BEFORE}" >&2
        echo "  after:  ${_now}" >&2
        exit 1
    fi
}

phase1_backup() {
    section "Phase 1 - Backup (service running)"
    _tx_mark ownership_intent \
        || die "cannot commit the legacy app-root ownership intent"
    _secure_legacy_app_root
    _tx_mark ownership_complete \
        || die "app root secured but ownership checkpoint could not be committed"
    _capture_trust_digest

    local _ts
    _ts="$(date +%Y%m%d-%H%M%S)"
    BACKUP_DIR="${BACKUP_ROOT}/${_ts}-${CCC_UPDATE_ATTEMPT_ID}"
    [[ ! -e "${BACKUP_DIR}" && ! -L "${BACKUP_DIR}" ]] \
        || die "backup path collision before intent: ${BACKUP_DIR}"
    _tx_mark backup_intent "backup_dir=${BACKUP_DIR}" \
        || die "cannot commit the attempt-bound backup intent"
    _prepare_backup_root
    mkdir -m 0700 "${BACKUP_DIR}" \
        || die "backup directory collision or creation failure: ${BACKUP_DIR}"
    _BACKUP_CREATED_BY_ATTEMPT=true
    _validate_recorded_backup_dir "${BACKUP_DIR}" "${CCC_UPDATE_ATTEMPT_ID}" \
        || die "new backup directory failed its exact transaction binding"
    info "Backup directory: ${BACKUP_DIR}"

    step "1a - Backing up ${CONF_DIR}"
    # -C / makes paths relative (etc/conduit-cc/...) so extraction via
    #   tar -xzf conf.tar.gz -C /
    # correctly restores to /etc/conduit-cc/ on any machine.
    # .env is EXCLUDED in BOTH archive directions (A-.env): the live file is
    # preserved across update/rollback and revalidated via the canonical CLI.
    tar --exclude='etc/conduit-cc/.env' -czf "${BACKUP_DIR}/conf.tar.gz" -C / etc/conduit-cc
    info "${CONF_DIR} backed up ($(du -sh "${BACKUP_DIR}/conf.tar.gz" | cut -f1))"

    step "1b - Backing up ${APP_DIR} (code only, not venv)"
    # Runtime trees are excluded: rollback selects the preserved immutable
    # previous runtime and NEVER reconstructs dependencies from pip-freeze.
    # __pycache__ and *.pyc are transient and not backup inputs.
    rsync -a \
        "${CCC_LIFECYCLE_EXCLUDES[@]}" \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude 'ccc.db' \
        "${APP_DIR}/" "${BACKUP_DIR}/app/"
    info "${APP_DIR} code backed up"

    step "1e - Backing up installed privileged helpers + sudoers (exact bytes)"
    # Exact rollback (accepted seam 3): record the PRECISE previous privileged
    # state so rollback can reconcile it -- restore recorded helpers, DELETE
    # new-only helpers, restore sudoers bytes (visudo-validated on restore).
    mkdir -p "${BACKUP_DIR}/bin"
    if [[ -d /opt/conduit-cc/bin && ! -L /opt/conduit-cc/bin ]]; then
        # Finding 7: validate the helper dir is an EXACT real, root-owned,
        # regular-file set (reject symlinks/foreign object types) before trusting
        # it as the rollback baseline.
        _foreign="$(find /opt/conduit-cc/bin -mindepth 1 \
                        \( ! -type f -o ! -user root -o -links +1 \) \
                        -print 2>/dev/null | head -5 || true)"
        [[ -z "${_foreign}" ]] || die "helper dir contains foreign/non-root objects: ${_foreign}"
        cp -a /opt/conduit-cc/bin/. "${BACKUP_DIR}/bin/"
        # unambiguous machine inventory: NUL-delimited, INCLUDING dotfiles, sorted.
        ( cd /opt/conduit-cc/bin && find . -mindepth 1 -maxdepth 1 -printf '%P\0' | sort -z ) \
            > "${BACKUP_DIR}/bin-manifest.nul"
        printf 'present\n' > "${BACKUP_DIR}/bin-present"
    else
        printf 'absent\n' > "${BACKUP_DIR}/bin-present"
    fi
    # Record sudoers PRESENCE/ABSENCE explicitly so rollback can remove a
    # newly-created file when none existed before.
    if [[ -L /etc/sudoers.d/conduit-cc ]]; then
        die "/etc/sudoers.d/conduit-cc is a symlink (refusing to record)"
    elif [[ -f /etc/sudoers.d/conduit-cc ]]; then
        cp -a /etc/sudoers.d/conduit-cc "${BACKUP_DIR}/sudoers.conduit-cc"
        printf 'present\n' > "${BACKUP_DIR}/sudoers-present"
    else
        printf 'absent\n' > "${BACKUP_DIR}/sudoers-present"
    fi
    info "privileged helper set + sudoers presence recorded (exact inventory)"
    step "1d - Backing up live systemd units"
    [[ ! -L "${SYSTEMD_UNIT}" && -f "${SYSTEMD_UNIT}" \
       && "$(stat -c '%U:%G:%a' "${SYSTEMD_UNIT}")" == "root:root:644" ]] \
        || die "CCC systemd unit is missing, symlinked, or non-canonical"
    cp -a "${SYSTEMD_UNIT}" "${BACKUP_DIR}/conduit-cc.service"
    local _conduit_unit="/etc/systemd/system/conduit.service"
    if [[ -L "${_conduit_unit}" ]]; then
        die "conduit.service is a symlink (refusing to back up)"
    elif [[ -e "${_conduit_unit}" ]]; then
        [[ -f "${_conduit_unit}" \
           && "$(stat -c '%U:%G:%a' "${_conduit_unit}")" == "root:root:644" ]] \
            || die "conduit.service is not a canonical root:root 0644 file"
        cp -a "${_conduit_unit}" "${BACKUP_DIR}/conduit.service"
        printf 'present\n' > "${BACKUP_DIR}/conduit-service-present"
    else
        printf 'absent\n' > "${BACKUP_DIR}/conduit-service-present"
    fi
    local _conduit_dropin="/etc/systemd/system/conduit.service.d"
    if [[ -L "${_conduit_dropin}" ]]; then
        die "conduit.service drop-in directory is a symlink (refusing to record)"
    elif [[ -e "${_conduit_dropin}" ]]; then
        [[ -d "${_conduit_dropin}" \
           && "$(stat -c '%U:%G:%a' "${_conduit_dropin}")" == "root:root:755" ]] \
            || die "conduit.service drop-in directory is not canonical root:root 0755"
        printf 'present\n' > "${BACKUP_DIR}/conduit-dropin-dir-present"
    else
        printf 'absent\n' > "${BACKUP_DIR}/conduit-dropin-dir-present"
    fi
    info "systemd unit state backed up exactly"

    _tx_mark backup_complete "previous_version=${CURRENT_VERSION}" \
        || die "cannot commit the backup-complete transaction checkpoint"
}

# Delete oldest entries in BACKUP_ROOT until only BACKUP_KEEP remain.
_rotate_backups() {
    local _json _lines _record _attempt _path _excess _removed=0
    local -a _existing=() _records=()
    _json="$(_rt attempt-backups)" || return 1
    _json="${_json#UPDATE_BACKUPS=}"
    _lines="$(printf '%s' "${_json}" | /usr/bin/python3 -I -c \
        'import json,sys
rows=json.load(sys.stdin)
for row in rows:
 print(row["attempt_id"]+"\\t"+row["backup_dir"])')" || return 1
    if [[ -n "${_lines}" ]]; then
        mapfile -t _records <<< "${_lines}"
        for _record in "${_records[@]}"; do
            IFS=$'\t' read -r _attempt _path <<< "${_record}"
            if [[ -e "${_path}" || -L "${_path}" ]]; then
                _validate_recorded_backup_dir "${_path}" "${_attempt}" || return 1
                _existing+=("${_record}")
            fi
        done
    fi
    _excess=$(( ${#_existing[@]} - BACKUP_KEEP ))
    if [[ "${_excess}" -gt 0 ]]; then
        for _record in "${_existing[@]:0:${_excess}}"; do
            IFS=$'\t' read -r _attempt _path <<< "${_record}"
            _validate_recorded_backup_dir "${_path}" "${_attempt}" || return 1
            rm -rf --one-file-system -- "${_path}" || return 1
            [[ ! -e "${_path}" && ! -L "${_path}" ]] || return 1
            _removed=$(( _removed + 1 ))
        done
        info "Removed ${_removed} record-authorized old backup(s); keeping ${BACKUP_KEEP}"
    else
        info "Authorized backup count: ${#_existing[@]}/${BACKUP_KEEP} - no rotation needed"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 2 - Candidate runtime staging (service still running)                #
#                                                                             #
#  Dependencies are installed only into a new, immutable candidate runtime.  #
#  The active selector and active runtime remain untouched while the service  #
#  is running. If staging fails, the candidate is discarded before downtime; #
#  the running service continues on its previous runtime.                     #
# --------------------------------------------------------------------------- #

phase2_preinstall() {
    section "Phase 2 - Candidate runtime staging (service running)"

    step "2a0 - Epic-1 boundary: verify runtime + provision state dirs"
    _verify_runtime_pre_downtime
    _provision_priv_state_dirs
    info "ownership/state boundary asserted"

    step "2a - Computing deterministic candidate runtime identity"
    # Full-strength 64-hex id over the exact bound inputs (refinement 1). The
    # dependency-input digest is per-arch: armv7l = sha256 of the verified
    # wheelhouse SHA256SUMS; aarch64 = sha256 of requirements-aarch64.lock.
    local _arch _pyver _abi _dkind _dig _commit _wh
    _arch="$(uname -m)"
    _pyver="$(/usr/bin/python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
    _abi="$(/usr/bin/python3 -c "import sysconfig;print(sysconfig.get_config_var('SOABI') or '')")"
    _wh="${CCC_WHEELHOUSE_DIR:-${SOURCE_DIR}/wheelhouse-armhf}"
    case "${_arch}" in
        armv7l)
            [[ -f "${_wh}/SHA256SUMS" ]] || die "wheelhouse SHA256SUMS missing for candidate identity"
            _dkind="wheelhouse-sha256sums"
            _dig="$(sha256sum "${_wh}/SHA256SUMS" | awk '{print $1}')"
            ;;
        aarch64)
            [[ -f "${SOURCE_DIR}/requirements-aarch64.lock" ]] || die "aarch64 lock missing for candidate identity"
            _dkind="aarch64-lock"
            _dig="$(sha256sum "${SOURCE_DIR}/requirements-aarch64.lock" | awk '{print $1}')"
            ;;
        *) die "unsupported architecture for candidate build: ${_arch}";;
    esac
    NEW_VERSION_ID="$(_read_version "${SOURCE_DIR}")"
    [[ "${NEW_VERSION_ID}" != "unknown" ]] || die "candidate APP_VERSION declaration is invalid"
    # Finding 5: no git fallback and no self-referential file inside the
    # candidate. The trusted caller supplies the signed/Owner-authorized source
    # identity out-of-band; payload bytes cannot override it.
    _commit="${CCC_AUTHORIZED_SOURCE_COMMIT}"
    local _tag="${CCC_AUTHORIZED_SOURCE_TAG}"
    [[ "${_commit}" =~ ^[0-9a-f]{40}$ ]] \
        || die "authorized source commit must be exactly 40 lowercase hex"
    [[ "${_tag}" == "v${NEW_VERSION_ID}" ]] \
        || die "authorized source tag must equal v${NEW_VERSION_ID}"
    CCC_CANDIDATE_ID="$(_rt candidate-id "${NEW_VERSION_ID}" "${_commit}" "${_arch}" "${_pyver}" "${_abi}" "${_dkind}" "${_dig}")" \
        || die "candidate identity computation failed"
    # Use the durable update transaction id for staging/quarantine ownership;
    # recovery can then remove only this attempt's pre-downtime staging.
    CCC_ATTEMPT_ID="${CCC_UPDATE_ATTEMPT_ID}"
    _tx_mark candidate_intent "candidate_id=${CCC_CANDIDATE_ID}" \
        || die "cannot commit the candidate-build intent"
    info "candidate runtime: ${CCC_CANDIDATE_ID:0:16}... (attempt ${CCC_ATTEMPT_ID})"

    step "2b - Building candidate runtime in attempt-owned staging (service running)"
    # NEVER pip into the active/previous runtime (accepted architecture): the
    # candidate is a fresh venv in .venvs staging; deps install per the
    # unchanged arch policy (RPi2 offline wheelhouse; RPi4 hash-locked online).
    if [[ -d "${APP_DIR}/.venvs/${CCC_CANDIDATE_ID}" ]]; then
        _rt revalidate "${CCC_CANDIDATE_ID}" \
            || die "existing candidate ${CCC_CANDIDATE_ID:0:16}.. failed LIVE revalidation (refusing reuse)"
        info "existing candidate live-revalidated (reused)"
    else
        local _cand_py
        _cand_py="$(_rt stage-candidate "${CCC_CANDIDATE_ID}" "${CCC_ATTEMPT_ID}")" \
            || die "candidate staging failed"
        install_python_deps "${_cand_py}" "${SOURCE_DIR}/requirements.txt" "${_wh}" \
            || die "candidate dependency install failed. Service is still running version ${CURRENT_VERSION}; the active runtime was NOT touched. Resolve the dependency issue and re-run update.sh."
        _rt finalize-candidate "${CCC_CANDIDATE_ID}" "${CCC_ATTEMPT_ID}" \
            "app_version=${NEW_VERSION_ID}" "commit=${_commit}" "tag=${_tag}" "arch=${_arch}" \
            "python_version=${_pyver}" "abi=${_abi}" "input_digest=${_dig}" \
            "input_digest_kind=${_dkind}" \
            || die "candidate validation/publication failed (active runtime untouched)"
        info "candidate runtime built, validated and published"
    fi
    _tx_mark candidate_ready \
        || die "cannot commit the candidate-ready transaction checkpoint"
}

# --------------------------------------------------------------------------- #
#  Phase 2b - Conduit binary update (independent of CCC downtime window)     #
#                                                                             #
#  Conduit and CCC are separate services.  This phase:                       #
#    1. Detects the new binary (SOURCE_DIR/conduit or downloads from GitHub)  #
#    2. Validates it (4 steps) before stopping the running Conduit            #
#    3. Backs up the current binary as conduit.bak for single-step rollback   #
#    4. Stops Conduit, swaps the binary, starts Conduit                      #
#    5. Verifies post-swap (3 checks): active, metrics, version file         #
#    6. Rolls back via conduit.bak if any post-swap check fails               #
#                                                                             #
#  Skipped automatically if Conduit is not installed (no ${CONDUIT_BIN_DIR}) #
#  CCC service remains running throughout this entire phase.                  #
# --------------------------------------------------------------------------- #

phase2b_conduit_update() {
    section "Phase 2b - Conduit binary update"

    # ---- Skip if Conduit is not installed ---------------------------------- #
    if [[ ! -f "${CONDUIT_BIN_DIR}/conduit" ]]; then
        info "Conduit not installed at ${CONDUIT_BIN_DIR}/conduit — skipping"
        info "(Run install.sh to install Conduit for the first time)"
        return 0
    fi

    local _cur_conduit_ver="unknown"
    _cur_conduit_ver="$(cat "${CONDUIT_BIN_DIR}/version" 2>/dev/null || true)"
    info "Installed Conduit version: ${_cur_conduit_ver:-unknown}"

    # ---- Detect new binary source ----------------------------------------- #
    step "2b-a — Detecting new Conduit binary"
    local _conduit_tmp
    _conduit_tmp="$(mktemp /tmp/conduit.XXXXXX)"
    local _new_binary_src=""

    if [[ -f "${SOURCE_DIR}/conduit" && -x "${SOURCE_DIR}/conduit" ]]; then
        cp "${SOURCE_DIR}/conduit" "${_conduit_tmp}"
        _new_binary_src="${SOURCE_DIR}/conduit"
        info "Using binary from source directory: ${_new_binary_src}"
    else
        # Offer download only when version would change
        if [[ "${_cur_conduit_ver}" == "${CONDUIT_VERSION}" ]]; then
            info "Conduit is already at v${CONDUIT_VERSION} and no new binary found in ${SOURCE_DIR}/"
            info "Skipping Conduit binary update"
            rm -f "${_conduit_tmp}"
            return 0
        fi

        warn "Conduit binary not found in ${SOURCE_DIR}/ — downloading v${CONDUIT_VERSION}"
        local _gh_base="https://github.com/Psiphon-Inc/conduit/releases/download/release-cli-${CONDUIT_VERSION}"
        local _asset
        _asset="$(conduit_asset_for_arch "$(uname -m)")" || { rm -f "${_conduit_tmp}"; die "Unsupported architecture '$(uname -m)': no Conduit asset mapping (BL-0002 supports aarch64, armv7l)."; }

        local _checksums
        _checksums="$(curl -fsSL "${_gh_base}/checksums.txt")" || {
            rm -f "${_conduit_tmp}"
            die "Failed to download checksums.txt from GitHub." \
                "Check internet connectivity or place the binary at ${SOURCE_DIR}/conduit"
        }
        curl -fsSL -o "${_conduit_tmp}" "${_gh_base}/${_asset}" || {
            rm -f "${_conduit_tmp}"
            die "Failed to download conduit binary from GitHub." \
                "Check internet connectivity or place the binary at ${SOURCE_DIR}/conduit"
        }
        local _expected_sha _actual_sha
        _expected_sha="$(printf '%s\n' "${_checksums}" | grep "${_asset}" | awk '{print $1}')"
        [[ -n "${_expected_sha}" ]] || {
            rm -f "${_conduit_tmp}"
            die "Could not find checksum for '${_asset}' in checksums.txt."
        }
        _actual_sha="$(sha256sum "${_conduit_tmp}" | awk '{print $1}')"
        [[ "${_actual_sha}" == "${_expected_sha}" ]] || {
            rm -f "${_conduit_tmp}"
            die "SHA-256 checksum mismatch for conduit binary." \
                "Expected: ${_expected_sha}  Got: ${_actual_sha}"
        }
        info "SHA-256 verified: ${_actual_sha:0:16}..."
        _new_binary_src="github/v${CONDUIT_VERSION}"
    fi

    # ---- Pre-swap validation (4 steps) ------------------------------------ #
    # All 4 checks must pass before we stop the running Conduit service.
    step "2b-b — Pre-swap validation (conduit service remains running)"
    chmod +x "${_conduit_tmp}"
    [[ -x "${_conduit_tmp}" ]] || {
        rm -f "${_conduit_tmp}"
        die "New binary is not executable after chmod +x."
    }
    local _ver_out
    _ver_out="$("${_conduit_tmp}" --version 2>&1)" || {
        rm -f "${_conduit_tmp}"
        die "New binary failed --version (non-zero exit)." \
            "Binary may be corrupt or for the wrong architecture."
    }
    printf '%s\n' "${_ver_out}" | grep -q "${CONDUIT_VERSION}" || {
        rm -f "${_conduit_tmp}"
        die "New binary version mismatch: expected ${CONDUIT_VERSION}." \
            "Got: ${_ver_out}"
    }
    info "Pre-swap validation passed: ${_ver_out}"

    # ---- Backup current binary -------------------------------------------- #
    step "2b-c — Backing up current binary"
    cp "${CONDUIT_BIN_DIR}/conduit" "${CONDUIT_BIN_DIR}/conduit.bak"
    info "${CONDUIT_BIN_DIR}/conduit.bak created"

    # ---- Stop Conduit (brief downtime for Conduit only) ------------------- #
    step "2b-d — Stopping conduit service"
    systemctl stop conduit 2>/dev/null || true
    info "conduit stopped"

    # ---- Swap binary ------------------------------------------------------- #
    step "2b-e — Installing new binary"
    install -o root -g root -m 755 "${_conduit_tmp}" "${CONDUIT_BIN_DIR}/conduit"
    rm -f "${_conduit_tmp}"
    printf '%s\n' "${CONDUIT_VERSION}" > "${CONDUIT_BIN_DIR}/version"
    info "${CONDUIT_BIN_DIR}/conduit updated (root:root 755)"

    # ---- Start Conduit ----------------------------------------------------- #
    step "2b-g — Starting conduit service"
    systemctl start conduit || {
        warn "conduit failed to start — rolling back"
        _conduit_rollback "${_cur_conduit_ver}"
        die "Conduit start failed; rolled back to v${_cur_conduit_ver}." \
            "Check: journalctl -u conduit -n 50 --no-pager"
    }
    info "conduit started"

    # ---- Post-swap verification (3 checks) --------------------------------- #
    step "2b-h — Post-swap verification"

    # Check 1: systemctl is-active
    local _c_attempts=0 _c_max=6
    while [[ "${_c_attempts}" -lt "${_c_max}" ]]; do
        if systemctl is-active --quiet conduit 2>/dev/null; then
            info "Check 1/3: conduit.service is active"
            break
        fi
        _c_attempts=$(( _c_attempts + 1 ))
        step "  Waiting for conduit... (${_c_attempts}/${_c_max})"
        sleep 5
    done
    if ! systemctl is-active --quiet conduit 2>/dev/null; then
        warn "conduit.service not active after 30 s — rolling back"
        _conduit_rollback "${_cur_conduit_ver}"
        die "Conduit did not become active; rolled back to v${_cur_conduit_ver}." \
            "Check: journalctl -u conduit -n 50 --no-pager"
    fi

    # Check 2: version file matches CONDUIT_VERSION
    local _installed_ver
    _installed_ver="$(cat "${CONDUIT_BIN_DIR}/version" 2>/dev/null || true)"
    if [[ "${_installed_ver}" == "${CONDUIT_VERSION}" ]]; then
        info "Check 2/3: version file = ${_installed_ver}"
    else
        warn "Version file mismatch (got '${_installed_ver}') — rolling back"
        _conduit_rollback "${_cur_conduit_ver}"
        die "Version file check failed; rolled back to v${_cur_conduit_ver}."
    fi

    # Check 3: metrics endpoint responds (non-fatal — new nodes may be slow)
    if curl -sf "http://127.0.0.1:9090/metrics" 2>/dev/null \
            | grep -q "conduit_max_common_clients"; then
        info "Check 3/3: metrics endpoint reachable"
    else
        warn "Check 3/3: metrics endpoint not yet reachable (normal on first start)"
        warn "Verify later: curl http://127.0.0.1:9090/metrics | grep conduit_max_common_clients"
    fi

    info "Conduit updated: ${_cur_conduit_ver} -> ${CONDUIT_VERSION}"
}

# Roll back conduit binary from .bak and restart.
# Called internally by phase2b_conduit_update on post-swap failure.
_conduit_rollback() {
    local _prev_ver="${1:-unknown}"
    set +e
    systemctl stop conduit 2>/dev/null || true
    if [[ -f "${CONDUIT_BIN_DIR}/conduit.bak" ]]; then
        install -o root -g root -m 755 \
            "${CONDUIT_BIN_DIR}/conduit.bak" \
            "${CONDUIT_BIN_DIR}/conduit"
        printf '%s\n' "${_prev_ver}" > "${CONDUIT_BIN_DIR}/version"
        systemctl daemon-reload 2>/dev/null || true
        if systemctl start conduit 2>/dev/null; then
            info "Conduit rolled back to v${_prev_ver}"
        else
            error "Conduit rollback start failed."
            error "Manual recovery: systemctl start conduit"
        fi
    else
        error "conduit.bak not found — cannot roll back."
        error "Restore manually from ${CONDUIT_BIN_DIR}/conduit.bak"
    fi
    set -euo pipefail
}

# --------------------------------------------------------------------------- #
#  Phase 3 - Deploy (DOWNTIME WINDOW BEGINS)                                  #
#                                                                             #
#  _DOWNTIME_STARTED is set true after systemctl stop succeeds.               #
#  From that point, any set -e failure triggers _on_exit -> phase5_rollback.  #
#                                                                             #
#  rsync uses --delete so /opt/conduit-cc exactly mirrors the source after    #
#  every update.  Stale files from renamed or deleted modules are removed.    #
#  --delete respects all --exclude rules: venv, ccc.db, __pycache__, .git,   #
#  and .env are never deleted from APP_DIR.                                   #
# --------------------------------------------------------------------------- #

phase3_deploy() {
    section "Phase 3 - Deploy"

    step "3a - Stopping ${SERVICE_NAME}"
    _tx_mark downtime_intent \
        || die "cannot commit downtime intent; service remains running"
    systemctl stop "${SERVICE_NAME}"
    # Set AFTER stop so rollback knows to restart the service.
    _DOWNTIME_STARTED=true
    _tx_mark downtime_started "downtime_started=true" \
        || die "service stopped but downtime checkpoint could not be committed"
    info "${SERVICE_NAME} stopped  [DOWNTIME STARTS]"

    step "3a1 - Runtime store conversion (one-time, idempotent, write-ahead recorded)"
    # B3: convert the legacy real-directory venv into the immutable store and
    # publish the selector symlink. Runs INSIDE the stopped-service window; a
    # crash at any boundary leaves a classifiable, resumable transition record
    # in /var/lib/ccc-update (ccc-runtime diagnose). No-op when already converted.
    if [[ ! -L "${APP_DIR}/venv" ]]; then
        # Record conversion ownership BEFORE the first selector/store mutation.
        _tx_mark conversion_intent "converted_by_attempt=true" \
            || die "cannot commit runtime-conversion intent"
        # The service is stopped and the intent is durable. Only now may the
        # legacy tree be mutated; no legacy interpreter is ever run as root.
        _secure_legacy_venv
        _rt convert-legacy || die "runtime store conversion failed (see ccc-runtime diagnose)"
        _tx_mark conversion_complete \
            || die "runtime converted but checkpoint could not be committed"
    else
        _tx_mark conversion_intent "converted_by_attempt=false" \
            || die "cannot commit the already-converted runtime intent"
        _rt validate-selector >/dev/null || die "selector invalid before deploy"
        _tx_mark conversion_complete \
            || die "cannot commit the already-converted runtime checkpoint"
    fi

    step "3a1t - Trust-anchor transaction (bootstrap ceremony only)"
    _tx_mark trust_intent \
        || die "cannot commit trust-transition intent"
    if [[ -n "${CCC_TRUST_ANCHOR_FILE}" && -n "${CCC_TRUST_FINGERPRINT}" ]]; then
        # EXACT lstat classification (finding 4): the ONLY acceptable existing
        # trust dir is a real dir, root-owned, mode 0700, whose anchor (if any)
        # is a regular root-owned 0600 file. Anything else -- symlink, wrong
        # owner/mode, foreign object -- is quarantined under an attempt-bound
        # ROOT-ONLY path in /var/lib/ccc-update (OUTSIDE deploy deletion), never
        # promoted, never restored, never authorized.
        _qroot="/var/lib/ccc-update/trust-quarantine-${CCC_UPDATE_ATTEMPT_ID}"
        _ok_dir=0
        if [[ -d "${APP_DIR}/trust" && ! -L "${APP_DIR}/trust" && "$(stat -c '%U:%a' "${APP_DIR}/trust")" == "root:700" ]]; then
            _ok_dir=1
            _trust_foreign="$(find "${APP_DIR}/trust" -mindepth 1 -maxdepth 1 \
                ! -name allowed_signers -print -quit 2>/dev/null || true)"
            [[ -z "${_trust_foreign}" ]] || _ok_dir=0
            if [[ -e "${APP_DIR}/trust/allowed_signers" || -L "${APP_DIR}/trust/allowed_signers" ]]; then
                if [[ ! -L "${APP_DIR}/trust/allowed_signers" && -f "${APP_DIR}/trust/allowed_signers" && "$(stat -c '%U:%a' "${APP_DIR}/trust/allowed_signers")" == "root:600" ]]; then
                    :   # acceptable existing anchor; provisioner decides identical/conflict
                else
                    install -d -o root -g root -m 0700 "${_qroot}"
                    mv "${APP_DIR}/trust/allowed_signers" "${_qroot}/allowed_signers.legacy"
                    warn "legacy anchor QUARANTINED to ${_qroot} (never promoted)"
                fi
            fi
        fi
        if [[ "${_ok_dir}" != "1" && ( -e "${APP_DIR}/trust" || -L "${APP_DIR}/trust" ) ]]; then
            install -d -o root -g root -m 0700 "${_qroot}"
            mv "${APP_DIR}/trust" "${_qroot}/trust.legacy"
            warn "legacy trust object QUARANTINED to ${_qroot} (deliberate security transition)"
        fi
        install -d -o root -g root -m 0700 "${APP_DIR}/trust"
        # Provision through the STAGED trusted provisioner (implementation from
        # the verified snapshot; mutation target fixed to the installed tree),
        # then verify via the verifier's own reader. Completes BEFORE service
        # start / health / success marker.
        /usr/bin/python3 -I "${SOURCE_DIR}/deployment/bin/ccc-provision-trust-anchor"             install --from "${CCC_TRUST_ANCHOR_FILE}"             --fingerprint "${CCC_TRUST_FINGERPRINT}" --target /opt/conduit-cc             || die "trust-anchor provisioning failed (transaction aborted before service start)"
        /usr/bin/python3 -I "${SOURCE_DIR}/deployment/bin/ccc-provision-trust-anchor" \
            verify --target /opt/conduit-cc \
            || die "post-install trust-anchor verification failed"
        _verify_trust_dir
        _capture_trust_digest      # the AUTHORIZED anchor is the preserved baseline now
        info "trust anchor provisioned inside the transaction (root:600, verified)"
    fi
    _tx_mark trust_complete "trust_done=true" \
        || die "cannot commit the trust-complete transaction checkpoint"

    step "3a2 - Activating candidate runtime (atomic selector flip)"
    # Pre-flip target validation + post-flip auto-restore live inside activate();
    # an invalid candidate can never become the visible selector.
    _rt validate-target "${CCC_CANDIDATE_ID}" \
        || die "candidate failed pre-activation target validation"
    local _previous_runtime
    _previous_runtime="$(_rt validate-selector | sed -n 's/^RUNTIME_SELECTOR=OK id=//p')"
    [[ -n "${_previous_runtime}" ]] || die "cannot determine the previous runtime"
    _tx_mark activation_intent "previous_runtime=${_previous_runtime}" \
        || die "cannot commit candidate-activation intent"
    _rt activate "${CCC_CANDIDATE_ID}" \
        || die "candidate activation failed (selector unchanged or restored)"
    _tx_mark activated "activation_done=true" \
        || die "candidate activated but checkpoint could not be committed"

    step "3b - Deploying new code (rsync --delete)"
    _tx_mark deploy_intent \
        || die "cannot commit code-deploy intent"
    # The /bin entry of the shared contract is ANCHORED (leading slash) to the
    # transfer root, so it excludes ONLY the top-level ${APP_DIR}/bin -- the helper dir,
    # which is owned and re-provisioned by step 3b2 below from deployment/bin.
    # The source tree has no top-level bin/, so without this exclude --delete
    # would try to remove ${APP_DIR}/bin while the running ccc-update-apply
    # worker executes from it ("cannot delete non-empty directory: bin").
    # The slash anchors the rule so deployment/bin/ (the helper SOURCE) is still
    # deployed normally.
    # --checksum (CP-001): deterministic artifacts set mtime=0 (pack_tree), so a
    # same-length content change (e.g. "0.3.13" -> "0.3.14", identical byte size)
    # ties rsync's size+mtime quick-check against a mtime=0 destination and is
    # SILENTLY SKIPPED. Deciding by content hash guarantees the file is deployed.
    # Epic-1 ownership boundary (F1/F6): root-owned deploy; rsync -a alone would
    # preserve SOURCE uid/gid, so ownership/modes are normalized explicitly and
    # the broad recursive service-account chown is REMOVED (root helpers must never
    # execute service-writable code).
    rsync -a --checksum --delete \
        --chown=root:root \
        --chmod=D0755,F0644 \
        "${CCC_LIFECYCLE_EXCLUDES[@]}" \
        --exclude 'ccc.db' \
        --exclude '__pycache__/' \
        --exclude '.git/' \
        --exclude '.env' \
        "${SOURCE_DIR}/" "${APP_DIR}/"
    chown root:root "${APP_DIR}"
    chmod 0755 "${APP_DIR}"
    _verify_app_dir_ownership
    _verify_venv_ownership
    _verify_store_ownership
    _verify_trust_dir
    _verify_bin_dir
    _assert_trust_anchor_unchanged
    info "Code deployed to ${APP_DIR} (root-owned, service read/execute only)"

    step "3b1 - Purging stale Python bytecode"
    _purge_bytecode
    info "Stale __pycache__/*.pyc purged under ${APP_DIR} (venv preserved)"

    step "3b2 - Re-provisioning privileged helpers + sudoers"
    # install.sh provisions /opt/conduit-cc/bin helpers and the sudoers grant, but
    # earlier update.sh did NOT re-provision them -- so an upgraded host could run
    # stale helper binaries and miss the restore grant (S4B-2.4). Re-install ALL
    # privileged helpers (root:root 0755) from the freshly-rsynced deployment/bin,
    # and rewrite the sudoers file. The bin dir is created by install.sh; ensure
    # it exists for robustness.
    install -d -o root -g root -m 0755 /opt/conduit-cc/bin
    for _h in ccc-apply-conduit-config ccc-personal-compartment ccc-ryve-claim ccc-restore-apply ccc-apply-https-port ccc-update-apply ccc-provision-trust-anchor ccc-env ccc-runtime; do
        install -o root -g root -m 0755 \
            "${APP_DIR}/deployment/bin/${_h}" "/opt/conduit-cc/bin/${_h}"
        _h_meta="$(stat -c '%U:%a' "/opt/conduit-cc/bin/${_h}")"
        [ "${_h_meta}" = "root:755" ] || die \
            "Helper ${_h} ownership/perms wrong (${_h_meta}); expected root:755"
    done
    info "Privileged helpers re-installed (root:root 0755)"

    # Safe sudoers write: render to a temp file, validate with `visudo -cf` BEFORE
    # it goes live, set 0440, then atomically replace. A malformed /etc/sudoers.d
    # file can break sudo host-wide, so it must never be written live unvalidated
    # (phase-1 backup does not cover /etc/sudoers.d).
    local _sudoers_file="/etc/sudoers.d/${SERVICE_NAME}"
    local _sudoers_tmp
    _sudoers_tmp="$(mktemp)"
    cat > "${_sudoers_tmp}" <<EOF
# Conduit Control Center — allow ${APP_USER} to control the Conduit service
# Generated by update.sh — do not edit manually
${APP_USER} ALL=(root) NOPASSWD: /bin/systemctl start conduit
${APP_USER} ALL=(root) NOPASSWD: /bin/systemctl stop conduit
${APP_USER} ALL=(root) NOPASSWD: /bin/systemctl restart conduit
# REVIEWED EXCEPTION (Epic 1 A1): ccc-apply-conduit-config keeps a bare-path
# grant because it is a parameterized privileged API -- fixed verbs (apply/
# rollback), bounded INTEGER-only options, hardcoded destinations, no path/
# unit/free-string argument anywhere (contract-tested). Sudoers cannot express
# "any integer within bounds", so the helper's own parser is the firewall.
${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-apply-conduit-config
# EXACT public surface: only the literal 'apply' verb is authorized. The
# internal '__run-worker' subcommand is reachable ONLY via the root-created
# transient unit, never through these grants (missing/extra/substituted
# arguments are rejected by sudoers exact-argument matching).
${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-restore-apply apply
${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-update-apply apply
${APP_USER} ALL=(conduit) NOPASSWD: /opt/conduit-cc/bin/ccc-personal-compartment
${APP_USER} ALL=(conduit) NOPASSWD: /opt/conduit-cc/bin/ccc-ryve-claim
EOF
    if ! visudo -cf "${_sudoers_tmp}"; then
        rm -f "${_sudoers_tmp}"
        die "sudoers syntax check failed (temp not installed); live sudoers unchanged."
    fi
    chmod 440 "${_sudoers_tmp}"
    mv -f "${_sudoers_tmp}" "${_sudoers_file}"
    info "${_sudoers_file} updated (440)"

    step "3c - Atomically updating managed systemd units"
    # This is the single systemd-unit writer. Both units are covered by the
    # attempt-bound backup and deploy_intent checkpoint; M2 only verifies them.
    _install_unit_atomic "${APP_DIR}/deployment/conduit-cc.service" "${SYSTEMD_UNIT}" \
        || die "cannot atomically install ${SYSTEMD_UNIT}"
    local _conduit_unit="/etc/systemd/system/conduit.service"
    if [[ -e "${_conduit_unit}" || -L "${_conduit_unit}" ]]; then
        [[ ! -L "${_conduit_unit}" && -f "${_conduit_unit}" ]] \
            || die "managed conduit.service destination is unsafe"
        _install_unit_atomic "${APP_DIR}/deployment/conduit.service" "${_conduit_unit}" \
            || die "cannot atomically install ${_conduit_unit}"
        install -d -o root -g root -m 0755 /etc/systemd/system/conduit.service.d
        info "${_conduit_unit} updated atomically"
    else
        info "Conduit unit is unmanaged on this host; leaving it absent"
    fi
    systemctl daemon-reload
    info "managed systemd unit transaction published"

    # E3 audit directory (ADR-0003 Phase B): must exist BEFORE the service
    # restart in the health-check phase, because the updated unit's
    # ReadWritePaths=/var/log/conduit-cc-audit binds at start. Root-owned parent
    # (/var/log); dir root:conduit-cc 0750 (service traverses + reads, cannot
    # write/unlink/rename). Idempotent.
    install -d -o root -g conduit-cc -m 0750 /var/log/conduit-cc-audit
    info "/var/log/conduit-cc-audit ensured (0750, root:conduit-cc)"

    step "3d - Updating nginx site configuration"
    # Preserve the configured HTTPS port (single source of truth:
    # config.json web.https_port). Legacy installs without the key infer 443 and
    # persist it, so behaviour is unchanged. update.sh NEVER resets a non-443
    # install to 443 and NEVER migrates the port automatically.
    local _https_port
    _https_port="$(json_get "d.get('web',{}).get('https_port',443)" \
        < "${CONF_DIR}/config.json" 2>/dev/null || true)"
    [[ "${_https_port}" =~ ^[0-9]+$ ]] || _https_port=443
    # Persist inferred 443 for legacy installs (idempotent for current ones).
    python3 - "${CONF_DIR}/config.json" "${_https_port}" <<'PYEOF'
import json, sys
path, port = sys.argv[1], int(sys.argv[2])
try:
    with open(path) as fh:
        cfg = json.load(fh)
except FileNotFoundError:
    cfg = {}
cfg.setdefault("web", {})["https_port"] = port
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
PYEOF
    # Fail safe: if the persisted port is held by a DIFFERENT (non-nginx)
    # listener, abort rather than silently changing a working deployment.
    if ss -Htln 2>/dev/null | awk '{print $4}' | sed 's/.*://' \
            | grep -qx "${_https_port}"; then
        if ! ss -Htlnp 2>/dev/null | grep -E ":${_https_port}([[:space:]]|\$)" \
                | grep -q nginx; then
            die "HTTPS port ${_https_port} is in use by a non-nginx service." \
                "Resolve the conflict, then re-run update. The port was not changed."
        fi
    fi
    # Apply via the shared helper (renders host + port + redirect suffix, runs
    # nginx -t, reloads, reconciles UFW, restores the prior site on failure).
    /opt/conduit-cc/bin/ccc-apply-https-port apply \
        --port "${_https_port}" --hostname "${CF_RECORD_NAME}" \
        || die "Failed to apply HTTPS port ${_https_port}; previous nginx site restored."
    info "nginx site config updated (HTTPS port ${_https_port})"

    step "3e - Updating nginx rate-limiting zone"
    cat > "${NGINX_RATELIMIT}" << 'RATELIMIT_EOF'
# Conduit Control Center -- login endpoint rate limiting zone (Issue #34)
# Referenced by: /etc/nginx/sites-available/conduit-cc
#   limit_req zone=login_limit burst=9 nodelay;
#
# 10 r/m  = 1 request every 6 seconds per unique IP address.
# 10m     = ~160,000 IP state entries before LRU eviction.
#
# Managed by install.sh / update.sh. Do not edit directly.
limit_req_zone $binary_remote_addr zone=login_limit:10m rate=10r/m;
RATELIMIT_EOF
    chmod 644 "${NGINX_RATELIMIT}"

    if command -v nginx &>/dev/null; then
        if nginx -t 2>/dev/null; then
            info "nginx configuration valid"
            if systemctl is-active --quiet nginx 2>/dev/null; then
                systemctl reload nginx
                info "nginx reloaded"
            else
                info "nginx not active - skipping reload"
            fi
        else
            # Re-run without redirect so the operator sees the error before
            # rollback fires.
            nginx -t
            die "nginx configuration test failed after update." \
                "Check ${NGINX_AVAILABLE} for syntax errors."
        fi
    else
        info "nginx not found - skipping config test and reload"
    fi

    step "3f - Updating DDNS script"
    cp "${APP_DIR}/scripts/cloudflare-ddns.sh" "${DDNS_BIN}"
    chmod 755 "${DDNS_BIN}"
    chown root:root "${DDNS_BIN}"
    info "${DDNS_BIN} updated"

    # Re-provision the logrotate config (SD-card protection). Keeps an existing
    # install current; installs it on systems updated from a pre-logrotate build.
    step "3f2 - Updating logrotate config"
    if command -v logrotate >/dev/null 2>&1; then
        install -o root -g root -m 0644 \
            "${APP_DIR}/deployment/conduit-cc.logrotate" \
            /etc/logrotate.d/conduit-cc
        info "logrotate config refreshed (/etc/logrotate.d/conduit-cc)"
    else
        warn "logrotate not found; skipping ${LOG_DIR} rotation config"
    fi

    step "3g - Refreshing ccc-unlock symlink"
    ln -sf "${APP_DIR}/scripts/ccc-unlock" /usr/local/bin/ccc-unlock
    info "ccc-unlock → ${APP_DIR}/scripts/ccc-unlock"

    # Journal read access for the Logs page (GET /api/logs).
    # The Logs page runs, as ${APP_USER} and WITHOUT sudo:  journalctl -u conduit
    # systemd-journal membership is required to read another unit's journal.
    # Run BEFORE (re)starting the service so the new process inherits the group
    # (also repairs existing installs that predate the journal-access fix).
    if getent group systemd-journal >/dev/null; then
        usermod -aG systemd-journal "${APP_USER}"
        info "${APP_USER} ensured in systemd-journal (Logs page: journalctl -u conduit)"
    else
        warn "systemd-journal group not found - Logs page may return HTTP 503"
    fi

    # 3h - ${SERVICE_NAME} is intentionally NOT started here (BS1 Commit 3).
    # The new CCC code must not serve until the reduced-capable helper and the
    # updated conduit.service are verified (phase_m2_config_write_artifacts and
    # phase_bs1_reduced_guard); otherwise the new backend could invoke
    # an OLD helper with --reduced-* args. The start is deferred to
    # phase3b_start_service, which runs after phaseM2 + the guard.
    phase_m2_config_write_artifacts
    _tx_mark deployed \
        || die "cannot commit the deployed transaction checkpoint"
    info "${SERVICE_NAME} deploy complete; start deferred until M2 artifacts are in place"
}

# --------------------------------------------------------------------------- #
#  Phase 4 - Health verification                                              #
#                                                                             #
#  Polls /api/health until:                                                   #
#    - status == "ok"              (service is handling requests)             #
#    - version == NEW_VERSION      (new code is loaded, not the old version)  #
#                                                                             #
#  The version check catches the edge case where the service starts on the    #
#  old code (e.g. stale .pyc or systemd restart caching) despite the deploy.  #
#                                                                             #
#  A timeout here triggers die() -> EXIT trap -> phase5_rollback.             #
# --------------------------------------------------------------------------- #

phase4_verify() {
    section "Phase 4 - Health verification"

    step "4a - Waiting for ${NEW_VERSION} to become healthy"
    local _attempts=0
    local _max=$(( HEALTH_TIMEOUT / HEALTH_INTERVAL ))
    local _response _status _version

    while [[ "${_attempts}" -lt "${_max}" ]]; do
        _response="$(curl -sf "http://127.0.0.1:8000/api/health" 2>/dev/null)" \
            || true
        if [[ -n "${_response}" ]]; then
            _status="$(echo "${_response}" | json_get "d.get('status','')")"
            _version="$(echo "${_response}" | json_get "d.get('version','')")"
            if [[ "${_status}" == "ok" ]]; then
                if [[ "${_version}" == "${NEW_VERSION}" ]]; then
                    info "Health check passed (version=${_version})  [DOWNTIME ENDS]"
                    _tx_mark health_verified \
                        || die "health passed but checkpoint could not be committed"
                    _tx_mark success \
                        || die "health passed but transaction success could not be committed"
                    _UPDATE_SUCCEEDED=true
                    _rt gc >/dev/null 2>&1 || true   # retention: current+previous kept
                    return 0
                else
                    warn "Service healthy but running v${_version};" \
                         "expected v${NEW_VERSION} -- still waiting"
                fi
            fi
        fi
        _attempts=$(( _attempts + 1 ))
        step "  Waiting... (${_attempts}/${_max})"
        sleep "${HEALTH_INTERVAL}"
    done

    die \
        "Service did not reach healthy on version ${NEW_VERSION} within ${HEALTH_TIMEOUT}s." \
        "Check: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
}

# --------------------------------------------------------------------------- #
#  Phase 5 - Rollback                                                         #
#                                                                             #
#  Called by _on_exit when update fails after _DOWNTIME_STARTED=true.         #
#  Runs with set +e (already applied by _on_exit) so errors in individual     #
#  rollback steps do not abort the overall rollback.                          #
#                                                                             #
#  Returns 0 on full success, 1 if any step failed.                           #
# --------------------------------------------------------------------------- #

phase5_rollback() {
    # _on_exit has already applied set +e before calling this function.
    # Do NOT call die() here - it calls exit, which re-triggers the EXIT trap.

    local _failed=false _rb_phase
    _ROLLBACK_ACTIVE=true
    _ROLLBACK_ATTEMPTED=true
    _rb_phase="$(_tx_phase 2>/dev/null || true)"
    case "${_rb_phase}" in
        rollback_started|runtime_restored|files_restored|service_restore_intent) ;;
        rolled_back) _ROLLBACK_ACTIVE=false; return 0;;
        diagnostic_failure|success|"")
            error "transaction phase ${_rb_phase:-<unreadable>} cannot enter rollback"
            _ROLLBACK_ACTIVE=false
            return 1
            ;;
        *)
            if _tx_mark rollback_started; then
                _rb_phase="rollback_started"
            else
                error "cannot commit rollback-started checkpoint"
                _failed=true
            fi
            ;;
    esac

    if [[ -z "${BACKUP_DIR}" ]] || [[ ! -d "${BACKUP_DIR}" ]]; then
        error "Backup directory not found: ${BACKUP_DIR:-<unset>}"
        error "Cannot perform automatic rollback."
        _tx_mark diagnostic_failure >/dev/null 2>&1 || true
        _ROLLBACK_ACTIVE=false
        return 1
    fi
    if ! _validate_recorded_backup_dir "${BACKUP_DIR}" "${CCC_UPDATE_ATTEMPT_ID}"; then
        error "Backup directory failed its exact transaction binding: ${BACKUP_DIR}"
        _tx_mark diagnostic_failure >/dev/null 2>&1 || true
        _ROLLBACK_ACTIVE=false
        return 1
    fi

    # ---- 5a  Stop service -------------------------------------------------- #
    step "5a - Stopping service"
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    info "Service stopped"

    # ---- 5a1  STATE-AWARE runtime selector rollback (FIRST; refinement 3) --- #
    # Runs while the new runtime tooling is still on disk (the staging runner
    # is the GC-protected rollback reserve). Exact branching on recorded state:
    #   none      -> no selector mutation to undo
    #   converted -> this attempt converted the legacy layout: rollback-conversion
    #   activated -> rollback-activation first; then rollback-conversion ONLY if
    #                this same attempt also performed the conversion
    step "5a1 - Runtime selector rollback (transaction-aware)"
    local _candidate_id _current_runtime _activation_done _converted_by_attempt
    local _previous_runtime
    _candidate_id="$(_tx_fact candidate_id 2>/dev/null || true)"
    _activation_done="$(_tx_fact activation_done 2>/dev/null || true)"
    _converted_by_attempt="$(_tx_fact converted_by_attempt 2>/dev/null || true)"
    _previous_runtime="$(_tx_fact previous_runtime 2>/dev/null || true)"
    _current_runtime="$(_rt validate-selector 2>/dev/null \
        | sed -n 's/^RUNTIME_SELECTOR=OK id=//p' || true)"

    # The disk is authoritative across the tiny window between an atomic flip
    # and its completion mark: if the selector already names this attempt's
    # candidate, rollback activation even when activation_done was not persisted.
    if [[ -n "${_candidate_id}" && "${_current_runtime}" == "${_candidate_id}" ]]; then
        if _rt rollback-activation; then
            info "candidate activation rolled back (previous runtime selected)"
        else
            error "activation rollback failed - selector state needs diagnosis"
            _failed=true
        fi
    elif [[ -n "${_previous_runtime}" && \
            "${_current_runtime}" == "${_previous_runtime}" ]]; then
        # Resume after a crash between the selector flip and the following
        # transaction checkpoint: the exact recorded previous runtime is
        # already selected, so replaying rollback-activation would be wrong.
        info "previous runtime already selected (resuming selector rollback)"
    elif [[ "${_converted_by_attempt}" == "True" && \
            -d "${APP_DIR}/venv" && ! -L "${APP_DIR}/venv" ]]; then
        # Resume after a crash after rollback-conversion but before
        # runtime_restored: the original real-directory selector is already
        # back and validate-selector correctly has no symlink id to report.
        info "legacy real-directory runtime already restored (resuming rollback)"
    elif [[ "${_activation_done}" == "True" ]]; then
        error "recorded activation has an unclassifiable selector state"
        _failed=true
    else
        info "candidate activation not observed for this attempt"
    fi

    # This fact is written ahead of conversion. rollback-conversion is
    # idempotent when the mutation never began, and completes/undoes an
    # interrupted conversion using the runtime store's own transition record.
    if [[ "${_converted_by_attempt}" == "True" ]]; then
        if _rt rollback-conversion; then
            info "legacy conversion rolled back (real-directory venv restored)"
        else
            error "conversion rollback failed - selector state needs diagnosis"
            _failed=true
        fi
    fi
    if ! "${_failed}" && [[ "${_rb_phase}" == "rollback_started" ]]; then
        _tx_mark runtime_restored \
            || { error "cannot commit runtime-restored checkpoint"; _failed=true; }
        if ! "${_failed}"; then _rb_phase="runtime_restored"; fi
    fi

    # ---- 5b  Restore /etc/conduit-cc --------------------------------------- #
    step "5b - Restoring ${CONF_DIR} from backup"
    if [[ -f "${BACKUP_DIR}/conf.tar.gz" ]]; then
        # extract-side .env exclusion: even a LEGACY/crafted archive containing
        # an etc/conduit-cc/.env member can never overwrite the live file.
        if tar --exclude='etc/conduit-cc/.env' -xzf "${BACKUP_DIR}/conf.tar.gz" -C /; then
            info "${CONF_DIR} restored"
        else
            error "tar extraction failed for conf.tar.gz."
            _failed=true
        fi
    else
        warn "conf.tar.gz not found - skipping ${CONF_DIR} restore"
    fi

    # ---- 5c  Restore /opt/conduit-cc code ---------------------------------- #
    step "5c - Restoring ${APP_DIR} code from backup"
    if [[ -d "${BACKUP_DIR}/app" ]]; then
        # --checksum (CP-001): the rollback restore must be content-exact. Backups
        # preserve mtime=0, so without --checksum a same-length changed file could
        # tie the size+mtime quick-check and NOT be reverted -- a silent PARTIAL
        # rollback that reports success. Content-hash comparison prevents that.
        if rsync -a --checksum --delete \
                --chown=root:root \
                --chmod=D0755,F0644 \
                "${CCC_LIFECYCLE_EXCLUDES[@]}" \
                --exclude '__pycache__/' \
                --exclude '*.pyc' \
                --exclude 'ccc.db' \
                "${BACKUP_DIR}/app/" "${APP_DIR}/"; then
            # Epic-1: rollback restores the SAME root-owned contract as deploy;
            # the old broad recursive service-account chown (with suppressed errors) is gone.
            _rollback_check _verify_app_dir_ownership
            _rollback_check _verify_store_ownership
            _rollback_check _verify_trust_dir
            # NOTE: the authorized trust anchor is DELIBERATELY preserved and may
            # differ from any pre-bootstrap legacy bytes -- byte-identity is NOT
            # asserted on the rollback path (finding 4). The anchor is validated
            # for correct object/owner/mode by _verify_trust_dir above.
            info "${APP_DIR} code restored (root-owned)"
            step "5c1 - Purging stale Python bytecode (post-restore)"
            _purge_bytecode
            info "Stale bytecode purged under ${APP_DIR} (venv preserved)"
        else
            error "rsync restore failed for ${APP_DIR}."
            _failed=true
        fi
    else
        error "Backup app/ directory not found - code not restored."
        _failed=true
    fi

    # ---- 5d  Runtime already restored by the SELECTOR (never pip) ----------- #
    step "5d - Verifying restored runtime (no reinstall; selector-based rollback)"
    # The previous runtime was NEVER modified (pip only ever ran in candidate
    # staging), so rollback needs no dependency reconstruction and no network:
    # the selector flip in 5a1 restored the exact previous bytes.
    if [[ -L "${APP_DIR}/venv" ]]; then
        _rt validate-selector >/dev/null \
            || { error "restored selector failed validation"; _failed=true; }
        info "restored runtime verified via selector gate"
    elif [[ -d "${APP_DIR}/venv" ]]; then
        info "legacy real-directory runtime restored (pre-conversion layout)"
    else
        error "no runtime present at ${APP_DIR}/venv after rollback"
        _failed=true
    fi

    # ---- 5d1  Reconcile installed helpers + sudoers to the EXACT record ----- #
    step "5d1 - Restoring exact previous helper set + sudoers"
    if [[ -f "${BACKUP_DIR}/bin-present" ]]; then
        local _bin_before _helper_failed=false
        _bin_before="$(cat "${BACKUP_DIR}/bin-present")"
        case "${_bin_before}" in
            present|absent) ;;
            *) error "invalid helper-directory presence record"; _helper_failed=true;;
        esac
        if [[ "${_bin_before}" == "present" || "${_bin_before}" == "absent" ]]; then
            if [[ -e /opt/conduit-cc/bin || -L /opt/conduit-cc/bin ]]; then
                if [[ -L /opt/conduit-cc/bin || ! -d /opt/conduit-cc/bin || \
                      "$(stat -c '%U:%a' /opt/conduit-cc/bin 2>/dev/null || true)" != "root:755" ]]; then
                    error "live helper directory is not a real root-owned mode-0755 directory"
                    _helper_failed=true
                else
                    # The exact fixed path is authorized by the presence record.
                    # Remove the complete current set (including dotfiles and
                    # same-name nested objects), then reconstruct prior bytes.
                    rm -rf /opt/conduit-cc/bin
                fi
            fi
            if ! "${_helper_failed}" && [[ "${_bin_before}" == "present" ]]; then
                install -d -o root -g root -m 0755 /opt/conduit-cc/bin
                if cp -a "${BACKUP_DIR}/bin/." /opt/conduit-cc/bin/; then
                    info "helper directory rebuilt from the exact previous byte set"
                else
                    error "previous helper byte set could not be restored"
                    _helper_failed=true
                fi
            elif ! "${_helper_failed}"; then
                info "helper directory was absent before update - new directory removed"
            fi
        fi
        if "${_helper_failed}"; then
            _failed=true
        else
            info "helper set reconciled to the recorded previous state"
        fi
    else
        warn "no helper-set record in backup (pre-v0.3.19 backup) - helpers left as-is"
    fi
    # sudoers: restore bytes if it existed; REMOVE the new file if it did not.
    local _sudoers_before
    _sudoers_before="$(cat "${BACKUP_DIR}/sudoers-present" 2>/dev/null || echo unknown)"
    if [[ "${_sudoers_before}" == "absent" ]]; then
        rm -f /etc/sudoers.d/conduit-cc
        info "sudoers was absent before update - new file removed"
    elif [[ "${_sudoers_before}" == "present" && \
            -f "${BACKUP_DIR}/sudoers.conduit-cc" ]]; then
        local _sd_tmp
        _sd_tmp="$(mktemp /etc/sudoers.d/.conduit-cc.XXXXXX)"
        cp "${BACKUP_DIR}/sudoers.conduit-cc" "${_sd_tmp}"
        chown root:root "${_sd_tmp}"; chmod 440 "${_sd_tmp}"
        if visudo -cf "${_sd_tmp}"; then
            mv -f "${_sd_tmp}" /etc/sudoers.d/conduit-cc
            info "previous sudoers restored (visudo-validated, atomic)"
        else
            rm -f "${_sd_tmp}"
            error "recorded sudoers failed visudo validation - live sudoers unchanged"
            _failed=true
        fi
    else
        error "invalid or incomplete sudoers presence record"
        _failed=true
    fi
    # A4 deliberate exception: a trust anchor provisioned during this attempt's
    # ceremony is PRESERVED (never restored from any backup) -- rollback is a
    # recorded SECURITY TRANSITION for the anchor, not byte-identity.

    # ---- 5e  Restore the exact recorded systemd-unit state ----------------- #
    step "5e - Restoring exact systemd unit state"
    local _conduit_unit="/etc/systemd/system/conduit.service"
    local _conduit_before _dropin_before
    if [[ ! -L "${BACKUP_DIR}/conduit-cc.service" \
          && -f "${BACKUP_DIR}/conduit-cc.service" ]] \
            && _install_unit_atomic "${BACKUP_DIR}/conduit-cc.service" "${SYSTEMD_UNIT}"; then
        info "${SYSTEMD_UNIT} restored atomically"
    else
        error "exact CCC systemd-unit backup is missing or unsafe"
        _failed=true
    fi

    _conduit_before="$(cat "${BACKUP_DIR}/conduit-service-present" 2>/dev/null || echo invalid)"
    case "${_conduit_before}" in
        present)
            if [[ ! -L "${BACKUP_DIR}/conduit.service" \
                  && -f "${BACKUP_DIR}/conduit.service" ]] \
                    && _install_unit_atomic "${BACKUP_DIR}/conduit.service" "${_conduit_unit}"; then
                info "${_conduit_unit} restored atomically"
            else
                error "recorded conduit.service bytes are missing or unsafe"
                _failed=true
            fi
            ;;
        absent)
            if [[ -d "${_conduit_unit}" && ! -L "${_conduit_unit}" ]]; then
                error "refusing to remove unexpected directory at ${_conduit_unit}"
                _failed=true
            elif rm -f -- "${_conduit_unit}"; then
                info "${_conduit_unit} restored to recorded absence"
            else
                error "cannot restore recorded absence of ${_conduit_unit}"
                _failed=true
            fi
            ;;
        *)
            error "invalid conduit.service presence record"
            _failed=true
            ;;
    esac

    _dropin_before="$(cat "${BACKUP_DIR}/conduit-dropin-dir-present" 2>/dev/null || echo invalid)"
    case "${_dropin_before}" in
        present)
            [[ ! -L /etc/systemd/system/conduit.service.d \
               && -d /etc/systemd/system/conduit.service.d \
               && "$(stat -c '%U:%G:%a' /etc/systemd/system/conduit.service.d 2>/dev/null || true)" == "root:root:755" ]] \
                || { error "recorded conduit drop-in directory is missing or unsafe"; _failed=true; }
            ;;
        absent)
            if [[ -L /etc/systemd/system/conduit.service.d ]]; then
                error "refusing to remove symlinked conduit drop-in directory"
                _failed=true
            elif [[ -d /etc/systemd/system/conduit.service.d ]]; then
                rmdir /etc/systemd/system/conduit.service.d 2>/dev/null \
                    || { error "new conduit drop-in directory is not empty; refusing removal"; _failed=true; }
            elif [[ -e /etc/systemd/system/conduit.service.d ]]; then
                error "unexpected object at conduit drop-in directory path"
                _failed=true
            fi
            ;;
        *)
            error "invalid conduit drop-in-directory presence record"
            _failed=true
            ;;
    esac
    if ! systemctl daemon-reload; then
        error "systemd daemon-reload failed after exact unit restoration"
        _failed=true
    fi
    if ! "${_failed}" && [[ "${_rb_phase}" == "runtime_restored" ]]; then
        _tx_mark files_restored \
            || { error "cannot commit files-restored checkpoint"; _failed=true; }
        if ! "${_failed}"; then _rb_phase="files_restored"; fi
    fi

    # ---- 5f  Re-apply nginx configuration ---------------------------------- #
    step "5f - Re-applying nginx configuration"
    if command -v nginx &>/dev/null && [[ -n "${CF_RECORD_NAME:-}" ]] \
            && [[ -f "${APP_DIR}/deployment/conduit-cc.nginx" ]]; then
        # Mirror the forward deploy path (step 3d) exactly instead of the old
        # hand-rolled partial substitution: that inline `sed` resolved only
        # <CF_RECORD_NAME> and left <CF_HTTPS_PORT>/<CF_HTTPS_REDIRECT_SUFFIX>
        # unresolved in the written file, and only warned (never restored) on
        # a failed `nginx -t`. The shared helper renders every placeholder,
        # backs up the previous site first, and restores that backup itself
        # on failure, so this can never leave a half-rendered file on disk.
        # ${CONF_DIR} was already restored from backup in step 5b above, so
        # its https_port is the correct pre-attempt value, not the candidate's.
        local _rb_https_port
        _rb_https_port="$(json_get "d.get('web',{}).get('https_port',443)" \
            < "${CONF_DIR}/config.json" 2>/dev/null || true)"
        [[ "${_rb_https_port}" =~ ^[0-9]+$ ]] || _rb_https_port=443
        # Rollback-context validators must never exit the process here (that
        # would abort rollback before the service restart in 5g/5h); record a
        # soft failure instead, same as every other check in this function.
        if /opt/conduit-cc/bin/ccc-apply-https-port apply \
                --port "${_rb_https_port}" --hostname "${CF_RECORD_NAME}" \
                >/dev/null 2>&1; then
            info "nginx config re-applied (HTTPS port ${_rb_https_port})"
        else
            error "nginx re-apply failed after rollback; the helper restores its" \
                  "own last-known-good backup on failure, so the site on disk is" \
                  "not broken, but verify with 'nginx -t' before relying on HTTPS"
            _failed=true
        fi
    else
        warn "nginx rollback skipped (nginx absent, CF_RECORD_NAME empty," \
             "or nginx template missing)"
    fi

    # ---- 5g  Start service ------------------------------------------------- #
    step "5g - Starting ${SERVICE_NAME}"
    if [[ "${_rb_phase}" == "files_restored" ]]; then
        _tx_mark service_restore_intent \
            || { error "cannot commit service-restore intent"; _failed=true; }
        if ! "${_failed}"; then _rb_phase="service_restore_intent"; fi
    elif [[ "${_rb_phase}" != "service_restore_intent" ]]; then
        error "rollback transaction is not ready to restart the service (${_rb_phase})"
        _failed=true
    fi
    if systemctl start "${SERVICE_NAME}"; then
        info "${SERVICE_NAME} started"
    else
        error "Failed to start ${SERVICE_NAME}."
        _failed=true
    fi

    # ---- 5h  Rollback health check ----------------------------------------- #
    step "5h - Verifying rollback health (${CURRENT_VERSION})"
    local _rb_attempts=0
    local _rb_max=$(( HEALTH_TIMEOUT / HEALTH_INTERVAL ))
    local _rb_ok=false

    while [[ "${_rb_attempts}" -lt "${_rb_max}" ]]; do
        local _rb_resp
        _rb_resp="$(curl -sf "http://127.0.0.1:8000/api/health" 2>/dev/null)" || true
        if [[ -n "${_rb_resp}" ]]; then
            local _rb_status _rb_ver
            _rb_status="$(echo "${_rb_resp}" | json_get "d.get('status','')")"
            _rb_ver="$(echo "${_rb_resp}" | json_get "d.get('version','')")"
            if [[ "${_rb_status}" == "ok" && "${_rb_ver}" == "${CURRENT_VERSION}" ]]; then
                info "Rollback health check passed (version=${_rb_ver})"
                _rb_ok=true
                break
            elif [[ "${_rb_status}" == "ok" ]]; then
                warn "rollback service is healthy but reports v${_rb_ver}; expected v${CURRENT_VERSION}"
            fi
        fi
        _rb_attempts=$(( _rb_attempts + 1 ))
        step "  Waiting... (${_rb_attempts}/${_rb_max})"
        sleep "${HEALTH_INTERVAL}"
    done

    if ! "${_rb_ok}"; then
        error "Service did not become healthy after rollback."
        error "Check: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
        _failed=true
    fi

    if "${_failed}"; then
        _tx_mark diagnostic_failure >/dev/null 2>&1 || true
        _ROLLBACK_ACTIVE=false
        return 1
    fi
    _tx_mark rolled_back \
        || { error "cannot commit rolled-back terminal checkpoint"; _ROLLBACK_ACTIVE=false; return 1; }
    _ROLLBACK_ACTIVE=false
    return 0
}

# --------------------------------------------------------------------------- #
#  Phase 6 - Summary                                                          #
# --------------------------------------------------------------------------- #

phase6_summary() {
    section "Update complete"
    printf "\n"

    # Retention mutates backup history, so it runs only AFTER the update is
    # durably terminal-success. A hard crash while building the new backup can
    # therefore never delete the last known-good rollback reserve.
    step "6a - Rotating old backups after durable success (keeping last ${BACKUP_KEEP})"
    if ! _rotate_backups; then
        warn "backup rotation failed after a successful update; retained backups were not required for rollback"
    fi

    if [[ "${CURRENT_VERSION}" == "${NEW_VERSION}" ]]; then
        printf "  Re-deployed version ${BOLD}%s${RESET} (same version).\n" \
            "${NEW_VERSION}"
    else
        printf "  Updated ${BOLD}%s${RESET}  ->  ${BOLD}%s${RESET}.\n" \
            "${CURRENT_VERSION}" "${NEW_VERSION}"
    fi

    printf "\n"
    printf "  Backup retained at:\n"
    printf "    %s\n" "${BACKUP_DIR}"
    printf "\n"
    printf '  %bNot modified during update:%b\n' "${BOLD}" "${RESET}"
    printf "    %-42s%s\n" "${CONF_DIR}/.env" "(credentials and secrets)"
    printf "    %-42s%s\n" "${CONF_DIR}/tls/" "(TLS certificate and private key)"
    printf "    %-42s%s\n" "${CONF_DIR}/config.json" "(operator settings)"
    printf "    %-42s%s\n" "${CONF_DIR}/ccc.db" "(runtime database)"
    printf "    %-42s%s\n" "${LOG_DIR}/" "(logs)"
    printf "    %-42s%s\n" "${CONDUIT_DATA_DIR}/data/conduit_key.json" "(Conduit node identity — never touched)"
    printf "\n"
    printf '  %bPost-update review (optional):%b\n' "${BOLD}" "${RESET}"
    printf "    New config options: diff %s/config.example.json %s/config.json\n" \
        "${APP_DIR}" "${CONF_DIR}"
    printf "    Release notes:      cat %s/CHANGELOG.md\n" "${APP_DIR}"
    printf "\n"
    printf "  UFW rules were not modified.  Review with: sudo ufw status\n"
    printf "\n"
}

# --------------------------------------------------------------------------- #
#  Phase M2 - Configuration write-artifact verification                        #
# --------------------------------------------------------------------------- #
#  Read-only acceptance gate inside Phase 3. All helper, sudoers and unit       #
#  mutations are completed by the single transactional writer before this      #
#  function runs. A mismatch aborts before the deployed checkpoint is committed.#
phase_m2_config_write_artifacts() {
    section "Phase M2 - Config write artifacts"

    local _helper_src="${SOURCE_DIR}/deployment/bin/ccc-apply-conduit-config"
    local _helper_dst="/opt/conduit-cc/bin/ccc-apply-conduit-config"
    local _unit_src="${SOURCE_DIR}/deployment/conduit.service"
    local _unit_dst="/etc/systemd/system/conduit.service"
    local _sudoers="/etc/sudoers.d/conduit-cc"
    local _app_user="${APP_USER:-conduit-cc}"

    # Phase 3 is THE single writer for helpers, sudoers and systemd units. M2
    # must never append to policy, deploy a helper, create a directory, copy a
    # unit or call daemon-reload.
    local _hm
    [[ ! -L "${_helper_src}" && -f "${_helper_src}" \
       && ! -L "${_helper_dst}" && -f "${_helper_dst}" ]] \
        || die "M2 config helper source/deployed object is missing or unsafe"
    _hm="$(stat -c '%U:%G:%a' "${_helper_dst}")"
    [[ "${_hm}" == "root:root:755" ]] \
        || die "Config helper ownership/perms wrong (${_hm}); expected root:root:755"
    cmp -s "${_helper_src}" "${_helper_dst}" \
        || die "M2 config helper differs from the verified deployed source"
    info "Config helper verified (single Phase-3 writer; root:root 0755)"

    # (4b) Personal compartment helper (C4). Root-owned 0755; run AS conduit via
    # the (conduit) grant below. Not writable by ${_app_user} or conduit.
    local _pc_helper_src="${SOURCE_DIR}/deployment/bin/ccc-personal-compartment"
    local _pc_helper_dst="/opt/conduit-cc/bin/ccc-personal-compartment"
    local _pcm
    [[ ! -L "${_pc_helper_src}" && -f "${_pc_helper_src}" \
       && ! -L "${_pc_helper_dst}" && -f "${_pc_helper_dst}" ]] \
        || die "personal compartment helper source/deployed object is missing or unsafe"
    _pcm="$(stat -c '%U:%G:%a' "${_pc_helper_dst}")"
    [[ "${_pcm}" == "root:root:755" ]] \
        || die "Personal compartment helper ownership/perms wrong (${_pcm}); expected root:root:755"
    cmp -s "${_pc_helper_src}" "${_pc_helper_dst}" \
        || die "personal compartment helper differs from verified deployed source"

    # (4c) Ryve claim helper (Epic #3, R1). Root-owned 0755; run AS conduit via
    # the (conduit) grant below. Not writable by ${_app_user} or conduit.
    local _rv_helper_src="${SOURCE_DIR}/deployment/bin/ccc-ryve-claim"
    local _rv_helper_dst="/opt/conduit-cc/bin/ccc-ryve-claim"
    local _rvm
    [[ ! -L "${_rv_helper_src}" && -f "${_rv_helper_src}" \
       && ! -L "${_rv_helper_dst}" && -f "${_rv_helper_dst}" ]] \
        || die "Ryve claim helper source/deployed object is missing or unsafe"
    _rvm="$(stat -c '%U:%G:%a' "${_rv_helper_dst}")"
    [[ "${_rvm}" == "root:root:755" ]] \
        || die "Ryve claim helper ownership/perms wrong (${_rvm}); expected root:root:755"
    cmp -s "${_rv_helper_src}" "${_rv_helper_dst}" \
        || die "Ryve claim helper differs from verified deployed source"

    # (5-6) Sudoers is verification-only here. The atomic Phase-3 renderer is
    # the sole writer, so no crash can leave a partially appended live policy.
    [[ ! -L "${_sudoers}" && -f "${_sudoers}" \
       && "$(stat -c '%U:%G:%a' "${_sudoers}")" == "root:root:440" ]] \
        || die "M2 sudoers policy is missing, symlinked, or has wrong metadata"
    visudo -cf "${_sudoers}" >/dev/null \
        || die "M2 sudoers policy failed syntax verification"
    grep -Fxq "${_app_user} ALL=(root) NOPASSWD: ${_helper_dst}" "${_sudoers}" \
        || die "M2 config-helper sudoers grant is absent"
    grep -Fxq "${_app_user} ALL=(conduit) NOPASSWD: ${_pc_helper_dst}" "${_sudoers}" \
        || die "M2 personal-helper sudoers grant is absent"
    grep -Fxq "${_app_user} ALL=(conduit) NOPASSWD: ${_rv_helper_dst}" "${_sudoers}" \
        || die "M2 Ryve-helper sudoers grant is absent"
    info "M2 helper and sudoers contracts revalidated without mutation"

    # (7) Parameterized conduit.service and its helper-owned drop-in directory:
    # verify exact normalized bytes and metadata without mutation.
    if [[ -e "${_unit_dst}" || -L "${_unit_dst}" ]]; then
        [[ ! -L "${_unit_src}" && -f "${_unit_src}" \
           && ! -L "${_unit_dst}" && -f "${_unit_dst}" ]] \
            || die "managed conduit.service source/destination is unsafe"
        [[ "$(stat -c '%U:%G:%a' "${_unit_dst}")" == "root:root:644" ]] \
            || die "managed conduit.service metadata is not root:root 0644"
        cmp -s <(sed 's/\r$//' "${_unit_src}") "${_unit_dst}" \
            || die "managed conduit.service differs from the verified source"
        local _dropin_dir="/etc/systemd/system/conduit.service.d"
        [[ ! -L "${_dropin_dir}" && -d "${_dropin_dir}" \
           && "$(stat -c '%U:%G:%a' "${_dropin_dir}")" == "root:root:755" ]] \
            || die "conduit.service drop-in directory is missing or unsafe"
        info "conduit.service and drop-in directory verified without mutation"
    else
        info "Conduit unit is unmanaged on this host; unit verification skipped"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase BS1 guard - reduced-mode artifacts present after migration           #
# --------------------------------------------------------------------------- #
#  Fail-safe: confirm the DEPLOYED helper + unit actually support reduced mode  #
#  BEFORE the new CCC code is started. A mismatch (stale source, partial copy,  #
#  old unit) aborts the update; the EXIT trap then runs phase5_rollback to      #
#  restore the previous working state (old code + old helper). Read-only.       #
phase_bs1_reduced_guard() {
    section "Phase BS1 - Reduced-mode artifact guard"
    local _helper_dst="/opt/conduit-cc/bin/ccc-apply-conduit-config"
    local _unit_dst="/etc/systemd/system/conduit.service"
    local _t

    step "BS1-a — Verifying reduced-capable helper"
    if [[ ! -f "${_helper_dst}" ]] || ! grep -q -- "--reduced-start-min" "${_helper_dst}"; then
        die "Reduced-mode helper missing or outdated (${_helper_dst} lacks --reduced-start-min)." \
            "The new backend requires the reduced-capable helper; aborting to roll back."
    fi
    info "Helper supports reduced-mode arguments"

    # The unit is only present when Conduit is managed on this host. When absent,
    # skip the unit guard (the helper guard above still applies).
    if [[ -f "${_unit_dst}" ]]; then
        step "BS1-b — Verifying conduit.service reduced tokens"
        for _t in \
            "--set InproxyReducedStartTime=\${CCC_REDUCED_START}" \
            "--set InproxyReducedEndTime=\${CCC_REDUCED_END}" \
            "--set InproxyReducedMaxCommonClients=\${CCC_REDUCED_MAXCOMMON}" \
            "--set InproxyReducedLimitUpstreamBytesPerSecond=\${CCC_REDUCED_UP}" \
            "--set InproxyReducedLimitDownstreamBytesPerSecond=\${CCC_REDUCED_DOWN}"; do
            grep -qF -- "${_t}" "${_unit_dst}" \
                || die "conduit.service is missing reduced token: ${_t}" \
                       "Aborting to roll back to the previous working unit."
        done
        for _t in CCC_REDUCED_START CCC_REDUCED_END CCC_REDUCED_MAXCOMMON CCC_REDUCED_UP CCC_REDUCED_DOWN; do
            grep -qE "^Environment=${_t}=" "${_unit_dst}" \
                || die "conduit.service is missing default: Environment=${_t}" \
                       "Aborting to roll back to the previous working unit."
        done
        # Personal-clients token + default (C2). The base unit MUST carry the =0
        # default so the braced ${CCC_MAX_PERSONAL_CLIENTS} never expands empty
        # (an empty --max-personal-clients argument fails Conduit startup). The
        # compartment ID must NOT be on ExecStart (auto-loaded from disk).
        grep -qF -- "--max-personal-clients \${CCC_MAX_PERSONAL_CLIENTS}" "${_unit_dst}" \
            || die "conduit.service is missing personal token: --max-personal-clients \${CCC_MAX_PERSONAL_CLIENTS}" \
                   "Aborting to roll back to the previous working unit."
        grep -qE "^Environment=CCC_MAX_PERSONAL_CLIENTS=0$" "${_unit_dst}" \
            || die "conduit.service is missing default: Environment=CCC_MAX_PERSONAL_CLIENTS=0" \
                   "Aborting to roll back to the previous working unit."
        if grep -qF -- "--compartment-id" "${_unit_dst}"; then
            die "conduit.service must NOT pass --compartment-id (auto-loaded from personal_compartment.json)" \
                "Aborting to roll back to the previous working unit."
        fi
        info "conduit.service has all reduced + personal-clients tokens + defaults"
    else
        info "Conduit unit not installed here — skipping unit token guard"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 3b - Start service (deferred until reduced artifacts are verified)    #
# --------------------------------------------------------------------------- #
#  The start moved here from phase3 step 3h (BS1 Commit 3) so the new CCC code  #
#  never serves before the reduced-capable helper + unit are in place.         #
phase3b_start_service() {
    section "Phase 3b - Start ${SERVICE_NAME}"
    step "3b-1 — Starting ${SERVICE_NAME}"
    _tx_mark service_start_intent \
        || die "cannot commit service-start intent"
    systemctl start "${SERVICE_NAME}"
    _tx_mark service_started \
        || die "service started but checkpoint could not be committed"
    info "${SERVICE_NAME} started"
}

# --------------------------------------------------------------------------- #
#  Entry point                                                                 #
# --------------------------------------------------------------------------- #

_parse_args "$@"
phase0_preflight
phase1_backup
phase2_preinstall
if [[ "${CCC_ONLY}" == true ]]; then
    info "CCC-only update (--ccc-only): skipping Conduit Core binary update (Phase 2b)"
else
    phase2b_conduit_update
fi
phase3_deploy
phase_bs1_reduced_guard
phase3b_start_service
phase4_verify
phase6_summary
