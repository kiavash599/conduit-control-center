# SPDX-License-Identifier: MIT
"""Pure backend V2 helpers (no Linux/pid dependency) so they run under real pytest
on Windows too (finding: test_api_update global Linux skip). Covers V2 artifact
SELECTION (`_release_assets`) + semver/notes/present. Selection is convenience-only
and non-authorizing (the helper re-authorizes), but its naming must be correct."""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="backend deps (fastapi) required")
from backend.api import update as upd  # noqa: E402


def _assets(version, arch):
    data = {"assets": [
        {"name": f"ccc-{version}.manifest.json", "browser_download_url": f"https://github.com/o/r/releases/download/v{version}/ccc-{version}.manifest.json"},
        {"name": f"ccc-{version}.manifest.json.sig", "browser_download_url": f"https://github.com/o/r/releases/download/v{version}/ccc-{version}.manifest.json.sig"},
        {"name": f"ccc-{version}-aarch64.tar.gz", "browser_download_url": f"https://github.com/o/r/releases/download/v{version}/ccc-{version}-aarch64.tar.gz"},
        {"name": f"ccc-{version}-armv7l.tar.gz", "browser_download_url": f"https://github.com/o/r/releases/download/v{version}/ccc-{version}-armv7l.tar.gz"},
    ]}
    return data


def test_release_assets_selects_this_platform(monkeypatch):
    data = _assets("0.3.16", "x")
    for arch in ("aarch64", "armv7l"):
        monkeypatch.setattr(upd, "_host_platform", lambda a=arch: a)
        res = upd._release_assets(data, "0.3.16")
        assert res["artifact_url"].endswith(f"ccc-0.3.16-{arch}.tar.gz")
        assert res["manifest_url"].endswith("ccc-0.3.16.manifest.json")
        assert res["signature_url"].endswith("ccc-0.3.16.manifest.json.sig")


def test_release_assets_missing_platform_artifact_raises(monkeypatch):
    monkeypatch.setattr(upd, "_host_platform", lambda: "riscv64")   # no such asset
    with pytest.raises(RuntimeError):
        upd._release_assets(_assets("0.3.16", "x"), "0.3.16")


def test_release_assets_host_allowlist(monkeypatch):
    # a non-allowlisted download host is ignored -> asset treated as missing
    monkeypatch.setattr(upd, "_host_platform", lambda: "aarch64")
    data = {"assets": [
        {"name": "ccc-0.3.16.manifest.json", "browser_download_url": "https://evil.example.com/x"},
        {"name": "ccc-0.3.16.manifest.json.sig", "browser_download_url": "https://github.com/o/r/x.sig"},
        {"name": "ccc-0.3.16-aarch64.tar.gz", "browser_download_url": "https://github.com/o/r/a.tgz"},
    ]}
    with pytest.raises(RuntimeError):
        upd._release_assets(data, "0.3.16")


def test_semver_and_notes_and_present():
    assert upd._semver("v1.2.3") == (1, 2, 3) and upd._semver("1.2") is None
    assert upd._sanitize_notes("# H\n- a <b>x</b>") == ["H", "a x"]
    out = upd._present({"latest": "99.0.0", "recommended_core": "2.0.0", "notes_preview": []},
                       installed_core="1.0.0", reachable=True)
    assert out["update_available"] is True and out["core_warning"] is True
