#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/reuse_authz.py -- strict, canonical authorization for the EXACT set of official
PyPI wheels reused (not source-built) in the armv7 wheelhouse (v0.3.17 reuse-first hybrid).

This is a RICH supply-chain authorization record and is deliberately SEPARATE from the pip
installation lock (`requirements-armv7.lock`): pip grammar carries no artifact identity, so
reuse authorization lives here. Each record binds the exact artifact identity so a different
file, tag, name, version, or origin cannot be substituted:

  { "name", "version", "filename", "sha256", "tags"[], "requires_python"(str|null) }

Top level: { "schema":"ccc-armv7-reuse-authz/1", "origin":"pypi",
             "target":{"python","platform","glibc"}, "wheels":[ ... ] }

Fail-closed guarantees (all reject -> ValueError):
  * strict JSON: duplicate object keys at ANY depth and NaN/Infinity are rejected
    (shared release/oci_manifest.strict_json_loads); strict UTF-8;
  * no unknown top-level or per-wheel fields;
  * origin is EXACTLY the permitted official-PyPI token;
  * every wheel filename is a well-formed, safe wheel filename (no path separators,
    traversal, absolute paths, control chars, or non-ASCII); its parsed name/version
    (PEP 503 normalized) MUST equal the record's name/version;
  * the tag set derived from the filename MUST equal the recorded `tags`;
  * every recorded name is already PEP 503 canonical (reject noncanonical);
  * no duplicate normalized name; no duplicate filename;
  * sha256 is a bare lowercase 64-hex;
  * MANDATORY, independent target compatibility (no invented tag semantics): the target block
    must equal the fixed v0.3.17 profile (CPython 3.10 / armv7l / glibc 2.35), each wheel's tag
    set MUST intersect the committed ordered RPi2 495-tag evidence (a required argument), and
    each wheel's Requires-Python MUST admit Python 3.10.12. Every trust boundary (acquisition,
    offline build, provenance validation, produce_release) applies this same policy.

Canonical bytes are deterministic (sorted keys, LF, no BOM); `sha256_hex` over them is the
digest bound into provenance (`authorizers.reuse_authz_sha256`).
"""
from __future__ import annotations

import hashlib
import re

# Standard PEP 440 semantics (release-tooling dependency; see requirements-dev.txt + the builder
# backend lock). Importing at module top means release tooling FAILS CLOSED at startup if the
# controlled `packaging` dependency is unavailable -- there is no hand-written fallback parser.
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from release import oci_manifest as _ocim

SCHEMA_ID = "ccc-armv7-reuse-authz/1"
PERMITTED_ORIGIN = "pypi"

_TOP_KEYS = ("schema", "origin", "target", "wheels")
_TARGET_KEYS = ("python", "platform", "glibc")
_WHEEL_KEYS = ("name", "version", "filename", "sha256", "tags", "requires_python")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# A safe wheel filename: ASCII, printable, no path separators/traversal/control, ends .whl.
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*\.whl$")
# A single wheel tag "<pytag>-<abitag>-<plattag>" (each an atomic, dot-free token here).
_TAG = re.compile(r"^[A-Za-z0-9]+-[A-Za-z0-9]+-[A-Za-z0-9_.+]+$")
_NAME_CANON = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Fixed v0.3.17 target profile (RPi2 armv7l/armhf, CPython 3.10, Ubuntu 22.04/Jammy, glibc 2.35).
TARGET_PROFILE = {"python": "cp310", "platform": "armv7l", "glibc": "2.35"}
TARGET_PY = (3, 10, 12)
_TARGET_VERSION = Version("3.10.12")


PYPI_METADATA_HOST = "pypi.org"
PYPI_FILE_HOST = "files.pythonhosted.org"


def origin_violation(url: str, allowed_host: str):
    """The ONE official-origin policy (shared by generation + acquisition). Returns a reason string
    if the URL is not strict HTTPS to the EXACT host with no embedded credentials and a default
    port; else None. Callers wrap the reason in their own fail-closed error type."""
    from urllib.parse import urlparse
    p = urlparse(url or "")
    if p.scheme != "https":
        return f"non-HTTPS URL ({url!r})"
    if p.hostname != allowed_host:
        return f"host not permitted, want {allowed_host!r} ({url!r})"
    if p.username is not None or p.password is not None:
        return f"embedded credentials in URL ({url!r})"
    if p.port not in (None, 443):
        return f"non-default port ({url!r})"
    return None


def canonical_lf(data: bytes) -> bytes:
    """The ONE canonical text-digest policy: LF-normalize before hashing at EVERY boundary
    (sanitizer, tag loader, generator, Phase B, provenance validator, producer) so a Windows
    CRLF/LF working-tree checkout cannot cause a target-tag digest drift."""
    return bytes(data).replace(b"\r\n", b"\n").replace(b"\r", b"\n")


class AuthzError(ValueError):
    """Raised on any reuse-authorization schema/identity violation (fail closed)."""


def parse_target_tags(data: bytes):
    """THE canonical target-tag parse/validate, over BYTES. This is the SINGLE implementation of the
    grammar / ordering / uniqueness / LF-canonical-digest semantics; ``load_target_tags`` is only the
    file wrapper, and every trust boundary (authorization, preflight, producer) routes through here so
    they cannot disagree about which artifacts are acceptable.
    Returns (ordered_tags:list, tag_set:frozenset, sha256:str) over the LF-canonical bytes."""
    raw = canonical_lf(data)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuthzError(f"target-tag artifact is not valid UTF-8: {exc}") from exc
    tags = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not tags or len(set(tags)) != len(tags):
        raise AuthzError("target-tag artifact empty or has duplicate tags")
    bad = [t for t in tags if not _TAG.match(t)]
    if bad:
        raise AuthzError(f"target-tag artifact contains a malformed wheel tag: {bad[:3]}")
    return tags, frozenset(tags), hashlib.sha256(raw).hexdigest()


def load_target_tags(path: str):
    """File wrapper over ``parse_target_tags`` (the canonical validator)."""
    with open(path, "rb") as fh:
        return parse_target_tags(fh.read())


def requires_python_ok(spec: str) -> bool:
    """True iff the Requires-Python specifier admits the exact target Python (3.10.12), by STANDARD
    PEP 440 semantics (`packaging.specifiers.SpecifierSet`). Malformed/unsupported specifiers ->
    False (fail closed). This is the ONE helper used by generation, authorization validation, and
    connected acquisition."""
    try:
        return SpecifierSet(spec).contains(_TARGET_VERSION, prereleases=True)
    except Exception:  # noqa: BLE001 -- InvalidSpecifier / bad input -> fail closed
        return False


_requires_python_ok = requires_python_ok   # backward-compatible alias


def normalize_name(name: str) -> str:
    """PEP 503 normalization: lowercase; runs of -, _, . collapse to a single -."""
    return re.sub(r"[-_.]+", "-", name).strip("-").lower()


def _parse_wheel_filename(fn: str):
    """Return (normalized_name, version, frozenset(expanded_tags)) or raise AuthzError.
    Rejects unsafe filenames BEFORE any structural parse."""
    if not isinstance(fn, str) or not _SAFE_FILENAME.match(fn) or ".." in fn:
        raise AuthzError(f"unsafe or malformed wheel filename: {fn!r}")
    base = fn[:-4]
    parts = base.split("-")
    # name-version(-build)?-pytag-abitag-plattag  -> 5 or 6 hyphen groups
    if len(parts) not in (5, 6):
        raise AuthzError(f"wheel filename does not have 5/6 tag fields: {fn!r}")
    dist_name, version = parts[0], parts[1]
    pytag, abitag, plattag = parts[-3], parts[-2], parts[-1]
    tags = frozenset(
        f"{p}-{a}-{pl}"
        for p in pytag.split(".") for a in abitag.split(".") for pl in plattag.split(".")
    )
    return normalize_name(dist_name), version, tags


def parse_wheel_filename(fn: str):
    """Public: return (normalized_name, version, frozenset(expanded_tags)) for a wheel filename,
    or raise AuthzError. Used to target-check ALL final wheels (built + reused)."""
    return _parse_wheel_filename(fn)


def _validate_wheel(rec: object, *, target_tags):
    if not isinstance(rec, dict):
        raise AuthzError("each wheel record must be an object")
    keys = set(rec)
    if keys != set(_WHEEL_KEYS):
        raise AuthzError(f"wheel record keys must be exactly {list(_WHEEL_KEYS)}; got {sorted(keys)}")
    name, version, filename, sha = rec["name"], rec["version"], rec["filename"], rec["sha256"]
    tags, req_py = rec["tags"], rec["requires_python"]
    if not isinstance(name, str) or not _NAME_CANON.match(name) or normalize_name(name) != name:
        raise AuthzError(f"wheel name must be PEP 503 canonical: {name!r}")
    if not isinstance(version, str) or not version:
        raise AuthzError(f"wheel version must be a non-empty string: {version!r}")
    if not isinstance(sha, str) or not _HEX64.match(sha):
        raise AuthzError(f"wheel sha256 must be a bare lowercase 64-hex: {sha!r}")
    if not (req_py is None or (isinstance(req_py, str) and req_py)):
        raise AuthzError(f"requires_python must be a non-empty string or null: {req_py!r}")
    if req_py is not None and not _requires_python_ok(req_py):
        raise AuthzError(f"requires_python {req_py!r} does not admit Python {'.'.join(map(str, TARGET_PY))}")
    if not isinstance(tags, list) or not tags or not all(isinstance(t, str) and _TAG.match(t) for t in tags):
        raise AuthzError(f"tags must be a non-empty list of well-formed wheel tags: {tags!r}")
    if len(set(tags)) != len(tags):
        raise AuthzError(f"duplicate tag in record for {filename!r}")
    fn_name, fn_ver, fn_tags = _parse_wheel_filename(filename)
    if fn_name != name:
        raise AuthzError(f"filename name {fn_name!r} != record name {name!r} ({filename!r})")
    if fn_ver != version:
        raise AuthzError(f"filename version {fn_ver!r} != record version {version!r} ({filename!r})")
    if fn_tags != frozenset(tags):
        raise AuthzError(f"recorded tags != filename tags for {filename!r}")
    # MANDATORY, independent target compatibility: intersect the wheel's tag set with the committed
    # ordered RPi2 495-tag evidence (no invented tag semantics).
    if not (fn_tags & set(target_tags)):
        raise AuthzError(f"wheel {filename!r} has no tag compatible with the committed target tag set")
    return {"name": name, "version": version, "filename": filename, "sha256": sha,
            "tags": sorted(tags), "requires_python": req_py}


def load_and_validate(raw_bytes: bytes, *, target_tags) -> dict:
    """Parse + strictly validate reuse-authorization bytes against the MANDATORY target-tag set.
    Returns {schema, origin, target, wheels:[normalized records]} or raises AuthzError. Callers at
    EVERY boundary (acquisition, offline build, provenance, produce_release) must supply the
    committed RPi2 495-tag set so target compatibility is enforced independently and identically."""
    if target_tags is None:
        raise AuthzError("target_tags is mandatory (committed RPi2 supported-tag set)")
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise AuthzError("reuse authorization must be bytes")
    try:
        text = bytes(raw_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuthzError(f"reuse authorization is not valid UTF-8: {exc}") from exc
    try:
        obj = _ocim.strict_json_loads(text)   # rejects duplicate keys (any depth) + NaN/Infinity
    except _ocim.ManifestError as exc:
        raise AuthzError(f"reuse authorization JSON rejected: {exc}") from exc
    if not isinstance(obj, dict) or set(obj) != set(_TOP_KEYS):
        raise AuthzError(f"top-level keys must be exactly {list(_TOP_KEYS)}")
    if obj["schema"] != SCHEMA_ID:
        raise AuthzError(f"schema must be {SCHEMA_ID!r}; got {obj['schema']!r}")
    if obj["origin"] != PERMITTED_ORIGIN:
        raise AuthzError(f"origin must be the permitted official-PyPI token {PERMITTED_ORIGIN!r}")
    target = obj["target"]
    if not isinstance(target, dict) or set(target) != set(_TARGET_KEYS) \
       or any(not isinstance(target.get(k), str) or not target.get(k) for k in _TARGET_KEYS):
        raise AuthzError(f"target must declare non-empty {list(_TARGET_KEYS)}")
    if {k: target[k] for k in _TARGET_KEYS} != TARGET_PROFILE:
        raise AuthzError(f"target must be exactly the v0.3.17 profile {TARGET_PROFILE}; got {target}")
    wheels = obj["wheels"]
    if not isinstance(wheels, list) or not wheels:
        raise AuthzError("wheels must be a non-empty list")
    out, seen_name, seen_file = [], set(), set()
    for rec in wheels:
        r = _validate_wheel(rec, target_tags=target_tags)
        if r["name"] in seen_name:
            raise AuthzError(f"duplicate normalized package name: {r['name']!r}")
        if r["filename"] in seen_file:
            raise AuthzError(f"duplicate filename: {r['filename']!r}")
        seen_name.add(r["name"])
        seen_file.add(r["filename"])
        out.append(r)
    return {"schema": obj["schema"], "origin": obj["origin"], "target": dict(target),
            "wheels": out}


def canonical_bytes(validated: dict) -> bytes:
    """Deterministic canonical serialization (sorted keys, LF, no BOM) of a VALIDATED authz
    object -- the bytes whose sha256 is bound into provenance."""
    import json
    return (json.dumps(validated, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()
