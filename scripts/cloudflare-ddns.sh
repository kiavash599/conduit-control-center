#!/usr/bin/env bash
# =============================================================================
# scripts/cloudflare-ddns.sh
# =============================================================================
# Cloudflare DDNS updater for Conduit Control Center.
#
# Script B behaviour: reads the current proxy (orange/grey cloud) status from
# the Cloudflare API and preserves it on every update.  The user controls the
# proxy toggle in the Cloudflare dashboard; this script never overrides it.
#
# Algorithm
# ---------
#   1. Source CF_API_TOKEN, CF_ZONE_NAME, CF_RECORD_NAME from /etc/conduit-cc/.env
#   2. Get current public IP from https://api.ipify.org
#   3. Get Zone ID      via GET /zones?name={CF_ZONE_NAME}
#   4. Get DNS A record via GET /zones/{zone_id}/dns_records?type=A&name={CF_RECORD_NAME}
#   5. If public IP == record IP  -> log result:"no_change" and exit 0
#   6. If public IP != record IP  -> PUT update preserving proxied status
#   7. Log result:"updated" or result:"error" to /var/log/conduit-cc/ddns.log
#
# Log format (one JSON line per run):
#   {"timestamp":"2026-06-07T12:00:00Z","record_name":"conduit.example.com",
#    "ip":"1.2.3.4","result":"updated","message":"A record updated from 1.2.3.3 to 1.2.3.4"}
#   {"timestamp":"...","record_name":"...","ip":"1.2.3.4","result":"no_change","message":"IP unchanged"}
#   {"timestamp":"...","record_name":"...","ip":null,"result":"error","message":"..."}
#
# The record_name field is included in every entry so that Issue #42 / #43 can
# support multiple DNS records in the future without changing the log format.
#
# Security
# --------
#   CF_API_TOKEN is sourced from .env into the environment.  It is passed to
#   curl via an Authorization header -- never as a query parameter, command
#   argument visible in process listings, or log message.
#
# Dependencies: bash 4+, curl, jq
#
# Install (handled by install.sh, Issue #9):
#   Cron: */5 * * * * conduit-cc /opt/conduit-cc/scripts/cloudflare-ddns.sh
#   Log rotation: /var/log/conduit-cc/ddns.log -- weekly, 4 rotations, compress
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

readonly ENV_FILE="/etc/conduit-cc/.env"
readonly LOG_FILE="/var/log/conduit-cc/ddns.log"
readonly IP_PROVIDER_URL="https://api.ipify.org"
readonly CF_API_BASE="https://api.cloudflare.com/client/v4"
readonly CURL_TIMEOUT=10

# ---------------------------------------------------------------------------
# _ensure_log_dir
# Creates the log directory if it does not exist.
# ---------------------------------------------------------------------------

_ensure_log_dir() {
    local log_dir
    log_dir="$(dirname "$LOG_FILE")"
    if [[ ! -d "$log_dir" ]]; then
        mkdir -p "$log_dir"
    fi
}

# ---------------------------------------------------------------------------
# _log_plain <result> <message>
# Fallback logger used only before jq availability is confirmed.
# Writes a minimal JSON line via printf.  Both arguments must be
# hardcoded strings -- no API-derived or user-supplied data.
# ---------------------------------------------------------------------------

_log_plain() {
    local result="$1"
    local message="$2"
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    _ensure_log_dir
    printf '{"timestamp":"%s","record_name":"unknown","ip":null,"result":"%s","message":"%s"}\n' \
        "$timestamp" "$result" "$message" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Dependency check -- before log_entry() which requires jq
# ---------------------------------------------------------------------------

if ! command -v curl &>/dev/null; then
    _log_plain "error" "curl is not installed; install via: sudo apt-get install curl"
    exit 1
fi

if ! command -v jq &>/dev/null; then
    _log_plain "error" "jq is not installed; install via: sudo apt-get install jq"
    exit 1
fi

# ---------------------------------------------------------------------------
# log_entry <ip_or_null> <result> <message>
# Appends one JSON line to LOG_FILE.  Uses jq for safe encoding so that
# API-derived text in <message> cannot produce malformed JSON.
#
# ip_or_null : an IPv4 address string, or the literal word "null"
# result     : "updated" | "no_change" | "error"
# message    : human-readable description
#
# _record_name_safe is set after sourcing .env.  The default "unknown" is
# used in the narrow window before the env file is loaded.
# ---------------------------------------------------------------------------

_record_name_safe="unknown"

log_entry() {
    local ip_val="$1"
    local result="$2"
    local message="$3"
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    _ensure_log_dir

    if [[ "$ip_val" == "null" ]]; then
        jq -cn \
            --arg ts  "$timestamp" \
            --arg rn  "$_record_name_safe" \
            --arg res "$result" \
            --arg msg "$message" \
            '{"timestamp":$ts,"record_name":$rn,"ip":null,"result":$res,"message":$msg}' \
            >> "$LOG_FILE"
    else
        jq -cn \
            --arg ts  "$timestamp" \
            --arg rn  "$_record_name_safe" \
            --arg ip  "$ip_val" \
            --arg res "$result" \
            --arg msg "$message" \
            '{"timestamp":$ts,"record_name":$rn,"ip":$ip,"result":$res,"message":$msg}' \
            >> "$LOG_FILE"
    fi
}

# ---------------------------------------------------------------------------
# Source /etc/conduit-cc/.env
# ---------------------------------------------------------------------------

if [[ ! -f "$ENV_FILE" ]]; then
    log_entry "null" "error" "env file not found: ${ENV_FILE}"
    exit 1
fi

# Temporarily disable nounset (-u) while sourcing .env.
# The bcrypt hash written to ADMIN_PASSWORD_HASH has the form $2b$12$...;
# without this guard, bash -u would treat $2 as an unbound positional
# parameter and abort the script before any JSON can be logged.
# nounset is re-enabled immediately after sourcing so the rest of the
# script still benefits from it.
set +u
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
set -u

# Re-declare sourced variables with safe defaults.
# Explicit assignment satisfies shellcheck SC2154 (var referenced but not
# assigned in this file).
# shellcheck disable=SC2154
CF_API_TOKEN="${CF_API_TOKEN:-}"
# shellcheck disable=SC2154
CF_ZONE_NAME="${CF_ZONE_NAME:-}"
# shellcheck disable=SC2154
CF_RECORD_NAME="${CF_RECORD_NAME:-}"

# Update the record name used by log_entry now that CF_RECORD_NAME is loaded.
_record_name_safe="${CF_RECORD_NAME:-unknown}"

# ---------------------------------------------------------------------------
# Validate required variables.
# CF_API_TOKEN is validated last so it is never echoed in an error message.
# ---------------------------------------------------------------------------

if [[ -z "$CF_ZONE_NAME" ]]; then
    log_entry "null" "error" "CF_ZONE_NAME is not set in ${ENV_FILE}"
    exit 1
fi

if [[ -z "$CF_RECORD_NAME" ]]; then
    log_entry "null" "error" "CF_RECORD_NAME is not set in ${ENV_FILE}"
    exit 1
fi

if [[ -z "$CF_API_TOKEN" ]]; then
    log_entry "null" "error" "CF_API_TOKEN is not set in ${ENV_FILE}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1 -- Get current public IP
# ---------------------------------------------------------------------------

current_ip=""
current_ip="$(curl -sf --max-time "$CURL_TIMEOUT" "$IP_PROVIDER_URL")" || {
    log_entry "null" "error" "failed to retrieve public IP from ${IP_PROVIDER_URL}"
    exit 1
}

# Basic IPv4 validation -- guards against empty strings or HTML error pages.
if ! [[ "$current_ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    log_entry "null" "error" "IP provider returned an unexpected value (not a valid IPv4 address)"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 2 -- Get Cloudflare Zone ID
# CF_API_TOKEN is passed via header; it never appears in a log message.
# ---------------------------------------------------------------------------

zone_response=""
zone_response="$(curl -sf --max-time "$CURL_TIMEOUT" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    "${CF_API_BASE}/zones?name=${CF_ZONE_NAME}")" || {
    log_entry "null" "error" "Cloudflare API unavailable (zones lookup for ${CF_ZONE_NAME})"
    exit 1
}

zone_id=""
zone_id="$(printf '%s' "$zone_response" | jq -r '.result[0].id // empty')"

if [[ -z "$zone_id" ]]; then
    log_entry "null" "error" "zone '${CF_ZONE_NAME}' not found in this Cloudflare account"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3 -- Get current DNS A record (ID, IP, proxy status)
# ---------------------------------------------------------------------------

record_response=""
record_response="$(curl -sf --max-time "$CURL_TIMEOUT" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    "${CF_API_BASE}/zones/${zone_id}/dns_records?type=A&name=${CF_RECORD_NAME}")" || {
    log_entry "null" "error" "Cloudflare API unavailable (DNS record lookup for ${CF_RECORD_NAME})"
    exit 1
}

record_id=""
record_id="$(printf '%s' "$record_response" | jq -r '.result[0].id // empty')"

if [[ -z "$record_id" ]]; then
    log_entry "null" "error" "DNS A record '${CF_RECORD_NAME}' not found in zone '${CF_ZONE_NAME}'"
    exit 1
fi

record_ip=""
record_ip="$(printf '%s' "$record_response" | jq -r '.result[0].content // empty')"

# Read proxy status.  Default to false if absent (guards against unexpected
# API shapes; should not occur for a normal Cloudflare A record).
record_proxied=""
record_proxied="$(printf '%s' "$record_response" | jq -r '.result[0].proxied // false')"

# ---------------------------------------------------------------------------
# Step 4 -- Compare IPs; exit early if unchanged (no API write call)
# ---------------------------------------------------------------------------

if [[ "$current_ip" == "$record_ip" ]]; then
    log_entry "$current_ip" "no_change" "IP unchanged"
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 5 -- Update the A record, preserving proxy status
#
# proxied is read from the existing record and passed back unchanged.
# The Cloudflare proxy toggle belongs to the user; this script never alters it.
# ttl=1 means "Auto" in Cloudflare terms (valid for proxied and direct).
# ---------------------------------------------------------------------------

update_payload=""
update_payload="$(jq -cn \
    --arg  name    "$CF_RECORD_NAME" \
    --arg  content "$current_ip" \
    --arg  proxied "$record_proxied" \
    '{"type":"A","name":$name,"content":$content,"proxied":($proxied == "true"),"ttl":1}')"

update_response=""
update_response="$(curl -sf --max-time "$CURL_TIMEOUT" \
    -X PUT \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$update_payload" \
    "${CF_API_BASE}/zones/${zone_id}/dns_records/${record_id}")" || {
    log_entry "null" "error" "Cloudflare API unavailable (DNS record update for ${CF_RECORD_NAME})"
    exit 1
}

update_success=""
update_success="$(printf '%s' "$update_response" | jq -r '.success')"

if [[ "$update_success" == "true" ]]; then
    log_entry "$current_ip" "updated" \
        "A record updated from ${record_ip} to ${current_ip}"
    exit 0
fi

# Update failed: extract error message from API response.
# If jq cannot parse the response, fall back to a safe default so that
# log_entry is always called before exit.
update_error="(could not parse API error response)"
_parsed_error=""
if _parsed_error="$(printf '%s' "$update_response" \
        | jq -r '[ .errors[]?.message // "unknown error" ] | join("; ")' \
        2>/dev/null)"; then
    update_error="$_parsed_error"
fi
log_entry "null" "error" "Cloudflare API rejected update: ${update_error}"
exit 1
