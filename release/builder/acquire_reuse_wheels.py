#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/builder/acquire_reuse_wheels.py -- CONTROLLED connected acquisition of the exact set
of official PyPI wheels authorized for reuse (v0.3.17 dual-origin).

Runs during the connected, pre-tag Owner phase -- NEVER inside the offline builder. It consumes
the committed reuse authorization (validated against the committed RPi2 target-tag set), selects
the EXACT authorized artifact (filename + sha256; never a dependency resolution), restricts all
traffic to official PyPI over a strict HTTPS-origin policy, downloads to a FRESH sibling staging
directory with no shared pip/index cache (raw HTTP; every byte re-hashed against authorization),
and publishes ONE atomic bundle:

    <bundle>/
      wheels/              exactly the 24 authorized wheel files (filename-addressed, hash-verified)
      acquisition-record.json   acquisition evidence (input hashes, per-wheel records)

The complete bundle is built + validated in a sibling staging dir and published with ONE atomic
rename. The final bundle must not already exist (no overwrite); no foreign files; failure removes
staging and publishes nothing; there is never a published store without its evidence (both live in
the one atomically-renamed bundle). Network is behind a small injectable ``Fetcher`` for tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

# repo root = parent of release/ = three levels up from release/builder/<this file>.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from release import ccc_release as _R  # noqa: E402  (V0317_REUSED_COUNT policy)
from release import reuse_authz as _authz  # noqa: E402

METADATA_HOST = _authz.PYPI_METADATA_HOST
FILE_HOST = _authz.PYPI_FILE_HOST
WHEELS_SUBDIR = "wheels"
EVIDENCE_NAME = "acquisition-record.json"


class AcquireError(RuntimeError):
    """Raised on any acquisition policy/verification violation (fail closed)."""


def check_origin(url: str, allowed_host: str) -> None:
    """Strict HTTPS-origin policy (shared with generation via reuse_authz.origin_violation):
    https only, EXACT host, no embedded credentials, default port only. Fails closed."""
    v = _authz.origin_violation(url, allowed_host)
    if v:
        raise AcquireError(f"URL origin rejected: {v}")


class Fetcher:
    """Default HTTPS fetcher restricted to the approved official hosts. Injectable for tests."""

    def json(self, url: str) -> dict:
        check_origin(url, METADATA_HOST)
        final, data = self._get(url)
        check_origin(final, METADATA_HOST)
        return json.loads(data.decode("utf-8"))

    def get(self, url: str):
        check_origin(url, FILE_HOST)
        final, data = self._get(url)
        check_origin(final, FILE_HOST)
        return final, data

    def _get(self, url: str):
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "ccc-reuse-acquire/1.0",
                                                   "Cache-Control": "no-cache", "Pragma": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 (origin-checked above)
            return r.geturl(), r.read()


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def acquire(authz_bytes: bytes, bundle_dir: str, *, fetcher: Fetcher, target_tags,
            input_hashes: dict = None) -> dict:
    """Acquire + verify the exact authorized wheels and publish ONE atomic bundle. Returns the
    evidence dict. Raises AcquireError on any violation; publishes nothing on failure."""
    authz = _authz.load_and_validate(authz_bytes, target_tags=target_tags)
    if len(authz["wheels"]) != _R.V0317_REUSED_COUNT:
        raise AcquireError(f"reuse authorization must contain exactly {_R.V0317_REUSED_COUNT} wheels; "
                           f"got {len(authz['wheels'])}")
    if os.path.exists(bundle_dir):
        raise AcquireError(f"bundle dir must not pre-exist (no overwrite): {bundle_dir!r}")
    import tempfile
    parent = os.path.dirname(os.path.abspath(bundle_dir)) or "."
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".acq-", dir=parent)
    try:
        wheels_dir = os.path.join(staging, WHEELS_SUBDIR)
        os.makedirs(wheels_dir)
        records, staged = [], set()
        for w in authz["wheels"]:
            name, ver, fn, want = w["name"], w["version"], w["filename"], w["sha256"]
            meta = fetcher.json(f"https://{METADATA_HOST}/pypi/{name}/{ver}/json")
            info = meta.get("info") or {}
            if _authz.normalize_name(info.get("name", "")) != name or info.get("version") != ver:
                raise AcquireError(f"PyPI metadata name/version mismatch for {name}=={ver}")
            entry = next((f for f in meta.get("urls", []) if f.get("filename") == fn), None)
            if entry is None:
                raise AcquireError(f"authorized wheel absent from official metadata: {fn!r}")
            if entry.get("packagetype") != "bdist_wheel":
                raise AcquireError(f"authorized artifact is not a wheel: {fn!r}")
            if entry.get("yanked"):
                raise AcquireError(f"authorized wheel is yanked upstream (fail closed): {fn!r}")
            if (entry.get("digests") or {}).get("sha256") != want:
                raise AcquireError(f"official metadata sha256 != authorization for {fn!r}")
            # Live Requires-Python must EXACTLY equal the committed authorization value (any upstream
            # drift fails closed), and that value must admit Python 3.10.12 (standard PEP 440).
            live_rp = entry.get("requires_python")
            if live_rp != w.get("requires_python"):
                raise AcquireError(f"live requires_python != authorization for {fn!r}: "
                                   f"{live_rp!r} != {w.get('requires_python')!r}")
            if live_rp is not None and not _authz.requires_python_ok(live_rp):
                raise AcquireError(f"requires_python {live_rp!r} does not admit Python 3.10.12 for {fn!r}")
            check_origin(entry.get("url", ""), FILE_HOST)          # core policy (independent of fetcher)
            final, data = fetcher.get(entry["url"])
            check_origin(final, FILE_HOST)                         # final redirect re-checked in the CORE
            got = _sha256(data)
            if got != want:
                raise AcquireError(f"downloaded bytes sha256 {got} != authorization {want} for {fn!r}")
            if os.path.basename(fn) != fn or fn in staged:
                raise AcquireError(f"unsafe or duplicate filename during staging: {fn!r}")
            staged.add(fn)
            with open(os.path.join(wheels_dir, fn), "wb") as fh:   # fresh regular file (no symlink)
                fh.write(data)
            records.append({"name": name, "version": ver, "filename": fn, "sha256": want,
                            "size": len(data), "source_url": final})
        if len(staged) != len(authz["wheels"]):
            raise AcquireError("staged wheel count != authorization count")
        extra = set(os.listdir(wheels_dir)) - staged
        if extra:
            raise AcquireError(f"foreign content in staged wheels: {sorted(extra)}")
        import time
        evidence = {
            "acquired_at_utc": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
            "count": len(records),
            "inputs": dict(input_hashes or {},
                           reuse_authz_sha256=_authz.sha256_hex(_authz.canonical_bytes(authz))),
            "wheels": sorted(records, key=lambda r: r["filename"]),
        }
        with open(os.path.join(staging, EVIDENCE_NAME), "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, indent=2, sort_keys=True)
        os.replace(staging, bundle_dir)                            # ONE atomic publish of the bundle
    except BaseException:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)                 # complete cleanup; publish nothing
        raise
    return evidence


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="acquire_reuse_wheels.py",
                                 description="Controlled connected acquisition of authorized reuse wheels.")
    ap.add_argument("--reuse-authz", required=True, help="committed reuse authorization JSON")
    ap.add_argument("--target-tags", required=True, help="committed release/builder/target-supported-tags.txt")
    ap.add_argument("--bundle", required=True, help="acquisition bundle dir (must NOT pre-exist)")
    a = ap.parse_args(argv)
    try:
        _tags, tset, tsha = _authz.load_target_tags(a.target_tags)
        with open(a.reuse_authz, "rb") as fh:
            authz_bytes = fh.read()
        ev = acquire(authz_bytes, a.bundle, fetcher=Fetcher(), target_tags=tset,
                     input_hashes={"target_tags_sha256": tsha})
    except (AcquireError, _authz.AuthzError, OSError) as exc:
        sys.stderr.write(f"ERROR: acquisition failed (fail closed): {exc}\n")
        return 1
    print(f"acquired {ev['count']} reuse wheels -> {a.bundle}/{WHEELS_SUBDIR} (+ {EVIDENCE_NAME})")
    print(f"reuse_authz_sha256={ev['inputs']['reuse_authz_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
