#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
release/ccc_release.py
----------------------
ADR-0003 Epic A — Signed Release Production (publisher side).

Produces the CCC canonical Signed Object for the Trusted Update Engine:

    Release Artifact          a content-fixed tarball of the release
    Manifest                  a canonical, minimal metadata document (S2)
    Manifest signature        an SSH (SSHSIG) Ed25519 signature over the manifest

Normative inputs (frozen by ADR-0003, do NOT change here):
  * Signed Object model = S2  (signed manifest binds metadata + content digest;
    the artifact is content-addressed by that digest).
  * Technology          = SSH signatures, Ed25519 (Cluster A).
  * The manifest carries NO trust material (no keys, no anchor) — Invariant §8.1.
  * The manifest is CANONICAL: its on-disk bytes are exactly the signed bytes.

Scope boundary: this is the PUBLISHER-side producer. It never contacts the
network, never verifies on a device, and never touches the on-device trust store
(that is Epic B). It runs on the publisher's machine with a private signing key
supplied by the operator (key custody is off-infrastructure, ADR-0003 Stage 3).

The private signing key is NEVER generated, embedded, or logged by this tool; it
is provided by path and handed only to `ssh-keygen`.
"""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from typing import Optional

try:
    from release import lock_validate as _lockval
except Exception:  # noqa: BLE001 - allow `python release/ccc_release.py` (script dir on path)
    import os as _os_boot
    import sys as _sys_boot
    _sys_boot.path.insert(0, _os_boot.path.dirname(_os_boot.path.dirname(_os_boot.path.abspath(__file__))))
    from release import lock_validate as _lockval

from release import canonical_bytes as _canon  # THE canonical text-byte rule (stdlib-only module)
from release import oci_manifest as _ocim  # release/ is importable (path patched above if needed)
from release import reuse_authz as _reuse_authz  # strict reused-wheel authorization (dual-origin)

# Re-export (NOT re-implement): exactly one normalisation/digest implementation exists, in
# release.canonical_bytes. The Phase-B host shell calls that module directly so it stays
# stdlib-only, while these names keep every existing call site and test coherent.
_to_lf = _canon.to_lf
canonical_file_sha256 = _canon.canonical_file_sha256

# --- Normative constants (ADR-0003) ---------------------------------------- #

PRODUCT = "conduit-control-center"          # Product identity (authoritative)
MANIFEST_FORMAT_VERSION = 2                  # V2 platform-artifact manifest schema
DIGEST_ALGORITHM = "sha256"                  # Content-digest algorithm
SSHSIG_NAMESPACE = "ccc-update-manifest"     # Fixed SSHSIG namespace (sign+verify)

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")   # matches backend/_version.py format


# --- Errors ---------------------------------------------------------------- #

class ReleaseError(Exception):
    """Raised for any producer-side release-production failure."""


# --- Canonicalisation & digest --------------------------------------------- #

def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Deterministic byte representation of a manifest.

    The bytes returned here are EXACTLY the bytes written to disk and EXACTLY the
    bytes that get signed. Determinism (sorted keys, no insignificant
    whitespace, UTF-8) is required so signing and verification operate on an
    identical, unambiguous input.
    """
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Manifest construction (S2) -------------------------------------------- #

SUPPORTED_PLATFORMS = ("aarch64", "armv7l")     # raw uname -m tokens (must match verifier)

# Files/markers that must NEVER enter a release artifact (Invariant I2 + secret
# exclusion). Scanned pre-sign over the FINAL composed tree of each artifact.
_FORBIDDEN_BASENAMES = frozenset({
    ".env", "allowed_signers", "trusted_publishers",
    "id_ed25519", "id_rsa", "id_ecdsa",
})
_SECRET_MARKERS = (b"PRIVATE KEY-----", b"BEGIN OPENSSH PRIVATE KEY")

# Binary payload extensions EXEMPT from the private-key-marker + no-NUL-in-text
# scan (legitimately binary). Everything else -- including extensionless
# executables/scripts and textual wheelhouse metadata such as SHA256SUMS -- IS
# scanned. A NUL in a text/source member is corruption and fails the release closed.
_BINARY_EXTS = frozenset({
    "whl", "gz", "tgz", "tar", "zip", "bz2", "xz", "7z",
    "png", "jpg", "jpeg", "gif", "ico", "webp", "bmp",
    "woff", "woff2", "ttf", "otf", "eot",
    "pdf", "so", "pyc", "pyo", "o", "a", "bin", "dat", "db", "sqlite",
    "jar", "class", "mo", "wasm",
})


def _is_hex64(v: object) -> bool:
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) is not None


def _is_oci_digest(v: object) -> bool:
    """Canonical OCI-style content digest: 'sha256:' + exactly 64 lowercase hex."""
    return isinstance(v, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", v) is not None


def _validate_wheelhouse_block(wh: object) -> None:
    """Fail-closed structural/semantic validation of the armv7l wheelhouse block
    (mirrors backend.update_verify._validate_wheelhouse)."""
    if not isinstance(wh, dict):
        raise ReleaseError("wheelhouse block must be an object")
    if wh.get("path") != "wheelhouse-armhf/":
        raise ReleaseError(f"wheelhouse path must be 'wheelhouse-armhf/': {wh.get('path')!r}")
    for field in ("bundle_sha256", "requirements_sha256", "lock_sha256",
                  "build_lock_sha256", "provenance_sha256"):
        if not _is_hex64(wh.get(field)):
            raise ReleaseError(f"wheelhouse {field} must be a sha256: {wh.get(field)!r}")
    prov = wh.get("provenance")
    if not isinstance(prov, str) or not prov or prov.startswith("/") or ".." in prov:
        raise ReleaseError(f"wheelhouse provenance reference invalid: {prov!r}")


def _bare_name(name: str) -> str:
    if not name or "/" in name or "\\" in name:
        raise ReleaseError(f"artifact_name must be a bare filename, got {name!r}")
    return name


def _validate_top_level(top_level: object) -> list:
    if not isinstance(top_level, list) or not top_level:
        raise ReleaseError("top_level must be a non-empty list")
    out = []
    for name in top_level:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ReleaseError(f"top_level entry must be a bare name: {name!r}")
        out.append(name)
    return sorted(set(out))


def build_artifact_entry(*, platform: str, name: str, artifact_bytes: bytes,
                         top_level: list, wheelhouse: Optional[dict] = None) -> dict:
    """One signed platform-artifact entry: platform (uname -m) + bare name + sha256
    content digest + the SIGNED top-level allowlist (the exact set of top-level
    members in this artifact), and (armv7l only) an internal-wheelhouse provenance
    block. aarch64 MUST NOT declare a wheelhouse (isolation)."""
    if platform not in SUPPORTED_PLATFORMS:
        raise ReleaseError(f"unsupported platform: {platform!r}")
    entry = {
        "platform": platform,
        "name": _bare_name(name),
        "digest": {"algorithm": DIGEST_ALGORITHM, "value": sha256_hex(artifact_bytes)},
        "top_level": _validate_top_level(top_level),
    }
    if platform == "armv7l":
        _validate_wheelhouse_block(wheelhouse)
        entry["wheelhouse"] = wheelhouse
    elif wheelhouse is not None:
        raise ReleaseError("aarch64 entry must not declare a wheelhouse")
    return entry


def build_manifest(
    *,
    version: str,
    source: dict,
    artifacts: list,
    dependency_locks: Optional[dict] = None,
    compatibility: Optional[dict] = None,
    product: str = PRODUCT,
    format_version: int = MANIFEST_FORMAT_VERSION,
) -> dict:
    """Assemble the canonical V2 manifest binding product, version, source/tag
    provenance, the per-platform artifact set (each bound by sha256 digest), and
    the dependency-lock digests. Carries NO trust material. Artifacts are sorted
    by platform so the canonical bytes are order-stable."""
    if not _SEMVER_RE.match(version):
        raise ReleaseError(f"version must be strict semver X.Y.Z, got {version!r}")
    if not isinstance(source, dict) or source.get("vcs") != "git" \
       or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit", ""))) \
       or not source.get("tag"):
        raise ReleaseError(f"source must be {{vcs:git, commit:<40hex>, tag:<str>}}, got {source!r}")
    if source["tag"] != f"v{version}":
        raise ReleaseError(f"source.tag must be 'v{version}', got {source['tag']!r}")
    if not artifacts:
        raise ReleaseError("at least one artifact entry is required")
    plats = [e["platform"] for e in artifacts]
    if len(artifacts) != 2 or sorted(plats) != ["aarch64", "armv7l"]:
        raise ReleaseError(f"a full V2 release requires EXACTLY one aarch64 + one armv7l entry; got {plats}")
    for e in artifacts:
        expected = f"ccc-{version}-{e['platform']}.tar.gz"
        if e["name"] != expected:
            raise ReleaseError(f"artifact name {e['name']!r} != canonical {expected!r}")
    locks = dependency_locks or {}
    for field in ("requirements_sha256", "aarch64_lock_sha256",
                  "armv7_lock_sha256", "armv7_build_lock_sha256"):
        if not _is_hex64(locks.get(field)):
            raise ReleaseError(f"dependency_locks.{field} must be a sha256 (mandatory)")
    return {
        "format_version": format_version,
        "product": product,
        "version": version,
        "source": {"vcs": "git", "commit": source["commit"], "tag": source["tag"]},
        "artifacts": sorted(artifacts, key=lambda e: e["platform"]),
        "dependency_locks": {
            "requirements_sha256": locks["requirements_sha256"],
            "aarch64_lock_sha256": locks["aarch64_lock_sha256"],
            "armv7_lock_sha256": locks["armv7_lock_sha256"],
            "armv7_build_lock_sha256": locks["armv7_build_lock_sha256"],
        },
        "compatibility": compatibility or {},
    }


def _wheelhouse_members(wheelhouse_dir: str) -> dict:
    """Collect {arcname -> bytes} for a wheelhouse directory under the fixed
    top-level path `wheelhouse-armhf/`. Bytes are left EXACT (never LF-munged);
    injected AFTER source canonicalization so wheels are byte-preserved."""
    base = os.path.abspath(wheelhouse_dir)
    if not os.path.isdir(base):
        raise ReleaseError(f"wheelhouse dir not found: {wheelhouse_dir!r}")
    out: dict = {}
    for root, dirs, files in os.walk(base):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, base).replace(os.sep, "/")
            with open(path, "rb") as fh:
                out[f"wheelhouse-armhf/{rel}"] = fh.read()
    if not out:
        raise ReleaseError("wheelhouse dir is empty")
    return out


def _read_provenance(path: str):
    """Read the armv7 wheelhouse provenance record (a BUILD OUTPUT). Returns
    (bytes, parsed_obj). Deep schema + bundle cross-check is `_validate_provenance`.
    Lifecycle: the dependency LOCKS are committed pre-tag (build-independent); the
    wheelhouse + this record are post-tag content-addressed inputs, digest-bound."""
    if not path or not os.path.isfile(path):
        raise ReleaseError(f"wheelhouse provenance record not found: {path!r}")
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        raise ReleaseError("wheelhouse provenance record is empty")
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"wheelhouse provenance record not valid JSON: {exc}")
    if not isinstance(obj, dict):
        raise ReleaseError("provenance record must be a JSON object")
    return data, obj


def _parse_wheel_name(fn: str):
    if not fn.endswith(".whl"):
        return None, None
    parts = fn[:-4].split("-")
    if len(parts) < 2:
        return None, None
    return parts[0].lower().replace("_", "-"), parts[1]


def _require_valid_lock(requirements_text: str, lock_text: str, label: str) -> None:
    problems = _lockval.validate(requirements_text, lock_text)
    if problems:
        raise ReleaseError(f"{label} is not a valid solution of requirements.txt: {problems}")


def _validate_runtime_lock_against_wheelhouse(runtime_lock_text: str, wheelhouse_members: dict,
                                              requirements_text: str) -> None:
    """Fail-closed: the injected armv7 runtime lock must (a) be a valid solution of
    canonical requirements.txt, and (b) be a BIJECTION with the embedded wheels --
    every pin (name==version, hashed) maps to exactly one embedded wheel of that
    name+version whose sha256 is among the pin's hashes, and every embedded wheel is
    covered. Missing/extra/duplicate/unhashed/version- or hash-mismatched -> reject."""
    _require_valid_lock(requirements_text, runtime_lock_text, "requirements-armv7.lock")
    pins = _parse_lock_pins(runtime_lock_text)              # {name: (version, {hashes})}
    if not pins:
        raise ReleaseError("requirements-armv7.lock has no pins")
    pin_set = {(name, ver) for name, (ver, _h) in pins.items()}
    wheel_map: dict = {}                                    # (name, version) -> wheel sha256
    for arc, dat in wheelhouse_members.items():
        fn = arc.split("/", 1)[1] if "/" in arc else arc
        if fn == "SHA256SUMS":
            continue
        name, ver = _parse_wheel_name(fn)
        if name is None:
            raise ReleaseError(f"non-wheel file in wheelhouse: {fn!r}")
        key = (name, ver)
        if key in wheel_map:
            raise ReleaseError(f"duplicate embedded wheel for {name}=={ver}")
        wheel_map[key] = sha256_hex(dat)
    wheel_set = set(wheel_map)
    if pin_set != wheel_set:
        missing = sorted(wheel_set - pin_set)              # embedded wheel not in the lock
        extra = sorted(pin_set - wheel_set)                # lock pin with no matching wheel
        raise ReleaseError(f"runtime lock != embedded wheels (missing_from_lock={missing}, "
                           f"extra_pins={extra})")
    for (name, ver), wsha in wheel_map.items():
        if wsha not in pins[name][1]:
            raise ReleaseError(f"embedded wheel {name}=={ver} sha256 not authorized by runtime lock")


def _parse_lock_pins(text: str) -> dict:
    """Parse a pip lock STRICTLY (closed grammar; finding 2). Fails closed on any
    unrecognized directive/line or duplicate pin."""
    pins, problems = _lockval.parse_lock(text)
    if problems:
        raise ReleaseError(f"malformed lock: {problems}")
    return pins


def _parse_sdist_name(fn: str):
    for suf in (".tar.gz", ".tgz", ".zip", ".tar.bz2"):
        if fn.endswith(suf):
            stem = fn[:-len(suf)]
            if "-" in stem:
                name, _, ver = stem.rpartition("-")
                return name.lower().replace("_", "-"), ver
    return None, None


CANONICAL_RECIPE_PATH = "release/builder/Containerfile"   # committed builder recipe (bound in provenance)
_ENV_REQUIRED = ("os", "os_id", "os_version_id", "arch", "apt_architecture",
                 "python", "rustc", "cargo", "gcc", "glibc")
BUILD_BACKENDS_LOCK_PATH = "release/builder/requirements-build-backends.lock"
APT_PACKAGES_PATH = "release/builder/apt-packages.list"
RUSTUP_SHA_PATH = "release/builder/rustup-init.sha256"
EXTRACTOR_TOOLS_IN_PATH = "release/builder/requirements-extractor-tools.in"
EXTRACTOR_TOOLS_LOCK_PATH = "release/builder/requirements-extractor-tools.lock"
BACKEND_SOURCE_ALLOWLIST_PATH = "release/builder/requirements-build-backends.source-allowlist"
REUSE_AUTHZ_PATH = "release/builder/armv7-reuse-authz.json"   # committed reused-wheel authorization (24)
TARGET_TAGS_PATH = "release/builder/target-supported-tags.txt"  # committed sanitized RPi2 495-tag evidence
SOLUTION_LOCK_PATH = "requirements-armv7-solution.lock"        # durable 30-pin solution (generator input)
BUILD_LOCK_PATH = "requirements-armv7-build.lock"              # DERIVED six-entry source-build partition

# --- v0.3.17 approved partition policy: the SINGLE authoritative source (consumed by the
# active-input generator, the producer, and the tests). These six packages publish no acceptable
# official armv7 wheel, so they are source-built; everyone else is reused. Versions come from the
# authoritative solution/build lock; exact sdist hashes are authorized ONLY by the six-entry lock. ---
V0317_SOURCE_BUILD_PACKAGES = frozenset({"cffi", "httptools", "markupsafe", "psutil", "pyyaml", "uvloop"})
V0317_BUILT_COUNT = 6
V0317_REUSED_COUNT = 24
V0317_TOTAL_COUNT = 30
TARGET_GLIBC = "2.35"                # Ubuntu 22.04 (Jammy) armhf baseline
TARGET_OS_ID = "ubuntu"
TARGET_OS_VERSION = "22.04"
TARGET_ARCH = ("armv7l", "armhf", "arm")
TARGET_DPKG_ARCH = "armhf"   # dpkg --print-architecture on the Jammy armhf builder


# --------------------------------------------------------------------------- #
#  Lifecycle-aware builder-input validation (finding 6).                       #
#                                                                              #
#  Three files drive the builder ceremony: apt-packages.list, rustup-init.     #
#  sha256, requirements-build-backends.lock. They may be legitimately ABSENT   #
#  before the builder gate is run (pre-tag development). But when present they  #
#  must pass strict semantic validation, and .example templates are NEVER      #
#  accepted as active. Release/tag production requires all three (require_      #
#  present=True). This replaces the brittle "the active lock must not exist"    #
#  assertion, which would have failed the day a real valid lock is committed.   #
# --------------------------------------------------------------------------- #
BUILDER_DIR = "release/builder"
BUILDER_INPUT_FILES = ("apt-packages.list", "rustup-init.sha256",
                       "requirements-build-backends.lock",
                       "requirements-build-backends.source-allowlist")
_PLACEHOLDER_TOKENS = ("example", "placeholder", "replace-me", "replaceme",
                       "changeme", "todo", "your-", "<")


def validate_armv7_solution(solution_text: str, requirements_text: str, *,
                            reuse_names=None, reuse_versions=None, build_pins=None) -> dict:
    """Validate the durable 30-pin armv7 solution lock (fail closed). Returns its pins
    {name:(version,{hashes})}. Enforces: exactly 30 unique closed-grammar hash-pins; a valid solution
    of requirements.txt; the approved six are a subset; and (when supplied) it partitions EXACTLY into
    the approved-six build set + the 24 reuse records with agreeing versions."""
    pins = _parse_lock_pins(solution_text)                      # closed grammar + no duplicate names
    if len(pins) != V0317_TOTAL_COUNT:
        raise ReleaseError(f"{SOLUTION_LOCK_PATH} must have exactly {V0317_TOTAL_COUNT} pins; got {len(pins)}")
    _require_valid_lock(requirements_text, solution_text, SOLUTION_LOCK_PATH)   # valid solution of requirements.txt
    approved = set(V0317_SOURCE_BUILD_PACKAGES)
    if not approved <= set(pins):
        raise ReleaseError(f"solution missing approved source-build packages: {sorted(approved - set(pins))}")
    reuse_expected = set(pins) - approved
    if len(reuse_expected) != V0317_REUSED_COUNT:
        raise ReleaseError(f"solution reuse partition must be {V0317_REUSED_COUNT}; got {len(reuse_expected)}")
    if build_pins is not None:
        if set(build_pins) != approved:
            raise ReleaseError(f"build lock packages != approved six; got {sorted(set(build_pins))}")
        for name in approved:
            if build_pins[name][0] != pins[name][0]:
                raise ReleaseError(f"build-lock version for {name!r} != solution ({build_pins[name][0]} != {pins[name][0]})")
    if reuse_names is not None:
        if set(reuse_names) != reuse_expected:
            raise ReleaseError("reuse authorization package set != solution reuse partition")
        if reuse_versions is not None:
            for name, ver in reuse_versions.items():
                if pins[name][0] != ver:
                    raise ReleaseError(f"reuse version for {name!r} != solution ({ver} != {pins[name][0]})")
    return pins


def validate_build_partition_lock(build_lock_text: str) -> dict:
    """THE role-appropriate validator for the DERIVED six-entry armv7 build lock.

    This lock is the SOURCE-BUILD PARTITION, not a complete solution of requirements.txt: it
    deliberately omits the 24 reused packages, so `lock_validate.validate()` (the complete-solution
    validator, correct for requirements-aarch64.lock / requirements-armv7.lock / the durable
    requirements-armv7-solution.lock) must NEVER be applied to it -- doing so would reject the legal
    generated state for "missing" packages that belong to the reuse partition by design.

    Enforces, reusing the existing authoritative primitives so no second set of rules can drift:
      * the closed pin/hash grammar via _parse_lock_pins (pinned `==`, sha256 hashes, no duplicate
        names, no directives) -- the SAME parser produce_release and the preflight use;
      * exactly the approved six source-built packages (V0317_SOURCE_BUILD_PACKAGES).
    Version agreement against the durable 30-pin solution is enforced separately by
    validate_armv7_solution(build_pins=...), which owns the cross-artifact relationship.

    Returns the parsed pins {name: (version, {hashes})}. Fails closed."""
    pins = _parse_lock_pins(build_lock_text)                 # closed grammar + hashes + no duplicates
    approved = set(V0317_SOURCE_BUILD_PACKAGES)
    if set(pins) != approved:
        missing = sorted(approved - set(pins))
        extra = sorted(set(pins) - approved)
        raise ReleaseError(f"{BUILD_LOCK_PATH} must contain EXACTLY the approved six source-built "
                           f"packages {sorted(approved)} (missing={missing}, extra={extra})")
    return pins


# --- IMAGE CONTEXT (Phase-A -> Phase-B byte-level binding) ------------------------------------- #
# The EXACT set of content-bearing files that construct the builder image. Five are COPYed into the
# image by the Containerfile (lines 33/44/61/62/63) and therefore remain readable inside the running
# image at /opt/ccc; the recipe itself is bound via the Phase-A-recorded CCC_RECIPE_SHA256. Together
# these six are the complete build context: nothing else contributes bytes to the image.
#
# NOTE ON SCOPE: the existing environment checks (installed APT state, effective backend versions)
# are SEMANTIC checks of the resulting installed state. They do NOT prove byte equality of the
# source inputs -- different bytes (comments, ordering, whitespace, duplicate entries) can describe
# the same effective installed state. All five copied files therefore require byte-level proof.
IMAGE_CONTEXT_FILES = (
    "release/builder/Containerfile",
    "release/builder/apt-packages.list",
    "release/builder/rustup-init.sha256",
    "release/builder/requirements-build-backends.lock",
    "release/builder/requirements-build-backends.source-allowlist",
    "release/builder/partition_backends.py",
)


def image_context_digest(per_file: dict) -> str:
    """Aggregate digest over EXACTLY the six canonical image-context entries.

    Deterministic and ORDER-INDEPENDENT at the API boundary: the mapping is serialised canonically
    as the entries sorted by canonical path, each rendered ``<path>\\x00<sha256>\\n`` and encoded
    UTF-8; the aggregate is the sha256 of that concatenation. The NUL separator makes the encoding
    unambiguous (no path or hash can contain it), so distinct mappings cannot collide.

    Rejects a non-mapping, any missing key, and any unknown key -- the six are exact."""
    if not isinstance(per_file, dict):
        raise ReleaseError("image context map must be an object")
    expected, got = set(IMAGE_CONTEXT_FILES), set(per_file)
    if got != expected:
        raise ReleaseError("image context map must contain EXACTLY the six canonical entries "
                           f"(missing={sorted(expected - got)}, unknown={sorted(got - expected)})")
    parts = []
    for path in sorted(IMAGE_CONTEXT_FILES):
        value = per_file[path]
        if not _is_hex64(value):
            raise ReleaseError(f"image context hash for {path!r} is not a 64-hex sha256")
        parts.append(f"{path}\x00{value}\n")
    return sha256_hex("".join(parts).encode("utf-8"))


def expected_image_context(canon: dict) -> dict:
    """Recompute the authoritative six-entry map from the CANONICAL COMMITTED bytes at the release
    commit. Fails closed if any image-context file is absent from the committed source."""
    out = {}
    for path in IMAGE_CONTEXT_FILES:
        data = canon.get(path)
        if data is None:
            raise ReleaseError(f"image-context file missing from committed source: {path}")
        out[path] = canonical_file_sha256(data)
    return out


def validate_image_context(image_context: object, aggregate: object, *, expected: dict = None) -> None:
    """Validation of the image-context binding (fail closed).

    STRUCTURE IS ALWAYS MANDATORY -- there is no legacy provenance mode without these fields: the
    per-file map and the aggregate digest must both be present, the map must have EXACTLY the six
    canonical keys with well-formed hashes, and the aggregate must be self-consistent with the map.

    When ``expected`` is supplied (the producer, and the builder's own in-container self-check), the
    map is ADDITIONALLY required to equal the canonical committed bytes, so provenance can never
    describe a build context other than the one committed at the release commit."""
    if image_context is None:
        raise ReleaseError("provenance.builder.image_context is required (six-entry per-file map)")
    if aggregate is None:
        raise ReleaseError("provenance.builder.image_context_sha256 is required")
    digest = image_context_digest(image_context)          # exact-six + hash-shape enforced here
    if not _is_hex64(aggregate) or aggregate != digest:
        raise ReleaseError(f"provenance.builder.image_context_sha256 mismatch "
                           f"(recorded {aggregate!r}, recomputed {digest})")
    if expected is None:
        return
    for path in sorted(IMAGE_CONTEXT_FILES):
        if image_context[path] != expected[path]:
            raise ReleaseError(
                f"provenance image-context hash for {path!r} does not match the committed bytes "
                f"(provenance {image_context[path]}, committed {expected[path]})")
    if digest != image_context_digest(expected):          # defence in depth: aggregate vs committed
        raise ReleaseError("image-context aggregate digest disagrees with the committed bytes")


def _reject_placeholder(text: str, label: str) -> None:
    low = text.lower()
    for tok in _PLACEHOLDER_TOKENS:
        if tok in low:
            raise ReleaseError(f"{label}: contains placeholder token {tok!r}; not an active input")


def validate_apt_packages_list(text: str) -> None:
    pkgs = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    if not pkgs:
        raise ReleaseError("apt-packages.list: no packages")
    _reject_placeholder("\n".join(pkgs), "apt-packages.list")
    for ln in pkgs:
        if "=" not in ln:
            raise ReleaseError(f"apt-packages.list: entry not pinned as name[:arch]=version: {ln!r}")
        tokpart, ver = ln.split("=", 1)
        _parse_apt_token(tokpart)   # validates name and optional :arch (fails closed)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+.:~\-]*", ver):
            raise ReleaseError(f"apt-packages.list: malformed version in {ln!r}")


def validate_rustup_sha(text: str) -> None:
    toks = [ln.strip().split()[0] for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    if len(toks) != 1:
        raise ReleaseError("rustup-init.sha256: must contain exactly one sha256")
    hx = toks[0].lower()
    if not _is_hex64(hx):
        raise ReleaseError("rustup-init.sha256: not a 64-hex sha256")
    if set(hx) == {"0"}:
        raise ReleaseError("rustup-init.sha256: all-zeros placeholder is not an active hash")


def validate_build_backends_lock(text: str) -> None:
    body = [ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    if not body:
        raise ReleaseError("requirements-build-backends.lock: empty/comment-only")
    pins = _parse_lock_pins(text)   # strict closed grammar; raises on malformed
    if not pins:
        raise ReleaseError("requirements-build-backends.lock: no pins")
    for name, (version, _hashes) in pins.items():
        if version in ("0.0.0", "0"):
            raise ReleaseError(
                f"requirements-build-backends.lock: placeholder version for {name}")


def validate_extractor_tools_lock(lock_text: str, in_text: str) -> None:
    """The committed extractor-tools lock (hash-pinned tomli for the connected-phase parser)
    must be a valid closed-grammar lock that pins the EXACT tomli version requested by the
    committed .in file (finding 4). Binds the extraction tool into the signed source chain."""
    in_versions = {}
    for raw in in_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)", line)
        if not m:
            raise ReleaseError(f"malformed requirements-extractor-tools.in line: {raw!r}")
        in_versions[m.group(1).lower()] = m.group(2)
    if "tomli" not in in_versions:
        raise ReleaseError("requirements-extractor-tools.in must request tomli")
    pins = _parse_lock_pins(lock_text)   # strict closed grammar; raises on malformed/unhashed/dup
    lock_versions = {k.lower(): v for k, (v, _h) in pins.items()}
    # CLOSED authorization: the lock must pin EXACTLY the .in-requested packages (tomli has no
    # runtime deps, so the authorized closure is exactly one pin). Reject any extra package.
    extra = set(lock_versions) - set(in_versions)
    if extra:
        raise ReleaseError(f"extractor-tools lock contains unauthorized package(s): {sorted(extra)}")
    missing = set(in_versions) - set(lock_versions)
    if missing:
        raise ReleaseError(f"extractor-tools lock missing authorized package(s): {sorted(missing)}")
    for name, ver in in_versions.items():
        if lock_versions[name] != ver:
            raise ReleaseError(
                f"extractor-tools lock pins {name} {lock_versions[name]!r} but .in requests {ver!r}")


_ALLOWLIST_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def _normalize_pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_backend_source_allowlist(allowlist_text: str, lock_text: str = None) -> None:
    """The committed backend source-allowlist authorizes specific build backends to be
    installed from a hash-pinned SDIST (source-built) because no official target wheel exists.
    Strict, fail-closed: names must ALREADY equal their PEP 503-normalized form (noncanonical
    spellings like ``CFFI`` or underscores are REJECTED, never silently normalized), non-empty,
    no duplicates, no malformed entries. When ``lock_text`` is provided, also enforce EXACT USE
    -- every allowlisted backend MUST be pinned in the committed requirements-build-backends.lock
    (an unused/unknown entry is rejected). ``lock_text=None`` performs grammar-only validation
    (lifecycle/pre-gate, where the lock may not yet exist)."""
    names, seen = [], set()
    for raw in allowlist_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _ALLOWLIST_NAME_RE.fullmatch(line):
            raise ReleaseError(f"backend source-allowlist: malformed entry {raw!r}")
        norm = _normalize_pep503(line)
        if line != norm:
            raise ReleaseError(f"backend source-allowlist: non-canonical entry {line!r} "
                               f"(must already be PEP 503-normalized: {norm!r})")
        if norm in seen:
            raise ReleaseError(f"backend source-allowlist: duplicate entry {norm!r}")
        seen.add(norm)
        names.append(norm)
    if not names:
        raise ReleaseError("backend source-allowlist is empty")
    if lock_text is not None:
        lock_names = {_normalize_pep503(k) for k in _parse_lock_pins(lock_text)}
        unused = [n for n in names if n not in lock_names]
        if unused:
            raise ReleaseError(f"backend source-allowlist entries not pinned in the backend lock "
                               f"(unused/unauthorized): {sorted(unused)}")


_INPUT_VALIDATORS = {
    "apt-packages.list": validate_apt_packages_list,
    "rustup-init.sha256": validate_rustup_sha,
    "requirements-build-backends.lock": validate_build_backends_lock,
    "requirements-build-backends.source-allowlist": validate_backend_source_allowlist,
}


def validate_builder_inputs(builder_dir: str, *, require_present: bool) -> dict:
    """Validate the three active builder inputs under ``builder_dir``.

    * absent   -> allowed only when ``require_present`` is False (pre-gate dev);
                  a release/tag production run passes ``require_present=True``.
    * present  -> must pass its strict semantic validator (fail closed).
    * .example -> never read here; templates are not active inputs.

    Returns {name: 'present'|'absent'}; raises ReleaseError on any violation.
    """
    status = {}
    for name in BUILDER_INPUT_FILES:
        path = os.path.join(builder_dir, name)
        if not os.path.isfile(path):
            if require_present:
                raise ReleaseError(f"required builder input absent: {name}")
            status[name] = "absent"
            continue
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
        if name == "requirements-build-backends.source-allowlist":
            # Lock-aware: grammar-only when the backend lock is absent (pre-tag dev, allowlist
            # committed first); grammar PLUS exact-use whenever the lock is present. Because the
            # lock is itself a required input, exact-use is MANDATORY at require_present=True.
            lock_path = os.path.join(builder_dir, "requirements-build-backends.lock")
            lock_text = None
            if os.path.isfile(lock_path):
                with open(lock_path, "rb") as _lf:
                    lock_text = _lf.read().decode("utf-8", "replace")
            validate_backend_source_allowlist(text, lock_text)
        else:
            _INPUT_VALIDATORS[name](text)
        status[name] = "present"
    return status


TARGET_TAG_COUNT = 495          # the committed RPi2 target-tag artifact is exactly this many tags


def validate_target_tags(raw: bytes):
    """THE canonical target-tag validator for the release path: delegates the grammar / ordering /
    uniqueness / LF-canonical-digest semantics to reuse_authz.parse_target_tags (one implementation)
    and adds the fixed release count rule. Called by BOTH release_preflight and produce_release so the
    gate and the producer cannot disagree about which target-tag artifacts are acceptable.
    Returns (ordered_tags, tag_set, sha256_of_lf_canonical_bytes)."""
    try:
        tags, tset, sha = _reuse_authz.parse_target_tags(raw)
    except _reuse_authz.AuthzError as exc:
        raise ReleaseError(f"{TARGET_TAGS_PATH}: {exc}") from exc
    if len(tags) != TARGET_TAG_COUNT:
        raise ReleaseError(f"{TARGET_TAGS_PATH} must contain exactly {TARGET_TAG_COUNT} tags; "
                           f"got {len(tags)}")
    return tags, tset, sha


def release_preflight(repo_dir: str, *, require_present: bool) -> dict:
    """AUTHORITATIVE release-readiness gate (invoked by CI + the Owner pre-tag), using the SAME
    validators produce_release uses -- so CI cannot report ready while the producer would reject.
    Validates the committed builder inputs, the durable 30-pin solution, the target-tag artifact, and
    (when generated) the six-entry build lock + 24-entry reuse authorization incl. the exact
    solution/partition/authz-vs-target-tags binding. ``require_present=False`` allows legitimate
    pre-generation dev state (absent generated active inputs); ``require_present=True`` requires the
    release-ready set. Returns a status dict; raises ReleaseError on any violation."""
    builder_dir = os.path.join(repo_dir, "release", "builder")
    req_path = os.path.join(repo_dir, "requirements.txt")
    if not os.path.isfile(req_path):
        raise ReleaseError("requirements.txt not found")
    with open(req_path, encoding="utf-8") as fh:
        requirements_text = fh.read()
    status = {"builder_inputs": validate_builder_inputs(builder_dir, require_present=require_present)}

    def _read(rel):
        p = os.path.join(repo_dir, rel)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as fh:
            return fh.read()

    tags_bytes = _read(TARGET_TAGS_PATH)                    # durable stable target-tag artifact
    tset = None
    if tags_bytes is None:
        if require_present:
            raise ReleaseError(f"required target-tag artifact absent: {TARGET_TAGS_PATH}")
        status["target_tags"] = "absent"
    else:
        _tags, tset, _tt_sha = validate_target_tags(tags_bytes)   # THE canonical validator
        status["target_tags"] = "present"

    sol_bytes = _read(SOLUTION_LOCK_PATH)                   # durable 30-pin solution
    if sol_bytes is None:
        if require_present:
            raise ReleaseError(f"required durable solution absent: {SOLUTION_LOCK_PATH}")
        status["solution"] = "absent"
    else:
        validate_armv7_solution(sol_bytes.decode("utf-8", "replace"), requirements_text)
        status["solution"] = "present"

    # ---- DERIVED ACTIVE INPUTS: strict three-state machine ------------------------------------ #
    # The six-entry build lock and the 24-entry reuse authorization are co-produced by
    # gen_active_inputs.py and committed ATOMICALLY. Therefore exactly two states are legal:
    #   pre_generation : BOTH absent  -> dev mode only (the generator has not been run yet)
    #   generated      : BOTH present -> validated as the exact disjoint 6+24=30 partition; both modes
    # Any HALF-STATE (exactly one present) is a broken atomic commit and is INVALID IN EVERY MODE --
    # it is precisely the state a partial commit would leave behind, so it must never be reported OK.
    authz_bytes = _read(REUSE_AUTHZ_PATH)                   # derived active input (24)
    build_bytes = _read(BUILD_LOCK_PATH)                    # derived active input (6)
    status["build_lock"] = "absent" if build_bytes is None else "present"
    status["reuse_authz"] = "absent" if authz_bytes is None else "present"
    if (build_bytes is None) != (authz_bytes is None):
        present, missing = ((BUILD_LOCK_PATH, REUSE_AUTHZ_PATH) if authz_bytes is None
                            else (REUSE_AUTHZ_PATH, BUILD_LOCK_PATH))
        raise ReleaseError(
            "derived active inputs are not atomic: the two co-produced inputs must be BOTH absent "
            f"(pre-generation) or BOTH present (generated); {present!r} is present but {missing!r} "
            "is absent. Commit both together (gen_active_inputs.py) or neither.")
    if build_bytes is None:                                 # -> pre_generation
        if require_present:
            raise ReleaseError("release mode requires the generated active inputs, but both are "
                               f"absent: {BUILD_LOCK_PATH}, {REUSE_AUTHZ_PATH}")
        status["derived_state"] = "pre_generation"
        return status
    # -> generated: BOTH present. The full partition is MANDATORY here, in dev mode as well as
    # release mode: once generated, an inconsistent partition is a defect, never a tolerable state.
    status["derived_state"] = "generated"
    if tset is None:
        raise ReleaseError("generated active inputs present but the target-tag artifact is absent")
    if sol_bytes is None:
        raise ReleaseError("generated active inputs present but the durable solution is absent: "
                           f"{SOLUTION_LOCK_PATH}")
    authz = _reuse_authz.load_and_validate(authz_bytes, target_tags=tset)
    reuse_versions = {w["name"]: w["version"] for w in authz["wheels"]}
    validate_armv7_solution(sol_bytes.decode("utf-8", "replace"), requirements_text,
                            reuse_names=set(reuse_versions), reuse_versions=reuse_versions,
                            build_pins=_parse_lock_pins(build_bytes.decode("utf-8", "replace")))
    status["partition"] = "validated"
    return status


def _canonical_env_bytes(env: dict) -> bytes:
    return json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _glibc_tuple(v: str):
    m = re.search(r"(\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


_APT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9+.\-]*")
_APT_ARCH_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")


def _parse_apt_token(tok: str):
    """Parse a Debian binary-package identity 'name' or 'name:arch'. Fails closed on
    multiple/empty/malformed qualifiers. Returns (name, arch_or_None)."""
    if tok.count(":") > 1:
        raise ReleaseError(f"apt token has multiple ':' qualifiers: {tok!r}")
    if ":" in tok:
        name, arch = tok.split(":", 1)
        if not _APT_NAME_RE.fullmatch(name) or not _APT_ARCH_RE.fullmatch(arch):
            raise ReleaseError(f"malformed architecture-qualified apt token: {tok!r}")
        return name, arch
    if not _APT_NAME_RE.fullmatch(tok):
        raise ReleaseError(f"malformed apt package name: {tok!r}")
    return tok, None


def _validate_apt_environment(authorized_text: str, env: dict) -> None:
    """Prove every authorized apt pin is actually installed in the recorded environment,
    architecture-aware (finding 2). ``env['apt']`` is the recorded installed-package map
    (``${binary:Package}`` -> ``${Version}``, installed-only; captured that way). Authorized
    pins are a SUBSET; extra/transitive packages are allowed (they remain fully recorded and
    covered by environment_sha256). Matching rules:
      * apt_architecture must be the target dpkg arch (armhf);
      * an explicitly qualified 'name:arch' matches that exact arch (native bare entries count
        as the native arch);
      * an unqualified 'name' resolves to exactly one NATIVE variant; a foreign-only match or
        multiple recorded architecture variants fail closed;
      * versions compare byte-exactly (epoch + Debian revision included).
    """
    apt_arch = env.get("apt_architecture")
    if apt_arch != TARGET_DPKG_ARCH:
        raise ReleaseError(f"builder apt_architecture must be {TARGET_DPKG_ARCH!r}; got {apt_arch!r}")
    recorded = env.get("apt")
    if not isinstance(recorded, dict) or not recorded:
        raise ReleaseError("provenance.builder.environment.apt must be a non-empty mapping")
    idx: dict = {}
    for key, ver in recorded.items():
        if not isinstance(ver, str) or not ver:
            raise ReleaseError(f"environment.apt[{key!r}] version must be a non-empty string")
        rname, rarch = _parse_apt_token(key)
        idx.setdefault(rname, []).append((rarch, ver))
    authorized = [ln.strip() for ln in authorized_text.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    if not authorized:
        raise ReleaseError("apt-packages.list has no authorized pins")
    for entry in authorized:
        if "=" not in entry:
            raise ReleaseError(f"apt-packages.list entry not name[:arch]=version: {entry!r}")
        tokpart, aver = entry.split("=", 1)
        aname, aarch = _parse_apt_token(tokpart)
        if not aver:
            raise ReleaseError(f"apt-packages.list entry missing version: {entry!r}")
        variants = idx.get(aname, [])
        if aarch is not None:
            matches = [(ra, v) for (ra, v) in variants
                       if ra == aarch or (ra is None and aarch == apt_arch)]
        else:
            if len(variants) > 1:
                raise ReleaseError(f"apt package {aname!r} has multiple architecture variants; "
                                   "the authorized entry must be architecture-qualified")
            matches = [(ra, v) for (ra, v) in variants if ra is None or ra == apt_arch]
        if not matches:
            raise ReleaseError(f"authorized apt package {entry!r} is not installed as a "
                               "native/architecture-matching package in the builder environment")
        if len(matches) > 1:
            raise ReleaseError(f"authorized apt package {entry!r} is ambiguous across architectures")
        _ra, rver = matches[0]
        if rver != aver:
            raise ReleaseError(f"authorized apt package {aname!r} version mismatch: "
                               f"authorized {aver!r}, installed {rver!r}")


def _validate_builder(builder: object, *, recipe_sha256: str, build_backends_lock_sha256: str,
                      build_backends_lock_text: str, apt_packages_sha256: str,
                      rustup_init_file_sha256: str, apt_packages_text: str,
                      extractor_tools_lock_sha256: str,
                      build_backends_source_allowlist_sha256: str, manifest_bytes=None,
                      image_context_expected: dict = None) -> None:
    """Fail-closed validation of the builder provenance block. Binds the builder to
    the COMMITTED recipe and the COMMITTED build-backends lock (by sha256), the pinned
    base image, and the STORE-AGNOSTIC runtime identity (runtime_image_id +
    image_manifest_digest + image_config_digest + image_identity_mode): when manifest bytes
    are provided the mode is re-derived and cross-checked (containerd: runtime_image_id ==
    manifest digest; legacy: == config digest; both/neither -> fail closed), and a declared
    environment whose build_backends match the authorized lock and whose glibc does not
    exceed the target baseline."""
    if not isinstance(builder, dict):
        raise ReleaseError("provenance.builder must be an object")
    if not isinstance(builder.get("identity"), str) or not builder["identity"]:
        raise ReleaseError("provenance.builder.identity is required")
    # IMAGE-CONTEXT BINDING: the six committed files that construct the image, proven byte-for-byte
    # (five read back from inside the executing image, the recipe via Phase-A CCC_RECIPE_SHA256).
    # ALWAYS enforced structurally -- there is no legacy provenance without these fields; the
    # committed-byte comparison additionally runs whenever the caller supplies them (produce_release
    # and the builder's in-container self-check both do).
    validate_image_context(builder.get("image_context"), builder.get("image_context_sha256"),
                           expected=image_context_expected)
    if "image_digest" in builder:
        raise ReleaseError("provenance.builder.image_digest is ambiguous; use base_image_digest + "
                           "image_manifest_digest + runtime_image_id + image_config_digest")
    if "image_id" in builder:
        raise ReleaseError("provenance.builder.image_id is ambiguous under the containerd image "
                           "store; use runtime_image_id + image_config_digest + image_identity_mode")
    if builder.get("recipe_path") != CANONICAL_RECIPE_PATH:
        raise ReleaseError(f"provenance.builder.recipe_path must be {CANONICAL_RECIPE_PATH!r}")
    if not _is_hex64(builder.get("recipe_sha256")) or builder["recipe_sha256"] != recipe_sha256:
        raise ReleaseError("provenance.builder.recipe_sha256 must match the committed builder recipe")
    if not _is_hex64(builder.get("build_backends_lock_sha256")) \
       or builder["build_backends_lock_sha256"] != build_backends_lock_sha256:
        raise ReleaseError("provenance.builder.build_backends_lock_sha256 must match the committed "
                           "requirements-build-backends.lock")
    if not _is_hex64(builder.get("apt_packages_sha256")) \
       or builder["apt_packages_sha256"] != apt_packages_sha256:
        raise ReleaseError("provenance.builder.apt_packages_sha256 must match the committed apt-packages.list")
    if not _is_hex64(builder.get("rustup_init_file_sha256")) \
       or builder["rustup_init_file_sha256"] != rustup_init_file_sha256:
        raise ReleaseError("provenance.builder.rustup_init_file_sha256 must match the committed rustup-init.sha256")
    if not _is_hex64(builder.get("extractor_tools_lock_sha256")) \
       or builder["extractor_tools_lock_sha256"] != extractor_tools_lock_sha256:
        raise ReleaseError("provenance.builder.extractor_tools_lock_sha256 must match the committed "
                           "requirements-extractor-tools.lock (missing/malformed/mismatched/substituted)")
    if not _is_hex64(builder.get("build_backends_source_allowlist_sha256")) \
       or builder["build_backends_source_allowlist_sha256"] != build_backends_source_allowlist_sha256:
        raise ReleaseError("provenance.builder.build_backends_source_allowlist_sha256 must match the "
                           "committed requirements-build-backends.source-allowlist "
                           "(missing/malformed/mismatched/substituted)")
    if not _is_oci_digest(builder.get("base_image_digest")):
        raise ReleaseError("provenance.builder.base_image_digest must be 'sha256:<64 lowercase hex>'")
    if not _is_oci_digest(builder.get("image_manifest_digest")):
        raise ReleaseError("provenance.builder.image_manifest_digest must be 'sha256:<64 lowercase hex>'")
    # Store-agnostic runtime identity (containerd image store: .Id is the MANIFEST digest; legacy
    # graphdriver: .Id is the CONFIG digest). runtime_image_id is what Phase B executes.
    if not _is_oci_digest(builder.get("runtime_image_id")):
        raise ReleaseError("provenance.builder.runtime_image_id (docker .Id) is required, sha256:<64hex>")
    if not _is_oci_digest(builder.get("image_config_digest")):
        raise ReleaseError("provenance.builder.image_config_digest is required, sha256:<64hex>")
    _mode = builder.get("image_identity_mode")
    if _mode not in (_ocim.MODE_CONTAINERD, _ocim.MODE_LEGACY):
        raise ReleaseError("provenance.builder.image_identity_mode must be "
                           f"{_ocim.MODE_CONTAINERD!r} or {_ocim.MODE_LEGACY!r}")
    # SHARED, structural validation (release/oci_manifest): parse the raw manifest, enforce the
    # single-image schema-2/OCI shape + descriptors, RE-DERIVE the identity mode from the runtime
    # id, reject a recorded mode that disagrees (mode confusion), and cross-check the recomputed
    # manifest/config digests against the recorded fields. Index manifests are NOT allowed here.
    if manifest_bytes is not None:
        try:
            _r = _ocim.validate_capture(
                manifest_bytes, runtime_image_id=builder["runtime_image_id"],
                allow_index=False, expected_mode=_mode)
        except _ocim.ManifestError as _exc:
            raise ReleaseError(f"builder manifest identity invalid: {_exc}") from _exc
        if _r["manifest_digest"] != builder["image_manifest_digest"]:
            raise ReleaseError("provenance.builder.image_manifest_digest != recomputed manifest digest")
        if _r["config_digest"] != builder["image_config_digest"]:
            raise ReleaseError("provenance.builder.image_config_digest != manifest config.digest")
    env = builder.get("environment")
    if not isinstance(env, dict) or any(not isinstance(env.get(k), str) or not env.get(k)
                                        for k in _ENV_REQUIRED):
        raise ReleaseError(f"provenance.builder.environment must declare non-empty {list(_ENV_REQUIRED)}")
    # Architecture-aware, installed-state binding: every authorized apt pin must be present
    # in the recorded environment at the exact version (finding 2).
    _validate_apt_environment(apt_packages_text, env)
    if not isinstance(env.get("build_backends"), dict) or not env["build_backends"]:
        raise ReleaseError("provenance.builder.environment.build_backends must be a non-empty mapping")
    # STRUCTURAL Jammy/arch enforcement (finding 9): not merely a non-empty OS string.
    if env["os_id"] != TARGET_OS_ID or env["os_version_id"] != TARGET_OS_VERSION:
        raise ReleaseError(f"builder OS must be {TARGET_OS_ID} {TARGET_OS_VERSION} (Jammy); "
                           f"got id={env['os_id']!r} version_id={env['os_version_id']!r}")
    if env["arch"] not in TARGET_ARCH:
        raise ReleaseError(f"builder arch must be one of {list(TARGET_ARCH)} (armv7l target); got {env['arch']!r}")
    tgt, got = _glibc_tuple(TARGET_GLIBC), _glibc_tuple(env["glibc"])
    if got is None or got > tgt:
        raise ReleaseError(f"builder glibc {env['glibc']!r} exceeds target baseline {TARGET_GLIBC} "
                           "(wheels must be built no newer than Ubuntu 22.04 armhf)")
    # Every AUTHORIZED backend (from the committed lock) must be installed at the pinned
    # version (finding 5): declared environment is bound to the authoritative lock.
    backend_pins = _parse_lock_pins(build_backends_lock_text)
    if not backend_pins:
        raise ReleaseError("build-backends lock is empty/invalid (must pin >=1 backend)")
    bbn = {str(k).lower().replace("_", "-"): v for k, v in env["build_backends"].items()}
    for name, (ver, _h) in backend_pins.items():
        if bbn.get(name) != ver:
            raise ReleaseError(f"authorized build backend {name}=={ver} not present in the recorded "
                               f"environment at that version (got {bbn.get(name)!r})")
    if not _is_hex64(builder.get("environment_sha256")):
        raise ReleaseError("provenance.builder.environment_sha256 must be a sha256")
    if builder["environment_sha256"] != sha256_hex(_canonical_env_bytes(env)):
        raise ReleaseError("provenance.builder.environment_sha256 does not match the recorded environment")


def _validate_provenance(obj: dict, wheelhouse_members: dict, bundle_sha256: str,
                         build_lock_text: str, recipe_sha256: str,
                         build_backends_lock_sha256: str, build_backends_lock_text: str,
                         apt_packages_sha256: str, rustup_init_file_sha256: str,
                         apt_packages_text: str, extractor_tools_lock_sha256: str,
                         build_backends_source_allowlist_sha256: str,
                         image_manifest_bytes=None, reuse_authz_text=None, target_tags=None,
                         expected_target_tags_sha256=None, image_context_expected=None) -> None:
    """Strict, fail-closed provenance schema + cross-check against the ACTUAL
    embedded wheelhouse and its SHA256SUMS.

    Schema:
      builder: {identity, recipe_path, recipe_sha256, base_image_digest,
                image_manifest_digest, runtime_image_id, image_config_digest,
                image_identity_mode, environment{...}, environment_sha256,
                image_context{<exactly the six IMAGE_CONTEXT_FILES>: sha256},
                image_context_sha256}
      NOTE: image_context + image_context_sha256 are MANDATORY (no legacy mode). Their
      structure is always validated; when image_context_expected is supplied they must also
      equal the canonical committed bytes.
      bundle:  {sha256: <bundle_sha256>}
      wheels:  [{sdist_name:str, sdist_sha256:hex64,
                 wheel_filename:str, wheel_sha256:hex64}, ...]

    Cross-checks: the set of recorded wheel_filenames == the set of actual .whl-ish
    files in the wheelhouse (no missing / extra / duplicate); each recorded
    wheel_sha256 == the real file sha256 == its SHA256SUMS entry; SHA256SUMS is
    present and covers EXACTLY the wheels; provenance.bundle.sha256 == the embedded
    bundle digest. Anything short of an exact description of the embedded bundle
    fails closed."""
    _validate_builder(obj.get("builder"), recipe_sha256=recipe_sha256,
                      build_backends_lock_sha256=build_backends_lock_sha256,
                      build_backends_lock_text=build_backends_lock_text,
                      apt_packages_sha256=apt_packages_sha256,
                      rustup_init_file_sha256=rustup_init_file_sha256,
                      apt_packages_text=apt_packages_text,
                      extractor_tools_lock_sha256=extractor_tools_lock_sha256,
                      build_backends_source_allowlist_sha256=build_backends_source_allowlist_sha256,
                      manifest_bytes=image_manifest_bytes,
                      image_context_expected=image_context_expected)
    bundle = obj.get("bundle")
    if not isinstance(bundle, dict) or bundle.get("sha256") != bundle_sha256:
        raise ReleaseError("provenance.bundle.sha256 must equal the embedded bundle digest")
    wheels = obj.get("wheels")
    if not isinstance(wheels, list) or not wheels:
        raise ReleaseError("provenance.wheels must be a non-empty list")

    # Actual wheelhouse contents (strip the fixed prefix); split payload vs metadata.
    actual: dict = {}
    sums: dict = {}
    for arc, dat in wheelhouse_members.items():
        fn = arc.split("/", 1)[1] if "/" in arc else arc
        if fn == "SHA256SUMS":
            for ln in dat.decode("utf-8", "replace").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                parts = ln.split()
                if len(parts) < 2:
                    raise ReleaseError(f"malformed SHA256SUMS line: {ln!r}")
                sums[parts[-1]] = parts[0]
            continue
        actual[fn] = sha256_hex(dat)
    if not sums:
        raise ReleaseError("wheelhouse SHA256SUMS is missing")
    if set(sums) != set(actual):
        raise ReleaseError("SHA256SUMS does not cover exactly the embedded wheels")
    for fn, h in actual.items():
        if sums.get(fn) != h:
            raise ReleaseError(f"SHA256SUMS mismatch for {fn!r}")

    # DUAL-ORIGIN: every wheel declares exactly one origin (built|reused). Built wheels carry the
    # authorized sdist; reused wheels must NOT carry sdist fields. No package under both/neither.
    recorded: dict = {}          # wheel_filename -> wheel_sha256 (all wheels)
    built_names: dict = {}       # norm name -> version (origin=built)
    reused_names: dict = {}      # norm name -> version (origin=reused)
    reused_files: dict = {}      # wheel_filename -> sha256 (origin=reused)
    for w in wheels:
        if not isinstance(w, dict):
            raise ReleaseError("each provenance wheel record must be an object")
        origin = w.get("origin")
        if origin not in ("built", "reused"):
            raise ReleaseError(f"provenance wheel record must declare origin built|reused: {w!r}")
        wf, wh = w.get("wheel_filename"), w.get("wheel_sha256")
        if not isinstance(wf, str) or not wf or not _is_hex64(wh):
            raise ReleaseError(f"provenance wheel record missing/invalid wheel identity: {w!r}")
        if wf in recorded:
            raise ReleaseError(f"duplicate provenance wheel record: {wf!r}")
        recorded[wf] = wh
        wn, wv = _parse_wheel_name(wf)
        if wn is None:
            raise ReleaseError(f"unparseable wheel_filename: {wf!r}")
        if wn in built_names or wn in reused_names:
            raise ReleaseError(f"package {wn!r} recorded under more than one wheel/origin")
        if origin == "built":
            sn, ss = w.get("sdist_name"), w.get("sdist_sha256")
            if not isinstance(sn, str) or not sn or not _is_hex64(ss):
                raise ReleaseError(f"built wheel record needs sdist_name+sdist_sha256: {w!r}")
            built_names[wn] = wv
        else:  # reused
            if w.get("sdist_name") is not None or w.get("sdist_sha256") is not None:
                raise ReleaseError(f"reused wheel record must NOT carry sdist fields: {w!r}")
            reused_names[wn] = wv
            reused_files[wf] = wh
    if set(recorded) != set(actual):
        missing = sorted(set(actual) - set(recorded))
        extra = sorted(set(recorded) - set(actual))
        raise ReleaseError(f"provenance wheels != embedded wheels (missing={missing}, extra={extra})")
    for wf, wh in recorded.items():
        if actual[wf] != wh:
            raise ReleaseError(f"provenance wheel_sha256 mismatch for {wf!r}")

    # BUILT authorization: every built sdist must be authorized by the (six-entry) build lock,
    # and the set of built packages must EQUAL the build-lock package set (no missing/extra). An
    # unapproved sdist cannot be built and then legitimized by writing its hash into provenance.
    build_pins = _parse_lock_pins(build_lock_text)
    if not build_pins:
        raise ReleaseError("empty/invalid armv7 build-input lock")
    src_pkgs: dict = {}
    for w in wheels:
        if w.get("origin") != "built":
            continue
        sd, ss = w["sdist_name"], w["sdist_sha256"]
        name, ver = _parse_sdist_name(sd) if isinstance(sd, str) else (None, None)
        if name is None:
            raise ReleaseError(f"unparseable sdist_name: {sd!r}")
        if name in src_pkgs:
            raise ReleaseError(f"duplicate source record for package: {name!r}")
        src_pkgs[name] = (ver, ss)
        if name not in build_pins:
            raise ReleaseError(f"unapproved sdist (absent from build lock): {name!r}")
        bver, bhashes = build_pins[name]
        if ver != bver:
            raise ReleaseError(f"sdist version mismatch for {name!r}: {ver} != {bver}")
        if ss not in bhashes:
            raise ReleaseError(f"sdist hash not authorized by build lock for {name!r}")
    if set(src_pkgs) != set(build_pins):
        missing = sorted(set(build_pins) - set(src_pkgs))
        extra = sorted(set(src_pkgs) - set(build_pins))
        raise ReleaseError(f"built wheels != build lock (missing={missing}, extra={extra})")

    # REUSED authorization: every reused wheel must match the committed reuse authorization by
    # EXACT filename + sha256; the reused package/version set must EQUAL the authorization; and
    # provenance.authorizers.reuse_authz_sha256 must bind the exact canonical authorization bytes.
    if reused_files or reused_names:
        if reuse_authz_text is None:
            raise ReleaseError("reused wheels present but no reuse authorization supplied")
        _raw = reuse_authz_text.encode("utf-8") if isinstance(reuse_authz_text, str) else reuse_authz_text
        authz = _reuse_authz.load_and_validate(_raw, target_tags=target_tags)
        authz_by_file = {w["filename"]: w for w in authz["wheels"]}
        authz_names = {w["name"]: w["version"] for w in authz["wheels"]}
        if set(reused_files) != set(authz_by_file):
            missing = sorted(set(authz_by_file) - set(reused_files))
            extra = sorted(set(reused_files) - set(authz_by_file))
            raise ReleaseError(f"reused wheels != reuse authorization (missing={missing}, extra={extra})")
        for wf, wh in reused_files.items():
            if authz_by_file[wf]["sha256"] != wh:
                raise ReleaseError(f"reused wheel {wf!r} sha256 not authorized by reuse authorization")
        if reused_names != authz_names:
            raise ReleaseError("reused package/version set != reuse authorization")
        authorizers = obj.get("authorizers")
        expected = _reuse_authz.sha256_hex(_reuse_authz.canonical_bytes(authz))
        if not isinstance(authorizers, dict) or authorizers.get("reuse_authz_sha256") != expected:
            raise ReleaseError("provenance.authorizers.reuse_authz_sha256 must bind the exact "
                               "canonical reuse authorization bytes")
        if expected_target_tags_sha256 is not None and \
           authorizers.get("target_tags_sha256") != expected_target_tags_sha256:
            raise ReleaseError("provenance.authorizers.target_tags_sha256 must equal the committed "
                               "target-tag artifact sha256 (recomputed from canonical source bytes)")
    # PARTITION: built and reused are disjoint (enforced above) and together cover EXACTLY the
    # embedded wheelhouse packages -- no missing, extra, duplicate, or foreign-origin package.
    wheel_pkgs = {_parse_wheel_name(fn)[0] for fn in actual}
    if (set(built_names) | set(reused_names)) != wheel_pkgs:
        raise ReleaseError("built|reused packages do not partition the wheelhouse exactly")


def _secret_scan(tree: dict) -> None:
    """Fail-closed pre-sign scan of the composed artifact tree (Invariant I2 +
    no-NUL-in-text). Rejects forbidden secret files and private-key markers, and
    rejects any NUL byte in a NON-binary member. Only true binary payloads (by
    extension: wheels, images, archives, fonts, compiled objects) are exempt --
    extensionless executables/scripts AND textual wheelhouse metadata (e.g.
    SHA256SUMS) ARE scanned."""
    for arcname, data in tree.items():
        base = arcname.rsplit("/", 1)[-1]
        if base in _FORBIDDEN_BASENAMES or base.endswith((".pem", ".key")):
            raise ReleaseError(f"refusing to package secret-bearing file: {arcname!r}")
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        if ext in _BINARY_EXTS:
            continue   # legitimately-binary payload: exempt from marker/NUL scan
        for marker in _SECRET_MARKERS:
            if marker in data:
                raise ReleaseError(f"private-key marker found in {arcname!r}")
        if b"\x00" in data:
            raise ReleaseError(f"NUL byte in text member {arcname!r} (no-NUL-in-text invariant)")


# --- Canonicalization layer (.gitattributes-driven) ------------------------ #
#
# ADR-0003 defines a *Canonical Release Artifact*. Canonicality is a property of
# the ARTIFACT (deterministic, reproducible, platform-independent bytes), not of
# the storage backend. Git is therefore ONE valid producer of a source tree, not
# the definition of canonical. The production producer is tag-only (--git-ref);
# collected {path -> bytes} tree through this layer before packing, so the same
# content yields byte-identical artifacts regardless of the OS/checkout that
# produced the tree (this is what a Windows CRLF checkout broke for 0.3.13).
#
# Classification is EXPLICIT-FIRST and FAIL-SAFE:
#   * the tree's own `.gitattributes` is the ruleset (text / -text / binary /
#     eol=lf) — the same declaration Git checkout and `git archive` honour;
#   * files with no explicit rule fall back to a conservative content sniff;
#   * anything uncertain or detected-binary is left BYTE-EXACT (never rewritten),
#     so a misclassification can never corrupt a binary.
# The ONLY transformation applied is CRLF/CR -> LF for text files. The canonical
# artifact is LF-only (Linux target).


def parse_gitattributes(text: str) -> list[tuple[str, dict]]:
    """Parse the subset of `.gitattributes` relevant to canonicalization.

    Returns an ordered list of (pattern, attrs). `attrs` may contain:
      * "text": True (text) | False (binary / -text) | "auto" (text=auto)
      * "eol": "lf" | "crlf"  (an explicit eol also implies text)
    Later matching rules override earlier ones (Git's last-match-wins).
    Unrecognised tokens (diff, filter, merge, …) are ignored.
    """
    rules: list[tuple[str, dict]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern, tokens = parts[0], parts[1:]
        attrs: dict = {}
        for tok in tokens:
            if tok in ("binary", "-text", "!text"):
                attrs["text"] = False
            elif tok == "text":
                attrs["text"] = True
            elif tok == "text=auto":
                attrs["text"] = "auto"
            elif tok.startswith("eol="):
                attrs["eol"] = tok.split("=", 1)[1]
                attrs.setdefault("text", True)   # an explicit eol implies text
        if attrs:
            rules.append((pattern, attrs))
    return rules


def _attr_match(pattern: str, path_rel: str) -> bool:
    """Pragmatic gitattributes/gitignore-style match (glob via fnmatch).

    A pattern without a slash matches the basename at any depth; a pattern with
    a slash (or a leading `/`) matches the full repo-relative path.
    """
    name = path_rel.rsplit("/", 1)[-1]
    if pattern.startswith("/"):
        return fnmatch.fnmatch(path_rel, pattern[1:])
    if "/" in pattern:
        return fnmatch.fnmatch(path_rel, pattern)
    return fnmatch.fnmatch(name, pattern)


def attrs_for(path_rel: str, rules: list[tuple[str, dict]]) -> dict:
    """Merge all rules matching `path_rel`; later rules win per attribute."""
    merged: dict = {}
    for pattern, attrs in rules:
        if _attr_match(pattern, path_rel):
            merged.update(attrs)
    return merged


def _looks_binary(data: bytes) -> bool:
    """Conservative binary sniff: a NUL byte in the first 8 KiB (Git's heuristic).
    Used only for files with no explicit text/binary declaration."""
    return b"\x00" in data[:8192]


# NOTE: _to_lf and canonical_file_sha256 are re-exported near the imports from the stdlib-only
# release.canonical_bytes module -- there is exactly ONE implementation and it is not here.


def is_text(path_rel: str, data: bytes, rules: list[tuple[str, dict]]) -> bool:
    """Decide whether a file is text (and thus LF-normalised) or left byte-exact.

    Explicit declaration wins; otherwise a conservative content sniff decides,
    defaulting to "leave alone" (binary) when a NUL byte is present.
    """
    text = attrs_for(path_rel, rules).get("text")
    if text is False:          # binary / -text  -> byte-exact
        return False
    if text is True:           # text / eol=…    -> LF
        return True
    # text == "auto" or unset  -> conservative content sniff
    return not _looks_binary(data)


def canonicalize_tree(raw: dict[str, bytes]) -> dict[str, bytes]:
    """Apply the canonicalization ruleset to a collected {arcname -> bytes} tree.

    The ruleset is the tree's OWN `.gitattributes` (if present), so the same
    declaration used by Git checkout / `git archive` drives release production.
    Text files are LF-normalised; binary/uncertain files are untouched.
    """
    rules = parse_gitattributes(raw.get(".gitattributes", b"").decode("utf-8", "replace"))
    canon: dict[str, bytes] = {}
    for arcname in sorted(raw):
        data = raw[arcname]
        if is_text(arcname, data, rules):
            data = _to_lf(data)
        canon[arcname] = data
    return canon


# --- Tree collectors (producers) ------------------------------------------- #

def _raw_from_dir(source_dir: str) -> dict[str, bytes]:
    """Collect {arcname -> raw bytes} from a local source directory (generic /
    backwards-compatible canonicalization helper used by deterministic-artifact
    tests). This is NOT a production provenance path -- production releases are
    tag-only via --git-ref. The `.git` directory, if present, is excluded."""
    src = os.path.abspath(source_dir)
    if not os.path.isdir(src):
        raise ReleaseError(f"source is not a directory: {source_dir!r}")
    raw: dict[str, bytes] = {}
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != ".git"]
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            arcname = os.path.relpath(path, src).replace(os.sep, "/")
            with open(path, "rb") as fh:
                raw[arcname] = fh.read()
    return raw


def _raw_from_git_ref(ref: str, repo_dir: str = ".") -> dict[str, bytes]:
    """Collect {path -> blob bytes} for all tracked files at `ref` straight from
    the Git object database (the canonical `--git-ref` producer).

    Blob bytes are read with `git cat-file`, so they are the normalised content
    stored on commit — independent of the working tree's OS/checkout. They still
    pass through `canonicalize_tree` (idempotent belt-and-suspenders)."""
    listing = _run(["git", "-C", repo_dir, "ls-tree", "-r", "-z", "--name-only", ref])
    if listing.returncode != 0:
        raise ReleaseError(
            f"git ls-tree failed for ref {ref!r}: {listing.stderr.decode(errors='replace').strip()}"
        )
    names = [n for n in listing.stdout.decode("utf-8").split("\0") if n]
    raw: dict[str, bytes] = {}
    for name in names:
        blob = _run(["git", "-C", repo_dir, "cat-file", "blob", f"{ref}:{name}"])
        if blob.returncode != 0:
            raise ReleaseError(
                f"git cat-file failed for {ref}:{name}: {blob.stderr.decode(errors='replace').strip()}"
            )
        raw[name] = blob.stdout
    return raw


# --- Content-fixed artifact packer ----------------------------------------- #

def pack_tree(mapping: dict[str, bytes]) -> bytes:
    """Pack a {arcname -> bytes} mapping into a content-fixed .tar.gz.

    Determinism is enforced by sorting members and normalising metadata
    (mtime=0, uid/gid=0, empty owner names, canonical mode 0644) plus a gzip
    header with mtime=0. Two runs over identical content yield identical bytes,
    so the content digest is stable.
    """
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for arcname in sorted(mapping):
            data = mapping[arcname]
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))

    out = io.BytesIO()
    # gzip with mtime=0 so the gzip header is deterministic.
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def build_deterministic_artifact(source_dir: str) -> bytes:
    """Canonicalize a source directory and pack it into a content-fixed .tar.gz.

    Backwards-compatible convenience wrapper: collect -> canonicalize -> pack.
    Line endings are normalised per the tree's `.gitattributes`, so a CRLF
    working-tree checkout can no longer contaminate the artifact.
    """
    return pack_tree(canonicalize_tree(_raw_from_dir(source_dir)))


def build_canonical_artifact_from_git_ref(ref: str, repo_dir: str = ".") -> bytes:
    """Canonical production build: object-DB tree at `ref` -> canonicalize -> pack."""
    return pack_tree(canonicalize_tree(_raw_from_git_ref(ref, repo_dir)))


# --- Signing (SSH / Ed25519) ----------------------------------------------- #

def _run(cmd: list[str], *, stdin: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, input=stdin, capture_output=True, shell=False,
    )


def public_allowed_signers_line(key_path: str, principal: str) -> str:
    """Derive the allowed-signers (trust-store) entry for a signing key.

    The publisher publishes THIS public line; the device trust store (Epic B) is
    built from it. The private key is never read by this helper — `ssh-keygen -y`
    derives the public key only.
    """
    proc = _run(["ssh-keygen", "-y", "-f", key_path])
    if proc.returncode != 0:
        raise ReleaseError(f"cannot derive public key: {proc.stderr.decode(errors='replace').strip()}")
    pub = proc.stdout.decode().strip()
    # allowed_signers: "<principal> <keytype> <base64>"; drop any trailing comment.
    parts = pub.split()
    if len(parts) < 2:
        raise ReleaseError("unexpected public-key format from ssh-keygen -y")
    return f"{principal} {parts[0]} {parts[1]}"


def sign_manifest(manifest_path: str, key_path: str, *, namespace: str = SSHSIG_NAMESPACE) -> str:
    """Sign the manifest file with ssh-keygen -Y sign; returns the .sig path.

    ssh-keygen writes `<manifest_path>.sig` (armored SSHSIG). The signed bytes are
    exactly the manifest file's bytes (which are canonical, see above).

    Note: `ssh-keygen -Y sign` does NOT overwrite an existing signature file, so a
    stale `.sig` would silently survive a re-sign. To guarantee the returned
    signature is the one just produced by `key_path`, any pre-existing signature
    is removed first."""
    sig_path = manifest_path + ".sig"
    try:
        os.remove(sig_path)
    except FileNotFoundError:
        pass
    proc = _run(["ssh-keygen", "-Y", "sign", "-f", key_path, "-n", namespace, manifest_path])
    if proc.returncode != 0:
        raise ReleaseError(f"signing failed: {proc.stderr.decode(errors='replace').strip()}")
    if not os.path.exists(sig_path):
        raise ReleaseError("signing produced no signature file")
    return sig_path


def verify_signed_manifest(
    manifest_path: str,
    sig_path: str,
    allowed_signers_path: str,
    *,
    identity: str,
    namespace: str = SSHSIG_NAMESPACE,
) -> bool:
    """Producer-side QA verification (proves the Signed Object is verifiable
    offline). The DEVICE verifier is Epic B; this helper is not the device path.
    Returns True iff the signature verifies against the allowed-signers store."""
    with open(manifest_path, "rb") as fh:
        data = fh.read()
    proc = _run(
        ["ssh-keygen", "-Y", "verify", "-f", allowed_signers_path,
         "-I", identity, "-n", namespace, "-s", sig_path],
        stdin=data,
    )
    return proc.returncode == 0


# --- Release production ----------------------------------------------------- #

def _resolve_source(git_ref, repo_dir):
    """Resolve the canonical source tree + provenance from a TAG only (ADR-0003 I4).
    Production releases MUST be built from a tag under refs/tags/; caller-asserted
    source directories/commits/tags are NOT accepted (that path is removed). The
    recorded commit is the peeled tag commit (annotated tags handled), and the tree
    is archived from that same tagged commit."""
    if not git_ref:
        raise ReleaseError("--git-ref vX.Y.Z is required (tagged-source provenance; I4)")
    chk = _run(["git", "-C", repo_dir, "rev-parse", "--verify", "--quiet", f"refs/tags/{git_ref}"])
    if chk.returncode != 0:
        raise ReleaseError(f"--git-ref must be a tag under refs/tags/: {git_ref!r}")
    peel = _run(["git", "-C", repo_dir, "rev-parse", f"refs/tags/{git_ref}^{{commit}}"])
    if peel.returncode != 0:
        raise ReleaseError(f"cannot peel tag {git_ref!r} to a commit")
    commit = peel.stdout.decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseError(f"peeled commit is not a 40-hex sha: {commit!r}")
    raw = _raw_from_git_ref(f"refs/tags/{git_ref}", repo_dir)
    return raw, {"vcs": "git", "commit": commit, "tag": git_ref}


def produce_release(
    *,
    version: str,
    out_dir: str,
    key_path: str,
    wheelhouse_armv7_dir: str,
    provenance_armv7_path: str,
    armv7_runtime_lock_path: str,
    image_manifest_path: str,
    git_ref: Optional[str] = None,
    source_dir: Optional[str] = None,
    source_commit: Optional[str] = None,
    source_tag: Optional[str] = None,
    repo_dir: str = ".",
    expected_requirements_sha256: Optional[str] = None,
    expected_armv7_lock_sha256: Optional[str] = None,
    expected_aarch64_lock_sha256: Optional[str] = None,
    expected_armv7_build_lock_sha256: Optional[str] = None,
    recommended_conduit_core: Optional[str] = None,
) -> dict:
    """Produce the V2 signed release: TWO deterministic platform artifacts
    (aarch64 without a wheelhouse; armv7l = same source + embedded wheelhouse-armhf/)
    bound by ONE canonical V2 manifest and ONE signature. Both artifacts are
    required for a full release. Pre-sign secret-exclusion is enforced on both
    composed trees.

    Determinism: given (source commit/tag, wheelhouse bundle), both artifacts are
    byte-reproducible; the only difference is the injected wheelhouse.
    """
    if source_dir is not None or source_commit is not None or source_tag is not None:
        raise ReleaseError("caller-asserted source provenance is not accepted; use --git-ref vX.Y.Z")
    raw, source = _resolve_source(git_ref, repo_dir)
    canon = canonicalize_tree(raw)

    # Dependency digests are computed from the CANONICAL (LF-normalized) committed
    # bytes IN the artifact tree -- never from caller input -- so the binding is
    # independent of the producer's checkout line endings. Optional caller values
    # are used ONLY as expected-value cross-checks (fail closed on mismatch).
    def _canon_sha(name: str) -> str:
        if name not in canon:
            raise ReleaseError(f"required committed file missing from source tree: {name!r}")
        return sha256_hex(canon[name])
    # Build-INDEPENDENT digests come from the canonical committed bytes (pre-tag).
    requirements_sha = _canon_sha("requirements.txt")
    aarch64_lock_sha = _canon_sha("requirements-aarch64.lock")
    armv7_build_lock_sha = _canon_sha("requirements-armv7-build.lock")
    build_lock_text = canon["requirements-armv7-build.lock"].decode("utf-8", "replace")
    # The committed reused-wheel authorization (dual-origin): strictly validated here; its exact
    # canonical bytes are bound into provenance (authorizers.reuse_authz_sha256) and its package
    # set forms the reuse half of the 30-package partition (below).
    reuse_authz_bytes = canon.get(REUSE_AUTHZ_PATH)
    if reuse_authz_bytes is None:
        raise ReleaseError(f"committed reused-wheel authorization missing from source: {REUSE_AUTHZ_PATH}")
    # The committed target-tag artifact is bound by recomputing its sha256 from the CANONICAL source
    # bytes (not "it is in Git") and requiring an exact provenance match downstream.
    _target_tags_bytes = canon.get(TARGET_TAGS_PATH)
    if _target_tags_bytes is None:
        raise ReleaseError(f"committed target-tag artifact missing from source: {TARGET_TAGS_PATH}")
    # THE canonical target-tag validator -- the SAME call release_preflight makes, so the gate and
    # the producer cannot disagree (grammar, ordering, uniqueness, count, LF-canonical digest).
    _tt_ordered, _target_tags, _target_tags_sha = validate_target_tags(_target_tags_bytes)
    _reuse_validated = _reuse_authz.load_and_validate(reuse_authz_bytes, target_tags=_target_tags)
    recipe_bytes = canon.get(CANONICAL_RECIPE_PATH)
    if recipe_bytes is None:
        raise ReleaseError(f"canonical builder recipe missing from source: {CANONICAL_RECIPE_PATH}")
    recipe_sha = sha256_hex(recipe_bytes)
    bb_lock_bytes = canon.get(BUILD_BACKENDS_LOCK_PATH)
    if bb_lock_bytes is None:
        raise ReleaseError(f"committed build-backends lock missing from source: {BUILD_BACKENDS_LOCK_PATH}")
    bb_lock_text = bb_lock_bytes.decode("utf-8", "replace")
    bb_lock_sha = sha256_hex(bb_lock_bytes)
    # All three committed builder inputs are REQUIRED for release production and bound
    # by sha256 in provenance (finding 6): recipe + backend lock (above) + apt list + rustup hash.
    apt_pkgs_bytes = canon.get(APT_PACKAGES_PATH)
    if apt_pkgs_bytes is None:
        raise ReleaseError(f"committed builder input missing from source: {APT_PACKAGES_PATH}")
    apt_pkgs_sha = sha256_hex(apt_pkgs_bytes)
    rustup_bytes = canon.get(RUSTUP_SHA_PATH)
    if rustup_bytes is None:
        raise ReleaseError(f"committed builder input missing from source: {RUSTUP_SHA_PATH}")
    rustup_file_sha = sha256_hex(rustup_bytes)
    # Present builder inputs at the release gate must pass strict semantic validation
    # (finding 6): reject placeholders/malformed even if they are byte-bound in provenance.
    validate_apt_packages_list(apt_pkgs_bytes.decode("utf-8", "replace"))
    validate_rustup_sha(rustup_bytes.decode("utf-8", "replace"))
    validate_build_backends_lock(bb_lock_text)
    # The extractor-tools .in AND .lock are REQUIRED at the release/tag producer gate (F1):
    # invoking the producer means the authoritative pinned-parser inputs must both exist and
    # validate as a CLOSED tomli closure. (An incomplete pre-release working tree simply does
    # not invoke produce_release; the gate itself never accepts their absence.)
    _ext_in = canon.get(EXTRACTOR_TOOLS_IN_PATH)
    if _ext_in is None:
        raise ReleaseError(f"committed extractor-tools input missing from source: {EXTRACTOR_TOOLS_IN_PATH}")
    _ext_lock = canon.get(EXTRACTOR_TOOLS_LOCK_PATH)
    if _ext_lock is None:
        raise ReleaseError(f"committed extractor-tools lock missing from source: {EXTRACTOR_TOOLS_LOCK_PATH}")
    validate_extractor_tools_lock(_ext_lock.decode("utf-8", "replace"),
                                  _ext_in.decode("utf-8", "replace"))
    extractor_tools_lock_sha = sha256_hex(_ext_lock)
    # The backend source-allowlist is a REQUIRED, security-relevant committed input: it must
    # exist, parse strictly, and every allowlisted backend must be pinned in the backend lock.
    _allowlist = canon.get(BACKEND_SOURCE_ALLOWLIST_PATH)
    if _allowlist is None:
        raise ReleaseError(f"committed backend source-allowlist missing from source: "
                           f"{BACKEND_SOURCE_ALLOWLIST_PATH}")
    validate_backend_source_allowlist(_allowlist.decode("utf-8", "replace"), bb_lock_text)
    backend_source_allowlist_sha = sha256_hex(_allowlist)
    # The raw OCI image manifest is embedded into the SIGNED artifact so the producer
    # (and any later auditor) can INDEPENDENTLY recompute image_manifest_digest (finding 4).
    if not image_manifest_path or not os.path.isfile(image_manifest_path):
        raise ReleaseError(f"OCI image manifest file not found: {image_manifest_path!r}")
    with open(image_manifest_path, "rb") as fh:
        image_manifest_bytes = fh.read()
    if not image_manifest_bytes:
        raise ReleaseError("OCI image manifest is empty")

    # aarch64 = canonical source only (NO wheelhouse — isolation).
    aarch64_bytes = pack_tree(canon)

    # armv7l = canonical source + wheelhouse-armhf/ + provenance record + the
    # POST-TAG, build-DEPENDENT runtime wheel lock (requirements-armv7.lock),
    # injected as a content-addressed release input (never committed). Provenance is
    # strictly validated against the embedded bundle/SHA256SUMS AND the build lock.
    wh_members = _wheelhouse_members(wheelhouse_armv7_dir)
    bundle_sha = sha256_hex(pack_tree(wh_members))
    prov_bytes, prov_obj = _read_provenance(provenance_armv7_path)
    _validate_provenance(prov_obj, wh_members, bundle_sha, build_lock_text, recipe_sha,
                         bb_lock_sha, bb_lock_text, apt_pkgs_sha, rustup_file_sha,
                         apt_pkgs_bytes.decode("utf-8", "replace"), extractor_tools_lock_sha,
                         backend_source_allowlist_sha,
                         image_manifest_bytes=image_manifest_bytes,
                         reuse_authz_text=reuse_authz_bytes.decode("utf-8", "replace"),
                         target_tags=_target_tags, expected_target_tags_sha256=_target_tags_sha,
                         # Recomputed from the CANONICAL COMMITTED bytes at the release commit:
                         # provenance may not describe a build context other than the committed one.
                         image_context_expected=expected_image_context(canon))
    provenance_sha = sha256_hex(prov_bytes)
    if not armv7_runtime_lock_path or not os.path.isfile(armv7_runtime_lock_path):
        raise ReleaseError(f"armv7 runtime lock not found: {armv7_runtime_lock_path!r}")
    with open(armv7_runtime_lock_path, "rb") as fh:
        armv7_lock_bytes = _to_lf(fh.read())
    armv7_lock_sha = sha256_hex(armv7_lock_bytes)

    # Semantic lock validation at the PRODUCER boundary (not only in CI), BY ROLE:
    #   * the aarch64 lock is a valid COMPLETE SOLUTION of canonical requirements.txt (as is the
    #     durable armv7 solution lock, checked via validate_armv7_solution below);
    #   * the six-entry armv7 BUILD lock is the source-build PARTITION, validated by
    #     validate_build_partition_lock -- NOT as a complete solution (see the note below);
    #   * the injected armv7 runtime lock is valid AND a bijection with the embedded wheels.
    requirements_text = canon["requirements.txt"].decode("utf-8", "replace")
    _require_valid_lock(requirements_text,
                        canon["requirements-aarch64.lock"].decode("utf-8", "replace"),
                        "requirements-aarch64.lock")
    # NOTE: the six-entry armv7 build lock is the SOURCE-BUILD partition, NOT a full solution of
    # requirements.txt. The complete-solution property is enforced on the runtime lock (below),
    # which is a bijection with the embedded wheelhouse. The build lock still parses under the
    # closed grammar via _parse_lock_pins.
    _validate_runtime_lock_against_wheelhouse(
        armv7_lock_bytes.decode("utf-8", "replace"), wh_members, requirements_text)
    # DURABLE SOLUTION: the committed 30-pin solution is the authoritative closure; the six-entry
    # build lock + 24-entry reuse authorization must partition it EXACTLY with agreeing versions.
    _solution_bytes = canon.get(SOLUTION_LOCK_PATH)
    if _solution_bytes is None:
        raise ReleaseError(f"durable 30-pin solution missing from source: {SOLUTION_LOCK_PATH}")
    _reuse_versions = {w["name"]: w["version"] for w in _reuse_validated["wheels"]}
    # Role-appropriate validation of the six-entry build lock (grammar + exactly the approved six),
    # via the SAME shared helper the lock-schema fixtures and the drift test use.
    _build_pins = validate_build_partition_lock(build_lock_text)
    validate_armv7_solution(_solution_bytes.decode("utf-8", "replace"), requirements_text,
                            reuse_names=set(_reuse_versions), reuse_versions=_reuse_versions,
                            build_pins=_build_pins)
    # PARTITION invariant (dual-origin): the build lock (source-built) and the reuse authorization
    # (official wheels) are DISJOINT and together cover EXACTLY the runtime-lock package closure.
    _build_names = set(_build_pins)
    _reuse_names = {w["name"] for w in _reuse_validated["wheels"]}
    _runtime_names = set(_parse_lock_pins(armv7_lock_bytes.decode("utf-8", "replace")))
    if _build_names & _reuse_names:
        raise ReleaseError(f"build/reuse origin overlap: {sorted(_build_names & _reuse_names)}")
    if (_build_names | _reuse_names) != _runtime_names:
        _miss = sorted(_runtime_names - (_build_names | _reuse_names))
        _extra = sorted((_build_names | _reuse_names) - _runtime_names)
        raise ReleaseError("build lock + reuse authorization do not partition the runtime lock "
                           f"(missing={_miss}, extra={_extra})")
    # v0.3.17 approved-partition policy (single source): exactly the approved six built, 24 reused, 30 total.
    if _build_names != set(V0317_SOURCE_BUILD_PACKAGES):
        raise ReleaseError(f"build lock packages != approved six {sorted(V0317_SOURCE_BUILD_PACKAGES)}; "
                           f"got {sorted(_build_names)}")
    if (len(_build_names) != V0317_BUILT_COUNT or len(_reuse_names) != V0317_REUSED_COUNT
            or len(_runtime_names) != V0317_TOTAL_COUNT):
        raise ReleaseError(f"partition counts must be {V0317_BUILT_COUNT}/{V0317_REUSED_COUNT}/"
                           f"{V0317_TOTAL_COUNT}; got {len(_build_names)}/{len(_reuse_names)}/{len(_runtime_names)}")

    for _label, _computed, _expected in (
            ("requirements_sha256", requirements_sha, expected_requirements_sha256),
            ("aarch64_lock_sha256", aarch64_lock_sha, expected_aarch64_lock_sha256),
            ("armv7_lock_sha256", armv7_lock_sha, expected_armv7_lock_sha256),
            ("armv7_build_lock_sha256", armv7_build_lock_sha, expected_armv7_build_lock_sha256)):
        if _expected is not None and _expected != _computed:
            raise ReleaseError(f"{_label} cross-check failed: expected {_expected!r}, computed {_computed!r}")

    armv7_tree = dict(canon)
    armv7_tree.update(wh_members)
    armv7_tree["provenance/wheelhouse-armv7.json"] = prov_bytes
    armv7_tree["provenance/image-manifest.json"] = image_manifest_bytes
    armv7_tree["requirements-armv7.lock"] = armv7_lock_bytes
    armv7_bytes = pack_tree(armv7_tree)

    # Fail-closed pre-sign secret scan of BOTH composed trees.
    _secret_scan(canon)
    _secret_scan(armv7_tree)

    aarch64_name = f"ccc-{version}-aarch64.tar.gz"
    armv7_name = f"ccc-{version}-armv7l.tar.gz"
    aarch64_top = sorted({m.split("/", 1)[0] for m in canon})
    armv7_top = sorted({m.split("/", 1)[0] for m in armv7_tree})
    wheelhouse = {
        "path": "wheelhouse-armhf/",
        "bundle_sha256": bundle_sha,
        "requirements_sha256": requirements_sha,
        "lock_sha256": armv7_lock_sha,
        "build_lock_sha256": armv7_build_lock_sha,
        "provenance": "provenance/wheelhouse-armv7.json",
        "provenance_sha256": provenance_sha,
    }
    artifacts = [
        build_artifact_entry(platform="aarch64", name=aarch64_name, artifact_bytes=aarch64_bytes,
                             top_level=aarch64_top),
        build_artifact_entry(platform="armv7l", name=armv7_name, artifact_bytes=armv7_bytes,
                             top_level=armv7_top, wheelhouse=wheelhouse),
    ]
    dependency_locks = {
        "requirements_sha256": requirements_sha,
        "aarch64_lock_sha256": aarch64_lock_sha,
        "armv7_lock_sha256": armv7_lock_sha,
        "armv7_build_lock_sha256": armv7_build_lock_sha,
    }
    manifest = build_manifest(
        version=version, source=source, artifacts=artifacts,
        dependency_locks=dependency_locks,
        compatibility={"recommended_conduit_core": recommended_conduit_core},
    )

    os.makedirs(out_dir, exist_ok=True)
    aarch64_out = os.path.join(out_dir, aarch64_name)
    armv7_out = os.path.join(out_dir, armv7_name)
    manifest_out = os.path.join(out_dir, f"ccc-{version}.manifest.json")
    with open(aarch64_out, "wb") as fh:
        fh.write(aarch64_bytes)
    with open(armv7_out, "wb") as fh:
        fh.write(armv7_bytes)
    with open(manifest_out, "wb") as fh:
        fh.write(canonical_manifest_bytes(manifest))
    sig_out = sign_manifest(manifest_out, key_path)
    return {
        "artifacts": {"aarch64": aarch64_out, "armv7l": armv7_out},
        "manifest": manifest_out,
        "signature": sig_out,
    }


# --- Trust-store helper ----------------------------------------------------- #

def emit_trusted_publishers(out_path: str, key_path: str, identity: str = PRODUCT + "-publisher") -> str:
    """Write a safe `trusted_publishers` (allowed-signers) file for `key_path`.

    OpenSSH allowed-signers files must be plain UTF-8 with NO BOM and LF line
    endings. Hand-authoring on Windows (e.g. PowerShell `Set-Content -Encoding
    utf8`) injects a BOM and CRLF, which breaks `ssh-keygen -Y verify`. Writing
    the bytes here (mode "wb", trailing "\\n") guarantees UTF-8/no-BOM/LF. Only
    the PUBLIC key is read (`ssh-keygen -y`); the private key is never embedded.
    """
    line = public_allowed_signers_line(key_path, identity)
    with open(out_path, "wb") as fh:
        fh.write((line + "\n").encode("utf-8"))
    return out_path


# --- CLI -------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ADR-0003 Epic A — produce a signed CCC release.")
    p.add_argument("--version", help="release semver X.Y.Z (required unless --emit-trusted-publishers)")
    p.add_argument("--sign-key", required=True, help="path to the publisher Ed25519 private key")
    p.add_argument("--git-ref", required=True,
                   help="REQUIRED: build from this tag under refs/tags/ (vX.Y.Z). Caller-asserted "
                        "source directories/commits are not accepted (tagged-source provenance, I4).")
    p.add_argument("--wheelhouse-armv7", help="armv7 wheelhouse dir to embed in the armv7l artifact")
    p.add_argument("--provenance-armv7", help="wheelhouse provenance record (JSON) to embed + bind")
    p.add_argument("--armv7-runtime-lock", help="requirements-armv7.lock (post-tag; injected + bound)")
    p.add_argument("--image-manifest", help="raw OCI image manifest file (embedded + digest recomputed)")
    p.add_argument("--expect-requirements-sha256", default=None, help="OPTIONAL expected sha256 cross-check")
    p.add_argument("--expect-armv7-lock-sha256", default=None, help="OPTIONAL expected sha256 cross-check")
    p.add_argument("--expect-aarch64-lock-sha256", default=None, help="OPTIONAL expected sha256 cross-check")
    p.add_argument("--expect-armv7-build-lock-sha256", default=None, help="OPTIONAL expected sha256 cross-check")
    p.add_argument("--repo", default=".", help="repository directory for --git-ref (default: .)")
    p.add_argument("--recommended-core", default=None, help="advisory recommended Conduit Core version")
    p.add_argument("--out", default="dist", help="output directory for the release asset set")
    p.add_argument("--emit-trusted-publishers", metavar="PATH",
                   help="write a safe UTF-8/no-BOM/LF trusted_publishers file for --sign-key and exit")
    p.add_argument("--identity", default=PRODUCT + "-publisher",
                   help="allowed-signers principal for --emit-trusted-publishers")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.emit_trusted_publishers:
            out = emit_trusted_publishers(args.emit_trusted_publishers, args.sign_key, args.identity)
            print(f"trusted_publishers: {out}")
            return 0
        if not args.version:
            raise ReleaseError("--version is required")
        if not (args.wheelhouse_armv7 and args.provenance_armv7 and args.armv7_runtime_lock
                and args.image_manifest):
            raise ReleaseError("--wheelhouse-armv7, --provenance-armv7, --armv7-runtime-lock and "
                               "--image-manifest are required")
        result = produce_release(
            version=args.version,
            out_dir=args.out,
            key_path=args.sign_key,
            wheelhouse_armv7_dir=args.wheelhouse_armv7,
            provenance_armv7_path=args.provenance_armv7,
            armv7_runtime_lock_path=args.armv7_runtime_lock,
            image_manifest_path=args.image_manifest,
            git_ref=args.git_ref,
            repo_dir=args.repo,
            expected_requirements_sha256=args.expect_requirements_sha256,
            expected_armv7_lock_sha256=args.expect_armv7_lock_sha256,
            expected_aarch64_lock_sha256=args.expect_aarch64_lock_sha256,
            expected_armv7_build_lock_sha256=args.expect_armv7_build_lock_sha256,
            recommended_conduit_core=args.recommended_core,
        )
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for k, v in result.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
