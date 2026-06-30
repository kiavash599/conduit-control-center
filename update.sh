#!/usr/bin/env bash
# update.sh - Conduit Control Center in-place updater
# ====================================================
# Upgrades CCC to a new version with automatic rollback on failure.
#
# Usage:
#   sudo bash update.sh              Update from the directory containing
#                                    this script (same pattern as install.sh)
#   sudo bash update.sh --source DIR Update from an explicit source directory
#   sudo bash update.sh --ccc-only   Update CCC only; skip the Conduit Core binary
#   sudo bash update.sh --non-interactive  Skip the confirmation prompt (automation;
#                                    aliases --yes, -y). Required when no TTY.
#   sudo bash update.sh --help       Show this help
#
# The source directory must be a checkout or unpacked tarball of the new
# version of conduit-control-center.  The typical workflow is:
#
#   cd ~/conduit-control-center   # or wherever you cloned the repo
#   git pull
#   sudo bash update.sh
#
# What this script does (in order):
#   Phase 0: Validate existing installation and source directory
#   Phase 1: Backup /etc/conduit-cc/ and /opt/conduit-cc/ (code, not venv)
#   Phase 2: Pre-install new Python dependencies (service stays running)
#   Phase 3: Stop service; deploy new code; update nginx, systemd, DDNS
#   Phase 4: Start service; verify health and version
#   Phase 5: (on failure) Restore backup; restart old version
#   Phase 6: Print summary
#
# Downtime window: Phase 3 (systemctl stop) through Phase 4 (health check).
# Dependencies are installed before the stop (Phase 2) to minimise downtime.
# Typical downtime: 15-30 seconds.  Longer if Python packages are upgraded.
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
#   restores exact package versions from the pip freeze snapshot, and restarts
#   the old version.
#
# Backup location: /var/backups/conduit-cc/<timestamp>/
# The last 3 backups are kept.  Older backups are deleted automatically.
#

set -euo pipefail

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
# shellcheck disable=SC2034  # mirrors install.sh constants (Issue #45); unused in update.sh
readonly CONDUIT_USER="conduit"
readonly CONDUIT_BIN_DIR="/opt/conduit"
readonly CONDUIT_DATA_DIR="/var/lib/conduit"

# --------------------------------------------------------------------------- #
#  Script state                                                                #
# --------------------------------------------------------------------------- #

# Populated by _parse_args; defaults to SCRIPT_DIR.
SOURCE_DIR=""

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

# Read a single value from /etc/conduit-cc/.env.
# Usage: _env_val KEY
_env_val() {
    grep -m1 "^${1}=" "${CONF_DIR}/.env" 2>/dev/null | cut -d= -f2- || true
}

# Extract APP_VERSION from a backend/_version.py file.
# Usage: _read_version /path/to/source-or-app-dir
_read_version() {
    local _f="${1}/backend/_version.py"
    [[ -f "${_f}" ]] || { printf "unknown"; return; }
    python3 -c "
exec(open('${_f}').read())
print(APP_VERSION)
" 2>/dev/null || printf "unknown"
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
    if "${_DOWNTIME_STARTED}" && ! "${_UPDATE_SUCCEEDED}"; then
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
    fi
    exit "${_rc}"
}
trap '_on_exit' EXIT

_print_manual_recovery() {
    printf '\n%bManual recovery steps:%b\n' "${BOLD}" "${RESET}" >&2
    if [[ -n "${BACKUP_DIR}" ]]; then
        printf "  1. tar -xzf %s/conf.tar.gz -C /\n" \
            "${BACKUP_DIR}" >&2
        printf "  2. rsync -a --delete --exclude 'venv/' %s/app/ %s/\n" \
            "${BACKUP_DIR}" "${APP_DIR}" >&2
        printf "  3. %s/venv/bin/pip install --force-reinstall -r %s/pip-freeze.txt\n" \
            "${APP_DIR}" "${BACKUP_DIR}" >&2
        printf "  4. cp %s/conduit-cc.service %s\n" \
            "${BACKUP_DIR}" "${SYSTEMD_UNIT}" >&2
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
                printf "Usage: sudo bash %s [--source DIR|--ccc-only|--non-interactive|--help]\n" "$0" >&2
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

# --------------------------------------------------------------------------- #
#  Phase 0 - Pre-flight                                                       #
# --------------------------------------------------------------------------- #

phase0_preflight() {
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

    step "0f - Reading CF_RECORD_NAME from ${CONF_DIR}/.env"
    CF_RECORD_NAME="$(_env_val CF_RECORD_NAME)"
    [[ -n "${CF_RECORD_NAME}" ]] || die \
        "CF_RECORD_NAME is empty in ${CONF_DIR}/.env." \
        "Check ${CONF_DIR}/.env - the file may be corrupted."
    info "CF_RECORD_NAME: ${CF_RECORD_NAME}"

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
    printf "  Backup:  %s/<timestamp>/\n" "${BACKUP_ROOT}"
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
}

# --------------------------------------------------------------------------- #
#  Phase 1 - Backup (service still running; no downtime)                     #
#                                                                             #
#  Three items are backed up:                                                 #
#    conf.tar.gz  - full /etc/conduit-cc/ including TLS keys and ccc.db      #
#    app/         - /opt/conduit-cc/ code, excluding venv and caches         #
#    pip-freeze.txt - exact installed package versions for rollback           #
#    conduit-cc.service - live systemd unit (may differ from APP_DIR copy)   #
# --------------------------------------------------------------------------- #

phase1_backup() {
    section "Phase 1 - Backup (service running)"

    local _ts
    _ts="$(date +%Y%m%d-%H%M%S)"
    BACKUP_DIR="${BACKUP_ROOT}/${_ts}"
    mkdir -p "${BACKUP_DIR}"
    chmod 700 "${BACKUP_DIR}"
    info "Backup directory: ${BACKUP_DIR}"

    step "1a - Backing up ${CONF_DIR}"
    # -C / makes paths relative (etc/conduit-cc/...) so extraction via
    #   tar -xzf conf.tar.gz -C /
    # correctly restores to /etc/conduit-cc/ on any machine.
    tar -czf "${BACKUP_DIR}/conf.tar.gz" -C / etc/conduit-cc
    info "${CONF_DIR} backed up ($(du -sh "${BACKUP_DIR}/conf.tar.gz" | cut -f1))"

    step "1b - Backing up ${APP_DIR} (code only, not venv)"
    # venv is excluded: it is large (~200 MB) and can be rebuilt from
    # pip-freeze.txt.  __pycache__ and *.pyc are transient; not worth saving.
    rsync -a \
        --exclude 'venv/' \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude 'ccc.db' \
        "${APP_DIR}/" "${BACKUP_DIR}/app/"
    info "${APP_DIR} code backed up"

    step "1c - Recording installed package versions"
    # Exact pip freeze snapshot used by phase5_rollback to restore packages
    # with --force-reinstall, ensuring downgrades work correctly.
    "${APP_DIR}/venv/bin/pip" freeze --quiet > "${BACKUP_DIR}/pip-freeze.txt"
    info "pip freeze: $(wc -l < "${BACKUP_DIR}/pip-freeze.txt") packages"

    step "1d - Backing up live systemd unit"
    cp "${SYSTEMD_UNIT}" "${BACKUP_DIR}/conduit-cc.service"
    info "systemd unit backed up"

    step "1e - Rotating old backups (keeping last ${BACKUP_KEEP})"
    _rotate_backups
}

# Delete oldest entries in BACKUP_ROOT until only BACKUP_KEEP remain.
_rotate_backups() {
    local _total
    _total="$(find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d \
        2>/dev/null | wc -l)"
    if [[ "${_total}" -gt "${BACKUP_KEEP}" ]]; then
        local _excess=$(( _total - BACKUP_KEEP ))
        find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d \
            | sort \
            | head -n "${_excess}" \
            | xargs rm -rf
        info "Removed ${_excess} old backup(s); keeping ${BACKUP_KEEP}"
    else
        info "Backup count: ${_total}/${BACKUP_KEEP} - no rotation needed"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 2 - Dependency pre-install (service still running)                  #
#                                                                             #
#  pip install runs BEFORE stopping the service.  uvicorn imports packages    #
#  once at startup; installing new packages into the running venv does not    #
#  affect the in-flight process.  The new code picks them up when it starts.  #
#                                                                             #
#  If pip fails here, die() exits before _DOWNTIME_STARTED=true so the       #
#  EXIT trap does not trigger rollback and the service keeps running.         #
# --------------------------------------------------------------------------- #

phase2_preinstall() {
    section "Phase 2 - Dependency pre-install (service running)"

    step "2a - Upgrading pip"
    "${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
    info "pip upgraded"

    step "2b - Installing dependencies from new requirements.txt"
    # Install from SOURCE_DIR (new version), not APP_DIR (old version).
    # pip upgrades, adds, or retains packages as needed.
    "${APP_DIR}/venv/bin/pip" install --quiet \
        -r "${SOURCE_DIR}/requirements.txt" || die \
        "pip install failed. Service is still running version ${CURRENT_VERSION}." \
        "Resolve the dependency issue and re-run update.sh."
    info "Dependencies installed"
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
        local _asset="conduit-linux-arm64"

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

    # ---- Update conduit.service unit --------------------------------------- #
    step "2b-f — Updating conduit.service"
    if [[ -f "${SOURCE_DIR}/deployment/conduit.service" ]]; then
        cp "${SOURCE_DIR}/deployment/conduit.service" /etc/systemd/system/conduit.service
        chown root:root /etc/systemd/system/conduit.service
        chmod 644 /etc/systemd/system/conduit.service
        systemctl daemon-reload
        info "/etc/systemd/system/conduit.service updated"
    else
        info "conduit.service not found in ${SOURCE_DIR}/deployment/ — keeping existing unit"
    fi

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
    systemctl stop "${SERVICE_NAME}"
    # Set AFTER stop so rollback knows to restart the service.
    _DOWNTIME_STARTED=true
    info "${SERVICE_NAME} stopped  [DOWNTIME STARTS]"

    step "3b - Deploying new code (rsync --delete)"
    # --exclude '/bin/' is ANCHORED (leading slash) to the transfer root, so it
    # excludes ONLY the top-level ${APP_DIR}/bin -- the privileged helper dir,
    # which is owned and re-provisioned by step 3b2 below from deployment/bin.
    # The source tree has no top-level bin/, so without this exclude --delete
    # would try to remove ${APP_DIR}/bin while the running ccc-update-apply
    # worker executes from it ("cannot delete non-empty directory: bin").
    # The slash anchors the rule so deployment/bin/ (the helper SOURCE) is still
    # deployed normally.
    rsync -a --delete \
        --exclude 'venv/' \
        --exclude 'ccc.db' \
        --exclude '__pycache__/' \
        --exclude '.git/' \
        --exclude '.env' \
        --exclude '/bin/' \
        "${SOURCE_DIR}/" "${APP_DIR}/"
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    info "Code deployed to ${APP_DIR}"

    step "3b2 - Re-provisioning privileged helpers + sudoers"
    # install.sh provisions /opt/conduit-cc/bin helpers and the sudoers grant, but
    # earlier update.sh did NOT re-provision them -- so an upgraded host could run
    # stale helper binaries and miss the restore grant (S4B-2.4). Re-install ALL
    # privileged helpers (root:root 0755) from the freshly-rsynced deployment/bin,
    # and rewrite the sudoers file. The bin dir is created by install.sh; ensure
    # it exists for robustness.
    install -d -o root -g root -m 0755 /opt/conduit-cc/bin
    for _h in ccc-apply-conduit-config ccc-personal-compartment ccc-ryve-claim ccc-restore-apply ccc-apply-https-port ccc-update-apply; do
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
${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-apply-conduit-config
${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-restore-apply
${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-update-apply
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

    step "3c - Updating systemd unit"
    # daemon-reload here precedes the service start in the health-check phase, so
    # the new StateDirectory=conduit-cc (S4B-2.4) is created on the next start.
    cp "${APP_DIR}/deployment/conduit-cc.service" "${SYSTEMD_UNIT}"
    systemctl daemon-reload
    info "${SYSTEMD_UNIT} updated"

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
    # updated conduit.service are installed (phase_m2_config_write_artifacts) and
    # verified (phase_bs1_reduced_guard); otherwise the new backend could invoke
    # an OLD helper with --reduced-* args. The start is deferred to
    # phase3b_start_service, which runs after phaseM2 + the guard.
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
                    _UPDATE_SUCCEEDED=true
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

    local _failed=false

    if [[ -z "${BACKUP_DIR}" ]] || [[ ! -d "${BACKUP_DIR}" ]]; then
        error "Backup directory not found: ${BACKUP_DIR:-<unset>}"
        error "Cannot perform automatic rollback."
        return 1
    fi

    # ---- 5a  Stop service -------------------------------------------------- #
    step "5a - Stopping service"
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    info "Service stopped"

    # ---- 5b  Restore /etc/conduit-cc --------------------------------------- #
    step "5b - Restoring ${CONF_DIR} from backup"
    if [[ -f "${BACKUP_DIR}/conf.tar.gz" ]]; then
        if tar -xzf "${BACKUP_DIR}/conf.tar.gz" -C /; then
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
        if rsync -a --delete \
                --exclude 'venv/' \
                --exclude '__pycache__/' \
                --exclude '*.pyc' \
                --exclude 'ccc.db' \
                "${BACKUP_DIR}/app/" "${APP_DIR}/"; then
            chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" 2>/dev/null || true
            info "${APP_DIR} code restored"
        else
            error "rsync restore failed for ${APP_DIR}."
            _failed=true
        fi
    else
        error "Backup app/ directory not found - code not restored."
        _failed=true
    fi

    # ---- 5d  Restore exact package versions -------------------------------- #
    step "5d - Restoring Python package versions"
    if [[ -f "${BACKUP_DIR}/pip-freeze.txt" ]]; then
        # --force-reinstall ensures packages are downgraded/restored even if
        # phase2_preinstall installed newer versions.
        if "${APP_DIR}/venv/bin/pip" install --quiet \
                --force-reinstall \
                -r "${BACKUP_DIR}/pip-freeze.txt"; then
            info "Package versions restored from pip-freeze.txt"
        else
            warn "pip restore failed - the venv may have mixed versions."
            warn "Manual fix: ${APP_DIR}/venv/bin/pip install --force-reinstall -r ${BACKUP_DIR}/pip-freeze.txt"
        fi
    else
        warn "pip-freeze.txt not found - package versions not restored."
        warn "The venv may have packages from the failed update."
    fi

    # ---- 5e  Restore systemd unit ------------------------------------------ #
    step "5e - Restoring systemd unit"
    local _unit_src
    if [[ -f "${BACKUP_DIR}/conduit-cc.service" ]]; then
        _unit_src="${BACKUP_DIR}/conduit-cc.service"
    elif [[ -f "${APP_DIR}/deployment/conduit-cc.service" ]]; then
        _unit_src="${APP_DIR}/deployment/conduit-cc.service"
        warn "Using APP_DIR copy of service unit (backup copy not found)."
    else
        error "Cannot find service unit to restore."
        _failed=true
        _unit_src=""
    fi
    if [[ -n "${_unit_src}" ]]; then
        if cp "${_unit_src}" "${SYSTEMD_UNIT}" && systemctl daemon-reload; then
            info "Systemd unit restored"
        else
            error "Failed to restore systemd unit."
            _failed=true
        fi
    fi

    # ---- 5f  Re-apply nginx configuration ---------------------------------- #
    step "5f - Re-applying nginx configuration"
    if command -v nginx &>/dev/null && [[ -n "${CF_RECORD_NAME:-}" ]] \
            && [[ -f "${APP_DIR}/deployment/conduit-cc.nginx" ]]; then
        if sed "s|<CF_RECORD_NAME>|${CF_RECORD_NAME}|g" \
                "${APP_DIR}/deployment/conduit-cc.nginx" \
                > "${NGINX_AVAILABLE}" 2>/dev/null; then
            info "nginx config re-applied"
            if nginx -t 2>/dev/null; then
                if systemctl is-active --quiet nginx 2>/dev/null; then
                    if systemctl reload nginx 2>/dev/null; then
                        info "nginx reloaded"
                    fi
                fi
            else
                warn "nginx -t failed after rollback - check config manually"
            fi
        else
            warn "nginx config not re-applied - check ${NGINX_AVAILABLE}"
        fi
    else
        warn "nginx rollback skipped (nginx absent, CF_RECORD_NAME empty," \
             "or nginx template missing)"
    fi

    # ---- 5g  Start service ------------------------------------------------- #
    step "5g - Starting ${SERVICE_NAME}"
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
            if [[ "${_rb_status}" == "ok" ]]; then
                info "Rollback health check passed (version=${_rb_ver})"
                _rb_ok=true
                break
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

    "${_failed}" && return 1 || return 0
}

# --------------------------------------------------------------------------- #
#  Phase 6 - Summary                                                          #
# --------------------------------------------------------------------------- #

phase6_summary() {
    section "Update complete"
    printf "\n"

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
#  Phase M2 - Configuration write artifacts (ALWAYS runs)                      #
# --------------------------------------------------------------------------- #
#  Deploys/re-asserts the M2 config-write artifacts on EVERY update, decoupled #
#  from the Conduit binary update (phase2b_conduit_update can early-return when #
#  Conduit is absent or already at the target version). The literal->Environment#
#  unit migration is value-preserving, so this runs daemon-reload only when the #
#  unit actually changed and NEVER restarts/stops Conduit (no downtime).       #
phase_m2_config_write_artifacts() {
    section "Phase M2 - Config write artifacts"

    local _helper_src="${SOURCE_DIR}/deployment/bin/ccc-apply-conduit-config"
    local _helper_dst="/opt/conduit-cc/bin/ccc-apply-conduit-config"
    local _unit_src="${SOURCE_DIR}/deployment/conduit.service"
    local _unit_dst="/etc/systemd/system/conduit.service"
    local _sudoers="/etc/sudoers.d/conduit-cc"
    local _app_user="${APP_USER:-conduit-cc}"

    # (1-4) Helper + drop-in dir + ownership/perms. Always; harmless if Conduit
    # is not installed (CCC-owned artifacts).
    if [[ -f "${_helper_src}" ]]; then
        step "M2-a — Installing config helper"
        install -d -o root -g root -m 0755 /opt/conduit-cc/bin
        install -o root -g root -m 0755 "${_helper_src}" "${_helper_dst}"
        install -d -o root -g root -m 0755 /etc/systemd/system/conduit.service.d
        local _hm
        _hm="$(stat -c '%U:%G:%a' "${_helper_dst}")"
        [[ "${_hm}" == "root:root:755" ]] || die \
            "Config helper ownership/perms wrong (${_hm}); expected root:root:755"
        info "Config helper installed (root:root 0755, not writable by ${_app_user})"
    else
        warn "config helper not in source (${_helper_src}) — skipping helper install"
    fi

    # (4b) Personal compartment helper (C4). Root-owned 0755; run AS conduit via
    # the (conduit) grant below. Not writable by ${_app_user} or conduit.
    local _pc_helper_src="${SOURCE_DIR}/deployment/bin/ccc-personal-compartment"
    local _pc_helper_dst="/opt/conduit-cc/bin/ccc-personal-compartment"
    if [[ -f "${_pc_helper_src}" ]]; then
        step "M2-a2 — Installing personal compartment helper"
        install -o root -g root -m 0755 "${_pc_helper_src}" "${_pc_helper_dst}"
        local _pcm
        _pcm="$(stat -c '%U:%G:%a' "${_pc_helper_dst}")"
        [[ "${_pcm}" == "root:root:755" ]] || die \
            "Personal compartment helper ownership/perms wrong (${_pcm}); expected root:root:755"
        info "Personal compartment helper installed (root:root 0755)"
    else
        warn "personal compartment helper not in source (${_pc_helper_src}) — skipping"
    fi

    # (4c) Ryve claim helper (Epic #3, R1). Root-owned 0755; run AS conduit via
    # the (conduit) grant below. Not writable by ${_app_user} or conduit.
    local _rv_helper_src="${SOURCE_DIR}/deployment/bin/ccc-ryve-claim"
    local _rv_helper_dst="/opt/conduit-cc/bin/ccc-ryve-claim"
    if [[ -f "${_rv_helper_src}" ]]; then
        step "M2-a3 — Installing Ryve claim helper"
        install -o root -g root -m 0755 "${_rv_helper_src}" "${_rv_helper_dst}"
        local _rvm
        _rvm="$(stat -c '%U:%G:%a' "${_rv_helper_dst}")"
        [[ "${_rvm}" == "root:root:755" ]] || die \
            "Ryve claim helper ownership/perms wrong (${_rvm}); expected root:root:755"
        info "Ryve claim helper installed (root:root 0755)"
    else
        warn "ryve claim helper not in source (${_rv_helper_src}) — skipping"
    fi

    # (5-6) Exact sudoers helper grant; append-if-missing; 0440; visudo -c.
    if [[ -f "${_sudoers}" ]]; then
        if ! grep -qF "${_helper_dst}" "${_sudoers}"; then
            step "M2-b — Adding sudoers helper grant"
            printf '%s\n' "${_app_user} ALL=(root) NOPASSWD: ${_helper_dst}" >> "${_sudoers}"
            chown root:root "${_sudoers}"
            chmod 440 "${_sudoers}"
            visudo -cf "${_sudoers}" || die "sudoers syntax check failed after adding helper grant"
            info "sudoers helper grant added + validated"
        else
            info "sudoers helper grant already present"
        fi
        # (6b) Personal compartment grant (C4): runas=conduit (NOT root).
        if ! grep -qF "(conduit) NOPASSWD: ${_pc_helper_dst}" "${_sudoers}"; then
            step "M2-b2 — Adding personal compartment sudoers grant"
            printf '%s\n' "${_app_user} ALL=(conduit) NOPASSWD: ${_pc_helper_dst}" >> "${_sudoers}"
            chown root:root "${_sudoers}"
            chmod 440 "${_sudoers}"
            visudo -cf "${_sudoers}" || die "sudoers syntax check failed after adding personal grant"
            info "personal compartment sudoers grant added + validated"
        else
            info "personal compartment sudoers grant already present"
        fi
        # (6c) Ryve claim grant (Epic #3, R1): runas=conduit (NOT root).
        if ! grep -qF "(conduit) NOPASSWD: ${_rv_helper_dst}" "${_sudoers}"; then
            step "M2-b3 — Adding Ryve claim sudoers grant"
            printf '%s\n' "${_app_user} ALL=(conduit) NOPASSWD: ${_rv_helper_dst}" >> "${_sudoers}"
            chown root:root "${_sudoers}"
            chmod 440 "${_sudoers}"
            visudo -cf "${_sudoers}" || die "sudoers syntax check failed after adding ryve grant"
            info "Ryve claim sudoers grant added + validated"
        else
            info "Ryve claim sudoers grant already present"
        fi
    else
        warn "${_sudoers} missing — run install.sh; skipping sudoers grant"
    fi

    # (7) Parameterized conduit.service: deploy only if Conduit is managed here,
    # and daemon-reload ONLY when the unit changed. Value-preserving: NO restart.
    if [[ -f "${_unit_dst}" && -f "${_unit_src}" ]]; then
        if ! cmp -s "${_unit_src}" "${_unit_dst}"; then
            step "M2-c — Updating parameterized conduit.service (value-preserving)"
            cp "${_unit_src}" "${_unit_dst}"
            chown root:root "${_unit_dst}"
            chmod 644 "${_unit_dst}"
            systemctl daemon-reload
            info "conduit.service updated + daemon-reload (no restart)"
        else
            info "conduit.service already current — no daemon-reload needed"
        fi
    else
        info "Conduit unit not installed here — skipping unit update (install.sh handles fresh installs)"
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
    systemctl start "${SERVICE_NAME}"
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
phase_m2_config_write_artifacts
phase_bs1_reduced_guard
phase3b_start_service
phase4_verify
phase6_summary
