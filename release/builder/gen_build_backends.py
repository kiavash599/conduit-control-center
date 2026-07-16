#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/builder/gen_build_backends.py -- connected-phase generation gate for the
backend source-allowlist (finding: authorized backend-sdist allowlist, constraint 3).

Establishes, using the COMPLETE effective compatibility-tag set of the exact target
interpreter/platform (pip's own tag matching -- never a hand-enumerated partial list),
that every allowlisted backend has NO compatible official wheel, and records the target
tags + result to EXTERNAL ceremony evidence. A compatible official wheel for an allowlisted
package is ALLOWLIST DRIFT and fails generation (the package must be installed as a wheel).

The pip calls are injectable so the gate is unit-testable without a network; the Owner runs
the real gate during the controlled input-generation ceremony. Standard-library only."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile

_LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==(?P<ver>[^\s]+)"
    r"(?:\s+--hash=sha256:[0-9a-f]{64})+\s*$")


class GenError(RuntimeError):
    pass


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_allowlist(text: str) -> list:
    names, seen = [], set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", line):
            raise GenError(f"malformed allowlist entry: {raw!r}")
        n = _normalize(line)
        if line != n:
            raise GenError(f"non-canonical allowlist entry {line!r} (must be PEP 503-normalized: {n!r})")
        if n in seen:
            raise GenError(f"duplicate allowlist entry: {n}")
        seen.add(n)
        names.append(n)
    if not names:
        raise GenError("source-allowlist is empty")
    return names


def lock_versions(text: str) -> dict:
    versions = {}
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = _LOCK_LINE.match(s)
        if not m:
            raise GenError(f"unrecognized backend lock line: {raw!r}")
        versions[_normalize(m.group("name"))] = m.group("ver")
    if not versions:
        raise GenError("backend lock has no pins")
    return versions


def _default_compatible_tags() -> list:  # pragma: no cover - real pip
    out = subprocess.run([sys.executable, "-m", "pip", "debug", "--verbose"],
                         capture_output=True, text=True, check=False).stdout
    return _parse_compatible_tags(out)


def _parse_compatible_tags(pip_debug_output: str) -> list:
    """Parse the full 'Compatible tags:' block emitted by `pip debug --verbose`. This is
    pip's own COMPLETE effective tag set for THIS interpreter/platform."""
    tags, capture = [], False
    for line in pip_debug_output.splitlines():
        if "Compatible tags:" in line:
            capture = True
            continue
        if capture:
            if not line.strip():
                break
            if line[:1] not in (" ", "\t"):   # dedent -> end of the block
                break
            tok = line.strip()
            if tok.startswith("..."):          # "... and N more" trailer
                continue
            tags.append(tok.split()[0])
    if not tags:
        raise GenError("could not determine compatible tags from `pip debug --verbose`")
    return tags


OFFICIAL_PYPI_INDEX = "https://pypi.org/simple/"
_SDIST_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".zip")


def _pip_probe_cmd(name: str, version: str, dest: str, *, only_binary: bool) -> list:
    """Build the isolated, official-PyPI, cache-disabled pip download probe command.
    `--isolated` makes pip IGNORE ambient configuration/environment, `--index-url` pins the
    OFFICIAL index (a custom/ambient index cannot silently answer), and `--no-cache-dir`
    forces a LIVE fetch (a cached index response/artifact cannot pose as index reachability)."""
    # --no-cache-dir forces a LIVE fetch from the official index: a cached simple-index
    # response or cached artifact must never let the sdist fallback answer offline and turn an
    # index/network failure into false no-wheel evidence.
    return [sys.executable, "-m", "pip", "download", "--isolated", "--no-cache-dir", "--no-deps",
            "--index-url", OFFICIAL_PYPI_INDEX,
            ("--only-binary=:all:" if only_binary else "--no-binary=:all:"),
            f"{name}=={version}", "-d", dest]


def _run_pip_probe(name: str, version: str, *, only_binary: bool) -> bool:  # pragma: no cover - real pip
    with tempfile.TemporaryDirectory() as d:
        rc = subprocess.run(_pip_probe_cmd(name, version, d, only_binary=only_binary),
                            capture_output=True, text=True, check=False).returncode
        got = os.listdir(d)
        if only_binary:
            return rc == 0 and any(f.lower().endswith(".whl") for f in got)
        return rc == 0 and any(f.lower().endswith(_SDIST_SUFFIXES) for f in got)


def probe_target_wheel(name: str, version: str, *, wheel_probe=None, sdist_probe=None) -> str:
    """Fail-closed, tri-state target-wheel probe. Returns:
      * "wheel"    -- a compatible official wheel exists (=> allowlist drift);
      * "no-wheel" -- POSITIVELY established: no wheel, but the exact sdist resolves from the
                      SAME official index (proving index reachability + package/version exist);
      * (raises GenError) -- indeterminate: neither wheel nor sdist resolved -> probe/index/
                      network/TLS/tool error. Never silently reported as "no-wheel"."""
    wprobe = wheel_probe or (lambda n, v: _run_pip_probe(n, v, only_binary=True))
    sprobe = sdist_probe or (lambda n, v: _run_pip_probe(n, v, only_binary=False))
    if wprobe(name, version):
        return "wheel"
    if sprobe(name, version):
        return "no-wheel"
    raise GenError(f"indeterminate target-wheel probe for {name}=={version}: neither a compatible "
                   "wheel nor the exact sdist resolved from the official index -- treat as a "
                   "probe/index/network/TLS/tool error, NEVER as no-wheel")


def assert_allowlist_no_drift(allowlist: list, versions: dict, *, evidence_path: str,
                              tags_fn=None, probe_fn=None) -> dict:
    """Prove, fail-closed, that every allowlisted backend has NO compatible official wheel
    (positively established via the tri-state probe). Any drift OR indeterminate probe raises
    and NO successful evidence is written. Records the positive basis for each no-wheel result."""
    tags = (tags_fn or _default_compatible_tags)()
    probe = probe_fn or probe_target_wheel
    results = {}
    for name in allowlist:
        if name not in versions:
            raise GenError(f"allowlisted backend {name!r} is not pinned in the backend lock")
        ver = versions[name]
        state = probe(name, ver)                       # "wheel" | "no-wheel" | raises
        if state == "wheel":
            raise GenError(f"allowlist drift: a compatible official wheel EXISTS for "
                           f"{name}=={ver} for the target -- de-allowlist it (install as a wheel)")
        if state != "no-wheel":
            raise GenError(f"unexpected probe state {state!r} for {name}=={ver}")
        results[name] = ver
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write("# External backend source-allowlist target-wheel-availability evidence.\n")
        fh.write("# (ceremony only; NOT part of any signed artifact)\n")
        fh.write("official_index=" + OFFICIAL_PYPI_INDEX + "\n")
        fh.write("probe_cache=disabled\n")
        fh.write("target_compatible_tags=" + ",".join(tags) + "\n")
        fh.write("target_compatible_tag_count=%d\n" % len(tags))
        for name in sorted(results):
            fh.write(f"no_compatible_wheel_confirmed_via_sdist={name}=={results[name]}\n")
    return results


# --------------------------------------------------------------------------- #
#  Gap 2: bind generation to the ACTUAL mixed distribution directory + lock.   #
# --------------------------------------------------------------------------- #
_LOCK_FULL = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==(?P<ver>[^\s]+)"
    r"(?P<h>(?:\s+--hash=sha256:[0-9a-f]{64})+)\s*$")


def _lock_full(text: str) -> dict:
    pins = {}
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = _LOCK_FULL.match(s)
        if not m:
            raise GenError(f"unrecognized backend lock line: {raw!r}")
        n = _normalize(m.group("name"))
        if n in pins:
            raise GenError(f"duplicate backend lock pin: {n}")
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group("h")))
        pins[n] = (m.group("ver"), hashes)
    if not pins:
        raise GenError("backend lock has no pins")
    return pins


def _dist_type(fn: str):
    """Return (normalized_name, version, kind) for a distribution file; kind in {wheel,sdist}."""
    low = fn.lower()
    if low.endswith(".whl"):
        parts = fn[:-4].split("-")
        if len(parts) >= 2:
            return _normalize(parts[0]), parts[1], "wheel"
        return None, None, None
    for suf in _SDIST_SUFFIXES:
        if low.endswith(suf):
            stem = fn[:-len(suf)]
            m = re.match(r"^(.+?)-(\d[^-]*)$", stem)
            if m:
                return _normalize(m.group(1)), m.group(2), "sdist"
    return None, None, None


def verify_mixed_closure(dist_dir: str, lock_text: str, allowlist: list) -> None:
    """Enforce an EXACT bijection between the on-disk distribution directory and the
    authoritative lock, with allowlist-driven file-type policy (finding: gap 2):
      * allowlisted names must be SDISTs; all other closure entries must be WHEELs;
      * every file's sha256 appears in the lock for its exact name==version;
      * every lock entry is represented by exactly one file of the expected type;
      * no extra, missing, duplicate, or unauthorized distribution."""
    pins = _lock_full(lock_text)
    allow = set(allowlist)
    unpinned = [a for a in allow if a not in pins]
    if unpinned:
        raise GenError(f"allowlisted backend(s) not pinned in the lock: {sorted(unpinned)}")
    files = [f for f in sorted(os.listdir(dist_dir)) if os.path.isfile(os.path.join(dist_dir, f))]
    seen = {}
    for fn in files:
        name, ver, kind = _dist_type(fn)
        if name is None:
            raise GenError(f"unrecognized distribution file (not a wheel/sdist): {fn}")
        if name in seen:
            raise GenError(f"duplicate distribution for {name!r}: {fn} and {seen[name]}")
        if name not in pins:
            raise GenError(f"unauthorized distribution not present in the lock: {fn}")
        pver, phashes = pins[name]
        if ver != pver:
            raise GenError(f"distribution version mismatch for {name!r}: {ver} != {pver} ({fn})")
        with open(os.path.join(dist_dir, fn), "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        if sha not in phashes:
            raise GenError(f"distribution hash for {fn} is not pinned in the lock")
        expected = "sdist" if name in allow else "wheel"
        if kind != expected:
            raise GenError(f"{name!r} must be a {expected} per the allowlist policy; got {kind} ({fn})")
        seen[name] = fn
    missing = set(pins) - set(seen)
    if missing:
        raise GenError(f"lock entries with no matching distribution file: {sorted(missing)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen_build_backends.py",
                                 description="Backend source-allowlist target-wheel drift gate.")
    ap.add_argument("--allowlist", required=True)
    ap.add_argument("--lock", required=True, help="the generated requirements-build-backends.lock")
    ap.add_argument("--dist-dir", required=True,
                    help="the ACTUAL mixed distribution directory the lock was generated from")
    ap.add_argument("--evidence", required=True, help="external evidence output path")
    a = ap.parse_args(argv)
    try:
        with open(a.allowlist, "r", encoding="utf-8") as fh:
            allow = parse_allowlist(fh.read())
        with open(a.lock, "r", encoding="utf-8") as fh:
            lock_text = fh.read()
        versions = lock_versions(lock_text)
        # (1) bind the lock to the ACTUAL dist directory (exact bijection + file-type policy)
        verify_mixed_closure(a.dist_dir, lock_text, allow)
        # (2) fail-closed target-wheel drift gate for the allowlisted sdists
        assert_allowlist_no_drift(allow, versions, evidence_path=a.evidence)
    except (OSError, GenError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
