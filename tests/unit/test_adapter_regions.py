# SPDX-License-Identifier: MIT
"""
Unit tests for Regional Analytics RA-1 backend (aggregate-only top regions).

Covers the labelled-series parser (order-independent labels, scope filtering,
unlabelled/malformed skipping, duplicate summing) and get_regions aggregation
(traffic = up+down, clients = connected, zero-exclusion, sort DESC, Top-10 cap,
degrade-to-empty).
"""
from __future__ import annotations

import backend.conduit.adapter as adp
from backend.conduit.models import RegionStat

# Labelled region series in BOTH label orders; an unlabelled aggregate (ignored);
# a personal-scope line (excluded by scope=common); and a scope-absent line
# (treated as common).
METRICS = (
    "conduit_bytes_uploaded 999999\n"
    'conduit_region_bytes_uploaded{scope="common",region="SA"} 200000000\n'
    'conduit_region_bytes_downloaded{region="SA",scope="common"} 95300000\n'
    'conduit_region_connected_clients{scope="common",region="SA"} 1\n'
    'conduit_region_bytes_uploaded{scope="common",region="AE"} 100000000\n'
    'conduit_region_bytes_downloaded{scope="common",region="AE"} 82200000\n'
    'conduit_region_connected_clients{scope="common",region="AE"} 1\n'
    'conduit_region_bytes_uploaded{scope="personal",region="US"} 500000000\n'
    'conduit_region_connected_clients{scope="personal",region="US"} 3\n'
    'conduit_region_bytes_uploaded{region="MM"} 131700000\n'
    'conduit_region_connected_clients{region="MM"} 1\n'
)


# --------------------------- parser ---------------------------
def test_parse_common_only_order_independent():
    up = adp._parse_region_series(METRICS, "conduit_region_bytes_uploaded", scope="common")
    # SA, AE (common) + MM (scope-absent -> common). US is personal -> excluded.
    assert up == {"SA": 200000000, "AE": 100000000, "MM": 131700000}


def test_parse_skips_unlabelled_no_region_and_malformed():
    text = (
        "conduit_region_bytes_uploaded 5\n"                                # unlabelled
        'conduit_region_bytes_uploaded{scope="common",region="SA"} notanum\n'  # bad value
        'conduit_region_bytes_uploaded{scope="common"} 7\n'               # no region
    )
    assert adp._parse_region_series(text, "conduit_region_bytes_uploaded") == {}


def test_parse_sums_duplicate_lines():
    text = (
        'conduit_region_connected_clients{scope="common",region="SA"} 1\n'
        'conduit_region_connected_clients{scope="common",region="SA"} 2\n'
    )
    assert adp._parse_region_series(text, "conduit_region_connected_clients") == {"SA": 3}


def test_parse_excludes_other_scope():
    up = adp._parse_region_series(METRICS, "conduit_region_bytes_uploaded", scope="personal")
    assert up == {"US": 500000000}


# --------------------------- get_regions ---------------------------
async def test_get_regions_aggregates_sorts_caps(monkeypatch):
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: METRICS)
    rows = await adp.get_regions(scope="common", limit=10)
    assert [r.region for r in rows] == ["SA", "AE", "MM"]   # traffic DESC; US (personal) excluded
    assert isinstance(rows[0], RegionStat)
    assert rows[0].traffic_bytes == 200000000 + 95300000 and rows[0].clients == 1
    assert rows[2].region == "MM" and rows[2].traffic_bytes == 131700000 and rows[2].clients == 1


async def test_get_regions_excludes_zero_and_caps_top10(monkeypatch):
    text = (
        'conduit_region_bytes_uploaded{scope="common",region="ZZ"} 0\n'
        'conduit_region_connected_clients{scope="common",region="ZZ"} 0\n'
        + "".join(
            f'conduit_region_bytes_uploaded{{scope="common",region="R{i:02d}"}} {1000 - i}\n'
            for i in range(15)
        )
    )
    monkeypatch.setattr(adp, "_fetch_metrics_text", lambda _u: text)
    rows = await adp.get_regions(scope="common", limit=10)
    assert len(rows) == 10                                  # Top-10 cap
    assert all(r.region != "ZZ" for r in rows)             # zero traffic+clients excluded
    assert all(rows[i].traffic_bytes >= rows[i + 1].traffic_bytes for i in range(len(rows) - 1))


async def test_get_regions_metrics_unreachable_is_empty(monkeypatch):
    def boom(_u):
        raise OSError("down")
    monkeypatch.setattr(adp, "_fetch_metrics_text", boom)
    assert await adp.get_regions() == []
