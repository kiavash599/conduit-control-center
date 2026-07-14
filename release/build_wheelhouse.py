#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/build_wheelhouse.py -- deterministic armv7 wheelhouse + provenance builder
(ADR-0003 Amendment A1). Consumes ONLY sdists authorized by
`requirements-armv7-build.lock`, verifies each sdist hash BEFORE build, builds each
in the recorded builder environment, records the sdist->wheel mapping, writes
SHA256SUMS, and emits the strict provenance JSON accepted by
`ccc_release._validate_provenance`. Refuses missing / extra / duplicate / ambiguous
build outputs. The BUILD step is injectable (`build_fn`) so the tool is fully
testable; the eventual real build remains Owner-gated (this module never runs it
automatically)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

try:
    from release import ccc_release as _R
except Exception:  # noqa: BLE001 - allow running as a script from repo root
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from release import ccc_release as _R

ReleaseError = _R.ReleaseError


def _default_build_fn(sdist_path, sdist_name, name, version):
    """Real builder: `pip wheel` the sdist with NO deps / NO build isolation / NO
    index (offline, from the authorized sdist only). Returns (wheel_filename, bytes).
    Runs ONLY when invoked as a real build (never in tests)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cp = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
             "--no-index", "-w", td, sdist_path],
            capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            raise ReleaseError(f"pip wheel failed for {sdist_name}: {cp.stderr.strip()}")
        whls = [f for f in os.listdir(td) if f.endswith(".whl")]
        if len(whls) != 1:
            raise ReleaseError(f"ambiguous build output for {sdist_name}: {whls}")
        with open(os.path.join(td, whls[0]), "rb") as fh:
            return whls[0], fh.read()


def build_wheelhouse(*, build_lock_path: str, sdist_dir: str, out_dir: str,
                     builder_identity: str, builder_image_digest: str, build_fn=None) -> dict:
    if not builder_identity or not _R._is_oci_digest(builder_image_digest):
        raise ReleaseError("builder identity required; image_digest must be 'sha256:<64 lowercase hex>'")
    build_fn = build_fn or _default_build_fn
    with open(build_lock_path, encoding="utf-8") as fh:
        build_lock_text = fh.read()
    build_pins = _R._parse_lock_pins(build_lock_text)
    if not build_pins:
        raise ReleaseError("empty/invalid armv7 build-input lock")

    # Collect + authorize sdists (verify each hash BEFORE build).
    sdists: dict = {}
    for fn in sorted(os.listdir(sdist_dir)):
        path = os.path.join(sdist_dir, fn)
        if not os.path.isfile(path):
            continue
        name, ver = _R._parse_sdist_name(fn)
        if name is None:
            raise ReleaseError(f"unrecognised file in sdist dir (not an sdist): {fn!r}")
        with open(path, "rb") as f:
            sha = _R.sha256_hex(f.read())
        if name in sdists:
            raise ReleaseError(f"duplicate sdist for package {name!r}")
        sdists[name] = (ver, fn, path, sha)
    if set(sdists) != set(build_pins):
        missing = sorted(set(build_pins) - set(sdists))
        extra = sorted(set(sdists) - set(build_pins))
        raise ReleaseError(f"sdists != build lock (missing={missing}, extra={extra})")
    for name, (ver, fn, _p, sha) in sdists.items():
        bver, bhashes = build_pins[name]
        if ver != bver:
            raise ReleaseError(f"sdist version mismatch for {name!r}: {ver} != {bver}")
        if sha not in bhashes:
            raise ReleaseError(f"sdist hash not authorized by build lock for {name!r}")

    # Build each authorized sdist -> exactly one wheel of the same name+version.
    os.makedirs(out_dir, exist_ok=True)
    wheels: list = []
    seen_wheel: set = set()
    for name, (ver, sfn, spath, ssha) in sorted(sdists.items()):
        wname, wbytes = build_fn(spath, sfn, name, ver)
        if not isinstance(wname, str) or not wname.endswith(".whl") or not isinstance(wbytes, (bytes, bytearray)):
            raise ReleaseError(f"invalid build output for {sfn!r}")
        wn, wv = _R._parse_wheel_name(wname)
        if wn != name or wv != ver:
            raise ReleaseError(f"build output {wname!r} does not match {name}=={ver}")
        if wname in seen_wheel:
            raise ReleaseError(f"duplicate/ambiguous build output: {wname!r}")
        seen_wheel.add(wname)
        with open(os.path.join(out_dir, wname), "wb") as fh:
            fh.write(wbytes)
        wheels.append({"sdist_name": sfn, "sdist_sha256": ssha,
                       "wheel_filename": wname, "wheel_sha256": _R.sha256_hex(bytes(wbytes))})

    wheels.sort(key=lambda w: w["wheel_filename"])
    with open(os.path.join(out_dir, "SHA256SUMS"), "w", encoding="utf-8") as fh:
        for w in wheels:
            fh.write("%s  %s\n" % (w["wheel_sha256"], w["wheel_filename"]))

    members = _R._wheelhouse_members(out_dir)
    bundle_sha = _R.sha256_hex(_R.pack_tree(members))
    provenance = {
        "builder": {"identity": builder_identity, "image_digest": builder_image_digest},
        "bundle": {"sha256": bundle_sha},
        "wheels": wheels,
    }
    # Self-check: the emitted provenance MUST pass the strict device-side validator.
    _R._validate_provenance(provenance, members, bundle_sha, build_lock_text)
    return {"provenance": provenance, "bundle_sha256": bundle_sha, "wheelhouse_dir": out_dir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="build_wheelhouse.py")
    ap.add_argument("--build-lock", required=True)
    ap.add_argument("--sdist-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--builder-identity", required=True)
    ap.add_argument("--builder-image-digest", required=True)
    ap.add_argument("--provenance-out", required=True)
    a = ap.parse_args(argv)
    res = build_wheelhouse(build_lock_path=a.build_lock, sdist_dir=a.sdist_dir, out_dir=a.out_dir,
                           builder_identity=a.builder_identity, builder_image_digest=a.builder_image_digest)
    with open(a.provenance_out, "w", encoding="utf-8") as fh:
        json.dump(res["provenance"], fh)
    print(f"wheelhouse: {a.out_dir}  bundle_sha256={res['bundle_sha256']}  provenance={a.provenance_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
