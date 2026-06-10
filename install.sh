#!/usr/bin/env bash
# install.sh — Conduit Control Center installer
# ==============================================
# Installs the CCC dashboard on Ubuntu 22.04 ARM64 behind a Cloudflare proxy.
#
# Usage:   sudo bash install.sh
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

# --------------------------------------------------------------------------- #
#  Constants                                                                   #
# --------------------------------------------------------------------------- #

readonly APP_USER="conduit-cc"
readonly APP_DIR="/opt/conduit-cc"
readonly CONF_DIR="/etc/conduit-cc"
readonly TLS_DIR="/etc/conduit-cc/tls"
readonly LOG_DIR="/var/log/conduit-cc"
readonly SERVICE_NAME="conduit-cc"
readonly NGINX_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}"
readonly NGINX_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}"
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
ADMIN_USERNAME=""
ADMIN_PASSWORD=""   # cleared immediately after hashing in Phase 2g

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

# Assign a value to a named variable without nameref.
# Usage: assign VARNAME "value"
assign() { printf -v "$1" '%s' "$2"; }

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
    os_id="$(. /etc/os-release && echo "${ID}")"
    os_version="$(. /etc/os-release && echo "${VERSION_ID}")"
    os_arch="$(uname -m)"

    [[ "${os_id}" == "ubuntu" ]] || die \
        "Unsupported OS: ${os_id}. Ubuntu 22.04 is required."
    [[ "${os_version}" == "22.04" ]] || die \
        "Unsupported Ubuntu version: ${os_version}." \
        "Ubuntu 22.04 LTS (Jammy) is required."
    [[ "${os_arch}" == "aarch64" ]] || die \
        "Unsupported architecture: ${os_arch}." \
        "ARM64 (aarch64) is required — this installer targets Raspberry Pi 4."

    info "OS: Ubuntu ${os_version} ${os_arch}"

    # ---- 1b  Pre-install checklist confirmation ----------------------------- #
    step "1b — Pre-install checklist"
    printf "\n"
    printf "  Before continuing, confirm you have completed all steps in:\n"
    printf "  ${BOLD}docs/pre-install.md${RESET}\n\n"
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

    # ---- 1e  Zone name — validates token AND Zone:Zone:Read permission ------ #
    step "1e — Cloudflare zone name"
    prompt CF_ZONE_NAME "Zone name (e.g. example.com)"
    [[ -n "${CF_ZONE_NAME}" ]] || die "Zone name cannot be empty."

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
        printf "  Alternatively, place the conduit binary at ${SCRIPT_DIR}/conduit\n"
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
    printf "  ${BOLD}Installation summary${RESET}\n"
    printf "  %-24s %s\n" "Zone:"        "${CF_ZONE_NAME}"
    printf "  %-24s %s\n" "Hostname:"    "${CF_RECORD_NAME}"
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

    # ---- 2b  Application files --------------------------------------------- #
    step "2b — Copying application files to ${APP_DIR}"
    mkdir -p "${APP_DIR}"
    rsync -a \
        --exclude 'venv/' \
        --exclude 'ccc.db' \
        --exclude '__pycache__/' \
        --exclude '.git/' \
        --exclude '.env' \
        "${SCRIPT_DIR}/" "${APP_DIR}/"
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    info "Application files copied"

    # ---- 2c  Python virtual environment ------------------------------------ #
    step "2c — Setting up Python virtual environment"
    if [[ ! -f "${APP_DIR}/venv/bin/python3" ]]; then
        python3 -m venv "${APP_DIR}/venv"
        info "venv created at ${APP_DIR}/venv"
    else
        info "venv already exists — skipping creation"
    fi
    "${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
    "${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
    info "Python dependencies installed"

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
    if [[ -f "${CONF_DIR}/.env" ]]; then
        # Idempotent reinstall: preserve SESSION_SECRET, CF_API_TOKEN, and
        # other runtime values set after first install.  Only ADMIN_USERNAME
        # is updated here; ADMIN_PASSWORD_HASH is updated in Phase 2g.
        info ".env already exists — preserving (SESSION_SECRET and credentials kept)"
        if grep -q "^ADMIN_USERNAME=" "${CONF_DIR}/.env"; then
            sed -i "s|^ADMIN_USERNAME=.*|ADMIN_USERNAME=${ADMIN_USERNAME}|" \
                "${CONF_DIR}/.env"
        else
            printf 'ADMIN_USERNAME=%s\n' "${ADMIN_USERNAME}" >> "${CONF_DIR}/.env"
        fi
    else
        local _session_secret
        _session_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

        # Write all variables.  ADMIN_PASSWORD_HASH is left empty and written
        # by Phase 2g after the venv is ready.  CF_API_TOKEN is written here
        # because scripts/cloudflare-ddns.sh reads it from this file.
        cat > "${CONF_DIR}/.env" <<EOF
# Conduit Control Center — runtime configuration
# Generated by install.sh — do not edit unless instructed.
# See .env.example for documentation of each variable.

ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD_HASH=

SESSION_SECRET=${_session_secret}

CF_API_TOKEN=${CF_API_TOKEN}
CF_ZONE_NAME=${CF_ZONE_NAME}
CF_RECORD_NAME=${CF_RECORD_NAME}

TLS_CERT_PATH=${TLS_CERT_PATH}
TLS_KEY_PATH=${TLS_KEY_PATH}
EOF
        chown "${APP_USER}:${APP_USER}" "${CONF_DIR}/.env"
        chmod 600 "${CONF_DIR}/.env"
        info ".env written (600, ${APP_USER})"
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
    if grep -q "^ADMIN_PASSWORD_HASH=" "${CONF_DIR}/.env"; then
        sed -i "s|^ADMIN_PASSWORD_HASH=.*|ADMIN_PASSWORD_HASH='${_pw_hash}'|" \
            "${CONF_DIR}/.env"
    else
        printf "ADMIN_PASSWORD_HASH='%s'\n" "${_pw_hash}" >> "${CONF_DIR}/.env"
    fi
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

    # Substitute CF_RECORD_NAME placeholder in the nginx template.
    sed "s|<CF_RECORD_NAME>|${CF_RECORD_NAME}|g" \
        "${APP_DIR}/deployment/conduit-cc.nginx" > "${NGINX_AVAILABLE}"
    info "nginx config written to ${NGINX_AVAILABLE}"

    # Create sites-enabled symlink (ln -sf is idempotent).
    ln -sf "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
    info "nginx symlink: ${NGINX_ENABLED}"

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

    nginx -t 2>/dev/null || {
        nginx -t   # re-run without redirect so user sees the error
        die "nginx configuration test failed." \
            "Check ${NGINX_AVAILABLE} for syntax errors."
    }
    info "nginx config valid"

    # Reload if nginx is already running; otherwise it will start via systemd.
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
        info "nginx reloaded"
    fi

    # ---- 2j  UFW firewall -------------------------------------------------- #
    step "2j — Configuring UFW firewall"
    ufw allow 22/tcp  comment 'SSH'     &>/dev/null
    ufw allow 80/tcp  comment 'HTTP'    &>/dev/null
    ufw allow 443/tcp comment 'HTTPS'   &>/dev/null
    ufw --force enable &>/dev/null
    info "UFW: 22/80/443 open, firewall enabled"

    # ---- 2k  Systemd service ----------------------------------------------- #
    step "2k — Installing systemd service"
    cp "${APP_DIR}/deployment/conduit-cc.service" "${SYSTEMD_UNIT}"
    systemctl daemon-reload
    info "${SYSTEMD_UNIT} installed"

    # ---- 2l  sudoers rule for Conduit controls ----------------------------- #
    # adapter.py calls "sudo systemctl start|stop|restart conduit".
    # NoNewPrivileges is omitted from conduit-cc.service (see service file
    # header) to allow sudo's setuid bit to work.
    step "2l — Creating sudoers rule"
    cat > "${SUDOERS_FILE}" <<EOF
# Conduit Control Center — allow ${APP_USER} to control the Conduit service
# Generated by install.sh — do not edit manually
${APP_USER} ALL=(root) NOPASSWD: /bin/systemctl start conduit
${APP_USER} ALL=(root) NOPASSWD: /bin/systemctl stop conduit
${APP_USER} ALL=(root) NOPASSWD: /bin/systemctl restart conduit
EOF
    chmod 440 "${SUDOERS_FILE}"
    visudo -cf "${SUDOERS_FILE}" || die \
        "sudoers syntax check failed: ${SUDOERS_FILE}" \
        "Remove ${SUDOERS_FILE} and re-run the installer."
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
        local _asset="conduit-linux-arm64"

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
    cp "${APP_DIR}/deployment/conduit.service" "${_conduit_unit}"
    chown root:root "${_conduit_unit}"
    chmod 644 "${_conduit_unit}"
    systemctl daemon-reload
    info "${_conduit_unit} installed"

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
    # Conduit binds UDP ports for inproxy traffic.  The exact ports are not
    # documented in Psiphon source and may vary by version or configuration.
    # We cannot add UFW rules without knowing the ports.
    step "2x-h — Conduit firewall reminder"
    warn "ACTION REQUIRED: Conduit needs UFW rules for inproxy UDP traffic."
    warn "After install, run:  ss -ulnp | grep conduit"
    warn "Then for each UDP port listed, run:  ufw allow <port>/udp comment 'Conduit'"
    warn "(See docs/pre-install.md for details.)"

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
    printf "  ${GREEN}OK${RESET} Conduit Control Center is installed and running.\n"
    printf "\n"
    printf "  ${BOLD}Dashboard URL:${RESET}  https://${CF_RECORD_NAME}/\n"
    printf "  ${BOLD}Admin user:${RESET}     ${ADMIN_USERNAME}\n"
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
    printf "  ${YELLOW}ACTION REQUIRED — Conduit firewall:${RESET}\n"
    printf "    Conduit binds UDP port(s) for inproxy traffic.\n"
    printf "    Discover them:  ss -ulnp | grep conduit\n"
    printf "    Then add rules: ufw allow <port>/udp comment 'Conduit'\n"
    printf "\n"
    printf "  DDNS log:\n"
    printf "    tail -f ${LOG_DIR}/ddns.log\n"
    printf "\n"
    printf "  If your admin account is locked out:\n"
    printf "    sudo ccc-unlock\n"
    printf "\n"
    printf "  ${BOLD}Next steps:${RESET}\n"
    printf "    1. Open https://${CF_RECORD_NAME}/ and log in.\n"
    printf "    2. Add UFW rules for Conduit UDP port(s):\n"
    printf "         ss -ulnp | grep conduit\n"
    printf "         ufw allow <port>/udp comment 'Conduit'\n"
    printf "    3. Verify Conduit node status on the dashboard.\n"
    printf "    4. Verify Cloudflare SSL/TLS is set to Full (strict):\n"
    printf "       https://dash.cloudflare.com -> SSL/TLS -> Overview\n"
    printf "\n"
    printf "  ${CYAN}Docs:${RESET} docs/pre-install.md · docs/tls-setup.md\n"
    printf "\n"
}

# --------------------------------------------------------------------------- #
#  Entry point                                                                 #
# --------------------------------------------------------------------------- #

phase1_validate
phase2_install
phase3_summary
