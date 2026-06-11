# SPDX-License-Identifier: MIT
"""
Unit tests for backend/config.py

Coverage:
  - AppConfig defaults (empty dict → all documented defaults)
  - AppConfig partial overrides (only provided keys change)
  - get_app_config() singleton and file-loading behaviour
  - get_settings() singleton and dev defaults
"""
from __future__ import annotations

import json


from backend.config import AppConfig, get_app_config, get_settings


# ---------------------------------------------------------------------------
# AppConfig — defaults
# ---------------------------------------------------------------------------


class TestAppConfigDefaults:
    """AppConfig({}) must yield all documented default values."""

    def setup_method(self):
        self.cfg = AppConfig({})

    # app section
    def test_host(self):
        assert self.cfg.host == "127.0.0.1"

    def test_port(self):
        assert self.cfg.port == 8000

    def test_session_timeout_minutes(self):
        assert self.cfg.session_timeout_minutes == 60

    def test_max_failed_login_attempts(self):
        assert self.cfg.max_failed_login_attempts == 5

    def test_lockout_duration_minutes(self):
        assert self.cfg.lockout_duration_minutes == 15

    # conduit section
    def test_conduit_service_name(self):
        assert self.cfg.conduit_service_name == "conduit"

    def test_conduit_action_timeout_seconds(self):
        assert self.cfg.conduit_action_timeout_seconds == 10

    def test_conduit_metrics_port(self):
        assert self.cfg.conduit_metrics_port == 9090

    # metrics section
    def test_metrics_cache_ttl_seconds(self):
        assert self.cfg.metrics_cache_ttl_seconds == 5

    # alerts section
    def test_cpu_temp_warning_celsius(self):
        assert self.cfg.cpu_temp_warning_celsius == 70.0

    def test_cpu_temp_critical_celsius(self):
        assert self.cfg.cpu_temp_critical_celsius == 80.0

    def test_ram_warning_percent(self):
        assert self.cfg.ram_warning_percent == 80.0

    def test_ram_critical_percent(self):
        assert self.cfg.ram_critical_percent == 90.0

    def test_disk_warning_percent(self):
        assert self.cfg.disk_warning_percent == 75.0

    def test_disk_critical_percent(self):
        assert self.cfg.disk_critical_percent == 85.0

    # logs section
    def test_logs_viewer_default_lines(self):
        assert self.cfg.logs_viewer_default_lines == 200

    def test_logs_viewer_max_lines(self):
        assert self.cfg.logs_viewer_max_lines == 1000


# ---------------------------------------------------------------------------
# AppConfig — partial overrides
# ---------------------------------------------------------------------------


class TestAppConfigPartialOverride:
    """Provided keys are used; omitted keys fall back to defaults."""

    def test_override_max_failed_login_attempts(self):
        cfg = AppConfig({"app": {"max_failed_login_attempts": 3}})
        assert cfg.max_failed_login_attempts == 3
        # other app defaults intact
        assert cfg.lockout_duration_minutes == 15

    def test_override_lockout_duration_minutes(self):
        cfg = AppConfig({"app": {"lockout_duration_minutes": 30}})
        assert cfg.lockout_duration_minutes == 30
        assert cfg.max_failed_login_attempts == 5

    def test_override_session_timeout(self):
        cfg = AppConfig({"app": {"session_timeout_minutes": 120}})
        assert cfg.session_timeout_minutes == 120

    def test_override_conduit_service_name(self):
        cfg = AppConfig({"conduit": {"service_name": "my-conduit"}})
        assert cfg.conduit_service_name == "my-conduit"
        assert cfg.conduit_metrics_port == 9090  # default intact

    def test_override_alert_thresholds(self):
        cfg = AppConfig({
            "alerts": {
                "cpu_temp_warning_celsius": 65.0,
                "ram_critical_percent": 95.0,
            }
        })
        assert cfg.cpu_temp_warning_celsius == 65.0
        assert cfg.ram_critical_percent == 95.0
        # other alert defaults intact
        assert cfg.disk_warning_percent == 75.0

    def test_override_metrics_cache_ttl(self):
        cfg = AppConfig({"metrics": {"cache_ttl_seconds": 10}})
        assert cfg.metrics_cache_ttl_seconds == 10

    def test_unrecognised_section_ignored(self):
        """Extra sections in config.json must not raise."""
        cfg = AppConfig({"unknown_section": {"foo": "bar"}})
        assert cfg.host == "127.0.0.1"  # defaults unaffected


# ---------------------------------------------------------------------------
# get_app_config()
# ---------------------------------------------------------------------------


class TestGetAppConfig:
    def test_returns_appconfig_instance(self):
        assert isinstance(get_app_config(), AppConfig)

    def test_cached_returns_same_object(self):
        assert get_app_config() is get_app_config()

    def test_loads_values_from_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"app": {"lockout_duration_minutes": 99}}))

        import backend.config as config_mod
        monkeypatch.setattr(config_mod, "_DEFAULT_CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "_PROD_CONFIG_FILE", tmp_path / "nonexistent.json")
        get_app_config.cache_clear()

        cfg = get_app_config()
        assert cfg.lockout_duration_minutes == 99

    def test_falls_back_to_defaults_when_file_missing(self, tmp_path, monkeypatch):
        import backend.config as config_mod
        monkeypatch.setattr(config_mod, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.json")
        monkeypatch.setattr(config_mod, "_PROD_CONFIG_FILE", tmp_path / "also_missing.json")
        get_app_config.cache_clear()

        cfg = get_app_config()
        assert cfg.max_failed_login_attempts == 5  # default

    def test_falls_back_on_invalid_json(self, tmp_path, monkeypatch):
        bad_file = tmp_path / "config.json"
        bad_file.write_text("{ not valid json }")

        import backend.config as config_mod
        monkeypatch.setattr(config_mod, "_DEFAULT_CONFIG_FILE", bad_file)
        monkeypatch.setattr(config_mod, "_PROD_CONFIG_FILE", tmp_path / "nonexistent.json")
        get_app_config.cache_clear()

        cfg = get_app_config()
        assert cfg.session_timeout_minutes == 60  # default


# ---------------------------------------------------------------------------
# get_settings()
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_returns_settings_instance(self):
        from backend.config import Settings
        assert isinstance(get_settings(), Settings)

    def test_cached_returns_same_object(self):
        assert get_settings() is get_settings()

    def test_default_app_port(self):
        assert get_settings().app_port == 8000

    def test_default_admin_username(self):
        assert get_settings().admin_username == "admin"

    def test_default_log_level(self):
        assert get_settings().log_level == "INFO"
