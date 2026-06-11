#!/usr/bin/env bash
# uninstall.sh — Conduit Control Center uninstaller
# ===================================================
# Removes the CCC dashboard installed by install.sh.
#
# Usage:
#   sudo bash uninstall.sh            Standard: removes app files, preserves
#                                     configuration and data
#   sudo bash uninstall.sh --purge    Purge: removes everything including TLS
#                                     keys, .env, database, logs, and user
#   sudo bash uninstall.sh --help     Show this help
#
# Standard mode removes:
#   /opt/conduit-cc/                              application files and venv
#   /etc/systemd/system/conduit-cc.service        systemd unit
#   /etc/nginx/sites-available/conduit-cc         nginx site config
#   /etc/nginx/sites-enabled/conduit-cc           nginx symlink
#   /etc/nginx/conf.d/conduit-cc-ratelimit.conf   nginx rate-limiting zone
#   /etc/sudoers.d/conduit-cc                     sudoers rule
#   /usr/local/bin/cloudflare-ddns.sh             DDNS script
#   /usr/local/bin/ccc-unlock                     utility symlink
#   conduit-cc user crontab                       DDNS cron job
#
# Standard mode preserves:
#   /etc/conduit-cc/      TLS certificate, private key, .env, config.json, ccc.db
#   /var/log/conduit-cc/  DDNS and application logs
#   conduit-cc system user
#
# Purge mode (--purge) additionally removes all preserved items:
#   /etc/conduit-cc/tls/origin.key   TLS private key  (UNRECOVERABLE)
#   /etc/conduit-cc/.env             CF_API_TOKEN, SESSION_SECRET (UNRECOVERABLE)
#   /etc/conduit-cc/ccc.db           runtime database
#   /etc/conduit-cc/config.json      operator configuration
#   /var/log/conduit-cc/             all DDNS and application logs
#   conduit-cc system user and group
#
# UFW rules (22/tcp, 80/tcp, 443/tcp) are NEVER removed automatically.
# The nginx default site is NOT restored if install.sh removed it.
# Review UFW with: sudo ufw status
#

set -euo pipefail

# --------------------------------------------------------------------------- #
#  Constants — must match install.sh                                          #
# --------------------------------------------------------------------------- #

readonly APP_USER="conduit-cc"
readonly APP_DIR="/opt/conduit-cc"
readonly CONF_DIR="/etc/conduit-cc"
readonly LOG_DIR="/var/log/conduit-cc"
readonly SERVICE_NAME="conduit-cc"

# Psiphon Conduit — must match install.sh constants (Issue #45)
readonly CONDUIT_USER="conduit"
readonly CONDUIT_BIN_DIR="/opt/conduit"
readonly CONDUIT_DATA_DIR="/var/lib/conduit"
readonly NGINX_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}"
readonly NGINX_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}"
readonly NGINX_RATELIMIT="/etc/nginx/conf.d/${SERVICE_NAME}-ratelimit.conf"
readonly CRON_D_FILE="/etc/cron.d/${SERVICE_NAME}"
readonly SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
readonly SUDOERS_FILE="/etc/sudoers.d/${SERVICE_NAME}"
readonly DDNS_BIN="/usr/local/bin/cloudflare-ddns.sh"
readonly CCC_UNLOCK_BIN="/usr/local/bin/ccc-unlock"

# --------------------------------------------------------------------------- #
#  Script state                                                                #
# --------------------------------------------------------------------------- #

# Set by argument parsing below.
PURGE=false

# Tracks whether /opt/conduit-cc was fully removed.
# phase6_purge reads this flag before calling userdel.  Deleting the system
# user while files owned by that UID remain on disk produces orphaned UIDs.
# (Architecture Correction A)
_APP_DIR_REMOVED=false

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

info()    { printf "${GREEN}  OK${RESET}  %s\n" "$*"; }
step()    { printf "${CYAN}[CCC]${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}  !${RESET}  %s\n" "$*" >&2; }
section() { printf "\n${BOLD}%s${RESET}\n%s\n" "$*" "$(printf '=%.0s' {1..60})"; }

die() {
    printf "\n${RED}ERROR:${RESET} %s\n" "$1" >&2
    [[ -n "${2:-}" ]] && printf "${YELLOW}  FIX:${RESET} %s\n" "$2" >&2
    exit 1
}

# --------------------------------------------------------------------------- #
#  Argument parsing                                                            #
# --------------------------------------------------------------------------- #

for _arg in "$@"; do
    case "${_arg}" in
        --purge)
            PURGE=true
            ;;
        --help|-h)
            # Extract header comments from this script (lines 2 through the
            # blank line before set -euo pipefail).
            sed -n '2,/^set -euo pipefail/p' "$0" \
                | grep '^#' | sed 's/^#[[:space:]]\{0,1\}//'
            exit 0
            ;;
        *)
            printf "Unknown option: %s\n" "${_arg}" >&2
            printf "Usage: sudo bash %s [--purge|--help]\n" "$0" >&2
            exit 1
            ;;
    esac
done

# --------------------------------------------------------------------------- #
#  Root check                                                                  #
# --------------------------------------------------------------------------- #

[[ "${EUID}" -eq 0 ]] || die \
    "This uninstaller must be run as root." \
    "Run: sudo bash uninstall.sh${PURGE:+ --purge}"

# --------------------------------------------------------------------------- #
#  Phase 0 — Confirmation                                                     #
# --------------------------------------------------------------------------- #

phase0_confirm() {
    section "Conduit Control Center — Uninstaller"
    printf "\n"

    printf '  %bWill remove:%b\n' "${BOLD}" "${RESET}"
    printf "    %s\n" "${APP_DIR}/  (application files and Python venv)"
    printf "    %s\n" "${SYSTEMD_UNIT}"
    printf "    %s\n" "${NGINX_AVAILABLE}"
    printf "    %s\n" "${NGINX_ENABLED}"
    printf "    %s\n" "${NGINX_RATELIMIT}"
    printf "    %s\n" "${SUDOERS_FILE}"
    printf "    %s\n" "${DDNS_BIN}"
    printf "    %s\n" "${CCC_UNLOCK_BIN}"
    printf "    %s\n" "conduit-cc crontab  (DDNS cron job)"
    printf "    %s\n" "${CONDUIT_BIN_DIR}/  (Conduit binary — if present)"
    printf "    %s\n" "/etc/systemd/system/conduit.service  (if present)"
    printf "\n"

    if "${PURGE}"; then
        # Correction C: name origin.key and SESSION_SECRET explicitly so the
        # operator understands exactly what is unrecoverable.
        printf '  %b%b--purge: will also permanently delete:%b\n' "${RED}" "${BOLD}" "${RESET}"
        printf "    %-52s %s\n" \
            "${CONF_DIR}/tls/origin.key" "<-- TLS private key  (UNRECOVERABLE)"
        printf "    %-52s %s\n" \
            "${CONF_DIR}/.env" "<-- CF_API_TOKEN, SESSION_SECRET  (UNRECOVERABLE)"
        printf "    %-52s\n" "${CONF_DIR}/ccc.db         (runtime database)"
        printf "    %-52s\n" "${CONF_DIR}/tls/origin.pem"
        printf "    %-52s\n" "${CONF_DIR}/config.json"
        printf "    %-52s\n" "${LOG_DIR}/  (all DDNS and application logs)"
        printf "    %-52s\n" "conduit-cc system user and group"
        printf "    %-52s %s\n" \
            "${CONDUIT_DATA_DIR}/" "<-- conduit_key.json  (UNRECOVERABLE — resets broker reputation)"
        printf "    %-52s\n" "conduit system user"
        printf "\n"
        printf '  %bUFW rules (22/80/443) are NOT removed.%b\n' "${BOLD}" "${RESET}"
        printf "\n"
        printf '  %bThis data CANNOT be recovered.%b\n' "${RED}" "${RESET}"
        printf "\n"
        local _confirm
        printf '  Type '\''%byes%b'\'' to confirm permanent deletion: ' "${BOLD}" "${RESET}"
        read -r _confirm
        [[ "${_confirm}" == "yes" ]] || die "Purge cancelled. No changes made."
    else
        printf '  %bWill preserve (use --purge to remove):%b\n' "${BOLD}" "${RESET}"
        printf "    %s\n" "${CONF_DIR}/  (TLS cert and key, .env, config.json, ccc.db)"
        printf "    %s\n" "${LOG_DIR}/  (DDNS and application logs)"
        printf "    %s\n" "conduit-cc system user"
        printf "    %s\n" "${CONDUIT_DATA_DIR}/  (conduit_key.json — Conduit node identity)"
        printf "\n"
        printf '  %bUFW rules (22/80/443) are NOT removed.%b\n' "${BOLD}" "${RESET}"
        printf "\n"
        local _confirm
        read -r -p "  Continue? [y/N]: " _confirm
        [[ "${_confirm,,}" == "y" ]] || die "Uninstall cancelled. No changes made."
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 1 — Stop and disable service                                         #
# --------------------------------------------------------------------------- #

phase1_service() {
    section "Phase 1 — Stopping and removing service"

    step "1a — Stopping ${SERVICE_NAME}"
    # Safe if service is not running, not enabled, or unit file is missing.
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    info "Service stopped (or was not running)"

    step "1b — Disabling ${SERVICE_NAME}"
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    info "Service disabled (or was not enabled)"

    step "1c — Removing systemd unit"
    rm -f "${SYSTEMD_UNIT}"
    info "${SYSTEMD_UNIT} removed"

    step "1d — Reloading systemd daemon"
    systemctl daemon-reload
    info "systemd daemon reloaded"
}

# --------------------------------------------------------------------------- #
#  Phase 2 — nginx configuration                                              #
#                                                                             #
#  Removal order matters:                                                     #
#    1. Remove sites-enabled symlink (deactivates site in nginx memory).      #
#    2. Remove sites-available config (removes the limit_req reference).      #
#    3. Remove conf.d ratelimit zone (now safe — no site references it).      #
#    4. nginx -t, then reload.                                                #
#                                                                             #
#  Reversing steps 2 and 3 would leave a site file that references the       #
#  now-deleted login_limit zone, causing nginx -t to fail (CD-1 in reverse). #
#                                                                             #
#  Correction D: nginx -t failure is a warning, not fatal.  All CCC files    #
#  are already removed at this point.                                         #
#  Correction E: guard for nginx not installed on the system.                #
# --------------------------------------------------------------------------- #

phase2_nginx() {
    section "Phase 2 — Removing nginx configuration"

    step "2a — Removing nginx sites-enabled symlink"
    rm -f "${NGINX_ENABLED}"
    info "${NGINX_ENABLED} removed"

    step "2b — Removing nginx site config"
    rm -f "${NGINX_AVAILABLE}"
    info "${NGINX_AVAILABLE} removed"

    step "2c — Removing nginx rate-limiting zone"
    rm -f "${NGINX_RATELIMIT}"
    info "${NGINX_RATELIMIT} removed"

    # Correction E: skip nginx operations entirely if nginx is not installed.
    if ! command -v nginx &>/dev/null; then
        info "nginx not found — skipping config test and reload"
        return 0
    fi

    step "2d — Testing nginx configuration"
    # Correction D: nginx -t failure after CCC removal is non-fatal.  An
    # unrelated nginx config issue should not abort the uninstaller; the CCC
    # files are already gone.  Print a remediation message and continue.
    if nginx -t 2>/dev/null; then
        info "nginx configuration valid"
        step "2e — Reloading nginx"
        if systemctl is-active --quiet nginx 2>/dev/null; then
            systemctl reload nginx
            info "nginx reloaded"
        else
            info "nginx is not active — skipping reload"
        fi
    else
        warn "nginx -t failed after CCC removal (unrelated nginx config issue)."
        warn "All CCC nginx files have already been removed."
        warn "Resolve the nginx config issue, then reload manually:"
        warn "  nginx -t && systemctl reload nginx"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 3 — DDNS cron job and script                                        #
#                                                                             #
#  install.sh installs the cron job via 'crontab -u conduit-cc', stored at  #
#  /var/spool/cron/crontabs/conduit-cc — not /etc/cron.d/conduit-cc.        #
#                                                                             #
#  Correction F: 'crontab -u conduit-cc -r' fails if the user does not      #
#  exist.  Guard with 'id' check for idempotency on second run or partial    #
#  install state.                                                             #
# --------------------------------------------------------------------------- #

phase3_ddns() {
    section "Phase 3 — Removing DDNS cron job and script"

    step "3a — Removing conduit-cc user crontab"
    # Correction F: guard for user not existing.
    if id "${APP_USER}" &>/dev/null; then
        crontab -u "${APP_USER}" -r 2>/dev/null || true
        info "conduit-cc crontab removed"
    else
        info "conduit-cc user does not exist — crontab removal skipped"
    fi

    # Defensive removal of /etc/cron.d/conduit-cc.
    # install.sh does not create this file; update.sh or a future version may
    # switch to the cron.d mechanism.  rm -f is a no-op if the file is absent.
    step "3b — Removing ${CRON_D_FILE} (defensive guard)"
    rm -f "${CRON_D_FILE}"
    info "${CRON_D_FILE} removed (or did not exist)"

    step "3c — Removing DDNS script"
    rm -f "${DDNS_BIN}"
    info "${DDNS_BIN} removed"
}

# --------------------------------------------------------------------------- #
#  Phase 4 — sudoers rule and utilities                                      #
# --------------------------------------------------------------------------- #

phase4_utilities() {
    section "Phase 4 — Removing sudoers rule and utilities"

    step "4a — Removing sudoers rule"
    rm -f "${SUDOERS_FILE}"
    info "${SUDOERS_FILE} removed"

    step "4b — Removing ccc-unlock symlink"
    rm -f "${CCC_UNLOCK_BIN}"
    info "${CCC_UNLOCK_BIN} removed"
}

# --------------------------------------------------------------------------- #
#  Phase 4b — Conduit removal (Issue #45)                                   #
#                                                                             #
#  Standard mode (no --purge):                                               #
#    - Stops and disables conduit.service                                    #
#    - Removes /opt/conduit/ (binary + version file)                         #
#    - Removes /etc/systemd/system/conduit.service                           #
#    - PRESERVES /var/lib/conduit/ (contains conduit_key.json — node identity)#
#    - PRESERVES conduit system user (owns /var/lib/conduit/)                #
#                                                                             #
#  Purge mode (--purge):                                                      #
#    - Additionally removes /var/lib/conduit/ (conduit_key.json UNRECOVERABLE)#
#    - Removes conduit system user                                            #
#                                                                             #
#  conduit_key.json: losing this file resets Psiphon broker reputation to    #
#  zero.  The default for keypair handling is PRESERVE (no --purge required).#
# --------------------------------------------------------------------------- #

phase4b_conduit_remove() {
    section "Phase 4b — Removing Conduit"

    # Check if Conduit is installed at all; skip gracefully if not.
    if [[ ! -f "${CONDUIT_BIN_DIR}/conduit" ]] \
            && [[ ! -f "/etc/systemd/system/conduit.service" ]]; then
        info "Conduit not found (${CONDUIT_BIN_DIR}/conduit absent) — skipping"
        return 0
    fi

    # ---- 4b-a  Stop and disable conduit service ---------------------------- #
    step "4b-a — Stopping conduit service"
    systemctl stop conduit 2>/dev/null || true
    info "conduit stopped (or was not running)"

    step "4b-b — Disabling conduit service"
    systemctl disable conduit 2>/dev/null || true
    info "conduit disabled (or was not enabled)"

    step "4b-c — Removing conduit.service unit"
    rm -f /etc/systemd/system/conduit.service
    systemctl daemon-reload
    info "/etc/systemd/system/conduit.service removed"

    # ---- 4b-b  Remove binary directory ------------------------------------- #
    # /opt/conduit/ contains only the binary and version file — no user data.
    step "4b-d — Removing Conduit binary (${CONDUIT_BIN_DIR}/)"
    rm -rf "${CONDUIT_BIN_DIR}"
    info "${CONDUIT_BIN_DIR}/ removed"

    # ---- 4b-c  Keypair handling --------------------------------------------- #
    if "${PURGE}"; then
        # --purge: operator confirmed deletion of conduit_key.json above.
        step "4b-e — Removing Conduit data directory (--purge)"
        warn "Removing ${CONDUIT_DATA_DIR}/ — conduit_key.json will be PERMANENTLY DELETED."
        rm -rf "${CONDUIT_DATA_DIR}"
        info "${CONDUIT_DATA_DIR}/ removed"

        # Remove conduit system user (safe — data dir is gone)
        step "4b-f — Removing conduit system user"
        userdel "${CONDUIT_USER}" 2>/dev/null || true
        if getent group "${CONDUIT_USER}" &>/dev/null; then
            groupdel "${CONDUIT_USER}" 2>/dev/null || true
        fi
        info "conduit user/group removed"
    else
        # Standard mode: preserve keypair and user.
        printf "\n"
        printf '  %b%bCONDUIT NODE IDENTITY%b\n' "${YELLOW}" "${BOLD}" "${RESET}"
        printf '  %s/data/conduit_key.json has been PRESERVED.\n' "${CONDUIT_DATA_DIR}"
        printf "\n"
        printf "  This file is your Psiphon broker identity keypair.\n"
        printf "  Losing it resets your node's broker reputation to zero.\n"
        printf "\n"
        printf "  Back it up now if you plan to migrate to a new device:\n"
        printf "    sudo cp %s/data/conduit_key.json ~/conduit_key.json.bak\n" \
            "${CONDUIT_DATA_DIR}"
        printf "\n"
        printf "  To remove the keypair permanently (cannot be undone):\n"
        printf "    sudo bash uninstall.sh --purge\n"
        printf "\n"
        info "${CONDUIT_DATA_DIR}/ preserved (contains conduit_key.json)"
        info "conduit system user preserved (owns ${CONDUIT_DATA_DIR}/)"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 5 — Application directory                                           #
#                                                                             #
#  Correction A: track whether rm -rf succeeded.  phase6_purge uses          #
#  _APP_DIR_REMOVED to decide whether userdel is safe.                       #
# --------------------------------------------------------------------------- #

phase5_appdir() {
    section "Phase 5 — Removing application directory"

    step "5a — Removing ${APP_DIR}"
    if rm -rf "${APP_DIR}"; then
        _APP_DIR_REMOVED=true
        info "${APP_DIR} removed"
    else
        _APP_DIR_REMOVED=false
        warn "Failed to fully remove ${APP_DIR} — some files may remain."
        warn "conduit-cc user will NOT be deleted to prevent orphaned UIDs."
        warn "Remove ${APP_DIR} manually, then run: userdel ${APP_USER}"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 6 — Purge: configuration, logs, and user (--purge only)            #
#                                                                             #
#  Correction A: userdel is skipped if any directory owned by conduit-cc     #
#  could not be fully removed.  Orphaned file ownership on a production      #
#  server is worse than retaining a locked system account.                   #
# --------------------------------------------------------------------------- #

phase6_purge() {
    section "Phase 6 — Purge: removing configuration, logs, and user"

    # Track whether each conduit-cc-owned directory was cleanly removed.
    local _conf_removed=false
    local _log_removed=false

    step "6a — Removing configuration directory ${CONF_DIR}"
    if rm -rf "${CONF_DIR}"; then
        _conf_removed=true
        info "${CONF_DIR} removed"
    else
        warn "${CONF_DIR} could not be fully removed."
    fi

    step "6b — Removing log directory ${LOG_DIR}"
    if rm -rf "${LOG_DIR}"; then
        _log_removed=true
        info "${LOG_DIR} removed"
    else
        warn "${LOG_DIR} could not be fully removed."
    fi

    # Correction A: delete the user only when all directories owned by
    # conduit-cc have been successfully removed.
    step "6c — Removing conduit-cc user and group"
    if ! "${_APP_DIR_REMOVED}"; then
        warn "Skipping user deletion: ${APP_DIR} was not fully removed."
        warn "Remove it manually, then run: userdel ${APP_USER}"
        return 0
    fi
    if ! "${_conf_removed}" || ! "${_log_removed}"; then
        warn "Skipping user deletion: one or more directories were not fully removed."
        warn "Remove ${CONF_DIR} and ${LOG_DIR} manually, then run: userdel ${APP_USER}"
        return 0
    fi

    # All owned directories are gone — safe to delete the user.
    userdel "${APP_USER}" 2>/dev/null || true
    info "conduit-cc user removed (or did not exist)"

    # groupdel only if a separate primary group exists.
    # useradd --system does not always create a distinct group.
    if getent group "${APP_USER}" &>/dev/null; then
        groupdel "${APP_USER}" 2>/dev/null || true
        info "conduit-cc group removed"
    else
        info "conduit-cc group not present (or already removed)"
    fi
}

# --------------------------------------------------------------------------- #
#  Phase 7 — Summary                                                         #
# --------------------------------------------------------------------------- #

phase7_summary() {
    section "Uninstall complete"
    printf "\n"

    if "${PURGE}"; then
        printf "  All Conduit Control Center files, configuration, and data have\n"
        printf "  been removed (including conduit_key.json).\n"
    else
        printf "  Conduit Control Center has been uninstalled.\n"
        printf "\n"
        printf '  %bPreserved (use --purge to remove):%b\n' "${BOLD}" "${RESET}"
        printf "    %s\n" "${CONF_DIR}/  TLS cert and key, .env, config.json, ccc.db"
        printf "    %s\n" "${LOG_DIR}/  DDNS and application logs"
        printf "    %s\n" "conduit-cc system user"
        printf "    %s\n" "${CONDUIT_DATA_DIR}/  Conduit data directory (conduit_key.json)"
        printf "    %s\n" "conduit system user"
        printf "\n"
        printf "  If you reinstall, install.sh will reuse the preserved .env\n"
        printf "  and TLS files automatically.  The Conduit node identity\n"
        printf "  (conduit_key.json) will also be reused automatically.\n"
        printf "\n"
        printf "  If reinstall fails due to a corrupt .env or ccc.db, run:\n"
        printf "    sudo bash uninstall.sh --purge\n"
    fi

    printf "\n"
    printf '  %bUFW rules were NOT modified.%b\n' "${BOLD}" "${RESET}"
    printf "  Review with:  sudo ufw status\n"
    printf "  To close ports 80 and 443 if no longer needed:\n"
    printf "    sudo ufw delete allow 80/tcp\n"
    printf "    sudo ufw delete allow 443/tcp\n"
    printf "\n"
    printf '  %bnginx default site:%b not restored automatically.\n' "${BOLD}" "${RESET}"
    printf "  If install.sh removed it, restore with:\n"
    printf "    sudo ln -sf /etc/nginx/sites-available/default \\\\\n"
    printf "                /etc/nginx/sites-enabled/default\n"
    printf "    sudo systemctl reload nginx\n"
    printf "\n"
}

# --------------------------------------------------------------------------- #
#  Entry point                                                                 #
# --------------------------------------------------------------------------- #

phase0_confirm
phase1_service
phase2_nginx
phase3_ddns
phase4_utilities
phase4b_conduit_remove
phase5_appdir

if "${PURGE}"; then
    phase6_purge
fi

phase7_summary
