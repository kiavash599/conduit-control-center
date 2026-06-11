# SPDX-License-Identifier: MIT
"""
Unit tests for backend/conduit/adapter.py

Coverage targets:
  - _check_permission_denied()   — pure function, all flag paths
  - _parse_prometheus_gauge()    — pure function, hit/miss/bad-value
  - _service_name()              — reads config
  - _run()                       — mocked asyncio.create_subprocess_exec
  - get_status()                 — mocked _run, all status map entries + errors
  - get_last_changed()           — mocked _run, timestamp parsing, empty, errors
  - start() / stop() / restart() — mocked _control_action via module injection
  - get_version()                — mocked _run, cached, file-not-found, timeout
  - get_traffic_metrics()        — mocked asyncio.to_thread, URLError, OSError
"""
from __future__ import annotations

import asyncio
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import backend.conduit.adapter as adapter_mod
from backend.conduit.adapter import (
    ConduitAdapterError,
    ConduitPermissionError,
    _check_permission_denied,
    _parse_prometheus_gauge,
    get_last_changed,
    get_status,
    get_traffic_metrics,
    get_version,
    start,
    stop,
    restart,
)


# ---------------------------------------------------------------------------
# Shared config fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_adapter_globals():
    """Reset module-level caches between tests."""
    adapter_mod._version_checked = False
    adapter_mod._version_cache = None
    adapter_mod._traffic_cache = None   # type: ignore[attr-defined]
    adapter_mod._traffic_cache_ts = 0.0  # type: ignore[attr-defined]
    yield
    adapter_mod._version_checked = False
    adapter_mod._version_cache = None


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    cfg = SimpleNamespace(
        conduit_service_name="conduit",
        conduit_action_timeout_seconds=5,
        conduit_metrics_port=9090,
        metrics_cache_ttl_seconds=5,
    )
    monkeypatch.setattr("backend.conduit.adapter.get_app_config", lambda: cfg)


# ---------------------------------------------------------------------------
# _check_permission_denied()
# ---------------------------------------------------------------------------


class TestCheckPermissionDenied:
    def test_returncode_zero_always_false(self):
        assert _check_permission_denied(0, "permission denied") is False

    def test_permission_denied_in_stderr(self):
        assert _check_permission_denied(1, "Permission denied") is True

    def test_access_denied_in_stderr(self):
        assert _check_permission_denied(1, "Access denied") is True

    def test_interactive_auth_required(self):
        assert _check_permission_denied(1, "Interactive authentication required") is True

    def test_polkit_in_stderr(self):
        assert _check_permission_denied(1, "polkit error occurred") is True

    def test_sorry_user_sudo_message(self):
        assert _check_permission_denied(1, "Sorry, user conduit-cc is not allowed") is True

    def test_sudoers_in_stderr(self):
        assert _check_permission_denied(1, "not in the sudoers file") is True

    def test_generic_error_not_permission(self):
        assert _check_permission_denied(1, "some generic error") is False

    def test_empty_stderr(self):
        assert _check_permission_denied(1, "") is False


# ---------------------------------------------------------------------------
# _parse_prometheus_gauge()
# ---------------------------------------------------------------------------


class TestParsePrometheusGauge:
    _PAYLOAD = (
        "# HELP conduit_bytes_uploaded Total bytes uploaded\n"
        "# TYPE conduit_bytes_uploaded gauge\n"
        "conduit_bytes_uploaded 1073741824\n"
        'conduit_bytes_uploaded{scope="common"} 524288000\n'
        "conduit_bytes_downloaded 2048\n"
    )

    def test_unlabelled_line_parsed(self):
        result = _parse_prometheus_gauge(self._PAYLOAD, "conduit_bytes_uploaded")
        assert result == 1073741824

    def test_second_metric_parsed(self):
        result = _parse_prometheus_gauge(self._PAYLOAD, "conduit_bytes_downloaded")
        assert result == 2048

    def test_metric_not_present_returns_none(self):
        result = _parse_prometheus_gauge(self._PAYLOAD, "conduit_bytes_unknown")
        assert result is None

    def test_labelled_line_not_matched(self):
        # The labelled line "conduit_bytes_uploaded{...}" must not match.
        payload = 'conduit_bytes_uploaded{scope="common"} 999\n'
        result = _parse_prometheus_gauge(payload, "conduit_bytes_uploaded")
        assert result is None

    def test_bad_value_returns_none(self):
        payload = "conduit_bytes_uploaded notanumber\n"
        result = _parse_prometheus_gauge(payload, "conduit_bytes_uploaded")
        assert result is None

    def test_float_value_rounded_to_int(self):
        payload = "conduit_bytes_uploaded 1073741824.7\n"
        result = _parse_prometheus_gauge(payload, "conduit_bytes_uploaded")
        assert result == 1073741824

    def test_zero_value(self):
        payload = "conduit_bytes_uploaded 0\n"
        result = _parse_prometheus_gauge(payload, "conduit_bytes_uploaded")
        assert result == 0

    def test_empty_text_returns_none(self):
        result = _parse_prometheus_gauge("", "conduit_bytes_uploaded")
        assert result is None


# ---------------------------------------------------------------------------
# get_status() — mocked _run
# ---------------------------------------------------------------------------


class TestGetStatus:
    @pytest.fixture
    def mock_run(self):
        with patch("backend.conduit.adapter._run") as m:
            yield m

    async def test_active_maps_to_running(self, mock_run):
        mock_run.return_value = (0, "active", "")
        assert await get_status() == "running"

    async def test_inactive_maps_to_stopped(self, mock_run):
        mock_run.return_value = (3, "inactive", "")
        assert await get_status() == "stopped"

    async def test_activating_maps_to_starting(self, mock_run):
        mock_run.return_value = (0, "activating", "")
        assert await get_status() == "starting"

    async def test_deactivating_maps_to_stopping(self, mock_run):
        mock_run.return_value = (0, "deactivating", "")
        assert await get_status() == "stopping"

    async def test_failed_maps_to_error(self, mock_run):
        mock_run.return_value = (1, "failed", "")
        assert await get_status() == "error"

    async def test_unknown_string_maps_to_error(self, mock_run):
        mock_run.return_value = (1, "some_unexpected_state", "")
        assert await get_status() == "error"

    async def test_unit_not_found_rc4_raises(self, mock_run):
        mock_run.return_value = (4, "", "Unit conduit.service not found")
        with pytest.raises(ConduitAdapterError):
            await get_status()

    async def test_permission_denied_raises(self, mock_run):
        mock_run.return_value = (1, "", "permission denied")
        with pytest.raises(ConduitPermissionError):
            await get_status()


# ---------------------------------------------------------------------------
# get_last_changed() — mocked _run
# ---------------------------------------------------------------------------


class TestGetLastChanged:
    @pytest.fixture
    def mock_run(self):
        with patch("backend.conduit.adapter._run") as m:
            yield m

    async def test_timestamp_with_weekday_prefix_parsed(self, mock_run):
        mock_run.return_value = (
            0,
            "ActiveEnterTimestamp=Sat 2026-05-31 14:30:00 UTC",
            "",
        )
        result = await get_last_changed()
        assert result == "2026-05-31T14:30:00Z"

    async def test_timestamp_without_weekday_parsed(self, mock_run):
        mock_run.return_value = (
            0,
            "ActiveEnterTimestamp=2026-05-31 14:30:00 UTC",
            "",
        )
        result = await get_last_changed()
        assert result == "2026-05-31T14:30:00Z"

    async def test_empty_timestamp_returns_none(self, mock_run):
        mock_run.return_value = (0, "ActiveEnterTimestamp=", "")
        result = await get_last_changed()
        assert result is None

    async def test_malformed_timestamp_returns_none(self, mock_run):
        mock_run.return_value = (0, "ActiveEnterTimestamp=invalid-date-here", "")
        result = await get_last_changed()
        assert result is None

    async def test_nonzero_rc_permission_error_raises(self, mock_run):
        mock_run.return_value = (1, "", "permission denied")
        with pytest.raises(ConduitPermissionError):
            await get_last_changed()

    async def test_nonzero_rc_generic_raises(self, mock_run):
        mock_run.return_value = (1, "", "some error")
        with pytest.raises(ConduitAdapterError):
            await get_last_changed()

    async def test_run_called_with_tz_utc(self, mock_run):
        """systemctl must be invoked with TZ=UTC so local timezone is ignored."""
        mock_run.return_value = (
            0,
            "ActiveEnterTimestamp=Sat 2026-05-31 14:30:00 UTC",
            "",
        )
        await get_last_changed()
        args_used = mock_run.call_args[0][0]  # first positional arg (the list)
        assert args_used[:2] == ["env", "TZ=UTC"], (
            f"Expected _run to be called with ['env', 'TZ=UTC', ...], got {args_used}"
        )


# ---------------------------------------------------------------------------
# start() / stop() / restart() — exercise the REAL _control_action
# ---------------------------------------------------------------------------


class TestControlActions:
    """
    Drive the real _control_action through start()/stop()/restart() by mocking
    only its boundaries (_run for the systemctl call, get_status for the poll).
    The helper is NOT injected — if it is removed again, these tests fail
    instead of silently passing (regression guard for the F821 defect).
    """

    async def test_control_action_exists(self):
        # Regression guard: the production helper must be defined.
        assert asyncio.iscoroutinefunction(adapter_mod._control_action)

    async def test_start_runs_systemctl_and_waits_for_running(self):
        with patch("backend.conduit.adapter._run") as mock_run, \
             patch("backend.conduit.adapter.get_status") as mock_status:
            mock_run.return_value = (0, "", "")
            mock_status.return_value = "running"
            result = await start()
        mock_run.assert_awaited_once_with(["sudo", "systemctl", "start", "conduit"])
        assert result == {
            "success": True,
            "status": "running",
            "message": "Conduit start successful.",
        }

    async def test_stop_runs_systemctl_and_waits_for_stopped(self):
        with patch("backend.conduit.adapter._run") as mock_run, \
             patch("backend.conduit.adapter.get_status") as mock_status:
            mock_run.return_value = (0, "", "")
            mock_status.return_value = "stopped"
            result = await stop()
        mock_run.assert_awaited_once_with(["sudo", "systemctl", "stop", "conduit"])
        assert result["success"] is True
        assert result["status"] == "stopped"

    async def test_restart_runs_systemctl_and_waits_for_running(self):
        with patch("backend.conduit.adapter._run") as mock_run, \
             patch("backend.conduit.adapter.get_status") as mock_status:
            mock_run.return_value = (0, "", "")
            mock_status.return_value = "running"
            result = await restart()
        mock_run.assert_awaited_once_with(["sudo", "systemctl", "restart", "conduit"])
        assert result["status"] == "running"

    async def test_permission_denied_propagates(self):
        # Non-zero rc + permission-denied stderr -> ConduitPermissionError,
        # raised before any polling.
        with patch("backend.conduit.adapter._run") as mock_run:
            mock_run.return_value = (1, "", "permission denied")
            with pytest.raises(ConduitPermissionError):
                await start()


# ---------------------------------------------------------------------------
# get_version() — mocked _run
# ---------------------------------------------------------------------------


class TestGetVersion:
    async def test_version_detected(self):
        with patch("backend.conduit.adapter._run", return_value=(0, "conduit version 1.2.3", "")):
            result = await get_version()
        assert result == "1.2.3"

    async def test_cached_after_first_call(self):
        with patch("backend.conduit.adapter._run", return_value=(0, "conduit version 2.0.0", "")) as m:
            await get_version()
            await get_version()  # second call must not shell out
        assert m.call_count == 1

    async def test_no_semver_in_output_returns_none(self):
        with patch("backend.conduit.adapter._run", return_value=(0, "conduit ready", "")):
            result = await get_version()
        assert result is None

    async def test_nonzero_returncode_returns_none(self):
        with patch("backend.conduit.adapter._run", return_value=(1, "", "not found")):
            result = await get_version()
        assert result is None

    async def test_file_not_found_returns_none(self):
        with patch("backend.conduit.adapter._run", side_effect=FileNotFoundError):
            result = await get_version()
        assert result is None

    async def test_timeout_returns_none(self):
        with patch("backend.conduit.adapter._run", side_effect=asyncio.TimeoutError):
            result = await get_version()
        assert result is None

    async def test_unexpected_exception_returns_none(self):
        with patch("backend.conduit.adapter._run", side_effect=RuntimeError("unexpected")):
            result = await get_version()
        assert result is None

    async def test_version_from_file(self):
        """Version file is read first; no subprocess is spawned."""
        with patch("backend.conduit.adapter.pathlib.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.return_value = "2.0.0\n"
            with patch("backend.conduit.adapter._run") as mock_run:
                result = await get_version()
        assert result == "2.0.0"
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# get_traffic_metrics() — mocked asyncio.to_thread
# ---------------------------------------------------------------------------


class TestGetTrafficMetrics:
    _PAYLOAD = (
        "# HELP conduit_bytes_uploaded ...\n"
        "conduit_bytes_uploaded 1024\n"
        "conduit_bytes_downloaded 2048\n"
    )

    async def test_success_returns_dict(self):
        with patch("backend.conduit.adapter.asyncio.to_thread", return_value=self._PAYLOAD):
            result = await get_traffic_metrics()
        assert result is not None
        assert result["bytes_uploaded"] == 1024
        assert result["bytes_downloaded"] == 2048

    async def test_url_error_returns_none(self):
        with patch(
            "backend.conduit.adapter.asyncio.to_thread",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = await get_traffic_metrics()
        assert result is None

    async def test_os_error_returns_none(self):
        with patch(
            "backend.conduit.adapter.asyncio.to_thread",
            side_effect=OSError("network unreachable"),
        ):
            result = await get_traffic_metrics()
        assert result is None

    async def test_unexpected_exception_returns_none(self):
        with patch(
            "backend.conduit.adapter.asyncio.to_thread",
            side_effect=RuntimeError("oops"),
        ):
            result = await get_traffic_metrics()
        assert result is None

    async def test_missing_metrics_in_payload_returns_none_values(self):
        with patch("backend.conduit.adapter.asyncio.to_thread", return_value="# no metrics\n"):
            result = await get_traffic_metrics()
        assert result is not None
        assert result["bytes_uploaded"] is None
        assert result["bytes_downloaded"] is None
