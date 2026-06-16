# SPDX-License-Identifier: MIT
"""Unit tests for backend.conduit.config_validation (M2). Pure; no I/O."""
from __future__ import annotations

from backend.conduit.config_validation import (
    mbps_to_bps,
    validate_bandwidth_mbps,
    validate_max_common_clients,
)


def test_mcc_valid():
    assert validate_max_common_clients(1) == (1, None)
    assert validate_max_common_clients(1000) == (1000, None)
    assert validate_max_common_clients(50) == (50, None)


def test_mcc_rejects_zero_and_range():
    assert validate_max_common_clients(0)[0] is None
    assert validate_max_common_clients(1001)[0] is None
    assert validate_max_common_clients(-5)[0] is None


def test_mcc_rejects_non_int_and_bool():
    assert validate_max_common_clients("50")[0] is None
    assert validate_max_common_clients(50.0)[0] is None
    assert validate_max_common_clients(True)[0] is None
    assert validate_max_common_clients(None)[0] is None


def test_bw_valid_and_unlimited():
    assert validate_bandwidth_mbps(40) == (40, None)
    assert validate_bandwidth_mbps(1) == (1, None)
    assert validate_bandwidth_mbps(-1) == (-1, None)        # unlimited
    assert validate_bandwidth_mbps(1000) == (1000, None)


def test_bw_rejects_zero_range_and_type():
    assert validate_bandwidth_mbps(0)[0] is None
    assert validate_bandwidth_mbps(1001)[0] is None         # default cap 1000
    assert validate_bandwidth_mbps(-2)[0] is None
    assert validate_bandwidth_mbps(True)[0] is None
    assert validate_bandwidth_mbps("40")[0] is None


def test_bw_custom_cap():
    assert validate_bandwidth_mbps(2000, max_mbps=5000) == (2000, None)
    assert validate_bandwidth_mbps(6000, max_mbps=5000)[0] is None


def test_mbps_to_bps():
    assert mbps_to_bps(40) == 5_000_000
    assert mbps_to_bps(1) == 125_000
    assert mbps_to_bps(-1) == 0     # unlimited -> metric 0
