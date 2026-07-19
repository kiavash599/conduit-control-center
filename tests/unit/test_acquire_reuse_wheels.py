# SPDX-License-Identifier: MIT
"""Controlled connected acquisition (release/builder/acquire_reuse_wheels): mocked network,
exact-artifact selection, strict HTTPS-origin policy, the EXACTLY-24 count gate, live
Requires-Python drift, final-redirect re-check, ONE atomic bundle, and every fail-closed path.
Also covers the direct CLI import bootstrap from a neutral cwd. No real network is used."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

from release import ccc_release as R
from release import reuse_authz as RA

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ACQ_PATH = _ROOT / "release" / "builder" / "acquire_reuse_wheels.py"
_spec = importlib.util.spec_from_file_location("acq_mod", str(_ACQ_PATH))
ACQ = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ACQ)
_TT = set((_ROOT / "release" / "builder" / "target-supported-tags.txt").read_text(encoding="utf-8").split())

_N = R.V0317_REUSED_COUNT   # exactly 24 authorized reuse wheels
_RP = ">=3.9"


def _wheel(i):
    """One synthetic authorized reuse wheel (universal py3-none-any tag, distinct bytes/sha)."""
    name = "reusepkg%02d" % i
    ver = "1.0"
    fn = f"{name}-{ver}-py3-none-any.whl"
    body = b"OFFICIAL-WHEEL-BYTES-" + name.encode()
    sha = hashlib.sha256(body).hexdigest()
    url = f"https://{ACQ.FILE_HOST}/packages/{i:02d}/{fn}"
    return {"name": name, "version": ver, "filename": fn, "sha256": sha, "tags": ["py3-none-any"],
            "requires_python": _RP, "_body": body, "_url": url}


_WHEELS = [_wheel(i) for i in range(1, _N + 1)]


def _authz_bytes(wheels=_WHEELS):
    return json.dumps({
        "schema": RA.SCHEMA_ID, "origin": "pypi", "target": dict(RA.TARGET_PROFILE),
        "wheels": [{k: w[k] for k in ("name", "version", "filename", "sha256", "tags", "requires_python")}
                   for w in wheels]}).encode()


class MockFetcher:
    """Serves correct official metadata + bytes for every wheel; ``tamper`` breaks ONE named field
    of ONE target wheel so a single fail-closed path can be exercised against a full 24-wheel authz."""

    def __init__(self, wheels=_WHEELS, *, tamper=None, target="reusepkg01"):
        self._by_name = {w["name"]: w for w in wheels}
        self._by_fn = {w["filename"]: w for w in wheels}
        self.tamper, self.target = tamper or {}, target

    def _t(self, w, key, default):
        return self.tamper[key] if (w["name"] == self.target and key in self.tamper) else default

    def json(self, url):
        # url: https://pypi.org/pypi/<name>/<ver>/json
        name = url.rstrip("/").split("/")[-3]
        w = self._by_name[name]
        return {"info": {"name": self._t(w, "info_name", w["name"]),
                         "version": self._t(w, "info_version", w["version"])},
                "urls": [{"filename": w["filename"], "packagetype": self._t(w, "packagetype", "bdist_wheel"),
                          "yanked": self._t(w, "yanked", False),
                          "digests": {"sha256": self._t(w, "meta_sha", w["sha256"])},
                          "requires_python": self._t(w, "live_rp", w["requires_python"]),
                          "url": self._t(w, "url", w["_url"])}]}

    def get(self, url):
        w = self._by_fn[url.rstrip("/").split("/")[-1]]
        final = self._t(w, "final_url", url)         # simulate a redirect to a different final URL
        body = self._t(w, "body", w["_body"])
        return final, body


def test_cli_imports_from_neutral_cwd(tmp_path):
    # regression: the documented CLI must import `release` when run from an unrelated cwd.
    r = subprocess.run([sys.executable, str(_ACQ_PATH), "--help"], cwd=str(tmp_path),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "acquire" in (r.stdout + r.stderr).lower()


def test_acquire_success_atomic_bundle_exactly_24(tmp_path):
    bundle = tmp_path / "bundle"
    ev = ACQ.acquire(_authz_bytes(), str(bundle), fetcher=MockFetcher(), target_tags=_TT)
    assert ev["count"] == _N
    assert sorted(p.name for p in bundle.iterdir()) == ["acquisition-record.json", "wheels"]
    assert len(list((bundle / "wheels").iterdir())) == _N
    for w in _WHEELS:
        assert (bundle / "wheels" / w["filename"]).read_bytes() == w["_body"]


@pytest.mark.parametrize("n", [_N - 1, _N + 1])
def test_acquire_requires_exactly_24(tmp_path, n):
    wheels = ([_wheel(i) for i in range(1, n + 1)] if n <= _N
              else _WHEELS + [_wheel(i) for i in range(_N + 1, n + 1)])
    bundle = tmp_path / "bundle"
    with pytest.raises(ACQ.AcquireError) as ei:
        ACQ.acquire(_authz_bytes(wheels), str(bundle), fetcher=MockFetcher(wheels), target_tags=_TT)
    assert "exactly" in str(ei.value) and str(_N) in str(ei.value)
    assert not bundle.exists()


def test_acquire_refuses_preexisting_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(ACQ.AcquireError):
        ACQ.acquire(_authz_bytes(), str(bundle), fetcher=MockFetcher(), target_tags=_TT)


@pytest.mark.parametrize("tamper,needle", [
    (dict(body=b"TAMPERED"), "sha256"),
    (dict(yanked=True), "yanked"),
    (dict(meta_sha="0" * 64), "metadata sha256"),
    (dict(packagetype="sdist"), "not a wheel"),
    (dict(info_version="9.9"), "name/version mismatch"),
    (dict(live_rp=">=3.11"), "live requires_python"),          # upstream RP drift fails closed
    (dict(url="http://" + ACQ.FILE_HOST + "/x/reusepkg01-1.0-py3-none-any.whl"), "non-HTTPS"),
    (dict(url="https://evil.example.com/x/reusepkg01-1.0-py3-none-any.whl"), "host not permitted"),
    (dict(final_url="https://evil.example.com/x/reusepkg01-1.0-py3-none-any.whl"), "host not permitted"),
])
def test_acquire_fail_closed_no_publish(tmp_path, tamper, needle):
    bundle = tmp_path / "bundle"
    with pytest.raises(ACQ.AcquireError) as ei:
        ACQ.acquire(_authz_bytes(), str(bundle), fetcher=MockFetcher(tamper=tamper), target_tags=_TT)
    assert needle in str(ei.value)
    assert not bundle.exists()                       # nothing published on failure


def test_real_fetcher_rejects_off_policy():
    f = ACQ.Fetcher()
    for bad in ("http://pypi.org/pypi/idna/3.18/json", "https://evil.example.com/pypi/x/json",
                "https://user:pw@pypi.org/pypi/x/json", "https://pypi.org:8443/pypi/x/json"):
        with pytest.raises(ACQ.AcquireError):
            f.json(bad)
    with pytest.raises(ACQ.AcquireError):
        f.get("https://evil.example.com/x.whl")
