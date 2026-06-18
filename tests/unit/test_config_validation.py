# SPDX-License-Identifier: MIT
"""Unit tests for backend.conduit.config_validation (M2). Pure; no I/O."""
from __future__ import annotations

from backend.conduit.config_validation import (
    mbps_to_bps,
    parse_hhmm,
    validate_bandwidth_mbps,
    validate_max_common_clients,
    validate_reduced,
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


# --------------------------- reduced window (BS1) ---------------------------
def test_parse_hhmm_valid():
    assert parse_hhmm("00:00") == (0, None)
    assert parse_hhmm("02:00") == (120, None)
    assert parse_hhmm("23:59") == (1439, None)


def test_parse_hhmm_invalid():
    for bad in ("24:00", "02:60", "2:00", "0200", "02:0", "", "aa:bb", 120, None):
        assert parse_hhmm(bad)[0] is None


def test_validate_reduced_disabled():
    assert validate_reduced(False, None, None, 0, 0, 50)[0] == {
        "start_min": -1, "end_min": -1,
        "reduced_max_common_clients": 0, "reduced_bandwidth_mbps": 0,
    }


def test_validate_reduced_enabled_to_minutes():
    n, e = validate_reduced(True, "02:00", "06:00", 10, 15, 50)
    assert e == []
    assert n == {"start_min": 120, "end_min": 360,
                 "reduced_max_common_clients": 10, "reduced_bandwidth_mbps": 15}


def test_validate_reduced_wraparound():
    n, e = validate_reduced(True, "22:00", "06:00", 10, 15, 50)
    assert e == [] and n["start_min"] == 1320 and n["end_min"] == 360


def test_validate_reduced_rejects():
    cases = [
        (True, "02:00", "02:00", 10, 15),   # start == end
        (True, "99:00", "06:00", 10, 15),   # bad start
        (True, "02:00", "06:00", 0, 15),    # max 0 while enabled
        (True, "02:00", "06:00", 60, 15),   # max > mcc (50)
        (True, "02:00", "06:00", 10, 0),    # bw 0 while enabled
        (True, "02:00", "06:00", 10, 1001), # bw out of range
        (True, "02:00", "06:00", True, 15), # bool not int
    ]
    for enabled, s, e, rc, bw in cases:
        n, errs = validate_reduced(enabled, s, e, rc, bw, 50)
        assert n is None and errs, (s, e, rc, bw)


def test_validate_reduced_parity_with_helper():
    # Ranges MUST match the root wrapper deployment/bin/ccc-apply-conduit-config.
    import importlib.util
    import pathlib
    from importlib.machinery import SourceFileLoader

    import backend.conduit.config_validation as cv

    helper_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "deployment" / "bin" / "ccc-apply-conduit-config"
    )
    loader = SourceFileLoader("ccc_helper_parity", str(helper_path))
    spec = importlib.util.spec_from_loader("ccc_helper_parity", loader)
    helper = importlib.util.module_from_spec(spec)
    loader.exec_module(helper)
    assert (cv.RMIN_MIN, cv.RMIN_MAX) == (helper.RMIN_MIN, helper.RMIN_MAX)
    assert (cv.RMC_MIN, cv.RMC_MAX) == (helper.RMC_MIN, helper.RMC_MAX)
    assert (cv.RBW_MIN, cv.RBW_MAX) == (helper.RBW_MIN, helper.RBW_MAX)
    # And the existing MCC/BW ranges (previously comment-only parity).
    assert (cv.MCC_MIN, cv.MCC_MAX) == (helper.MCC_MIN, helper.MCC_MAX)
    assert (cv.BW_MIN, cv.DEFAULT_BW_MAX_MBPS) == (helper.BW_MIN, helper.BW_MAX)
    # Personal-clients range parity (C3).
    assert (cv.MPC_MIN, cv.MPC_MAX) == (helper.MPC_MIN, helper.MPC_MAX)


def test_validate_max_personal_clients():
    from backend.conduit.config_validation import validate_max_personal_clients as v
    assert v(0) == (0, None)
    assert v(1000) == (1000, None)
    assert v(25) == (25, None)
    assert v(-1)[0] is None
    assert v(1001)[0] is None
    assert v(True)[0] is None        # bool rejected
    assert v("5")[0] is None         # non-int rejected
