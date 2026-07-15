#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/builder/extract_build_backends.py -- enumerate the PEP 517 build backends
required by the AUTHORIZED armv7 sdists, so they can be pinned in the builder image and
installed with --no-build-isolation --no-index.

Supply-chain guarantees (this file is part of the release chain, not a convenience helper):

  * Driven by the committed, hash-pinned sdist lock (requirements-armv7-build.lock). Every
    sdist on disk is hashed and matched to a lock pin BEFORE its archive is opened; the tool
    fails CLOSED on an unreadable/malformed lock, an unauthorized/extra file, a missing pin,
    duplicate content, an unrecognized extension, or a hash mismatch (an exact bijection).
  * The sdist layout must be UNAMBIGUOUS: exactly one top-level root directory, at most one
    root-level pyproject.toml, no duplicate members, no unsafe member names (absolute paths,
    traversal, backslashes, NUL, empty), and the pyproject.toml must be a REGULAR file
    (symlink/hardlink/device candidates are rejected). No "first matching member wins".
  * pyproject.toml is decoded with STRICT UTF-8 (invalid UTF-8 -> ExtractError) and parsed by
    a REAL TOML parser: tomllib on Python 3.11+, otherwise the hash-pinned tomli bootstrapped
    into an isolated connected-phase venv. There is NO regex fallback: malformed/unparsable
    TOML fails closed. Only a CLEAN, unambiguous ABSENCE of pyproject.toml selects the PEP 517
    legacy default backend (setuptools + wheel).

The UNION of requirements (one per line) is printed to stdout; feed it to release/gen_locks.py
to produce the hash-pinned requirements-build-backends.lock."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
import tarfile
import zipfile

# PEP 517 legacy default: a project declaring no build-system.requires builds with
# setuptools.build_meta:__legacy__, needing setuptools + wheel.
LEGACY_DEFAULT_REQUIRES = ("setuptools", "wheel")

_TAR_EXTS = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar")
_ZIP_EXTS = (".zip",)
_LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)=="
    r"(?P<ver>[^\s]+)(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})+)\s*$")


class ExtractError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
#  Real TOML parser (no regex fallback).                                       #
# --------------------------------------------------------------------------- #
def _load_toml(text: str) -> dict:
    """Parse TOML with a REAL parser. tomllib (3.11+) or tomli (pinned, 3.10). Neither
    available -> hard ExtractError. Malformed TOML -> ExtractError (never a silent None)."""
    mod = None
    try:
        import tomllib as mod  # type: ignore  # Python 3.11+
    except ImportError:
        try:
            import tomli as mod  # type: ignore  # pinned bootstrap on 3.10
        except ImportError as exc:
            raise ExtractError(
                "no TOML parser available: need Python 3.11+ tomllib or the pinned tomli "
                "bootstrapped via requirements-extractor-tools.lock") from exc
    try:
        return mod.loads(text)
    except Exception as exc:  # tomllib/tomli raise TOMLDecodeError
        raise ExtractError(f"malformed pyproject.toml: {exc}") from exc


def _requires_from_toml(text: str):
    """Return the build-system.requires list, or None for a CLEAN absence (legacy default)."""
    data = _load_toml(text)
    if not isinstance(data, dict):
        raise ExtractError("pyproject.toml top-level is not a table")
    bs = data.get("build-system")
    if not isinstance(bs, dict) or "requires" not in bs:
        return None  # no build-system.requires -> PEP 517 legacy default
    reqs = bs.get("requires")
    if not isinstance(reqs, list):
        raise ExtractError("build-system.requires must be a list")
    out = []
    for r in reqs:
        if not isinstance(r, str) or not r.strip():
            raise ExtractError("build-system.requires entries must be non-empty strings")
        out.append(r.strip())
    return out


# --------------------------------------------------------------------------- #
#  Sdist lock (authority) + archive-layout safety.                             #
# --------------------------------------------------------------------------- #
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_lock(text: str) -> dict:
    by_hash: dict = {}
    seen_pins: set = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LOCK_LINE.match(line)
        if not m:
            raise ExtractError(f"unrecognized lock line: {raw!r}")
        pin = f"{m.group('name')}=={m.group('ver')}"
        if pin in seen_pins:
            raise ExtractError(f"duplicate lock pin: {pin}")
        seen_pins.add(pin)
        for hx in re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group("hashes")):
            if hx in by_hash:
                raise ExtractError(f"hash {hx} pinned by both {by_hash[hx]} and {pin}")
            by_hash[hx] = pin
    if not by_hash:
        raise ExtractError("sdist lock is empty (no pins)")
    return by_hash


def _archive_ext(fn: str):
    low = fn.lower()
    for e in _TAR_EXTS:
        if low.endswith(e):
            return "tar"
    for e in _ZIP_EXTS:
        if low.endswith(e):
            return "zip"
    return None


def _check_member_names(names) -> None:
    """Reject duplicate members and unsafe names (absolute, traversal, backslash, NUL, empty)."""
    seen = set()
    for n in names:
        if n in seen:
            raise ExtractError(f"duplicate archive member: {n!r}")
        seen.add(n)
        if not n or n.startswith("/") or "\\" in n or "\x00" in n:
            raise ExtractError(f"unsafe archive member name: {n!r}")
        parts = n.strip("/").split("/")
        if any(p in ("", "..", ".") for p in parts):
            raise ExtractError(f"unsafe archive member name: {n!r}")


def _roots_and_candidates(names):
    roots = set()
    candidates = []
    for n in names:
        parts = n.strip("/").split("/")
        if parts:
            roots.add(parts[0])
        if len(parts) == 2 and parts[1] == "pyproject.toml":
            candidates.append(n)
    return roots, candidates


def _read_pyproject(path: str, kind: str):
    """Return the strictly-UTF-8-decoded pyproject.toml text, or None for a clean absence.
    Enforces the unambiguous-layout contract; fails closed otherwise."""
    if kind == "tar":
        with tarfile.open(path, "r:*") as t:
            members = t.getmembers()
            names = [m.name for m in members]
            _check_member_names(names)
            roots, candidates = _roots_and_candidates(names)
            if len(roots) != 1:
                raise ExtractError(f"sdist must have exactly one top-level root (got {sorted(roots)})")
            if len(candidates) > 1:
                raise ExtractError("multiple root-level pyproject.toml candidates")
            if not candidates:
                return None
            member = next(m for m in members if m.name == candidates[0])
            if not member.isfile():
                raise ExtractError("root pyproject.toml is not a regular file (symlink/hardlink/device)")
            data = t.extractfile(member).read()
    else:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            _check_member_names(names)
            roots, candidates = _roots_and_candidates(names)
            if len(roots) != 1:
                raise ExtractError(f"sdist must have exactly one top-level root (got {sorted(roots)})")
            if len(candidates) > 1:
                raise ExtractError("multiple root-level pyproject.toml candidates")
            if not candidates:
                return None
            info = z.getinfo(candidates[0])
            if info.is_dir():
                raise ExtractError("root pyproject.toml is a directory entry")
            mode = info.external_attr >> 16
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ExtractError("root pyproject.toml zip entry is not a regular file "
                                   "(symlink/hardlink/device/special mode rejected)")
            data = z.read(candidates[0])
    try:
        return data.decode("utf-8")   # STRICT: invalid UTF-8 raises
    except UnicodeDecodeError as exc:
        raise ExtractError(f"pyproject.toml is not valid UTF-8: {exc}") from exc


def extract(sdist_dir: str, lock_text: str) -> list:
    by_hash = _parse_lock(lock_text)
    files = sorted(f for f in os.listdir(sdist_dir)
                   if os.path.isfile(os.path.join(sdist_dir, f)))
    matched: dict = {}
    verified: list = []
    for fn in files:
        kind = _archive_ext(fn)
        if kind is None:
            raise ExtractError(f"unrecognized artifact in sdist dir (not an sdist): {fn}")
        path = os.path.join(sdist_dir, fn)
        hx = _sha256_file(path)
        if hx not in by_hash:
            raise ExtractError(f"sdist {fn} (sha256:{hx}) is not authorized by the build lock")
        if hx in matched:
            raise ExtractError(f"duplicate sdist content: {fn} and {matched[hx]} share sha256:{hx}")
        matched[hx] = fn
        verified.append((path, kind))
    missing = set(by_hash) - set(matched)
    if missing:
        raise ExtractError("lock pins have no matching sdist file: "
                           + ", ".join(sorted(by_hash[h] for h in missing)))
    reqs: set = set()
    for path, kind in verified:
        text = _read_pyproject(path, kind)
        requires = _requires_from_toml(text) if text is not None else None
        if not requires:   # clean absence OR empty -> legacy default backend
            reqs.update(LEGACY_DEFAULT_REQUIRES)
        else:
            reqs.update(r for r in requires if r)
    return sorted(reqs)


# --------------------------------------------------------------------------- #
#  tomli bootstrap lifecycle (Python 3.10 connected phase).                    #
# --------------------------------------------------------------------------- #
def _parse_extractor_in(in_text: str) -> dict:
    """Parse requirements-extractor-tools.in (name==version lines) -> {name: version}."""
    reqs = {}
    for raw in in_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)", line)
        if not m:
            raise ExtractError(f"malformed requirements-extractor-tools.in line: {raw!r}")
        name = m.group(1).lower()
        if name in reqs:
            raise ExtractError(f"duplicate .in package: {name}")
        reqs[name] = m.group(2)
    return reqs


def _lock_pins_strict(lock_text: str) -> dict:
    """Parse the closed-grammar lock (name==version + >=1 --hash) -> {name: version},
    rejecting unrecognized/unhashed/duplicate lines."""
    versions = {}
    for raw in lock_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LOCK_LINE.match(line)
        if not m:
            raise ExtractError(f"unrecognized/unhashed extractor-tools lock line: {raw!r}")
        name = m.group("name").lower()
        if name in versions:
            raise ExtractError(f"duplicate extractor-tools pin: {name}")
        versions[name] = m.group("ver")
    if not versions:
        raise ExtractError("extractor-tools lock is empty")
    return versions


def authorized_closure(lock_text: str, in_text: str) -> dict:
    """Enforce the CLOSED authorization relationship between the .in and the lock: the lock
    must pin EXACTLY the packages requested by the .in (tomli, whose closure is exactly one
    pin because it has no runtime dependencies), each at the requested version, hashed and
    non-duplicated. Rejects missing tomli, version drift, and any unauthorized extra package.
    Returns {name: version}."""
    requested = _parse_extractor_in(in_text)
    if "tomli" not in requested:
        raise ExtractError("requirements-extractor-tools.in must request tomli")
    pins = _lock_pins_strict(lock_text)
    extra = set(pins) - set(requested)
    if extra:
        raise ExtractError(f"extractor-tools lock contains unauthorized package(s): {sorted(extra)}")
    missing = set(requested) - set(pins)
    if missing:
        raise ExtractError(f"extractor-tools lock missing authorized package(s): {sorted(missing)}")
    for name, ver in requested.items():
        if pins[name] != ver:
            raise ExtractError(f"extractor-tools version drift for {name}: lock {pins[name]!r} "
                               f"!= .in {ver!r}")
    return dict(pins)


def _lock_versions(text: str) -> dict:
    versions = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LOCK_LINE.match(line)
        if not m:
            raise ExtractError(f"unrecognized extractor-tools lock line: {raw!r}")
        versions[m.group("name").lower()] = m.group("ver")
    return versions


def _default_pip_install(lock_path: str, venv_dir: str) -> None:  # pragma: no cover - real venv
    import subprocess
    subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
    pip = os.path.join(venv_dir, "bin", "pip")
    subprocess.run([pip, "install", "--require-hashes", "--no-deps", "-r", lock_path], check=True)


def _default_installed_version(venv_dir: str, dist: str) -> str:  # pragma: no cover - real venv
    import subprocess
    py = os.path.join(venv_dir, "bin", "python")
    out = subprocess.run(
        [py, "-c", f"import importlib.metadata as m; print(m.version('{dist}'))"],
        check=True, capture_output=True, text=True)
    return out.stdout.strip()


def bootstrap_extractor_venv(lock_path: str, venv_dir: str, *, in_text: str, pip_install=None,
                             installed_version=None, evidence_path: str = None) -> tuple:
    """Validate the CLOSED .in<->lock authorization, install ONLY that authorized closure into
    an ISOLATED venv with --require-hashes --no-deps, verify the installed tomli version equals
    the pin, and record evidence. Returns (tomli_version, lock_sha256). Fails closed on any
    mismatch or unauthorized package. The pip/version steps are injectable (network-free tests)."""
    with open(lock_path, "rb") as fh:
        raw = fh.read()
    lock_sha = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractError(f"extractor-tools lock is not valid UTF-8: {exc}") from exc
    closure = authorized_closure(text, in_text)   # rejects extras / drift / missing tomli
    pinned = closure["tomli"]
    (pip_install or _default_pip_install)(lock_path, venv_dir)
    got = (installed_version or (lambda v: _default_installed_version(v, "tomli")))(venv_dir)
    if got != pinned:
        raise ExtractError(f"installed tomli {got!r} != pinned {pinned!r}")
    if evidence_path:
        with open(evidence_path, "w", encoding="utf-8") as fh:
            fh.write("# External extractor-tools bootstrap evidence (ceremony only).\n")
            fh.write(f"extractor_tools_lock_sha256={lock_sha}\n")
            fh.write(f"tomli_version={got}\n")
            fh.write("authorized_closure=" + ",".join(f"{k}=={v}" for k, v in sorted(closure.items())) + "\n")
    return got, lock_sha


def _toml_parser_available() -> bool:
    try:
        import tomllib  # noqa: F401  (stdlib 3.11+)
        return True
    except ImportError:
        try:
            import tomli  # noqa: F401  (pinned bootstrap)
            return True
        except ImportError:
            return False


def _run_extract(build_lock: str, sdist_dir: str) -> int:
    with open(build_lock, "r", encoding="utf-8") as fh:
        lock_text = fh.read()
    for r in extract(sdist_dir, lock_text):
        sys.stdout.write(r + "\n")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="extract_build_backends.py",
        description="Enumerate authorized PEP 517 build backends. On Python 3.10 (no stdlib "
                    "tomllib) a hash-pinned tomli is bootstrapped into an isolated venv and "
                    "extraction is re-executed with that interpreter.")
    ap.add_argument("--sdist-dir", required=True)
    ap.add_argument("--build-lock", required=True,
                    help="committed requirements-armv7-build.lock (hash-pinned sdists; the authority)")
    ap.add_argument("--extractor-tools-in",
                    help="requirements-extractor-tools.in (requested tomli); needed to bootstrap on 3.10")
    ap.add_argument("--extractor-tools-lock",
                    help="requirements-extractor-tools.lock (hash-pinned tomli closure)")
    ap.add_argument("--bootstrap-venv", help="directory for the isolated bootstrap venv")
    ap.add_argument("--evidence", help="external bootstrap-evidence file path")
    # Sentinel: set on the re-exec so the isolated interpreter never re-bootstraps (no recursion).
    ap.add_argument("--isolated", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        if _toml_parser_available():
            return _run_extract(a.build_lock, a.sdist_dir)
        # No TOML parser in this interpreter.
        if a.isolated:
            raise ExtractError("isolated interpreter still lacks a TOML parser (tomli not installed)")
        for flag, val in (("--extractor-tools-in", a.extractor_tools_in),
                          ("--extractor-tools-lock", a.extractor_tools_lock),
                          ("--bootstrap-venv", a.bootstrap_venv),
                          ("--evidence", a.evidence)):
            if not val:
                raise ExtractError(
                    f"Python lacks stdlib tomllib (3.10); {flag} is required to bootstrap tomli")
        # Guard against evidence/output/input path collisions.
        _ev = os.path.abspath(a.evidence)
        for other in (a.build_lock, a.extractor_tools_lock, a.extractor_tools_in, a.bootstrap_venv):
            if other and os.path.abspath(other) == _ev:
                raise ExtractError(f"--evidence path collides with another input: {a.evidence!r}")
        with open(a.extractor_tools_in, "r", encoding="utf-8") as fh:
            in_text = fh.read()
        bootstrap_extractor_venv(a.extractor_tools_lock, a.bootstrap_venv,
                                 in_text=in_text, evidence_path=a.evidence)
        py = os.path.join(a.bootstrap_venv, "bin", "python")
        if not os.path.isfile(py):
            raise ExtractError(f"bootstrap venv interpreter not found: {py!r}")
        # Re-execute extraction with the isolated interpreter (tomli now importable). execv
        # REPLACES this process -> no recursion; --isolated blocks any further bootstrap.
        os.execv(py, [py, os.path.abspath(__file__),
                      "--sdist-dir", a.sdist_dir, "--build-lock", a.build_lock, "--isolated"])
        return 0  # unreachable after execv
    except ExtractError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
