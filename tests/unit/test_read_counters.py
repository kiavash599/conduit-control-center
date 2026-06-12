# SPDX-License-Identifier: MIT
"""
Unit tests for backend.conduit.adapter.read_counters() (P0 Step 0).

Test matrix category A (read_counters contract):
  - parses bytes_up / bytes_down / uptime / build_rev / is_live
  - exponential and integer notation parsed exactly
  - uptime kept as float (NOT truncated to int)
  - the unlabelled global gauge is used, never a labelled region line
  - missing required metric -> MetricsContractError (never coerced to 0)
  - unparseable required value -> MetricsContractError
  - missing optional build_rev / is_live -> None (no raise)
  - transport failure (URLError / HTTPError / OSError) -> ConduitUnreachableError
  - CounterReading carries no ts / seq

read_counters() is exercised with its HTTP fetch (_fetch_metrics_text) and
config accessor monkeypatched, so no real Conduit or network is required.
"""
from __future__ import annotations

import urllib.error
from types import SimpleNamespace

import pytest

import backend.conduit.adapter as adapter
from backend.conduit.adapter import (
    ConduitUnreachableError,
    MetricsContractError,
    read_counters,
)
from backend.traffic.models import CounterReading


# Representative payload mirroring the captured Raspberry Pi metrics, including
# exponential notation, a fractional uptime, the labelled build_info line, and a
# labelled region byte line that must NOT be matched as the global total.
_FULL = (
    "# HELP conduit_build_info Build information about the Conduit service\n"
    "# TYPE conduit_build_info gauge\n"
    'conduit_build_info{build_repo="https://github.com/Psiphon-Labs/'
    'psiphon-tunnel-core.git",build_rev="8531118",go_version="go1.24.12 '
    'linux/amd64",values_rev="none"} 1\n'
    "# TYPE conduit_bytes_downloaded gauge\n"
    "conduit_bytes_downloaded 2.179426155e+09\n"
    'conduit_bytes_downloaded{region="US",scope="common"} 12345\n'
    "# TYPE conduit_bytes_uploaded gauge\n"
    "conduit_bytes_uploaded 292319749\n"
    "# TYPE conduit_uptime_seconds gauge\n"
    "conduit_uptime_seconds 13325.321657109\n"
    "conduit_is_live 1\n"
)


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    """read_counters() reads conduit_metrics_port; provide a fixed value."""
    monkeypatch.setattr(
        adapter, "get_app_config",
        lambda: SimpleNamespace(conduit_metrics_port=9090),
    )


def _with_payload(monkeypatch, text: str) -> None:
    monkeypatch.setattr(adapter, "_fetch_metrics_text", lambda url: text)


def _with_fetch_error(monkeypatch, exc: BaseException) -> None:
    def _boom(url):
        raise exc
    monkeypatch.setattr(adapter, "_fetch_metrics_text", _boom)


# ---------------------------------------------------------------------------
# Happy path / parsing
# ---------------------------------------------------------------------------


class TestParsing:
    async def test_full_payload_parsed(self, monkeypatch):
        _with_payload(monkeypatch, _FULL)
        r = await read_counters()
        assert isinstance(r, CounterReading)
        assert r.bytes_up == 292319749
        assert r.bytes_down == 2179426155          # exact from 2.179426155e+09
        assert r.uptime_seconds == 13325.321657109  # float, not truncated
        assert r.build_rev == "8531118"
        assert r.is_live is True

    async def test_exponential_notation_exact(self, monkeypatch):
        _with_payload(monkeypatch, _FULL)
        r = await read_counters()
        assert r.bytes_down == 2179426155
        assert isinstance(r.bytes_down, int)

    async def test_uptime_is_float_not_truncated(self, monkeypatch):
        _with_payload(monkeypatch, _FULL)
        r = await read_counters()
        assert isinstance(r.uptime_seconds, float)
        assert r.uptime_seconds != 13325  # would fail if truncated to int

    async def test_global_used_not_labelled_region(self, monkeypatch):
        # Even when the labelled region line precedes the global, the global wins.
        text = (
            'conduit_bytes_downloaded{region="US"} 12345\n'
            "conduit_bytes_downloaded 999\n"
            "conduit_bytes_uploaded 5\n"
            "conduit_uptime_seconds 1.0\n"
        )
        _with_payload(monkeypatch, text)
        r = await read_counters()
        assert r.bytes_down == 999  # not 12345

    async def test_is_live_zero_is_false(self, monkeypatch):
        text = (
            "conduit_bytes_uploaded 1\n"
            "conduit_bytes_downloaded 2\n"
            "conduit_uptime_seconds 3.0\n"
            "conduit_is_live 0\n"
        )
        _with_payload(monkeypatch, text)
        assert (await read_counters()).is_live is False


# ---------------------------------------------------------------------------
# Required metrics must raise, never coerce to 0
# ---------------------------------------------------------------------------


class TestRequiredMetrics:
    async def test_missing_bytes_uploaded_raises(self, monkeypatch):
        text = (
            "conduit_bytes_downloaded 2\n"
            "conduit_uptime_seconds 3.0\n"
        )
        _with_payload(monkeypatch, text)
        with pytest.raises(MetricsContractError):
            await read_counters()

    async def test_missing_uptime_raises(self, monkeypatch):
        text = (
            "conduit_bytes_uploaded 1\n"
            "conduit_bytes_downloaded 2\n"
        )
        _with_payload(monkeypatch, text)
        with pytest.raises(MetricsContractError):
            await read_counters()

    async def test_unparseable_required_value_raises(self, monkeypatch):
        text = (
            "conduit_bytes_uploaded notanumber\n"
            "conduit_bytes_downloaded 2\n"
            "conduit_uptime_seconds 3.0\n"
        )
        _with_payload(monkeypatch, text)
        with pytest.raises(MetricsContractError):
            await read_counters()

    async def test_empty_payload_raises_contract_error(self, monkeypatch):
        _with_payload(monkeypatch, "")
        with pytest.raises(MetricsContractError):
            await read_counters()


# ---------------------------------------------------------------------------
# Optional metrics -> None
# ---------------------------------------------------------------------------


class TestOptionalMetrics:
    async def test_missing_build_rev_is_none(self, monkeypatch):
        text = (
            "conduit_bytes_uploaded 1\n"
            "conduit_bytes_downloaded 2\n"
            "conduit_uptime_seconds 3.0\n"
            "conduit_is_live 1\n"
        )
        _with_payload(monkeypatch, text)
        r = await read_counters()
        assert r.build_rev is None
        assert r.is_live is True

    async def test_missing_is_live_is_none(self, monkeypatch):
        text = (
            "conduit_bytes_uploaded 1\n"
            "conduit_bytes_downloaded 2\n"
            "conduit_uptime_seconds 3.0\n"
        )
        _with_payload(monkeypatch, text)
        assert (await read_counters()).is_live is None


# ---------------------------------------------------------------------------
# Transport failures -> ConduitUnreachableError
# ---------------------------------------------------------------------------


class TestTransportErrors:
    async def test_urlerror_maps_to_unreachable(self, monkeypatch):
        _with_fetch_error(monkeypatch, urllib.error.URLError("refused"))
        with pytest.raises(ConduitUnreachableError):
            await read_counters()

    async def test_httperror_maps_to_unreachable(self, monkeypatch):
        # HTTPError is a URLError subclass -> non-2xx status is unreachable.
        err = urllib.error.HTTPError("http://x/metrics", 500, "err", {}, None)
        _with_fetch_error(monkeypatch, err)
        with pytest.raises(ConduitUnreachableError):
            await read_counters()

    async def test_oserror_maps_to_unreachable(self, monkeypatch):
        _with_fetch_error(monkeypatch, OSError("timeout"))
        with pytest.raises(ConduitUnreachableError):
            await read_counters()


# ---------------------------------------------------------------------------
# Shape: CounterReading owns no ts / seq
# ---------------------------------------------------------------------------


class TestShape:
    async def test_no_ts_or_seq_fields(self, monkeypatch):
        _with_payload(monkeypatch, _FULL)
        r = await read_counters()
        assert not hasattr(r, "ts")
        assert not hasattr(r, "seq")
        assert not hasattr(r, "ts_utc")
