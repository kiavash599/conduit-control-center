# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/metrics.py

Coverage targets:
  - Pydantic models: CpuMetrics, RamMetrics, DiskMetrics, SystemMetrics, TrafficMetrics
  - _traffic_cache_valid()        — cache hit / cache miss
  - _get_cpu_temperature()        — no sensors_temperatures / empty dict /
                                    preferred key / fallback / exception
  - _collect_system_metrics()     — mocked psutil
  - GET /api/metrics/system       — via TestClient with auth override
  - GET /api/metrics/traffic      — cache hit, cache miss (metrics=None, metrics=dict)
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.metrics as metrics_mod
from backend.api.metrics import (
    CpuMetrics,
    DiskMetrics,
    RamMetrics,
    SystemMetrics,
    TrafficMetrics,
    _collect_system_metrics,
    _get_cpu_temperature,
    _traffic_cache_valid,
    router,
)
from backend.dependencies import AuthenticatedUser, get_current_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_traffic_cache():
    """Clear traffic cache state before each test."""
    metrics_mod._traffic_cache = None
    metrics_mod._traffic_cache_ts = 0.0
    yield
    metrics_mod._traffic_cache = None
    metrics_mod._traffic_cache_ts = 0.0


@pytest.fixture(autouse=True)
def patch_metrics_config(monkeypatch):
    cfg = SimpleNamespace(metrics_cache_ttl_seconds=5)
    monkeypatch.setattr("backend.api.metrics.get_app_config", lambda: cfg)


@pytest.fixture
def metrics_client():
    app = FastAPI()
    app.include_router(router, prefix="/api/metrics")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TestPydanticModels:
    def test_cpu_metrics(self):
        m = CpuMetrics(usage_percent=45.0, temperature_celsius=62.5)
        assert m.usage_percent == 45.0
        assert m.temperature_celsius == 62.5

    def test_cpu_metrics_no_temp(self):
        m = CpuMetrics(usage_percent=30.0)
        assert m.temperature_celsius is None

    def test_ram_metrics(self):
        m = RamMetrics(total_bytes=4 * 1024**3, used_bytes=2 * 1024**3, used_percent=50.0)
        assert m.total_bytes == 4 * 1024**3

    def test_disk_metrics(self):
        m = DiskMetrics(total_bytes=32 * 1024**3, used_bytes=16 * 1024**3, used_percent=50.0)
        assert m.used_percent == 50.0

    def test_traffic_metrics_defaults(self):
        m = TrafficMetrics(timestamp="2026-06-01T00:00:00Z")
        assert m.bytes_sent is None
        assert m.bytes_received is None
        assert m.session_start is None


# ---------------------------------------------------------------------------
# _traffic_cache_valid()
# ---------------------------------------------------------------------------


class TestTrafficCacheValid:
    def test_no_cache_returns_false(self):
        assert _traffic_cache_valid() is False

    def test_fresh_cache_returns_true(self):
        metrics_mod._traffic_cache = TrafficMetrics(timestamp="2026-01-01T00:00:00Z")
        metrics_mod._traffic_cache_ts = time.monotonic()
        assert _traffic_cache_valid() is True

    def test_stale_cache_returns_false(self):
        metrics_mod._traffic_cache = TrafficMetrics(timestamp="2026-01-01T00:00:00Z")
        metrics_mod._traffic_cache_ts = time.monotonic() - 100  # 100s ago, TTL=5s
        assert _traffic_cache_valid() is False


# ---------------------------------------------------------------------------
# _get_cpu_temperature()
# ---------------------------------------------------------------------------


class TestGetCpuTemperature:
    def test_no_sensors_temperatures_attr_returns_none(self):
        with patch.object(psutil, "sensors_temperatures", None, create=True):
            with patch("backend.api.metrics.psutil") as mock_psutil:
                mock_psutil.sensors_temperatures = None
                # hasattr check: simulate attribute missing
                del mock_psutil.sensors_temperatures
                result = _get_cpu_temperature()
        assert result is None

    def test_empty_sensors_returns_none(self):
        with patch("backend.api.metrics.psutil") as mock_psutil:
            mock_psutil.sensors_temperatures = MagicMock(return_value={})
            mock_psutil.sensors_temperatures.return_value = {}
            result = _get_cpu_temperature()
        assert result is None

    def test_preferred_key_cpu_thermal_used(self):
        entry = SimpleNamespace(current=65.0)
        with patch("backend.api.metrics.psutil") as mock_psutil:
            mock_psutil.sensors_temperatures = MagicMock(
                return_value={"cpu_thermal": [entry]}
            )
            result = _get_cpu_temperature()
        assert result == 65.0

    def test_fallback_to_first_available_sensor(self):
        entry = SimpleNamespace(current=72.3)
        with patch("backend.api.metrics.psutil") as mock_psutil:
            mock_psutil.sensors_temperatures = MagicMock(
                return_value={"custom_sensor": [entry]}
            )
            result = _get_cpu_temperature()
        assert result == 72.3

    def test_exception_returns_none(self):
        with patch("backend.api.metrics.psutil") as mock_psutil:
            mock_psutil.sensors_temperatures = MagicMock(side_effect=RuntimeError("no sensor"))
            result = _get_cpu_temperature()
        assert result is None


# ---------------------------------------------------------------------------
# _collect_system_metrics()
# ---------------------------------------------------------------------------


class TestCollectSystemMetrics:
    def _make_vm(self, total=4*1024**3, used=2*1024**3, percent=50.0):
        return SimpleNamespace(total=total, used=used, percent=percent)

    def _make_disk(self, total=32*1024**3, used=16*1024**3, percent=50.0):
        return SimpleNamespace(total=total, used=used, percent=percent)

    def test_returns_system_metrics_instance(self):
        with patch("backend.api.metrics.psutil") as mock_psutil:
            mock_psutil.cpu_percent = MagicMock(return_value=20.0)
            mock_psutil.virtual_memory = MagicMock(return_value=self._make_vm())
            mock_psutil.disk_usage = MagicMock(return_value=self._make_disk())
            mock_psutil.sensors_temperatures = MagicMock(return_value={})
            result = _collect_system_metrics()
        assert isinstance(result, SystemMetrics)
        assert result.cpu.usage_percent == 20.0
        assert result.ram.total_bytes == 4 * 1024**3

    def test_disk_usage_called_with_root(self):
        with patch("backend.api.metrics.psutil") as mock_psutil:
            mock_psutil.cpu_percent = MagicMock(return_value=0.0)
            mock_psutil.virtual_memory = MagicMock(return_value=self._make_vm())
            disk_mock = MagicMock(return_value=self._make_disk())
            mock_psutil.disk_usage = disk_mock
            mock_psutil.sensors_temperatures = MagicMock(return_value={})
            _collect_system_metrics()
        disk_mock.assert_called_once_with("/")


# ---------------------------------------------------------------------------
# GET /api/metrics/system
# ---------------------------------------------------------------------------


class TestSystemMetricsRoute:
    def test_returns_200(self, metrics_client):
        with patch("backend.api.metrics._collect_system_metrics") as mock_collect:
            mock_collect.return_value = SystemMetrics(
                cpu=CpuMetrics(usage_percent=10.0),
                ram=RamMetrics(total_bytes=1024, used_bytes=512, used_percent=50.0),
                disk=DiskMetrics(total_bytes=2048, used_bytes=1024, used_percent=50.0),
            )
            response = metrics_client.get("/api/metrics/system")
        assert response.status_code == 200
        data = response.json()
        assert "cpu" in data
        assert "ram" in data
        assert "disk" in data


# ---------------------------------------------------------------------------
# GET /api/metrics/traffic
# ---------------------------------------------------------------------------


class TestTrafficMetricsRoute:
    def test_cache_miss_metrics_unavailable(self, metrics_client):
        with patch("backend.api.metrics.get_traffic_metrics", return_value=None), \
             patch("backend.api.metrics.get_last_changed", return_value=None):
            response = metrics_client.get("/api/metrics/traffic")
        assert response.status_code == 200
        data = response.json()
        assert data["bytes_sent"] is None
        assert data["bytes_received"] is None

    def test_cache_miss_metrics_available(self, metrics_client):
        with patch(
            "backend.api.metrics.get_traffic_metrics",
            return_value={"bytes_uploaded": 1024, "bytes_downloaded": 2048},
        ), patch("backend.api.metrics.get_last_changed", return_value="2026-01-01T00:00:00Z"):
            response = metrics_client.get("/api/metrics/traffic")
        assert response.status_code == 200
        data = response.json()
        assert data["bytes_sent"] == 1024
        assert data["bytes_received"] == 2048

    def test_cache_hit_returns_cached_result(self, metrics_client):
        cached = TrafficMetrics(
            bytes_sent=512,
            bytes_received=256,
            session_start=None,
            timestamp="2026-01-01T00:00:00Z",
        )
        metrics_mod._traffic_cache = cached
        metrics_mod._traffic_cache_ts = time.monotonic()
        response = metrics_client.get("/api/metrics/traffic")
        assert response.status_code == 200
        data = response.json()
        assert data["bytes_sent"] == 512

    def test_get_last_changed_error_degrades_gracefully(self, metrics_client):
        # The route catches ConduitAdapterError specifically, not bare Exception.
        from backend.conduit.adapter import ConduitAdapterError
        with patch("backend.api.metrics.get_traffic_metrics", return_value=None), \
             patch(
                 "backend.api.metrics.get_last_changed",
                 side_effect=ConduitAdapterError("adapter error"),
             ):
            response = metrics_client.get("/api/metrics/traffic")
        assert response.status_code == 200
        assert response.json()["session_start"] is None
