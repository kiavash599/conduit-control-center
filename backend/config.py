"""
backend/config.py
-----------------
Loads all runtime configuration from two sources:

  1. Environment variables / .env file  -> Pydantic Settings (secrets, ports, keys)
  2. config.json                        -> app behaviour (timeouts, thresholds, paths)

Usage
-----
    from backend.config import get_settings, get_app_config

    settings = get_settings()          # env-based settings (cached singleton)
    cfg = get_app_config()             # config.json-based values (cached singleton)

Both functions return the same cached object on every call -- safe to use as
FastAPI dependencies or plain imports.

Security note
-------------
SESSION_SECRET and CF_API_TOKEN are loaded from the environment only.
They are never written to logs, responses, or config.json.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env"
_DEFAULT_CONFIG_FILE = _PROJECT_ROOT / "config.json"

_PROD_ENV_FILE = Path("/etc/conduit-cc/.env")
_PROD_CONFIG_FILE = Path("/etc/conduit-cc/config.json")


def _resolve_path(dev: Path, prod: Path) -> Path:
    """Return the first path that exists, favouring the dev path."""
    if dev.exists():
        return dev
    if prod.exists():
        return prod
    return dev


# ---------------------------------------------------------------------------
# Public path helper
# ---------------------------------------------------------------------------


def get_env_file_path():
    """
    Return the path of the active .env file.

    Wraps the private _resolve_path() call so that other modules
    (e.g. backend/api/settings.py) can locate the .env file without
    coupling to the private path constants.

    Returns
    -------
    pathlib.Path -- path to the .env file (may not exist yet in a bare dev clone)
    """
    return _resolve_path(_DEFAULT_ENV_FILE, _PROD_ENV_FILE)


# ---------------------------------------------------------------------------
# Environment-based settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """
    Loaded from environment variables and the .env file.

    All fields have sensible defaults so the app starts on a fresh clone
    without a .env file.  SESSION_SECRET must be set to a real value in
    production -- the validator rejects the .env.example placeholder.
    """

    model_config = SettingsConfigDict(
        env_file=str(_resolve_path(_DEFAULT_ENV_FILE, _PROD_ENV_FILE)),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application ----------------------------------------------------------
    session_secret: str = Field(
        default="dev_secret_change_me_in_production",
        description=(
            "32-byte hex secret for signing session IDs. REQUIRED in production."
        ),
    )
    app_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    # Set False in local HTTP development; always True in production.
    secure_cookies: bool = Field(default=True)

    # -- Cloudflare -----------------------------------------------------------
    cf_api_token: str = Field(default="")
    cf_zone_name: str = Field(default="")
    cf_record_name: str = Field(default="")

    # -- TLS ------------------------------------------------------------------
    tls_cert_path: str = Field(default="/etc/conduit-cc/tls/origin.pem")
    tls_key_path: str = Field(default="/etc/conduit-cc/tls/origin.key")

    # -- Admin ----------------------------------------------------------------
    # bcrypt hash stored in .env; never the plaintext password.
    admin_password_hash: str = Field(default="")
    # Username for the single admin account.
    # Override via ADMIN_USERNAME in .env only if the default "admin" conflicts
    # with an existing system user or organisational policy.
    # Must match whatever username was used when the password hash was generated.
    admin_username: str = Field(default="admin")

    @field_validator("session_secret")
    @classmethod
    def _require_real_secret_in_prod(cls, v: str) -> str:
        placeholder = "replace_with_a_32_byte_random_hex_string"
        if v == placeholder:
            raise ValueError(
                "SESSION_SECRET is still set to the placeholder value. "
                "Generate a real secret: "
                "python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}, got {v!r}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    return Settings()


# ---------------------------------------------------------------------------
# config.json -- app behaviour settings
# ---------------------------------------------------------------------------


class AppConfig:
    """
    Typed wrapper around config.json.

    Attributes mirror the keys in config.example.json.  If a key is missing
    (e.g. on an older install) the default value is used so the app stays
    forward-compatible.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        app = data.get("app", {})
        self.host: str = app.get("host", "127.0.0.1")
        self.port: int = app.get("port", 8000)
        self.session_timeout_minutes: int = app.get("session_timeout_minutes", 60)
        self.max_failed_login_attempts: int = app.get("max_failed_login_attempts", 5)
        self.lockout_duration_minutes: int = app.get("lockout_duration_minutes", 15)

        # config.json "web" section. Missing key (older installs) -> 443, so the
        # displayed/configured HTTPS port stays backward-compatible.
        web = data.get("web", {})
        self.web_https_port: int = int(web.get("https_port", 443))

        conduit = data.get("conduit", {})
        self.conduit_service_name: str = conduit.get("service_name", "conduit")
        self.conduit_action_timeout_seconds: int = conduit.get(
            "action_timeout_seconds", 10
        )
        self.conduit_metrics_port: int = conduit.get("metrics_port", 9090)
        # M2 config write: soft upper bound for the editable bandwidth value
        # (Mbps). Must match the BW_MAX hardcoded in the root helper
        # (ccc-apply-conduit-config). Catches fat-finger input; -1 = unlimited.
        self.conduit_bandwidth_max_mbps: int = conduit.get("bandwidth_max_mbps", 1000)

        metrics = data.get("metrics", {})
        self.metrics_cache_ttl_seconds: int = metrics.get("cache_ttl_seconds", 5)

        alerts = data.get("alerts", {})
        self.cpu_temp_warning_celsius: float = alerts.get(
            "cpu_temp_warning_celsius", 70.0
        )
        self.cpu_temp_critical_celsius: float = alerts.get(
            "cpu_temp_critical_celsius", 80.0
        )
        self.ram_warning_percent: float = alerts.get("ram_warning_percent", 80.0)
        self.ram_critical_percent: float = alerts.get("ram_critical_percent", 90.0)
        self.disk_warning_percent: float = alerts.get("disk_warning_percent", 75.0)
        self.disk_critical_percent: float = alerts.get("disk_critical_percent", 85.0)

        logs = data.get("logs", {})
        self.logs_viewer_default_lines: int = logs.get("viewer_default_lines", 200)
        self.logs_viewer_max_lines: int = logs.get("viewer_max_lines", 1000)
        self.ddns_log_path: str = logs.get(
            "ddns_log_path", "/var/log/conduit-cc/ddns.log"
        )
        self.ddns_status_cache_seconds: int = logs.get("ddns_status_cache_seconds", 30)

        ddns = data.get("ddns", {})
        self.ddns_ip_provider_url: str = ddns.get(
            "ip_provider_url", "https://api.ipify.org"
        )

        # -- Traffic persistence collector (P0) ------------------------------
        # Ship-dark: the collector starts only when explicitly enabled. A
        # missing "traffic" section (e.g. an existing install) yields the
        # disabled default, so there is no behaviour change on upgrade.
        traffic = data.get("traffic", {})
        self.traffic_collector_enabled: bool = traffic.get("collector_enabled", False)
        self.traffic_collect_interval_seconds: float = traffic.get(
            "collect_interval_seconds", 60.0
        )
        self.traffic_gap_threshold_seconds: float = traffic.get(
            "gap_threshold_seconds", 90.0
        )
        self.traffic_snapshot_retention_days: int = traffic.get(
            "snapshot_retention_days", 7
        )
        self.traffic_delta_retention_days: int = traffic.get(
            "delta_retention_days", 90
        )
        self.traffic_hourly_retention_days: int = traffic.get(
            "hourly_retention_days", 180
        )

        # -- Contribution Advisor (A1) ---------------------------------------
        # Read-only advisory inputs/gating. A missing "advisor" section yields
        # the defaults below, so there is no behaviour change on upgrade.
        # NOTE: engine threshold bands (CPU/RAM/temp/demand/step) live in
        # backend.advisor.models.AdvisorPolicy, not here -- these are only the
        # API-side sampling/warm-up knobs.
        advisor = data.get("advisor", {})
        self.advisor_sample_window_seconds: int = advisor.get(
            "sample_window_seconds", 900
        )
        self.advisor_sample_throttle_seconds: int = advisor.get(
            "sample_throttle_seconds", 45
        )
        self.advisor_growth_min_samples: int = advisor.get("growth_min_samples", 10)
        self.advisor_growth_min_span_seconds: int = advisor.get(
            "growth_min_span_seconds", 600
        )
        self.advisor_growth_sample_pass_fraction: float = advisor.get(
            "growth_sample_pass_fraction", 0.80
        )
        self.advisor_hourly_history_hours: int = advisor.get("hourly_history_hours", 168)


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """
    Load and cache config.json.

    Falls back to all defaults if the file does not exist -- useful during
    local development before install.sh has run.
    """
    config_path = _resolve_path(_DEFAULT_CONFIG_FILE, _PROD_CONFIG_FILE)
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            logger.debug("Loaded config.json from %s", config_path)
            return AppConfig(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load %s (%s) -- using defaults", config_path, exc)
    else:
        logger.info(
            "config.json not found at %s or %s -- using defaults. "
            "Copy config.example.json to config.json to customise.",
            _DEFAULT_CONFIG_FILE,
            _PROD_CONFIG_FILE,
        )
    return AppConfig({})
