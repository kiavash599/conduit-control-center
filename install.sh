#!/usr/bin/env bash
# install.sh — Conduit Control Center installer
# ==============================================
# Installs the CCC dashboard on Ubuntu 22.04 ARM64 behind a Cloudflare proxy.
#
# Usage:   sudo bash install.sh --authorized-identity-file <verified-identity.json>
# Docs:    docs/pre-install.md
#          docs/tls-setup.md
#
# Phases:
#   Phase 1 — Validate prerequisites and collect configuration (no changes)
#   Phase 2 — Install and configure all components
#   Phase 3 — Print post-install summary
#
# Run from the root of the conduit-control-center repository.
#
# Architecture notes (deviations from Issue #9 spec — see commit message):
#   - ADMIN_PASSWORD_HASH written to .env (not config.json); backend reads it
#     from .env via pydantic-settings Settings.admin_password_hash
#   - Token validated in Phase 1e (zone lookup) not Phase 1d (ordering fix)
#   - rsync uses --exclude venv/ --exclude ccc.db --exclude __pycache__/
#   - nginx: default site removed, sites-enabled symlink created explicitly
#   - /var/log/conduit-cc/ created by install.sh (not DDNS script) with
#     conduit-cc ownership so the cron job can write immediately
#   - sudoers rule created for adapter.py sudo systemctl calls

set -euo pipefail
# Deterministic root-owned runtime/code modes on every host, independent of the
# invoking Owner/sudo policy. The recursive runtime gate rejects g/o-writable
# entries; candidate creation must not inherit an ambient 0002 umask.
umask 022

# --------------------------------------------------------------------------- #
#  Constants                                                                   #
# --------------------------------------------------------------------------- #

readonly APP_USER="conduit-cc"
readonly APP_DIR="/opt/conduit-cc"
readonly CONF_DIR="/etc/conduit-cc"
readonly TLS_DIR="/etc/conduit-cc/tls"
readonly LOG_DIR="/var/log/conduit-cc"
readonly SERVICE_NAME="conduit-cc"
readonly NGINX_RATELIMIT="/etc/nginx/conf.d/${SERVICE_NAME}-ratelimit.conf"
readonly SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
readonly SUDOERS_FILE="/etc/sudoers.d/${SERVICE_NAME}"
readonly DDNS_BIN="/usr/local/bin/cloudflare-ddns.sh"
readonly CF_API="https://api.cloudflare.com/client/v4"
readonly MIN_PW_LEN=12
readonly HEALTH_TIMEOUT=60      # seconds
readonly HEALTH_INTERVAL=5      # seconds

# Psiphon Conduit binary — installed alongside CCC (Issue #45)
# CONDUIT_VERSION is the only tested/supported release.  update.sh bumps it
# when a new Conduit release has been validated against CCC.
readonly CONDUIT_VERSION="2.0.0"
readonly CONDUIT_USER="conduit"
readonly CONDUIT_BIN_DIR="/opt/conduit"
readonly CONDUIT_DATA_DIR="/var/lib/conduit"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# Populated by phase1_validate; consumed by phase2_install / phase3_summary
CF_API_TOKEN=""
CF_ZONE_NAME=""
CF_ZONE_ID=""
CF_RECORD_NAME=""
TLS_CERT_PATH=""
TLS_KEY_PATH=""
HTTPS_PORT=""       # selected Cloudflare-supported public HTTPS port (Feature 1)
ADMIN_USERNAME=""
ADMIN_PASSWORD=""   # cleared immediately after hashing in Phase 2g
INSTALL_SOURCE_COMMIT=""
INSTALL_SOURCE_TAG=""
INSTALL_IDENTITY_FILE=""

# Set by phase1_validate step 1x; consumed by phase2_install step 2x-c.
# Values: an absolute path to the binary, or the string "download".
CONDUIT_BIN_SRC=""

# --------------------------------------------------------------------------- #
#  Terminal colours (disabled if not a TTY)                                   #
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

info()    { printf "${GREEN}  ✓${RESET}  %s\n" "$*"; }
step()    { printf "${CYAN}[CCC]${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}  !${RESET}  %s\n" "$*" >&2; }
section() { printf "\n${BOLD}%s${RESET}\n%s\n" "$*" "$(printf '─%.0s' {1..60})"; }

die() {
    printf "\n${RED}ERROR:${RESET} %s\n" "$1" >&2
    if [[ -n "${2:-}" ]]; then
        printf "${YELLOW}  FIX:${RESET} %s\n" "$2" >&2
    fi
    exit 1
}

_parse_install_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --authorized-source-commit)
                [[ $# -ge 2 ]] || die "--authorized-source-commit requires a value."
                INSTALL_SOURCE_COMMIT="$2"; shift 2;;
            --authorized-source-tag)
                [[ $# -ge 2 ]] || die "--authorized-source-tag requires a value."
                INSTALL_SOURCE_TAG="$2"; shift 2;;
            --authorized-identity-file)
                [[ $# -ge 2 ]] || die "--authorized-identity-file requires a path."
                INSTALL_IDENTITY_FILE="$2"; shift 2;;
            --help|-h)
                printf 'Usage: sudo bash install.sh --authorized-identity-file <verified-identity.json>\n'
                exit 0;;
            *) die "Unknown installer argument: $1";;
        esac
    done
    if [[ -n "${INSTALL_IDENTITY_FILE}" ]]; then
        [[ -z "${INSTALL_SOURCE_COMMIT}" && -z "${INSTALL_SOURCE_TAG}" ]] \
            || die "Use either --authorized-identity-file or direct source identity, never both."
        local _identity _expected_uid="${SUDO_UID:-0}"
        [[ "${_expected_uid}" =~ ^[0-9]+$ ]] \
            || die "Cannot determine the verified identity-file owner."
        if ! _identity="$(/usr/bin/python3 -I - "${INSTALL_IDENTITY_FILE}" \
                "${_expected_uid}" <<'PY'
import json
import os
import stat
import sys

path, expected_uid = sys.argv[1], int(sys.argv[2])
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(path, flags)
try:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise ValueError("not a single-link regular file")
    if st.st_uid != expected_uid or stat.S_IMODE(st.st_mode) != 0o600:
        raise ValueError("wrong owner or mode (expected invoking owner, 0600)")
    raw = os.read(fd, 64 * 1024 + 1)
finally:
    os.close(fd)
if len(raw) > 64 * 1024:
    raise ValueError("identity file too large")

def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out

doc = json.loads(
    raw.decode("utf-8"), object_pairs_hook=unique,
    parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
)
required = {
    "schema", "product", "version", "platform", "artifact_name",
    "source_commit", "source_tag", "manifest_sha256", "signature_sha256",
    "artifact_sha256",
}
if not isinstance(doc, dict) or set(doc) != required or doc.get("schema") != 1:
    raise ValueError("identity schema mismatch")
print(doc["source_commit"])
print(doc["source_tag"])
PY
        )"; then
            die "Verified install identity file is invalid or unsafe."
        fi
        INSTALL_SOURCE_COMMIT="${_identity%%$'\n'*}"
        INSTALL_SOURCE_TAG="${_identity#*$'\n'}"
        [[ "${INSTALL_SOURCE_COMMIT}" != "${INSTALL_SOURCE_TAG}" \
           && "${INSTALL_SOURCE_TAG}" != *$'\n'* ]] \
            || die "Verified install identity output is malformed."
    fi
    [[ "${INSTALL_SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] \
        || die "Fresh install requires the source commit from the verified signed release manifest."
    local _version
    _version="$(_read_version_file "${SCRIPT_DIR}/backend/_version.py" || true)"
    [[ -n "${_version}" && "${INSTALL_SOURCE_TAG}" == "v${_version}" ]] \
        || die "Fresh install source tag must match this verified tree (v${_version:-unknown})."
}

# Assign a value to a named variable without nameref.
# Usage: assign VARNAME "value"
assign() { printf -v "$1" '%s' "$2"; }

# Values written to the shell-sourced canonical .env are serialized inside
# single quotes. Refuse the only quote terminator plus line-framing controls;
# no user input may become shell syntax when cloudflare-ddns.sh sources it.
_require_env_scalar() {
    local _label="$1" _value="$2"
    [[ "${_value}" != *"'"* && "${_value}" != *$'\n'* && "${_value}" != *$'\r'* ]] \
        || die "${_label} contains a character that cannot be represented safely in .env."
}

# Parse the single exact APP_VERSION assignment as data; never execute a
# candidate's Python source merely to discover its version.
_read_version_file() {
    local _f="$1"
    [[ ! -L "${_f}" && -f "${_f}" ]] || return 1
    awk '
        /^APP_VERSION = "[0-9]+\.[0-9]+\.[0-9]+"$/ {
            value=$0
            sub(/^APP_VERSION = "/, "", value)
            sub(/"$/, "", value)
            count++
        }
        END { if (count != 1) exit 2; print value }
    ' "${_f}"
}

# Prompt for a plain-text value.  Result is stored in the named variable.
# Usage: prompt VARNAME "Prompt text" ["default"]
prompt() {
    local _var="$1"
    local _msg="$2"
    local _default="${3:-}"
    local _input
    if [[ -n "$_default" ]]; then
        read -r -p "  ${_msg} [${_default}]: " _input
        _input="${_input:-$_default}"
    else
        read -r -p "  ${_msg}: " _input
    fi
    assign "$_var" "$_input"
}

# Prompt for a secret value (input hidden).
# Usage: prompt_secret VARNAME "Prompt text"
prompt_secret() {
    local _var="$1"
    local _msg="$2"
    local _input
    read -r -s -p "  ${_msg}: " _input
    printf '\n'
    assign "$_var" "$_input"
}

# Call the Cloudflare API.  Always returns 0 (callers check output).
# CF_API_TOKEN must be set before calling.
# Usage: cf_api GET "/zones?name=example.com"
cf_api() {
    local _method="$1"
    local _path="$2"
    curl -sf -X "$_method" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json" \
        "${CF_API}${_path}" 2>/dev/null || true
}

# Extract a field from JSON on stdin using python3.
# $1 is a Python expression evaluated with 'd' bound to the parsed JSON.
# Returns empty string on any error.
# Usage: echo "$json" | json_get "d['result'][0]['id']"
json_get() {
    python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null || true
}

# Cloudflare-supported HTTPS ports, in selection-preference order (443 first,
# then 8443 as the most recognizable alternate, then the 20xx set). Feature 1.
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
# Args: <pip_bin> <requirements_file> <wheelhouse_dir>
# --------------------------------------------------------------------------- #
#  Epic-1 ownership invariants (F1/F6)                                        #
#                                                                             #
#  Deployed code under APP_DIR: root:root, dirs 0755 / files 0644 (helpers    #
#  0755 via their explicit `install` lines). The service account may read    #
#  execute but never write. Service-writable data lives OUTSIDE the           #
#  executable trust closure (/etc/conduit-cc, /var/log/..., /var/lib/...).    #
# --------------------------------------------------------------------------- #

# Verify no path under APP_DIR (excluding none) is owned/writable by the
# service account or carries setuid/setgid. Fail closed with the offenders.
_verify_app_dir_ownership() {
    # venv/.venvs are pruned here (verified by _verify_venv_ownership /
    # _verify_store_ownership); everything else in the executable closure must
    # be root:root, NOT group/other-WRITABLE (/022), no setuid/setgid (/6000),
    # and contain no symlinks (the selector at /venv is the only sanctioned
    # link and is pruned/validated separately).
    local _bad
    _bad="$(find "${APP_DIR}" \( -path "${APP_DIR}/venv" -o -path "${APP_DIR}/.venvs" \
                 -o -path "${APP_DIR}/ccc.db" \) -prune -o \
                 \( -not -user root -o -not -group root -o -perm /6000 -o -perm /022 -o -type l \) \
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
    local _m
    _m="$(stat -c '%U:%a' "${APP_DIR}/trust")"
    [[ "${_m}" == "root:700" ]] || { echo "ERROR: trust dir must be root:700 (got ${_m})" >&2; exit 1; }
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
    _bad="$(find /opt/conduit-cc/bin \( -not -user root -o -perm /022 -o -type l \) \
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
    local _bad
    _bad="$(find "${APP_DIR}/.venvs" \( -not -user root -o -perm /6022 \) \
                 -print 2>/dev/null | head -20 || true)"
    if [[ -n "${_bad}" ]]; then
        echo "ERROR: runtime store invariant violated (non-root or writable/setuid):" >&2
        echo "${_bad}" >&2
        exit 1
    fi
}

# ONE-TIME Epic-1 transition: make the real legacy venv root-owned and
# non-service-writable. Recursive -- but STRICTLY bounded to the validated venv:
#   * exact expected path only (${APP_DIR}/venv);
#   * must be a REAL directory (symlink/non-directory rejected);
#   * canonical path must remain inside APP_DIR (no traversal outside);
# Idempotent: safe to re-run on every install/update.
_secure_legacy_venv() {
    local _venv="${APP_DIR}/venv" _real
    [[ -e "${_venv}" || -L "${_venv}" ]] || return 0      # nothing to secure yet
    if [[ -L "${_venv}" ]]; then
        # Post-conversion: validate the selector via the full runtime-store gate.
        /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime validate-selector \
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
    # Reject hardlinks, special objects and escaping symlinks before any
    # recursive mutation.  `chown -hR` then never follows a symlink target.
    /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime validate-legacy-shape \
        || { echo "ERROR: legacy venv failed its pre-mutation shape gate" >&2; exit 1; }
    chown -hR root:root "${_venv}"
    chmod -R go-w "${_venv}"
    /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime validate-legacy \
        || { echo "ERROR: legacy venv failed its post-transition trust gate" >&2; exit 1; }
    info "legacy venv secured: root-owned, non-service-writable (${_venv})"
}

# Enforce the venv half of the trust closure (root-owned, no g/o write, no setuid).
_verify_venv_ownership() {
    [[ -e "${APP_DIR}/venv" || -L "${APP_DIR}/venv" ]] || return 0
    if [[ -L "${APP_DIR}/venv" ]]; then
        # Selector layout: GNU find with default -P would evaluate the SYMLINK
        # itself (mode 777) and false-fail /6022. The selector is validated by
        # the FULL runtime-store gate; the recursive ownership scan applies to
        # the store via _verify_store_ownership.
        /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime validate-selector >/dev/null \
            || { echo "ERROR: selector failed the runtime-store gate" >&2; exit 1; }
        return 0
    fi
    /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime validate-legacy >/dev/null \
        || { echo "ERROR: legacy venv failed the runtime-store gate" >&2; exit 1; }
}

# Epic-1 state boundary + trust-anchor directory provisioning (idempotent).
_provision_priv_state_dirs() {
    install -d -o root -g root -m 0700 /var/lib/ccc-update
    install -d -o root -g root -m 0700 /var/lib/ccc-update/attempts
    install -d -o root -g root -m 0755 /var/lib/ccc-status
    if [[ -L "${APP_DIR}/trust" ]]; then
        echo "ERROR: ${APP_DIR}/trust is a symlink (refusing to provision through it)" >&2
        exit 1
    fi
    install -d -o root -g root -m 0700 "${APP_DIR}/trust"
    info "privileged state dirs provisioned (/var/lib/ccc-update 0700, /var/lib/ccc-status 0755, ${APP_DIR}/trust 0700)"
}

# --------------------------------------------------------------------------- #
#  Epic-1/2 shared lifecycle path-filter contract (A4/B2) -- MIRRORS update.sh #
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

readonly CF_HTTPS_PORTS=(443 8443 2053 2083 2087 2096)

# Echo the set of occupied local TCP listening ports (space-separated, padded).
detect_occupied_tcp_ports() {
    ss -Htln 2>/dev/null | awk '{print $4}' | sed 's/.*://' \
        | grep -E '^[0-9]+$' | sort -un | tr '\n' ' '
}

# >>> CCC-FIREWALL-PLAN >>>
# ADR-0004 purpose-aware firewall. This block is extracted verbatim between the
# >>>/<<< markers by tests/unit/test_firewall_ssh_plan.py; keep markers on their
# own lines with no trailing code.
# =========================================================================== #
#  Purpose-aware firewall plan (BL-0002 / ADR-0004)                            #
# --------------------------------------------------------------------------- #
#  A listening socket is EVIDENCE, not authorization to expose a service. The  #
#  installer opens ONLY: the evidenced local SSH admin port(s); fixed HTTP 80; #
#  the installer-selected HTTPS port. No inbound Conduit UDP. No conventional  #
#  fallback. Any genuine disagreement fails closed BEFORE any UFW write.       #
#  The installer manages LOCAL board ports only; it never inspects router/NAT. #
#  Test seam: CCC_PROC_ROOT (proc tree); ss/sshd/systemctl/ufw stubbed on PATH.#
# =========================================================================== #

FW_SSH_PORTS=""     # resolved SSH admin ports (space-separated)
FW_EVID_SIG=""      # evidence signature captured at preflight
_FW_SIG=""          # scratch: signature from _firewall_collect_plan
_FW_PLAN=""         # scratch: resolved SSH plan from _firewall_collect_plan
_FW_L=""            # scratch: runtime listener set from _firewall_collect_plan
_FW_C=""            # scratch: configured set (csv/UNREADABLE/EMPTY)

_fw_valid_port() {
    local p="$1"
    [[ "${p}" =~ ^[0-9]+$ ]] || return 1
    (( p >= 1 && p <= 65535 )) || return 1
    return 0
}

_in_set() {
    local needle="$1"; shift
    local x
    for x in "$@"; do [[ "${x}" == "${needle}" ]] && return 0; done
    return 1
}

# stdin: `systemctl show ... -p Listen --value` lines "<addr> (Stream)".
# stdout: TCP Stream ports, one per line (AF_UNIX and non-Stream skipped).
_fw_parse_listen_stream() {
    local line addr port
    while IFS= read -r line; do
        [[ "${line}" == *"(Stream)"* ]] || continue
        addr="${line%% (*}"
        case "${addr}" in
            /*) continue ;;             # AF_UNIX path -> not a TCP port
        esac
        port="${addr##*:}"              # trailing :PORT (v4, [::]:PORT, IP:PORT)
        _fw_valid_port "${port}" && printf '%s\n' "${port}"
    done
}

# Validate the RAW CCC_SSH_PORTS value. stdout: deduped ports (one per line).
# Returns 0 on success, or 2 on ANY invalid input (empty/whitespace-only,
# embedded whitespace, leading/trailing/double comma, non-numeric, out-of-range).
# Unset-vs-set is decided by the caller; this function never returns 1.
_ssh_parse_override() {
    # $1 is the RAW value of CCC_SSH_PORTS (may be empty); the caller decides
    # unset-vs-set. Empty/whitespace-only -> fatal(2). Split on commas FIRST, trim
    # each element, reject empty elements (leading/trailing/double comma) and any
    # embedded whitespace ("12 22" must NOT become 1222). Dedup; range 1..65535.
    local raw="$1"
    local nospace="${raw//[[:space:]]/}"
    [[ -n "${nospace}" ]] || return 2
    [[ "${nospace}" == ,* || "${nospace}" == *, || "${nospace}" == *,,* ]] && return 2
    local -a parts=()
    IFS=',' read -r -a parts <<< "${raw}"   # split on commas WITHOUT pathname expansion
    local p out=()
    for p in "${parts[@]}"; do
        p="${p#"${p%%[![:space:]]*}"}"
        p="${p%"${p##*[![:space:]]}"}"
        [[ -n "${p}" ]] || return 2
        [[ "${p}" == *[[:space:]]* ]] && return 2
        _fw_valid_port "${p}" || return 2
        out+=("${p}")
    done
    [[ "${#out[@]}" -ge 1 ]] || return 2
    printf '%s\n' "${out[@]}" | sort -un
    return 0
}

# Persistent configured local SSH TCP ports (C).
# stdout: ports (newline) | "UNREADABLE" | "EMPTY".
_ssh_persistent_ports() {
    local sock_active sock_enabled
    sock_active="$(systemctl is-active ssh.socket 2>/dev/null || true)"
    sock_enabled="$(systemctl is-enabled ssh.socket 2>/dev/null || true)"
    if [[ "${sock_active}" == "active" || "${sock_enabled}" == "enabled" ]]; then
        # ssh.socket governs; read effective Listen property.
        local listen ports
        listen="$(systemctl show ssh.socket --property=Listen --value 2>/dev/null || true)"
        ports="$(printf '%s\n' "${listen}" | _fw_parse_listen_stream | sort -un)"
        [[ -n "${ports}" ]] && { printf '%s\n' "${ports}"; return 0; }
        printf 'UNREADABLE\n'; return 0
    fi
    # Disabled/inactive ssh.socket is IGNORED. Use sshd -T (resolves Include/drop-ins).
    if ! command -v sshd >/dev/null 2>&1 && [[ ! -x /usr/sbin/sshd ]]; then
        printf 'EMPTY\n'; return 0            # no sshd installed
    fi
    local sshdbin sshdt rc ports
    sshdbin="$(command -v sshd 2>/dev/null || echo /usr/sbin/sshd)"
    if sshdt="$("${sshdbin}" -T 2>/dev/null)"; then rc=0; else rc=$?; fi
    if (( rc != 0 )); then printf 'UNREADABLE\n'; return 0; fi
    ports="$(printf '%s\n' "${sshdt}" | awk 'tolower($1)=="port"{print $2}' \
             | while read -r p; do _fw_valid_port "${p}" && printf '%s\n' "${p}"; done | sort -un)"
    [[ -n "${ports}" ]] && { printf '%s\n' "${ports}"; return 0; }
    printf 'UNREADABLE\n'
}

# Runtime SSH listener ports (L) -- corroboration only (best-effort).
_ssh_runtime_ports() {
    ss -Hltnp 2>/dev/null | grep -iE 'users:\(\("(sshd|ssh)"' \
        | awk '{print $4}' | sed -E 's/.*:([0-9]+)$/\1/' \
        | grep -E '^[0-9]+$' | sort -un
}

# Active SSH session anchor A. stdout: local server port (if found).
# return 0 = found (port on stdout); 1 = not an SSH session; 2 = ambiguous.
# The caller derives "over SSH" from the return code (1 => not over SSH).
_ssh_session_port() {
    local proc="${CCC_PROC_ROOT:-/proc}"
    local start="${1:-$$}"

    local envA=""
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        local -a f
        read -r -a f <<< "${SSH_CONNECTION}"
        if [[ "${#f[@]}" -eq 4 ]] && _fw_valid_port "${f[3]}"; then envA="${f[3]}"; fi
    fi

    local pid="${start}" hops=0 prev="" anc_shell=""
    local -a chain=()
    while (( hops < 64 )); do
        local comm stat ppid
        comm="$(cat "${proc}/${pid}/comm" 2>/dev/null || true)"
        [[ -n "${comm}" ]] || break
        if [[ "${comm}" == "sshd" ]]; then
            chain+=("${pid}")
            [[ -z "${anc_shell}" && -n "${prev}" ]] && anc_shell="${prev}"
        fi
        stat="$(cat "${proc}/${pid}/stat" 2>/dev/null || true)"
        [[ -n "${stat}" ]] || break
        ppid="$(printf '%s' "${stat}" | sed -E 's/^[0-9]+ \(.*\) [^ ]+ ([0-9]+).*/\1/')"
        [[ "${ppid}" =~ ^[0-9]+$ ]] || break
        (( ppid <= 1 )) && break
        prev="${pid}"; pid="${ppid}"; hops=$((hops + 1))
    done

    # Recover SSH_CONNECTION from the validated ancestor login shell environ
    # (NUL-delimited) when sudo stripped it from our own environment.
    local ancA=""
    if [[ -n "${anc_shell}" && -r "${proc}/${anc_shell}/environ" ]]; then
        local envline val
        envline="$(tr '\0' '\n' < "${proc}/${anc_shell}/environ" 2>/dev/null | grep -m1 '^SSH_CONNECTION=' || true)"
        if [[ -n "${envline}" ]]; then
            val="${envline#SSH_CONNECTION=}"
            local -a af
            read -r -a af <<< "${val}"
            if [[ "${#af[@]}" -eq 4 ]] && _fw_valid_port "${af[3]}"; then ancA="${af[3]}"; fi
        fi
    fi

    if [[ "${#chain[@]}" -eq 0 ]]; then
        return 1                          # local console (no sshd ancestor)
    fi

    # Correlate established sockets owned by ANY chain sshd PID (privsep-aware).
    local ssout p line laddr lport ports=""
    ssout="$(ss -Htnp state established 2>/dev/null || true)"
    for p in "${chain[@]}"; do
        while IFS= read -r line; do
            [[ -n "${line}" ]] || continue
            case "${line}" in
                *"pid=${p},"*|*"pid=${p})"*) : ;;
                *) continue ;;
            esac
            # ss omits the State column when a state filter is used, so the
            # LOCAL endpoint is NOT a fixed field index: with "state established"
            # it is field 3, without a filter it is field 4. Extract the FIRST
            # address:port token (the local endpoint always precedes the peer),
            # which is layout-independent and never reads the peer port.
            laddr="$(printf '%s' "${line}" | awk '{for (i = 1; i <= NF; i++) if ($i ~ /:[0-9]+$/) { print $i; exit }}')"
            lport="${laddr##*:}"
            _fw_valid_port "${lport}" && ports+="${lport}"$'\n'
        done <<< "${ssout}"
    done
    ports="$(printf '%s' "${ports}" | grep -E '^[0-9]+$' | sort -un || true)"
    local n
    n="$(printf '%s\n' "${ports}" | sed '/^$/d' | wc -l | tr -d ' ')"

    if [[ "${n}" == "1" ]]; then
        local A; A="$(printf '%s\n' "${ports}" | sed '/^$/d' | head -n1)"
        # socket-derived A is authoritative; every available env candidate must agree.
        [[ -n "${envA}" && "${envA}" != "${A}" ]] && return 2
        [[ -n "${ancA}" && "${ancA}" != "${A}" ]] && return 2
        printf '%s\n' "${A}"; return 0
    elif [[ "${n}" == "0" ]]; then
        # no correlated socket: fall back to validated ancestor env, then own env.
        [[ -n "${ancA}" && -n "${envA}" && "${ancA}" != "${envA}" ]] && return 2
        local cand="${ancA:-${envA}}"
        if [[ -n "${cand}" ]]; then printf '%s\n' "${cand}"; return 0; fi
        return 2
    else
        return 2
    fi
}

# _resolve_ssh_plan <over_ssh> <A|""> <C_csv|UNREADABLE|EMPTY> <O_csv|NONE>
# stdout: "PLAN: p1 p2 ..." (possibly empty) OR "FATAL: <reason>". return 0 ok; 1 fatal.
_resolve_ssh_plan() {
    local over="$1" A="$2" C="$3" O="$4"
    local -a Cset=() Oset=()
    [[ "${C}" != "UNREADABLE" && "${C}" != "EMPTY" ]] && IFS=',' read -r -a Cset <<< "${C}"
    [[ "${O}" != "NONE" ]] && IFS=',' read -r -a Oset <<< "${O}"

    if [[ "${O}" != "NONE" ]]; then
        if [[ "${over}" == "1" ]]; then
            [[ -n "${A}" ]] || { echo "FATAL: over SSH but the active session port is undeterminable; cannot validate override"; return 1; }
            _in_set "${A}" "${Oset[@]}" || { echo "FATAL: CCC_SSH_PORTS omits the active SSH session port ${A}"; return 1; }
        fi
        echo "PLAN: $(printf '%s\n' "${Oset[@]}" | sort -un | tr '\n' ' ' | sed 's/ $//')"
        return 0
    fi

    if [[ "${over}" == "1" ]]; then
        [[ -n "${A}" ]] || { echo "FATAL: over SSH but the active session port is ambiguous/undeterminable"; return 1; }
        [[ "${C}" == "UNREADABLE" ]] && { echo "FATAL: over SSH; effective sshd configuration unreadable; cannot confirm the persistent SSH port(s)"; return 1; }
        _in_set "${A}" "${Cset[@]}" || { echo "FATAL: active SSH session port ${A} is not in the configured set (${C}); no union and no conventional fallback"; return 1; }
        echo "PLAN: $(printf '%s\n' "${Cset[@]}" | sort -un | tr '\n' ' ' | sed 's/ $//')"
        return 0
    fi

    # local console
    [[ "${C}" == "UNREADABLE" ]] && { echo "FATAL: local console; sshd present but effective configuration unreadable/ambiguous"; return 1; }
    [[ "${C}" == "EMPTY" ]] && { echo "PLAN: "; return 0; }
    echo "PLAN: $(printf '%s\n' "${Cset[@]}" | sort -un | tr '\n' ' ' | sed 's/ $//')"
    return 0
}

_fw_fatal() {
    local reason="$1" A="$2" C="$3" L="$4" O="$5"
    warn "FIREWALL PREFLIGHT FAILED — no firewall changes were made: ${reason}"
    warn "  Evidence: active-session-port=[${A:-none}] configured=[${C}] runtime-listeners=[${L:-none}] override=[${O}]"
    warn "  This installer manages LOCAL board ports only (never router/NAT forwarding)."
    warn "  Safe remediation — specify the intended LOCAL SSH admin port(s):"
    warn "    sudo env CCC_SSH_PORTS=<port[,port]> bash install.sh"
    die "SSH firewall plan could not be resolved safely; UFW was not modified."
}

_fw_print_plan() {
    info "Firewall plan (purpose | protocol | port | evidence) — no UFW changes yet:"
    if [[ -n "${FW_SSH_PORTS}" ]]; then
        info "  SSH administration | TCP | ${FW_SSH_PORTS} | session anchor + effective sshd config (fail-closed; no conventional fallback)"
    else
        info "  SSH administration | TCP | (none) | no sshd detected on local console"
    fi
    info "  HTTP redirect      | TCP | 80 | fixed CCC requirement"
    info "  CCC HTTPS          | TCP | ${HTTPS_PORT} | installer-selected"
    info "  Conduit inbound    | UDP | (none) | dynamic ports; no inbound rule"
}

_firewall_collect_plan() {   # echoes "SIG|||PLAN"; dies via _fw_fatal on fatal
    local A C L O Ccsv rc over res plan
    if A="$(_ssh_session_port "${CCC_FW_START_PID:-$$}")"; then rc=0; else rc=$?; fi
    over=1; [[ "${rc}" -eq 1 ]] && over=0
    C="$(_ssh_persistent_ports)"
    L="$(_ssh_runtime_ports | tr '\n' ' ' | sed 's/ *$//')"
    if [[ "${C}" == "UNREADABLE" ]]; then Ccsv="UNREADABLE"
    elif [[ -z "${C}" || "${C}" == "EMPTY" ]]; then Ccsv="EMPTY"
    else Ccsv="$(printf '%s\n' "${C}" | paste -sd, -)"; fi
    O="NONE"
    if [[ -n "${CCC_SSH_PORTS+x}" ]]; then
        # SET (even if empty) -> must be valid; empty/whitespace-only is fatal.
        local optmp
        if optmp="$(_ssh_parse_override "${CCC_SSH_PORTS}")"; then
            O="$(printf '%s\n' "${optmp}" | paste -sd, -)"
        else
            _fw_fatal "CCC_SSH_PORTS is set but invalid: comma-separated integer ports 1..65535 (empty, whitespace-only, embedded whitespace, trailing/double comma, non-numeric, out-of-range are rejected)" "${A}" "${Ccsv}" "${L}" "${CCC_SSH_PORTS}"
        fi
    fi
    [[ "${rc}" -eq 2 ]] && _fw_fatal "an SSH session is active but its administration port is ambiguous/undeterminable" "${A}" "${Ccsv}" "${L}" "${O}"
    if res="$(_resolve_ssh_plan "${over}" "${A}" "${Ccsv}" "${O}")"; then :; else
        _fw_fatal "$(printf '%s' "${res}" | sed 's/^FATAL: //')" "${A}" "${Ccsv}" "${L}" "${O}"
    fi
    plan="$(printf '%s' "${res}" | sed 's/^PLAN: //' | xargs 2>/dev/null || true)"
    _FW_SIG="over=${over};A=${A};C=${Ccsv};O=${O};L=${L};plan=${plan}"
    _FW_PLAN="${plan}"
    _FW_L="${L}"
    _FW_C="${Ccsv}"
}

_fw_l_warnings() {
    # Deterministic corroboration warnings; L never authorizes a port. C is
    # comma-separated; L is whitespace-separated. Both are turned into real arrays
    # so no word-splitting/globbing occurs (ShellCheck-clean, no disable directives).
    local C="$1" L="$2" cp lp
    [[ "${C}" == "UNREADABLE" || "${C}" == "EMPTY" ]] && return 0
    local -a Cset=() Lset=()
    IFS=',' read -r -a Cset <<< "${C}"
    if [[ -n "${L}" ]]; then
        read -r -a Lset <<< "${L}"
    fi
    for cp in "${Cset[@]}"; do
        _in_set "${cp}" "${Lset[@]}" || warn "SSH port ${cp}/tcp is configured but not currently listening; it will be opened for post-reboot access."
    done
    for lp in "${Lset[@]}"; do
        _in_set "${lp}" "${Cset[@]}" || warn "A runtime SSH listener on ${lp}/tcp is not in the effective configuration and will NOT be opened (a listening socket is evidence, not authorization)."
    done
    return 0
}

_firewall_preflight() {
    _firewall_collect_plan
    FW_EVID_SIG="${_FW_SIG}"
    FW_SSH_PORTS="${_FW_PLAN}"
    _fw_l_warnings "${_FW_C}" "${_FW_L}"
    _fw_print_plan
}

_firewall_apply() {
    local plan
    _firewall_collect_plan
    [[ "${_FW_SIG}" == "${FW_EVID_SIG}" ]] || die "SSH firewall evidence changed between preflight and apply; UFW not modified."
    plan="${_FW_PLAN}"

    # Capture the initial UFW active state (locale-stable) BEFORE any live write.
    local was_active=0
    LC_ALL=C ufw status 2>/dev/null | grep -qE '^Status: active' && was_active=1

    local -a rules=()
    local p
    for p in ${plan}; do rules+=("${p}|CCC SSH"); done
    rules+=("80|HTTP")
    rules+=("${HTTPS_PORT}|CCC HTTPS")

    # Non-mutating dry-run of every planned rule first.
    local r port comment
    for r in "${rules[@]}"; do
        port="${r%|*}"; comment="${r#*|}"
        ufw --dry-run allow "${port}/tcp" comment "${comment}" >/dev/null 2>&1 \
            || die "ufw --dry-run rejected ${port}/tcp (${comment}); no firewall changes made."
    done

    # Add-only, deterministic order; truthful partial-failure reporting (no rollback).
    local -a applied=()
    for r in "${rules[@]}"; do
        port="${r%|*}"; comment="${r#*|}"
        if ufw allow "${port}/tcp" comment "${comment}" >/dev/null 2>&1; then
            applied+=("${port}/tcp")
        else
            local statenote
            if [[ "${was_active}" == "1" ]]; then
                statenote="UFW remains ACTIVE: prior rules plus the successfully-added rules stay in effect (it was not reset or re-enabled)."
            else
                statenote="UFW remains INACTIVE (it was not enabled)."
            fi
            warn "Firewall rule ${port}/tcp (${comment}) could not be added."
            warn "  APPLIED before failure: ${applied[*]:-none}"
            warn "  FAILED: ${port}/tcp (${comment})"
            warn "  ${statenote} No rules were deleted (no rollback/atomicity claim)."
            die "Firewall rule application failed; aborted before enabling UFW."
        fi
    done

    # Pre-enable verification: live 'ufw status' when initially ACTIVE, staged
    # 'ufw show added' when initially INACTIVE. Locale-stable.
    local vsrc
    if [[ "${was_active}" == "1" ]]; then
        vsrc="$(LC_ALL=C ufw status 2>/dev/null || true)"
        for r in "${rules[@]}"; do
            port="${r%|*}"
            printf '%s\n' "${vsrc}" | grep -qE "^${port}/tcp[[:space:]]+ALLOW" \
                || die "Pre-enable verification failed (UFW already active): ${port}/tcp not present; no reset performed, prior rules preserved."
        done
    else
        vsrc="$(LC_ALL=C ufw show added 2>/dev/null || true)"
        for r in "${rules[@]}"; do
            port="${r%|*}"
            printf '%s\n' "${vsrc}" | grep -qE "allow ${port}/tcp" \
                || die "Pre-enable verification failed (UFW inactive): ${port}/tcp not staged; UFW not enabled."
        done
    fi

    ufw --force enable >/dev/null 2>&1 || die "ufw --force enable failed."

    # Post-enable verification: a missing planned rule is a LOUD ERROR with recovery
    # guidance and a nonzero exit -- and NO success summary is printed.
    # Read the effective UFW IPv6 setting; require the IPv4 ALLOW line for every
    # planned port, and the matching "(v6)" ALLOW line too when IPV6=yes.
    local ufwdefaults="${CCC_UFW_DEFAULTS:-/etc/default/ufw}"
    local ipv6="no"
    if [[ -r "${ufwdefaults}" ]]; then
        local _v6
        _v6="$(grep -E '^[[:space:]]*IPV6=' "${ufwdefaults}" 2>/dev/null | tail -n1 | sed -E 's/^[[:space:]]*IPV6=//; s/["\x27]//g' | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
        [[ "${_v6}" == "yes" ]] && ipv6="yes"
    fi
    local status
    status="$(LC_ALL=C ufw status 2>/dev/null || true)"
    local -a miss_desc=() miss_recover=()
    for r in "${rules[@]}"; do
        port="${r%|*}"; comment="${r#*|}"
        local pmiss=0
        printf '%s\n' "${status}" | grep -qE "^${port}/tcp[[:space:]]+ALLOW" || { miss_desc+=("${port}/tcp (v4)"); pmiss=1; }
        if [[ "${ipv6}" == "yes" ]]; then
            printf '%s\n' "${status}" | grep -qE "^${port}/tcp \(v6\)[[:space:]]+ALLOW" || { miss_desc+=("${port}/tcp (v6)"); pmiss=1; }
        fi
        [[ "${pmiss}" == "1" ]] && miss_recover+=("    sudo ufw allow ${port}/tcp comment '${comment}'")
    done
    if [[ "${#miss_desc[@]}" -gt 0 ]]; then
        warn "ERROR: UFW is enabled but planned rule(s) are MISSING: ${miss_desc[*]}"
        warn "  Your SSH session may be relying on connection tracking and could be lost"
        warn "  on reconnect/reboot. Recover NOW, before you disconnect:"
        warn "    sudo ufw status verbose"
        local m
        for m in "${miss_recover[@]}"; do warn "${m}"; done
        die "Firewall verification failed after enabling UFW; see the recovery guidance above."
    fi
    info "UFW enabled — added the evidenced SSH administration port(s) ${FW_SSH_PORTS:-<none>}, HTTP 80, and HTTPS ${HTTPS_PORT} alongside any pre-existing UFW rules; no inbound Conduit UDP."
}
# <<< CCC-FIREWALL-PLAN <<<

# Choose the public HTTPS port from CF_HTTPS_PORTS minus occupied ports.
# Default = 443 if free, else the first free port in preference order. On a
# re-run, the currently-configured port is treated as available (own listener).
# Result stored in HTTPS_PORT. Cloudflare-only; no custom ports.
select_https_port() {
    local occupied current p avail=()
    occupied=" $(detect_occupied_tcp_ports) "
    current=""
    [[ -f "${CONF_DIR}/config.json" ]] && current="$(json_get \
        "d.get('web',{}).get('https_port','')" < "${CONF_DIR}/config.json")"
    for p in "${CF_HTTPS_PORTS[@]}"; do
        if [[ "${occupied}" != *" ${p} "* || "${p}" == "${current}" ]]; then
            avail+=("${p}")
        fi
    done
    [[ ${#avail[@]} -gt 0 ]] || die \
        "No Cloudflare-supported HTTPS port is free (${CF_HTTPS_PORTS[*]})." \
        "Free one of those ports (see 'ss -ltn'), then re-run."
    local default="${avail[0]}" choice ok
    printf "\n  Cloudflare-supported HTTPS ports available: %s\n" "${avail[*]}"
    printf "  Press Enter for the default. Custom ports are not allowed.\n"
    while true; do
        prompt choice "HTTPS port" "${default}"
        ok=""
        for p in "${avail[@]}"; do [[ "${choice}" == "${p}" ]] && ok=1 && break; done
        [[ -n "${ok}" ]] && { HTTPS_PORT="${choice}"; break; }
        warn "Choose one of: ${avail[*]} (Cloudflare-supported and currently free)."
    done
    info "HTTPS port selected: ${HTTPS_PORT}"
}

# --------------------------------------------------------------------------- #
#  Phase 1 — Validation (no system changes)                                   #
# --------------------------------------------------------------------------- #

phase1_validate() {
    section "Phase 1 — Validation"

    # ---- 1a  Root check and OS / architecture ------------------------------- #
    step "1a — Checking privileges and operating system"

    [[ "${EUID}" -eq 0 ]] || die \
        "This installer must be run as root." \
        "Run: sudo bash install.sh"

    local os_id os_version os_arch
    # shellcheck source=/dev/null
    os_id="$(. /etc/os-release && echo "${ID}")"
    # shellcheck source=/dev/null
    os_version="$(. /etc/os-release && echo "${VERSION_ID}")"
    os_arch="$(uname -m)"

    [[ "${os_id}" == "ubuntu" ]] || die \
        "Unsupported OS: ${os_id}. Ubuntu 22.04 is required."
    [[ "${os_version}" == "22.04" ]] || die \
        "Unsupported Ubuntu version: ${os_version}." \
        "Ubuntu 22.04 LTS (Jammy) is required."
    case "${os_arch}" in
        aarch64|armv7l) : ;;
        *) die "Unsupported architecture: ${os_arch}. Supported: aarch64 (arm64, Raspberry Pi 3/4) and armv7l (armhf, Raspberry Pi 2)." ;;
    esac

    info "OS: Ubuntu ${os_version} ${os_arch}"

    # ---- 1b  Pre-install checklist confirmation ----------------------------- #
    step "1b — Pre-install checklist"
    printf "\n"
    printf "  Before continuing, confirm you have completed all steps in:\n"
    printf '  %bdocs/pre-install.md%b\n\n' "${BOLD}" "${RESET}"
    printf "  Checklist summary:\n"
    printf "    [ ] Cloudflare DNS A record created (not CNAME), proxy ON\n"
    printf "    [ ] SSL/TLS mode set to Full (strict)\n"
    printf "    [ ] Cloudflare Origin Certificate and key on this Pi\n"
    printf "    [ ] API token with Zone:DNS:Edit + Zone:Zone:Read\n\n"

    local _confirm
    read -r -p "  Have you completed docs/pre-install.md? [y/N]: " _confirm
    [[ "${_confirm,,}" == "y" ]] || die \
        "Installation cancelled." \
        "Complete docs/pre-install.md first, then re-run: sudo bash install.sh"

    # ---- 1c  System dependencies ------------------------------------------- #
    step "1c — Installing system dependencies"
    apt-get update -qq
    apt-get install -y -qq \
        python3 python3-pip python3-venv \
        nginx ufw curl jq \
        >/dev/null
    info "System dependencies installed"

    # ---- 1d  Collect Cloudflare API token (no API call yet) ---------------- #
    step "1d — Cloudflare API token"
    printf "  The token is stored in /etc/conduit-cc/.env and never printed.\n"
    prompt_secret CF_API_TOKEN "API token"
    [[ -n "${CF_API_TOKEN}" ]] || die "API token cannot be empty."
    _require_env_scalar "API token" "${CF_API_TOKEN}"

    # ---- 1e  Zone name — validates token AND Zone:Zone:Read permission ------ #
    step "1e — Cloudflare zone name"
    prompt CF_ZONE_NAME "Zone name (e.g. example.com)"
    [[ -n "${CF_ZONE_NAME}" ]] || die "Zone name cannot be empty."
    _require_env_scalar "Zone name" "${CF_ZONE_NAME}"

    local zone_response zone_success
    zone_response="$(cf_api GET "/zones?name=${CF_ZONE_NAME}")"
    [[ -n "${zone_response}" ]] || die \
        "Cloudflare API is unreachable." \
        "Check internet connectivity on this Pi."

    zone_success="$(echo "${zone_response}" | json_get "str(d.get('success',''))")"
    [[ "${zone_success}" == "True" ]] || die \
        "API token rejected by Cloudflare." \
        "Verify the token has Zone:Zone:Read permission and is not expired. See docs/pre-install.md Step 4."

    CF_ZONE_ID="$(echo "${zone_response}" | \
        json_get "d['result'][0]['id'] if d.get('result') else ''")"
    [[ -n "${CF_ZONE_ID}" ]] || die \
        "Zone '${CF_ZONE_NAME}' not found in your Cloudflare account." \
        "Enter the root domain (e.g. example.com), not a subdomain."

    info "Zone '${CF_ZONE_NAME}' found (ID: ${CF_ZONE_ID:0:8}...)"

    # ---- 1f  DNS A record — must exist and be proxied ---------------------- #
    step "1f — DNS A record"
    prompt CF_RECORD_NAME "Dashboard hostname (e.g. conduit.example.com)"
    [[ -n "${CF_RECORD_NAME}" ]] || die "Hostname cannot be empty."
    _require_env_scalar "Dashboard hostname" "${CF_RECORD_NAME}"

    local record_response record_count proxied
    record_response="$(cf_api GET \
        "/zones/${CF_ZONE_ID}/dns_records?type=A&name=${CF_RECORD_NAME}")"

    record_count="$(echo "${record_response}" | \
        json_get "len(d.get('result',[]))")"
    [[ "${record_count}" -gt 0 ]] 2>/dev/null || die \
        "DNS A record '${CF_RECORD_NAME}' not found." \
        "Create an A record in the Cloudflare dashboard. See docs/pre-install.md Step 2."

    proxied="$(echo "${record_response}" | \
        json_get "str(d['result'][0].get('proxied',''))")"
    [[ "${proxied}" == "True" ]] || die \
        "DNS record '${CF_RECORD_NAME}' exists but the Cloudflare proxy is OFF (grey cloud)." \
        "Enable the proxy (orange cloud) in the Cloudflare DNS panel. See docs/pre-install.md Step 2."

    info "A record '${CF_RECORD_NAME}' found, proxy ON"

    # ---- 1f2  HTTPS port selection (Feature 1) ----------------------------- #
    step "1f2 — HTTPS port"
    select_https_port

    # ---- 1g  TLS certificate — must exist and be issued by Cloudflare ------ #
    step "1g — TLS certificate"
    local _default_cert="${TLS_DIR}/origin.pem"
    prompt TLS_CERT_PATH "Certificate path" "${_default_cert}"
    [[ -f "${TLS_CERT_PATH}" ]] || die \
        "Certificate file not found: ${TLS_CERT_PATH}" \
        "Follow docs/tls-setup.md Path A to create and place the certificate."

    local cert_issuer
    cert_issuer="$(openssl x509 -noout -issuer -in "${TLS_CERT_PATH}" 2>/dev/null)" || die \
        "Cannot read certificate: ${TLS_CERT_PATH}" \
        "The file may be corrupt or not a PEM certificate. See docs/tls-setup.md."

    echo "${cert_issuer}" | grep -qi "cloudflare" || die \
        "Certificate issuer check failed." \
        "Expected a Cloudflare CA certificate (issuer contains 'Cloudflare').
       Got: ${cert_issuer}
       If this is a Let's Encrypt certificate, see docs/tls-setup.md Path B.
       For the recommended path, re-create the certificate via: Cloudflare → SSL/TLS → Origin Server."

    info "Certificate issuer: Cloudflare"

    # ---- 1h  TLS private key — must be RSA --------------------------------- #
    step "1h — TLS private key"
    local _default_key="${TLS_DIR}/origin.key"
    prompt TLS_KEY_PATH "Private key path" "${_default_key}"
    [[ -f "${TLS_KEY_PATH}" ]] || die \
        "Private key file not found: ${TLS_KEY_PATH}" \
        "Follow docs/tls-setup.md Path A to create and place the private key."

    openssl rsa -noout -check -in "${TLS_KEY_PATH}" &>/dev/null || die \
        "Private key check failed. The key must be RSA (2048)." \
        "Re-create the Cloudflare Origin Certificate with key type RSA (2048). See docs/tls-setup.md."

    # Verify cert / key are a matched pair
    local cert_mod key_mod
    cert_mod="$(openssl x509 -noout -modulus -in "${TLS_CERT_PATH}" 2>/dev/null | openssl md5)"
    key_mod="$(openssl rsa  -noout -modulus -in "${TLS_KEY_PATH}"  2>/dev/null | openssl md5)"
    [[ "${cert_mod}" == "${key_mod}" ]] || die \
        "Certificate and private key do not match (modulus mismatch)." \
        "You may have pasted the wrong key. Re-verify using docs/tls-setup.md Check 3."

    info "Private key: RSA, valid, matches certificate"

    # ---- 1i  Admin credentials --------------------------------------------- #
    step "1i — Admin account"
    prompt ADMIN_USERNAME "Admin username" "admin"
    [[ -n "${ADMIN_USERNAME}" ]] || die "Username cannot be empty."
    [[ "${ADMIN_USERNAME}" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] \
        || die "Username must be 1-64 ASCII letters, digits, dot, underscore or hyphen."
    _require_env_scalar "Admin username" "${ADMIN_USERNAME}"

    local _pw1 _pw2
    prompt_secret _pw1 "Admin password (min ${MIN_PW_LEN} characters)"
    [[ "${#_pw1}" -ge "${MIN_PW_LEN}" ]] || die \
        "Password must be at least ${MIN_PW_LEN} characters."
    prompt_secret _pw2 "Confirm admin password"
    [[ "${_pw1}" == "${_pw2}" ]] || die "Passwords do not match."
    ADMIN_PASSWORD="${_pw1}"
    unset _pw1 _pw2

    info "Admin credentials accepted"

    # ---- 1x  Conduit binary detection (no system changes) ------------------ #
    # Check PATH first, then the repository directory.  If neither has the
    # binary, offer a GitHub download (the actual download is deferred to
    # Phase 2x-c so Phase 1 remains read-only).
    step "1x — Detecting Psiphon Conduit binary (v${CONDUIT_VERSION})"

    local _conduit_path
    if _conduit_path="$(command -v conduit 2>/dev/null)"; then
        local _path_ver
        _path_ver="$("${_conduit_path}" --version 2>/dev/null | head -1)" || true
        info "Found conduit in PATH: ${_conduit_path}  (${_path_ver:-version unknown})"
        CONDUIT_BIN_SRC="${_conduit_path}"
    elif [[ -f "${SCRIPT_DIR}/conduit" && -x "${SCRIPT_DIR}/conduit" ]]; then
        local _dir_ver
        _dir_ver="$("${SCRIPT_DIR}/conduit" --version 2>/dev/null | head -1)" || true
        info "Found conduit in repository: ${SCRIPT_DIR}/conduit  (${_dir_ver:-version unknown})"
        CONDUIT_BIN_SRC="${SCRIPT_DIR}/conduit"
    else
        warn "Psiphon Conduit binary not found in PATH or ${SCRIPT_DIR}/"
        printf "\n"
        printf "  The installer can download conduit v%s from GitHub:\n" "${CONDUIT_VERSION}"
        printf "  https://github.com/Psiphon-Inc/conduit/releases/tag/v%s\n" \
            "${CONDUIT_VERSION}"
        printf "\n"
        printf '  Alternatively, place the conduit binary at %s/conduit\n' "${SCRIPT_DIR}"
        printf "  and re-run this installer (no download required).\n"
        printf "\n"
        local _dl_confirm
        read -r -p "  Download conduit v${CONDUIT_VERSION} from GitHub now? [y/N]: " \
            _dl_confirm
        [[ "${_dl_confirm,,}" == "y" ]] || die \
            "Conduit binary not available." \
            "Place the conduit binary at ${SCRIPT_DIR}/conduit and re-run install.sh"
        CONDUIT_BIN_SRC="download"
        info "Conduit v${CONDUIT_VERSION} will be downloaded in Phase 2x-c"
    fi

    # ---- 1j  Confirmation summary ------------------------------------------ #
    step "1j — Confirm installation"
    local _token_preview
    _token_preview="${CF_API_TOKEN:0:6}..."

    printf "\n"
    printf '  %bInstallation summary%b\n' "${BOLD}" "${RESET}"
    printf "  %-24s %s\n" "Zone:"        "${CF_ZONE_NAME}"
    printf "  %-24s %s\n" "Hostname:"    "${CF_RECORD_NAME}"
    printf "  %-24s %s\n" "HTTPS port:"  "${HTTPS_PORT}"
    printf "  %-24s %s\n" "API token:"   "${_token_preview}  (hidden)"
    printf "  %-24s %s\n" "Certificate:" "${TLS_CERT_PATH}"
    printf "  %-24s %s\n" "Private key:" "${TLS_KEY_PATH}"
    printf "  %-24s %s\n" "Admin user:"  "${ADMIN_USERNAME}"
    printf "  %-24s %s\n" "Install dir:" "${APP_DIR}"
    printf "  %-24s %s\n" "Config dir:"  "${CONF_DIR}"
    printf "  %-24s %s\n" "Conduit v${CONDUIT_VERSION}:" \
        "${CONDUIT_BIN_SRC} (install to ${CONDUIT_BIN_DIR}/conduit)"
    printf "\n"

    local _proceed
    read -r -p "  Proceed with installation? [y/N]: " _proceed
    [[ "${_proceed,,}" == "y" ]] || die "Installation cancelled by user."

    info "Phase 1 complete — all checks passed, proceeding to install"
}

# --------------------------------------------------------------------------- #
#  Phase 2 — Installation                                                      #
# --------------------------------------------------------------------------- #

phase2_install() {
    section "Phase 2 — Installation"

    # ---- 2a  System user --------------------------------------------------- #
    step "2a — Creating system user '${APP_USER}'"
    if id "${APP_USER}" &>/dev/null; then
        info "User '${APP_USER}' already exists — skipping"
    else
        useradd \
            --system \
            --no-create-home \
            --shell /usr/sbin/nologin \
            --comment "Conduit Control Center" \
            "${APP_USER}"
        info "User '${APP_USER}' created"
    fi

    # ---- 2a (cont.)  Journal read access for the Logs page ---------------- #
    # The Logs page (GET /api/logs) runs, as ${APP_USER} and WITHOUT sudo:
    #     journalctl -u conduit
    # A non-privileged user can only read another unit's journal when it is a
    # member of the systemd-journal group. Idempotent; must run before the
    # service is first started so the service process inherits the membership.
    if getent group systemd-journal >/dev/null; then
        usermod -aG systemd-journal "${APP_USER}"
        info "Added '${APP_USER}' to systemd-journal (Logs page: journalctl -u conduit)"
    else
        warn "systemd-journal group not found - Logs page may return HTTP 503"
    fi

    # ---- 2b  Application files --------------------------------------------- #
    step "2b — Copying application files to ${APP_DIR}"
    mkdir -p "${APP_DIR}"
    # Epic-1 ownership boundary (F1/F6): deployed code is ROOT-owned and never
    # writable by the service account. rsync -a would PRESERVE source uid/gid
    # (root execution is not an ownership invariant), so ownership and modes are
    # normalized explicitly. The old broad recursive service-account chown of APP_DIR
    # made root helpers execute service-writable code and is REMOVED; nothing
    # may reintroduce it (regression-tested).
    rsync -a \
        --chown=root:root \
        --chmod=D0755,F0644 \
        "${CCC_LIFECYCLE_EXCLUDES[@]}" \
        --exclude 'ccc.db' \
        --exclude '__pycache__/' \
        --exclude '.git/' \
        --exclude '.env' \
        "${SCRIPT_DIR}/" "${APP_DIR}/"
    chown root:root "${APP_DIR}"
    chmod 0755 "${APP_DIR}"
    _verify_app_dir_ownership
    # Epic-1 A3: the canonical .env CLI must exist BEFORE the first .env
    # operation (2f). Root-owned 0755; it has NO sudo grant (root-only tool).
    install -d -o root -g root -m 0755 /opt/conduit-cc/bin
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-env" /opt/conduit-cc/bin/ccc-env
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-runtime" /opt/conduit-cc/bin/ccc-runtime
    info "Application files copied (root-owned, service read/execute only)"

    # ---- 2b1  Purge stale Python bytecode (reinstall-over-existing) --------- #
    # Fresh installs have an empty APP_DIR (no-op); on reinstall-over-existing
    # this prevents the runtime loading stale bytecode after a same-size/mtime=0
    # source change. STRICTLY scoped to APP_DIR; venv AND its children are pruned
    # (dependency bytecode untouched); removes ONLY __pycache__ dirs and *.pyc.
    step "2b1 — Purging stale Python bytecode (reinstall-over-existing)"
    find "${APP_DIR}" \( -path "${APP_DIR}/venv" -o -path "${APP_DIR}/venv/*" \
                       -o -path "${APP_DIR}/.venvs" -o -path "${APP_DIR}/.venvs/*" \) -prune \
        -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find "${APP_DIR}" \( -path "${APP_DIR}/venv" -o -path "${APP_DIR}/venv/*" \
                       -o -path "${APP_DIR}/.venvs" -o -path "${APP_DIR}/.venvs/*" \) -prune \
        -o -type f -name '*.pyc' -delete 2>/dev/null || true
    info "Stale __pycache__/*.pyc purged under ${APP_DIR} (venv preserved)"

    # ---- 2c  Immutable candidate runtime ----------------------------------- #
    step "2c — Building and validating the initial immutable runtime"
    local _arch _pyver _abi _dkind _dig _lock _wh _version _candidate_id
    local _install_attempt _candidate_py _current_id=""
    _arch="$(uname -m)"
    _pyver="$(/usr/bin/python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
    _abi="$(/usr/bin/python3 -c "import sysconfig;print(sysconfig.get_config_var('SOABI') or '')")"
    _version="$(_read_version_file "${APP_DIR}/backend/_version.py")" \
        || die "installed APP_VERSION declaration is invalid"
    _wh="${CCC_WHEELHOUSE_DIR:-${SCRIPT_DIR}/wheelhouse-armhf}"
    case "${_arch}" in
        armv7l)
            [[ -f "${_wh}/SHA256SUMS" ]] \
                || die "wheelhouse SHA256SUMS missing for initial runtime identity"
            _dkind="wheelhouse-sha256sums"
            _dig="$(sha256sum "${_wh}/SHA256SUMS" | awk '{print $1}')"
            ;;
        aarch64)
            _lock="${APP_DIR}/requirements-aarch64.lock"
            [[ -f "${_lock}" ]] || die "aarch64 dependency lock missing for initial runtime identity"
            _dkind="aarch64-lock"
            _dig="$(sha256sum "${_lock}" | awk '{print $1}')"
            ;;
        *) die "Unsupported architecture '${_arch}' for initial runtime provisioning.";;
    esac
    _candidate_id="$(/usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime \
        candidate-id "${_version}" "${INSTALL_SOURCE_COMMIT}" "${_arch}" \
        "${_pyver}" "${_abi}" "${_dkind}" "${_dig}")" \
        || die "initial runtime identity computation failed"
    _install_attempt="${_candidate_id:0:32}"

    if [[ -L "${APP_DIR}/venv" ]]; then
        _current_id="$(/usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime validate-selector \
            | sed -n 's/^RUNTIME_SELECTOR=OK id=//p')" \
            || die "existing runtime selector is invalid"
        [[ "${_current_id}" == "${_candidate_id}" ]] \
            || die "This host already has a different active runtime. Use the signed updater, not install.sh."
    elif [[ -e "${APP_DIR}/venv" ]]; then
        die "A legacy real-directory runtime already exists. Use the v0.3.19 Owner bootstrap ceremony."
    fi

    /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime \
        reconcile-candidate "${_candidate_id}" "${_install_attempt}" >/dev/null \
        || die "cannot reconcile prior initial-runtime candidate publication"
    if [[ -d "${APP_DIR}/.venvs/${_candidate_id}" && ! -L "${APP_DIR}/.venvs/${_candidate_id}" ]]; then
        /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime revalidate "${_candidate_id}" \
            || die "existing initial candidate failed live revalidation"
        info "existing initial candidate live-revalidated"
    else
        _candidate_py="$(/usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime \
            stage-candidate "${_candidate_id}" "${_install_attempt}")" \
            || die "initial candidate staging failed"
        if ! install_python_deps "${_candidate_py}" "${APP_DIR}/requirements.txt" "${_wh}"; then
            /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime \
                discard-staging "${_install_attempt}" >/dev/null 2>&1 || true
            die "Python dependency installation failed; no runtime selector was published."
        fi
        /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime \
            finalize-candidate "${_candidate_id}" "${_install_attempt}" \
            "app_version=${_version}" "commit=${INSTALL_SOURCE_COMMIT}" \
            "tag=${INSTALL_SOURCE_TAG}" "arch=${_arch}" \
            "python_version=${_pyver}" "abi=${_abi}" \
            "input_digest=${_dig}" "input_digest_kind=${_dkind}" \
            || die "initial candidate validation/publication failed; selector remains absent"
        info "initial candidate dependencies installed and fully validated"
    fi
    if [[ -z "${_current_id}" ]]; then
        /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime \
            activate-initial "${_candidate_id}" \
            || die "initial runtime selector publication failed"
    fi
    /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-runtime validate-selector >/dev/null \
        || die "initial runtime selector failed the final gate"
    info "initial immutable runtime active (${_candidate_id:0:16}...)"

    # ---- 2d  Configuration directory --------------------------------------- #
    step "2d — Creating configuration directory ${CONF_DIR}"
    mkdir -p "${CONF_DIR}" "${TLS_DIR}"
    chown "${APP_USER}:${APP_USER}" "${CONF_DIR}"
    chmod 750 "${CONF_DIR}"
    # TLS dir: root-owned, 700 — nginx and app read as root or via ACL
    chown root:root "${TLS_DIR}"
    chmod 700 "${TLS_DIR}"
    info "${CONF_DIR} created (750, ${APP_USER})"

    # ---- 2e  TLS certificate and key --------------------------------------- #
    step "2e — Installing TLS certificate and key"
    local _canon_cert="${TLS_DIR}/origin.pem"
    local _canon_key="${TLS_DIR}/origin.key"

    if [[ "${TLS_CERT_PATH}" != "${_canon_cert}" ]]; then
        cp "${TLS_CERT_PATH}" "${_canon_cert}"
        info "Certificate copied to ${_canon_cert}"
    fi
    chmod 644 "${_canon_cert}"

    if [[ "${TLS_KEY_PATH}" != "${_canon_key}" ]]; then
        cp "${TLS_KEY_PATH}" "${_canon_key}"
        info "Private key copied to ${_canon_key}"
    fi
    chmod 600 "${_canon_key}"

    # Canonical paths used from this point forward
    TLS_CERT_PATH="${_canon_cert}"
    TLS_KEY_PATH="${_canon_key}"
    info "TLS files: cert=644, key=600"

    # ---- 2f  Write .env ---------------------------------------------------- #
    step "2f — Writing ${CONF_DIR}/.env"
    # Epic-1 A3 object-type gate BEFORE any branch: a live OR DANGLING symlink
    # (or any non-regular object) fails closed -- `-f` follows links and lets a
    # dangling symlink fall through to the create branch, where a redirection
    # would write through it as root (the F2-class .env exploit).
    if [[ -L "${CONF_DIR}/.env" ]] || { [[ -e "${CONF_DIR}/.env" ]] && [[ ! -f "${CONF_DIR}/.env" ]]; }; then
        echo "ERROR: ${CONF_DIR}/.env is not a regular file (symlink/foreign object); refusing" >&2
        exit 1
    fi
    if [[ -f "${CONF_DIR}/.env" ]]; then
        # Idempotent reinstall: preserve SESSION_SECRET, CF_API_TOKEN, and
        # other runtime values set after first install.  Only ADMIN_USERNAME
        # is updated here; ADMIN_PASSWORD_HASH is updated in Phase 2g.
        info ".env already exists — preserving (SESSION_SECRET and credentials kept)"
        # Epic-1 (F7): VALIDATE before mutation. Never chown/chmod an existing
        # path first: a hardlink is `-f` and would mutate a second inode name
        # before the canonical single-link gate could reject it.
        /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-env \
            assert-contract "${CONF_DIR}/.env" >/dev/null \
            || die "existing .env violates the canonical object/mode/ownership contract"
        # Canonical writer only (A3): value via bounded stdin from the Bash
        # BUILTIN printf (never an external argv), atomic replace inside.
        builtin printf '%s' "${ADMIN_USERNAME}" | \
            /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-env \
                set-key "${CONF_DIR}/.env" ADMIN_USERNAME \
            || die "canonical .env writer failed for ADMIN_USERNAME"
    else
        local _session_secret
        _session_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

        # Write all variables.  ADMIN_PASSWORD_HASH is left empty and written
        # by Phase 2g after the venv is ready.  CF_API_TOKEN is written here
        # because scripts/cloudflare-ddns.sh reads it from this file.
        # Canonical writer only (A3): full initial content via bounded stdin
        # (Bash BUILTIN printf; secrets never in any argv/environment). The
        # writer enforces regular-file-only, 0600, exact ownership, atomic
        # replace + fsync internally.
        local _env_content
        _env_content="# Conduit Control Center — runtime configuration
# Generated by install.sh — do not edit unless instructed.
# See .env.example for documentation of each variable.

ADMIN_USERNAME='${ADMIN_USERNAME}'
ADMIN_PASSWORD_HASH=

SESSION_SECRET='${_session_secret}'

CF_API_TOKEN='${CF_API_TOKEN}'
CF_ZONE_NAME='${CF_ZONE_NAME}'
CF_RECORD_NAME='${CF_RECORD_NAME}'

TLS_CERT_PATH='${TLS_CERT_PATH}'
TLS_KEY_PATH='${TLS_KEY_PATH}'
"
        builtin printf '%s' "${_env_content}" | \
            /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-env \
                init "${CONF_DIR}/.env" \
            || die "canonical .env writer failed (init)"
        unset _env_content
        info ".env written via canonical writer (600, ${APP_USER})"
    fi

    # ---- 2g  Hash admin password ------------------------------------------- #
    # Written to .env (not config.json); backend/config.py reads
    # ADMIN_PASSWORD_HASH from .env via pydantic-settings Settings class.
    step "2g — Hashing admin password"
    local _pw_hash
    # Pass password via stdin — avoids secret appearing in ps output.
    _pw_hash="$(printf '%s' "${ADMIN_PASSWORD}" | \
        "${APP_DIR}/venv/bin/python3" -c \
        'import sys, bcrypt
pw = sys.stdin.read()
print(bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode())')"

    # Clear the plaintext password immediately after hashing.
    unset ADMIN_PASSWORD

    # Single-quote the hash so that bash `source .env` under `set -euo pipefail`
    # does not interpret the $2b$12$... prefix as unbound positional parameters.
    # pydantic-settings strips surrounding single quotes before loading the value.
    # Canonical writer only (A3): the hash travels via bounded stdin from the
    # Bash BUILTIN printf -- never in any process argv or environment. The
    # writer single-quotes the value itself (bash-source safety preserved).
    builtin printf '%s' "${_pw_hash}" | \
        /usr/bin/python3 -I /opt/conduit-cc/bin/ccc-env \
            set-key "${CONF_DIR}/.env" ADMIN_PASSWORD_HASH \
        || die "canonical .env writer failed for ADMIN_PASSWORD_HASH"
    info "ADMIN_PASSWORD_HASH written to .env"

    # ---- 2h  config.json --------------------------------------------------- #
    step "2h — Writing config.json"
    if [[ -f "${CONF_DIR}/config.json" ]]; then
        info "${CONF_DIR}/config.json already exists — preserving"
    else
        cp "${APP_DIR}/config.example.json" "${CONF_DIR}/config.json"
        chown "${APP_USER}:${APP_USER}" "${CONF_DIR}/config.json"
        chmod 640 "${CONF_DIR}/config.json"
        info "config.json written from config.example.json"
    fi

    # ---- 2i  nginx configuration ------------------------------------------- #
    step "2i — Configuring nginx"

    # Remove Ubuntu default site — it listens on port 80 and conflicts with
    # the CCC HTTP→HTTPS redirect block.
    if [[ -L /etc/nginx/sites-enabled/default ]]; then
        rm -f /etc/nginx/sites-enabled/default
        info "Removed nginx default site symlink"
    fi

    # The nginx site is rendered, validated, reloaded, and symlinked below by the
    # shared ccc-apply-https-port helper — after the rate-limit zone exists, so
    # `nginx -t` passes (the site references the login_limit zone).

    # Write the rate-limiting zone into the http context.
    #
    # deployment/conduit-cc.nginx uses:
    #   limit_req zone=login_limit burst=9 nodelay;
    # nginx requires a matching limit_req_zone declaration in the http {} block.
    # A fresh Ubuntu 22.04 nginx install has no such zone; nginx -t would fail
    # without this file.
    #
    # /etc/nginx/conf.d/*.conf is included inside http {} by Ubuntu's stock
    # nginx.conf, making this the idiomatic injection point.  Prefer this over
    # sed-patching nginx.conf: it is idempotent and survives nginx package upgrades.
    cat > ${NGINX_RATELIMIT} << 'RATELIMIT_EOF'
# Conduit Control Center — login endpoint rate limiting zone (Issue #34)
# Referenced by: /etc/nginx/sites-available/conduit-cc
#   limit_req zone=login_limit burst=9 nodelay;
#
# 10 r/m  ≈ 1 request every 6 seconds per unique IP address.
# 10m     ≈ 160,000 IP state entries before LRU eviction.
#
# Managed by install.sh. Do not edit directly; changes will be overwritten
# on reinstall/update.
limit_req_zone $binary_remote_addr zone=login_limit:10m rate=10r/m;
RATELIMIT_EOF
    chmod 644 ${NGINX_RATELIMIT}
    info "Rate limiting zone written to ${NGINX_RATELIMIT}"

    # Install the shared HTTPS-port apply helper (root:root 0755) so install.sh,
    # update.sh, and a future CLI share one validated render/reload/UFW path.
    install -d -o root -g root -m 0755 /opt/conduit-cc/bin
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-apply-https-port" \
        /opt/conduit-cc/bin/ccc-apply-https-port

    # Render the site for the selected HTTPS port, validate, reload, open UFW.
    # The helper renders <CF_RECORD_NAME>/<CF_HTTPS_PORT>/redirect-suffix, creates
    # the sites-enabled symlink, backs up any prior site, runs `nginx -t`, and
    # reloads only on success (restoring the backup on failure).
    # ---- 2i-pre  Firewall preflight (READ-ONLY; precedes ALL UFW writes) --- #
    # ADR-0004: resolve the purpose-aware firewall plan (SSH port discovery)
    # BEFORE the HTTPS helper reconciles UFW and before any ufw write. Any
    # ambiguity/conflict/invalid override fails closed here (UFW untouched, the
    # helper NOT invoked). install.sh passes --skip-ufw so ALL UFW writes are
    # consolidated in _firewall_apply (2j).
    step "2i-pre — Firewall preflight (SSH administration port discovery)"
    _firewall_preflight

    /opt/conduit-cc/bin/ccc-apply-https-port apply --skip-ufw \
        --port "${HTTPS_PORT}" --hostname "${CF_RECORD_NAME}" \
        || die "Failed to apply HTTPS port ${HTTPS_PORT}." \
               "The previous nginx site (if any) was restored; see output above."
    info "nginx site applied on HTTPS port ${HTTPS_PORT}"

    # Persist the selected port to config.json (single source of truth) only
    # after a successful apply, so the SoT never diverges from the live config.
    python3 - "${CONF_DIR}/config.json" "${HTTPS_PORT}" <<'PYEOF'
import json, sys
path, port = sys.argv[1], int(sys.argv[2])
with open(path) as fh:
    cfg = json.load(fh)
cfg.setdefault("web", {})["https_port"] = port
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
PYEOF
    info "config.json web.https_port set to ${HTTPS_PORT}"

    # ---- 2j  UFW firewall (single consolidated transaction, ADR-0004) ------ #
    # Revalidate evidence, dry-run, add SSH/HTTP/HTTPS rules (add-only), verify,
    # then enable. SSH admin port(s) are the evidenced LOCAL sshd port(s) — never a
    # conventional 22 fallback. No inbound Conduit UDP. Add-before-enable.
    step "2j — Applying firewall plan and enabling UFW"
    _firewall_apply

    # ---- 2k  Systemd service ----------------------------------------------- #
    step "2k — Installing systemd service"
    sed 's/\r$//' "${APP_DIR}/deployment/conduit-cc.service" > "${SYSTEMD_UNIT}"  # LF-normalize systemd unit (field CRLF fix)
    systemctl daemon-reload
    info "${SYSTEMD_UNIT} installed"

    # ---- 2l-pre  Conduit config write helper (M2) ------------------------- #
    # Hardened root helper for the config write path. CCC invokes it via the
    # single sudoers line below; the helper validates its own input and writes
    # ONLY Environment= lines to the drop-in. Must be root-owned and NOT
    # writable by ${APP_USER}.
    step "2l — Installing Conduit config helper (M2)"
    install -d -o root -g root -m 0755 /opt/conduit-cc/bin
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-apply-conduit-config" \
        /opt/conduit-cc/bin/ccc-apply-conduit-config
    # Drop-in dir for CCC-managed Conduit config (Environment overrides only).
    install -d -o root -g root -m 0755 /etc/systemd/system/conduit.service.d
    helper_meta="$(stat -c '%U:%a' /opt/conduit-cc/bin/ccc-apply-conduit-config)"
    [ "${helper_meta}" = "root:755" ] || die \
        "Config helper ownership/perms wrong (${helper_meta}); expected root:755"
    info "Config helper installed (root:root 0755)"

    # ---- 2l-pc  Personal compartment helper (C4) --------------------------- #
    # Separate helper for the Personal Compartment identity. Runs AS conduit (via
    # the (conduit) sudoers grant below), NEVER root. Must be root-owned and NOT
    # writable by ${APP_USER} or conduit.
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-personal-compartment" \
        /opt/conduit-cc/bin/ccc-personal-compartment
    pc_helper_meta="$(stat -c '%U:%a' /opt/conduit-cc/bin/ccc-personal-compartment)"
    [ "${pc_helper_meta}" = "root:755" ] || die \
        "Personal compartment helper ownership/perms wrong (${pc_helper_meta}); expected root:755"
    info "Personal compartment helper installed (root:root 0755)"

    # ---- 2l-rv  Ryve claim helper (Epic #3, R1) ---------------------------- #
    # Separate helper for the Ryve Claim QR. Runs AS conduit (via the (conduit)
    # sudoers grant below), NEVER root. Root-owned 0755; not writable by
    # ${APP_USER} or conduit.
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-ryve-claim" \
        /opt/conduit-cc/bin/ccc-ryve-claim
    rv_helper_meta="$(stat -c '%U:%a' /opt/conduit-cc/bin/ccc-ryve-claim)"
    [ "${rv_helper_meta}" = "root:755" ] || die \
        "Ryve claim helper ownership/perms wrong (${rv_helper_meta}); expected root:755"
    info "Ryve claim helper installed (root:root 0755)"

    # ---- 2l-rs  Restore helper (Epic #4, S4B-2.1/2.4) ---------------------- #
    # Privileged restore applier. Runs AS root (via the (root) sudoers grant
    # below): it stops conduit-cc, runs restore_backup(), and restarts it. It
    # reads the encrypted backup + passphrase on stdin only. Root-owned 0755;
    # not writable by ${APP_USER}.
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-restore-apply" \
        /opt/conduit-cc/bin/ccc-restore-apply
    rs_helper_meta="$(stat -c '%U:%a' /opt/conduit-cc/bin/ccc-restore-apply)"
    [ "${rs_helper_meta}" = "root:755" ] || die \
        "Restore helper ownership/perms wrong (${rs_helper_meta}); expected root:755"
    info "Restore helper installed (root:root 0755)"

    # ---- 2l-up  Update helper (Feature 2) ---------------------------------- #
    # Privileged CCC updater. Runs AS root (via the (root) sudoers grant below):
    # reads a verified CCC release tarball on stdin, then runs the installed
    # update.sh --ccc-only. argv-only ("apply"); never takes a tag/ref/path.
    # Root-owned 0755; not writable by ${APP_USER}.
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-update-apply" \
        /opt/conduit-cc/bin/ccc-update-apply
    up_helper_meta="$(stat -c '%U:%a' /opt/conduit-cc/bin/ccc-update-apply)"
    # Epic-1 (F12): the Owner trust-anchor ceremony tool (root 0755; the anchor
    # itself is provisioned OUT-OF-BAND by the Owner, never by install).
    install -o root -g root -m 0755 \
        "${APP_DIR}/deployment/bin/ccc-provision-trust-anchor" \
        /opt/conduit-cc/bin/ccc-provision-trust-anchor
    _verify_bin_dir
    _verify_trust_dir
    [ "${up_helper_meta}" = "root:755" ] || die \
        "Update helper ownership/perms wrong (${up_helper_meta}); expected root:755"
    info "Update helper installed (root:root 0755)"

    # ---- 2l  sudoers rule for Conduit controls ----------------------------- #
    # adapter.py calls "sudo systemctl start|stop|restart conduit" and, for the
    # M2 config write path, "sudo /opt/conduit-cc/bin/ccc-apply-conduit-config".
    # S4B-2.4 adds the restore helper grant. NoNewPrivileges is omitted from
    # conduit-cc.service (see service file header) to allow sudo's setuid bit.
    #
    # Safe write: render to a temp file, validate with `visudo -cf` BEFORE it goes
    # live, set 0440, then atomically replace. A malformed /etc/sudoers.d file can
    # break sudo host-wide, so it must never be written live unvalidated.
    step "2l — Creating sudoers rule"
    _sudoers_tmp="$(mktemp)"
    cat > "${_sudoers_tmp}" <<EOF
# Conduit Control Center — allow ${APP_USER} to control the Conduit service
# Generated by install.sh — do not edit manually
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
    mv -f "${_sudoers_tmp}" "${SUDOERS_FILE}"
    info "${SUDOERS_FILE} created (440)"

    # ---- 2m  DDNS script and log directory --------------------------------- #
    step "2m — Installing DDNS script"
    cp "${APP_DIR}/scripts/cloudflare-ddns.sh" "${DDNS_BIN}"
    chmod 755 "${DDNS_BIN}"
    chown root:root "${DDNS_BIN}"
    info "${DDNS_BIN} installed"

    # Create the log directory with conduit-cc ownership BEFORE the first
    # DDNS run.  cloudflare-ddns.sh _ensure_log_dir() would create it as
    # root when run by install.sh, leaving the cron job unable to write.
    mkdir -p "${LOG_DIR}"
    chown "${APP_USER}:${APP_USER}" "${LOG_DIR}"
    chmod 755 "${LOG_DIR}"
    info "${LOG_DIR} created (755, ${APP_USER})"

    # ---- E3 audit directory (ADR-0003 Phase B) ----------------------------- #
    # Root-owned PARENT (/var/log); the dir is root:conduit-cc 0750 so the
    # unprivileged service can traverse + READ audit records but cannot
    # write/unlink them, and cannot rename the directory. Must exist BEFORE
    # conduit-cc.service first starts (below), because the unit's
    # ReadWritePaths=/var/log/conduit-cc-audit binds at service start.
    install -d -o root -g "${APP_USER}" -m 0750 /var/log/conduit-cc-audit
    info "/var/log/conduit-cc-audit created (0750, root:${APP_USER})"
    # Epic-1: privileged updater state (root-only), public status, trust anchor dir.
    _provision_priv_state_dirs

    # ---- 2m2  logrotate config for CCC logs (SD-card protection) ----------- #
    # Static config shipped in deployment/; rotates /var/log/conduit-cc/*.log.
    # logrotate is run by the OS (cron.daily / logrotate.timer); CCC adds no
    # timer of its own. No-op if logrotate is absent (warn only).
    step "2m2 — Installing logrotate config for ${LOG_DIR}"
    if command -v logrotate >/dev/null 2>&1; then
        install -o root -g root -m 0644 \
            "${APP_DIR}/deployment/conduit-cc.logrotate" \
            /etc/logrotate.d/conduit-cc
        if logrotate -d /etc/logrotate.d/conduit-cc >/dev/null 2>&1; then
            info "logrotate config installed + validated (/etc/logrotate.d/conduit-cc)"
        else
            warn "logrotate config installed but failed dry-run; check /etc/logrotate.d/conduit-cc"
        fi
    else
        warn "logrotate not found; install it (apt-get install -y logrotate) to rotate ${LOG_DIR}"
    fi

    # Install cron job for conduit-cc user (every 5 minutes).
    # Removes any existing CCC DDNS entry first to stay idempotent.
    #
    # Two set -e pitfalls avoided here:
    #   1. crontab -l exits 1 when the user has no crontab yet.  Capturing it
    #      with $(...) || true prevents the subshell from aborting.
    #   2. grep -v exits 1 when every input line matched (nothing passed through).
    #      The || true guard handles that edge case.
    local _cron_entry="*/5 * * * * ${DDNS_BIN} >> ${LOG_DIR}/ddns.log 2>&1"
    local _existing_cron
    _existing_cron="$(crontab -u "${APP_USER}" -l 2>/dev/null || true)"
    {
        if [[ -n "${_existing_cron}" ]]; then
            echo "${_existing_cron}" | grep -v "cloudflare-ddns" || true
        fi
        echo "${_cron_entry}"
    } | crontab -u "${APP_USER}" -
    info "DDNS cron job installed for ${APP_USER}"

    # Run DDNS script once immediately; failure is a warning, not a blocker.
    if "${DDNS_BIN}" >> "${LOG_DIR}/ddns.log" 2>&1; then
        info "Initial DDNS update succeeded"
    else
        warn "Initial DDNS update failed — DNS will sync on next cron run."
        warn "Check ${LOG_DIR}/ddns.log for details."
    fi

    # ---- 2n  ccc-unlock utility -------------------------------------------- #
    step "2n — Installing ccc-unlock utility"
    ln -sf "${APP_DIR}/scripts/ccc-unlock" /usr/local/bin/ccc-unlock
    info "ccc-unlock → ${APP_DIR}/scripts/ccc-unlock"

    # ---- 2x-a  Conduit system user ----------------------------------------- #
    step "2x-a — Creating system user '${CONDUIT_USER}'"
    if id "${CONDUIT_USER}" &>/dev/null; then
        info "User '${CONDUIT_USER}' already exists — skipping"
    else
        useradd \
            --system \
            --no-create-home \
            --shell /usr/sbin/nologin \
            --comment "Psiphon Conduit inproxy node" \
            "${CONDUIT_USER}"
        info "User '${CONDUIT_USER}' created"
    fi

    # ---- 2x-b  Conduit directories ----------------------------------------- #
    step "2x-b — Creating Conduit directories"
    # Binary dir: root-owned, 755 — binary is root:root 755
    mkdir -p "${CONDUIT_BIN_DIR}"
    chown root:root "${CONDUIT_BIN_DIR}"
    chmod 755 "${CONDUIT_BIN_DIR}"
    # Data dir and data subdirectory: conduit-owned, 700
    # /var/lib/conduit/data/conduit_key.json is written here by the binary (0600)
    mkdir -p "${CONDUIT_DATA_DIR}/data"
    chown "${CONDUIT_USER}:${CONDUIT_USER}" "${CONDUIT_DATA_DIR}"
    chmod 700 "${CONDUIT_DATA_DIR}"
    chown "${CONDUIT_USER}:${CONDUIT_USER}" "${CONDUIT_DATA_DIR}/data"
    chmod 700 "${CONDUIT_DATA_DIR}/data"
    info "${CONDUIT_BIN_DIR} (755, root:root) and ${CONDUIT_DATA_DIR} (700, ${CONDUIT_USER}) ready"

    # ---- 2x-c  Install Conduit binary -------------------------------------- #
    step "2x-c — Installing Conduit binary"
    local _conduit_tmp
    _conduit_tmp="$(mktemp /tmp/conduit.XXXXXX)"

    if [[ "${CONDUIT_BIN_SRC}" == "download" ]]; then
        local _gh_base="https://github.com/Psiphon-Inc/conduit/releases/download/release-cli-${CONDUIT_VERSION}"
        local _asset
        _asset="$(conduit_asset_for_arch "$(uname -m)")" || { rm -f "${_conduit_tmp}"; die "Unsupported architecture '$(uname -m)': no Conduit asset mapping (BL-0002 supports aarch64, armv7l)."; }

        step "  2x-c.1 — Downloading checksums.txt"
        local _checksums
        _checksums="$(curl -fsSL "${_gh_base}/checksums.txt")" || {
            rm -f "${_conduit_tmp}"
            die "Failed to download checksums.txt from GitHub." \
                "Check internet connectivity or place the binary at ${SCRIPT_DIR}/conduit"
        }

        step "  2x-c.2 — Downloading ${_asset}"
        curl -fsSL -o "${_conduit_tmp}" "${_gh_base}/${_asset}" || {
            rm -f "${_conduit_tmp}"
            die "Failed to download conduit binary from GitHub." \
                "Check internet connectivity or place the binary at ${SCRIPT_DIR}/conduit"
        }

        step "  2x-c.3 — Verifying SHA-256 checksum"
        local _expected_sha _actual_sha
        _expected_sha="$(printf '%s\n' "${_checksums}" | grep "${_asset}" | awk '{print $1}')"
        [[ -n "${_expected_sha}" ]] || {
            rm -f "${_conduit_tmp}"
            die "Could not find checksum for '${_asset}' in checksums.txt." \
                "The release assets may have changed — verify manually."
        }
        _actual_sha="$(sha256sum "${_conduit_tmp}" | awk '{print $1}')"
        [[ "${_actual_sha}" == "${_expected_sha}" ]] || {
            rm -f "${_conduit_tmp}"
            die "SHA-256 checksum mismatch for conduit binary." \
                "Expected: ${_expected_sha}  Got: ${_actual_sha}"
        }
        info "SHA-256 verified: ${_actual_sha:0:16}..."
    else
        cp "${CONDUIT_BIN_SRC}" "${_conduit_tmp}"
    fi

    # Pre-swap validation (4 steps) — confirm binary is usable before install
    step "  2x-c.4 — Pre-swap validation"
    chmod +x "${_conduit_tmp}"
    [[ -x "${_conduit_tmp}" ]] || {
        rm -f "${_conduit_tmp}"
        die "Binary is not executable after chmod +x."
    }
    local _ver_out
    _ver_out="$("${_conduit_tmp}" --version 2>&1)" || {
        rm -f "${_conduit_tmp}"
        die "Binary failed --version check (non-zero exit)." \
            "The binary may be corrupt or built for a different architecture."
    }
    printf '%s\n' "${_ver_out}" | grep -q "${CONDUIT_VERSION}" || {
        rm -f "${_conduit_tmp}"
        die "Binary version mismatch: expected ${CONDUIT_VERSION}." \
            "Got: ${_ver_out}"
    }
    info "Pre-swap validation passed: ${_ver_out}"

    # Install — atomic copy via install(1)
    install -o root -g root -m 755 "${_conduit_tmp}" "${CONDUIT_BIN_DIR}/conduit"
    rm -f "${_conduit_tmp}"
    info "${CONDUIT_BIN_DIR}/conduit installed (root:root 755)"

    # ---- 2x-d  Version file ------------------------------------------------ #
    step "2x-d — Recording Conduit version"
    printf '%s\n' "${CONDUIT_VERSION}" > "${CONDUIT_BIN_DIR}/version"
    info "${CONDUIT_BIN_DIR}/version: ${CONDUIT_VERSION}"

    # ---- 2x-e  Conduit systemd service ------------------------------------- #
    step "2x-e — Installing conduit.service"
    local _conduit_unit="/etc/systemd/system/conduit.service"
    sed 's/\r$//' "${APP_DIR}/deployment/conduit.service" > "${_conduit_unit}"  # LF-normalize systemd unit (field CRLF fix)
    chown root:root "${_conduit_unit}"
    chmod 644 "${_conduit_unit}"
    systemctl daemon-reload
    info "${_conduit_unit} installed"

    # ---- 2x-e2  Reduced-mode artifact guard (BS1) -------------------------- #
    # Fail-safe before starting Conduit: confirm the installed helper + unit
    # actually support reduced mode (helper accepts --reduced-* args; the unit
    # carries the static --set InproxyReduced* tokens + CCC_REDUCED_* defaults).
    step "2x-e2 — Verifying reduced-mode artifacts"
    local _ccc_helper="/opt/conduit-cc/bin/ccc-apply-conduit-config"
    local _t
    if [[ ! -f "${_ccc_helper}" ]] || ! grep -q -- "--reduced-start-min" "${_ccc_helper}"; then
        die "Reduced-mode helper missing/outdated (${_ccc_helper} lacks --reduced-start-min)."
    fi
    for _t in \
        "--set InproxyReducedStartTime=\${CCC_REDUCED_START}" \
        "--set InproxyReducedEndTime=\${CCC_REDUCED_END}" \
        "--set InproxyReducedMaxCommonClients=\${CCC_REDUCED_MAXCOMMON}" \
        "--set InproxyReducedLimitUpstreamBytesPerSecond=\${CCC_REDUCED_UP}" \
        "--set InproxyReducedLimitDownstreamBytesPerSecond=\${CCC_REDUCED_DOWN}"; do
        grep -qF -- "${_t}" "${_conduit_unit}" \
            || die "conduit.service missing reduced token: ${_t}"
    done
    for _t in CCC_REDUCED_START CCC_REDUCED_END CCC_REDUCED_MAXCOMMON CCC_REDUCED_UP CCC_REDUCED_DOWN; do
        grep -qE "^Environment=${_t}=" "${_conduit_unit}" \
            || die "conduit.service missing default: Environment=${_t}"
    done
    # ---- Personal-clients token guard (C2) --------------------------------- #
    # ExecStart references the braced ${CCC_MAX_PERSONAL_CLIENTS}; the base unit
    # MUST carry the =0 default so the expansion is never empty (an empty
    # --max-personal-clients argument fails Conduit startup). The compartment ID
    # must NOT be passed on ExecStart (Conduit auto-loads it from disk).
    grep -qF -- "--max-personal-clients \${CCC_MAX_PERSONAL_CLIENTS}" "${_conduit_unit}" \
        || die "conduit.service missing personal token: --max-personal-clients \${CCC_MAX_PERSONAL_CLIENTS}"
    grep -qE "^Environment=CCC_MAX_PERSONAL_CLIENTS=0$" "${_conduit_unit}" \
        || die "conduit.service missing default: Environment=CCC_MAX_PERSONAL_CLIENTS=0"
    if grep -qF -- "--compartment-id" "${_conduit_unit}"; then
        die "conduit.service must NOT pass --compartment-id (auto-loaded from personal_compartment.json)"
    fi
    info "Reduced-mode + personal-clients helper/unit tokens verified"

    # ---- 2x-f  Enable and start Conduit ------------------------------------ #
    step "2x-f — Enabling and starting conduit service"
    systemctl enable --now conduit
    info "conduit enabled and started"

    # ---- 2x-g  Verify Conduit is active ------------------------------------ #
    step "2x-g — Verifying conduit.service"
    local _c_attempts=0
    local _c_max=6   # 30 seconds

    while [[ "${_c_attempts}" -lt "${_c_max}" ]]; do
        if systemctl is-active --quiet conduit 2>/dev/null; then
            info "conduit.service is active"
            break
        fi
        _c_attempts=$(( _c_attempts + 1 ))
        step "  Waiting for conduit to start... (${_c_attempts}/${_c_max})"
        sleep 5
    done

    if ! systemctl is-active --quiet conduit 2>/dev/null; then
        warn "conduit.service did not become active within 30 seconds."
        warn "Check: journalctl -u conduit -n 30 --no-pager"
        warn "CCC installation will continue — Conduit can be started manually."
    else
        # Verify metrics endpoint — non-fatal; new nodes need time to bind
        if curl -sf "http://127.0.0.1:9090/metrics" 2>/dev/null \
                | grep -q "conduit_max_common_clients 50"; then
            info "Metrics endpoint verified: conduit_max_common_clients=50"
        else
            info "Metrics endpoint not yet ready (normal on first start — give it 30 s)"
        fi
    fi

    # ---- 2x-h  UFW firewall reminder --------------------------------------- #
    # Conduit binds dynamic, high-numbered UDP ports for its in-proxy peer
    # traffic. These change at runtime (field-confirmed on the armv7l RPi2
    # install, where the observed UDP set changed shortly after start), so
    # per-port UFW rules are ineffective. The validated reference deployment
    # (arm64 Pi 4 and the armv7l RPi2 field install) operates correctly with the
    # evidenced SSH administration port(s), TCP 80, and the selected HTTPS port
    # open, so CCC adds NO inbound UDP rules. Stateful UFW
    # already permits return traffic for Conduit's outbound in-proxy flows.
    # See docs/pre-install.md.
    step "2x-h — Conduit firewall reminder"
    warn "Conduit uses dynamic UDP ports for in-proxy traffic; they change at runtime."
    warn "The validated deployment runs with the evidenced SSH administration port(s), TCP 80,"
    warn "and the selected HTTPS port open (added alongside any pre-existing UFW rules),"
    warn "so inbound UDP rules are NOT required and CCC does NOT add them."
    warn "Do NOT add per-port UDP rules: the ports move and it only widens attack surface."
    warn "Inspect (optional):  ss -ulnp | grep conduit    (details: docs/pre-install.md)"

    # ---- 2o  Enable and start service -------------------------------------- #
    step "2o — Enabling and starting ${SERVICE_NAME}"
    systemctl enable --now "${SERVICE_NAME}"
    info "${SERVICE_NAME} enabled and started"

    # ---- 2p  Health check -------------------------------------------------- #
    step "2p — Waiting for service to become healthy"
    local _attempts=0
    local _max=$(( HEALTH_TIMEOUT / HEALTH_INTERVAL ))
    local _health_status

    while [[ "${_attempts}" -lt "${_max}" ]]; do
        local _response
        _response="$(curl -sf "http://127.0.0.1:8000/api/health" 2>/dev/null)" || true
        if [[ -n "${_response}" ]]; then
            _health_status="$(echo "${_response}" | \
                json_get "d.get('status','')")"
            if [[ "${_health_status}" == "ok" ]]; then
                local _version
                _version="$(echo "${_response}" | json_get "d.get('version','')")"
                info "Health check passed (version=${_version})"
                return 0
            fi
        fi
        _attempts=$(( _attempts + 1 ))
        step "  Waiting... (${_attempts}/${_max})"
        sleep "${HEALTH_INTERVAL}"
    done

    die \
        "Service did not become healthy within ${HEALTH_TIMEOUT} seconds." \
        "Check: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
}

# --------------------------------------------------------------------------- #
#  Phase 3 — Post-install summary                                             #
# --------------------------------------------------------------------------- #

phase3_summary() {
    section "Phase 3 — Installation complete"
    printf "\n"
    printf '  %bOK%b Conduit Control Center is installed and running.\n' "${GREEN}" "${RESET}"
    printf "\n"
    local _url_suffix=""
    [[ "${HTTPS_PORT}" != "443" ]] && _url_suffix=":${HTTPS_PORT}"
    printf '  %bDashboard URL:%b  https://%s%s/\n' "${BOLD}" "${RESET}" "${CF_RECORD_NAME}" "${_url_suffix}"
    printf '  %bAdmin user:%b     %s\n' "${BOLD}" "${RESET}" "${ADMIN_USERNAME}"
    if [[ "${HTTPS_PORT}" != "443" ]]; then
        printf '  %bNote:%b non-default HTTPS port %s — ensure your router forwards it and the Cloudflare record stays proxied.\n' "${YELLOW}" "${RESET}" "${HTTPS_PORT}"
    fi
    printf "\n"
    printf "  Service management:\n"
    printf "    systemctl status  conduit-cc\n"
    printf "    systemctl status  conduit\n"
    printf "    journalctl -u     conduit-cc -f\n"
    printf "    journalctl -u     conduit    -f\n"
    printf "\n"
    printf "  Conduit metrics endpoint:\n"
    printf "    curl http://127.0.0.1:9090/metrics | grep conduit_max_common_clients\n"
    printf "\n"
    printf '  %bConduit firewall (informational):%b\n' "${CYAN}" "${RESET}"
    printf "    Conduit uses dynamic UDP ports that change at runtime.\n"
    printf "    It runs with the evidenced SSH administration port(s), 80, and the selected HTTPS port open; no UDP rules needed.\n"
    printf "    Inspect (optional):  ss -ulnp | grep conduit   (see docs/pre-install.md)\n"
    printf "\n"
    printf "  DDNS log:\n"
    printf '    tail -f %s/ddns.log\n' "${LOG_DIR}"
    printf "\n"
    printf "  If your admin account is locked out:\n"
    printf "    sudo ccc-unlock\n"
    printf "\n"
    printf '  %bNext steps:%b\n' "${BOLD}" "${RESET}"
    printf '    1. Open https://%s%s/ and log in.\n' "${CF_RECORD_NAME}" "${_url_suffix}"
    printf "    2. (Optional) Inspect Conduit UDP ports; no UFW rules are required:\n"
    printf "         ss -ulnp | grep conduit\n"
    printf "    3. Verify Conduit node status on the dashboard.\n"
    printf "    4. Verify Cloudflare SSL/TLS is set to Full (strict):\n"
    printf "       https://dash.cloudflare.com -> SSL/TLS -> Overview\n"
    printf "\n"
    printf '  %bDocs:%b docs/pre-install.md · docs/tls-setup.md\n' "${CYAN}" "${RESET}"
    printf "\n"
}

# --------------------------------------------------------------------------- #
#  Entry point                                                                 #
# --------------------------------------------------------------------------- #

_parse_install_args "$@"
phase1_validate
phase2_install
phase3_summary
