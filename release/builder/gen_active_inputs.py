#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/builder/gen_active_inputs.py -- the CONTROLLED co-producer of the two v0.3.17 active
inputs (the six-entry source-build lock + the 24-entry reuse authorization), generated together
from hash-gated authoritative evidence. Nothing is fabricated or hand-edited: every hash is copied
from the verified official metadata / solution lock, and the two outputs + a full generation record
are staged as ONE atomic bundle for the Owner to review and commit atomically.

Authoritative inputs (each verified against an explicit expected SHA-256 BEFORE its content is
trusted):
  * official PyPI metadata artifact (per-version filenames, sha256, requires_python, yanked, url);
  * the committed ordered RPi2 495-tag evidence (release/builder/target-supported-tags.txt);
  * the six-sdist acquisition record (name/version/sdist-sha256 of the six proven packages);
  * the durable authoritative 30-package solution lock (requirements-armv7-solution.lock, 30 pins) --
    a DISTINCT durable input; the six-entry requirements-armv7-build.lock is a DERIVED output.

Selection: the six built packages are exactly ccc_release.WHEELHOUSE_SOURCE_BUILD_PACKAGES; the remaining
24 are reused, and each reused wheel is chosen deterministically as the candidate whose best-matching
target tag has the LOWEST index in the ordered 495 list (RPi2/pip best-first preference). Yanked /
missing / malformed / foreign-origin / requires_python-incompatible / duplicate / tied / zero
candidates all fail closed. The partition is proved disjoint, union == the exact 30 solution, and
counts 6/24/30 before anything is staged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from release import ccc_release as _R      # noqa: E402  (policy source + lock parser)
from release import oci_manifest as _ocim   # noqa: E402  (strict JSON: dup-key/NaN reject)
from release import reuse_authz as _authz   # noqa: E402

_SIX_KEYS = ("package", "filename", "sha256", "size", "url")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SDIST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*\.tar\.gz$")


class GenError(RuntimeError):
    """Raised on any generation policy/verification violation (fail closed)."""


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _require_hash(path: str, expected: str, label: str) -> str:
    got = _sha256_file(path)
    if got != expected:
        raise GenError(f"{label} sha256 mismatch: expected {expected}, got {got}")
    return got


def _rank(fn_tags, order_index):
    ranks = [order_index[t] for t in fn_tags if t in order_index]
    return min(ranks) if ranks else None


def _validate_six_record(raw_bytes: bytes, solution: dict, metadata: dict) -> dict:
    """Strictly consume the REAL six-sdist acquisition record (a JSON LIST of
    {package, filename, sha256, size, url}). Returns {norm_name: (version, sdist_sha256)} or raises
    GenError. Every field is an authorization input (nothing ignored)."""
    try:
        text = bytes(raw_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GenError(f"six-record not valid UTF-8: {exc}") from exc
    try:
        recs = _ocim.strict_json_loads(text)               # rejects duplicate keys + NaN/Infinity
    except _ocim.ManifestError as exc:
        raise GenError(f"six-record JSON rejected: {exc}") from exc
    if not isinstance(recs, list) or len(recs) != _R.WHEELHOUSE_BUILT_COUNT:
        raise GenError(f"six-record must be a JSON list of exactly {_R.WHEELHOUSE_BUILT_COUNT} records")
    approved = set(_R.WHEELHOUSE_SOURCE_BUILD_PACKAGES)
    out, seen_name, seen_file = {}, set(), set()
    for r in recs:
        if not isinstance(r, dict) or set(r) != set(_SIX_KEYS):
            raise GenError(f"six-record entry keys must be exactly {list(_SIX_KEYS)}; got {sorted(r) if isinstance(r, dict) else r!r}")
        # CANONICAL schema, not merely normalizable: security-relevant evidence must have exactly ONE
        # serialized identity. Reject non-strings and noncanonical spellings (case/underscore forms)
        # instead of silently coercing them into an approved name.
        raw_pkg = r["package"]
        if not isinstance(raw_pkg, str) or not raw_pkg:
            raise GenError(f"six-record package must be a non-empty string; got {raw_pkg!r}")
        pkg = _authz.normalize_name(raw_pkg)
        if pkg != raw_pkg:
            raise GenError(f"six-record package must be canonical: {raw_pkg!r} != canonical {pkg!r}")
        fn, sha, size, url = r["filename"], r["sha256"], r["size"], r["url"]
        if pkg not in approved:
            raise GenError(f"six-record package {pkg!r} not in the approved six")
        if pkg in seen_name:
            raise GenError(f"duplicate package in six-record: {pkg!r}")
        if fn in seen_file:
            raise GenError(f"duplicate filename in six-record: {fn!r}")
        seen_name.add(pkg)
        seen_file.add(fn)
        if not isinstance(fn, str) or not _SAFE_SDIST.match(fn) or ".." in fn:
            raise GenError(f"unsafe/malformed sdist filename: {fn!r}")
        n, ver = _R._parse_sdist_name(fn)
        if n != pkg:
            raise GenError(f"six-record filename name {n!r} != package {pkg!r}")
        if not isinstance(sha, str) or not _HEX64.match(sha):
            raise GenError(f"six-record sha256 malformed for {pkg!r}")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise GenError(f"six-record size must be a positive integer for {pkg!r}")
        v = _authz.origin_violation(url, _authz.PYPI_FILE_HOST)
        if v:
            raise GenError(f"six-record url origin: {v}")
        # cross-check the durable 30-pin solution
        if pkg not in solution or solution[pkg][0] != ver:
            raise GenError(f"six-record version for {pkg!r} != solution")
        if sha not in solution[pkg][1]:
            raise GenError(f"six-record sha256 for {pkg!r} not authorized by solution")
        # cross-check the official metadata (filename, sha256, size, exact url correspondence)
        meta = json.loads(metadata["packages"][pkg]["raw_metadata_json"])
        msd = next((u for u in meta.get("urls", [])
                    if u.get("packagetype") == "sdist" and u.get("filename") == fn), None)
        if msd is None:
            raise GenError(f"six-record sdist {fn!r} absent from official metadata")
        if (msd.get("digests") or {}).get("sha256") != sha:
            raise GenError(f"six-record sha256 for {pkg!r} != official metadata")
        if msd.get("size") != size:
            raise GenError(f"six-record size for {pkg!r} != official metadata")
        if msd.get("url") != url:
            raise GenError(f"six-record url for {pkg!r} != official metadata record")
        mv = _authz.origin_violation(msd.get("url", ""), _authz.PYPI_FILE_HOST)
        if mv:
            raise GenError(f"official sdist url origin: {mv}")
        out[pkg] = (ver, sha)
    if set(out) != approved:
        raise GenError(f"six-record package set != approved six: {sorted(set(out) ^ approved)}")
    return out


def generate(*, metadata_path, metadata_sha, tags_path, tags_sha, six_record_path, six_record_sha,
             solution_lock_path, solution_lock_sha, out_bundle):
    """Generate + validate both active inputs and stage them atomically into ``out_bundle``."""
    inputs = {
        "pypi_metadata_sha256": _require_hash(metadata_path, metadata_sha, "pypi-metadata"),
        "target_tags_sha256": _require_hash(tags_path, tags_sha, "target-tags"),
        "six_acquisition_record_sha256": _require_hash(six_record_path, six_record_sha, "six-acquisition-record"),
        "solution_lock_sha256": _require_hash(solution_lock_path, solution_lock_sha, "solution-lock"),
    }
    tags, tset, _ = _authz.load_target_tags(tags_path)
    order_index = {t: i for i, t in enumerate(tags)}
    metadata = json.loads(open(metadata_path, encoding="utf-8").read())
    solution = _R._parse_lock_pins(open(solution_lock_path, encoding="utf-8").read())  # {name:(ver,{sha})}
    if len(solution) != _R.WHEELHOUSE_TOTAL_COUNT:
        raise GenError(f"solution lock must have exactly {_R.WHEELHOUSE_TOTAL_COUNT} pins; got {len(solution)}")

    approved = set(_R.WHEELHOUSE_SOURCE_BUILD_PACKAGES)
    built_names = approved & set(solution)
    if built_names != approved:
        raise GenError(f"approved source-build packages absent from solution: {sorted(approved - set(solution))}")
    reused_names = set(solution) - approved
    if len(built_names) != _R.WHEELHOUSE_BUILT_COUNT or len(reused_names) != _R.WHEELHOUSE_REUSED_COUNT:
        raise GenError(f"partition counts wrong: built={len(built_names)} reused={len(reused_names)}")
    if built_names & reused_names or (built_names | reused_names) != set(solution):
        raise GenError("built/reused do not partition the solution exactly")

    # ---- six-entry build lock: strictly consume the REAL list-schema record (version derived from
    # the filename; sha256/size/url cross-checked against the solution AND official metadata). Never
    # fabricated. ----
    six = _validate_six_record(open(six_record_path, "rb").read(), solution, metadata)
    build_lines = [f"{name}=={six[name][0]} --hash=sha256:{six[name][1]}" for name in sorted(built_names)]

    # ---- 24-entry reuse authorization (deterministic ordered-tag selection) ----
    reuse_wheels, selections = [], []
    for name in sorted(reused_names):
        ver, _sdist_hashes = solution[name]
        meta = json.loads(metadata["packages"][name]["raw_metadata_json"])
        if _authz.normalize_name(meta["info"].get("name", "")) != name or meta["info"].get("version") != ver:
            raise GenError(f"metadata name/version mismatch for {name}=={ver}")
        candidates, rejected = [], []
        for u in meta.get("urls", []):
            if u.get("packagetype") != "bdist_wheel":
                continue
            fn = u["filename"]
            try:
                wn, wv, fn_tags = _authz._parse_wheel_filename(fn)
            except _authz.AuthzError as exc:
                rejected.append({"filename": fn, "reason": f"malformed:{exc}"})
                continue
            if wn != name or wv != ver:
                rejected.append({"filename": fn, "reason": "name/version"})
                continue
            if u.get("yanked"):
                rejected.append({"filename": fn, "reason": "yanked"})
                continue
            rp = u.get("requires_python")
            if rp is not None and not _authz.requires_python_ok(rp):
                rejected.append({"filename": fn, "reason": f"requires_python:{rp}"})
                continue
            rank = _rank(fn_tags, order_index)
            if rank is None:
                rejected.append({"filename": fn, "reason": "no-target-tag"})
                continue
            sha = (u.get("digests") or {}).get("sha256")
            if not sha:
                rejected.append({"filename": fn, "reason": "no-sha256"})
                continue
            ov = _authz.origin_violation(u.get("url", ""), _authz.PYPI_FILE_HOST)
            if ov:                                          # official origin required for reuse wheels
                rejected.append({"filename": fn, "reason": f"origin:{ov}"})
                continue
            candidates.append({"filename": fn, "rank": rank, "sha256": sha,
                               "tags": sorted(fn_tags), "requires_python": rp})
        if not candidates:
            raise GenError(f"no target-compatible official wheel for {name}=={ver} (fail closed)")
        candidates.sort(key=lambda c: (c["rank"], c["filename"]))
        best = candidates[0]
        if len(candidates) > 1 and candidates[1]["rank"] == best["rank"]:
            raise GenError(f"ambiguous tied-rank candidates for {name}=={ver}: "
                           f"{best['filename']} vs {candidates[1]['filename']}")
        reuse_wheels.append({"name": name, "version": ver, "filename": best["filename"],
                             "sha256": best["sha256"], "tags": best["tags"],
                             "requires_python": best["requires_python"]})
        selections.append({"package": name, "version": ver, "selected": best["filename"],
                           "selected_tag_rank": best["rank"],
                           "rationale": "lowest ordered-495 tag rank among target-compatible candidates",
                           "rejected": rejected})

    authz_obj = {"schema": _authz.SCHEMA_ID, "origin": "pypi", "target": dict(_authz.TARGET_PROFILE),
                 "wheels": sorted(reuse_wheels, key=lambda w: w["name"])}
    authz_bytes = (json.dumps(authz_obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
    # Self-validate the emitted authorization against the SAME mandatory target policy.
    validated = _authz.load_and_validate(authz_bytes, target_tags=tset)
    if len(validated["wheels"]) != _R.WHEELHOUSE_REUSED_COUNT:
        raise GenError("emitted reuse authorization count != 24")
    build_lock_text = ("# GENERATED by release/builder/gen_active_inputs.py -- DO NOT hand-edit.\n"
                       + "\n".join(build_lines) + "\n")

    # ---- stage BOTH outputs + generation record as ONE atomic bundle (never a repo half-state) ----
    if os.path.exists(out_bundle):
        raise GenError(f"output bundle must not pre-exist (no overwrite): {out_bundle!r}")
    parent = os.path.dirname(os.path.abspath(out_bundle)) or "."
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".gen-", dir=parent)
    try:
        bl = os.path.join(staging, "requirements-armv7-build.lock")
        ra = os.path.join(staging, "armv7-reuse-authz.json")
        with open(bl, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(build_lock_text)
        with open(ra, "wb") as fh:
            fh.write(authz_bytes)
        record = {
            "inputs": inputs,
            "outputs": {
                "requirements_armv7_build_lock_sha256": hashlib.sha256(build_lock_text.encode()).hexdigest(),
                "armv7_reuse_authz_sha256": hashlib.sha256(authz_bytes).hexdigest(),
                "armv7_reuse_authz_canonical_sha256": _authz.sha256_hex(_authz.canonical_bytes(validated)),
            },
            "partition": {"built": sorted(built_names), "reused": sorted(reused_names),
                          "counts": {"built": len(built_names), "reused": len(reused_names),
                                     "total": len(solution)}},
            "selections": selections,
        }
        with open(os.path.join(staging, "generation-record.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
        os.replace(staging, out_bundle)                    # ONE atomic publish of the staging bundle
    except BaseException:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen_active_inputs.py",
                                 description="Controlled co-producer of the two v0.3.17 active inputs.")
    ap.add_argument("--pypi-metadata", required=True)
    ap.add_argument("--pypi-metadata-sha256", required=True)
    ap.add_argument("--target-tags", required=True)
    ap.add_argument("--target-tags-sha256", required=True)
    ap.add_argument("--six-acquisition-record", required=True)
    ap.add_argument("--six-acquisition-record-sha256", required=True)
    ap.add_argument("--solution-lock", required=True)
    ap.add_argument("--solution-lock-sha256", required=True)
    ap.add_argument("--out-bundle", required=True, help="staging bundle dir (must NOT pre-exist)")
    a = ap.parse_args(argv)
    try:
        rec = generate(metadata_path=a.pypi_metadata, metadata_sha=a.pypi_metadata_sha256,
                       tags_path=a.target_tags, tags_sha=a.target_tags_sha256,
                       six_record_path=a.six_acquisition_record, six_record_sha=a.six_acquisition_record_sha256,
                       solution_lock_path=a.solution_lock, solution_lock_sha=a.solution_lock_sha256,
                       out_bundle=a.out_bundle)
    except (GenError, _authz.AuthzError, _R.ReleaseError, KeyError, OSError) as exc:
        sys.stderr.write(f"ERROR: active-input generation failed (fail closed): {exc}\n")
        return 1
    print(f"staged active inputs -> {a.out_bundle} "
          f"(built={rec['partition']['counts']['built']} reused={rec['partition']['counts']['reused']})")
    print("Review generation-record.json, then commit both files ATOMICALLY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
