#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/builder/read_builder_inputs.py -- the ONE strict, stdlib-only reader/validator for
the Phase-A -> Phase-B builder-inputs.kv handoff.

TRUST BOUNDARY: builder-inputs.kv is DATA, never code. It MUST NOT be `source`d, `.`-dotted, or
`eval`d by any consumer -- doing so would execute attacker- or corruption-controlled bytes
(`$(...)`, backticks, quotes, newline-smuggled assignments). This reader parses the file as pure
text, enforces the exact approved key schema and per-field constraints, and -- ONLY if the whole
file is valid -- emits the validated records as a deterministic, NUL-delimited `KEY=VALUE` stream
on stdout (exit 0). Any violation writes a diagnostic to stderr and exits non-zero with NOTHING on
stdout, so a consumer can never act on partial output.

The producer (Phase A) runs this same reader against its temporary file BEFORE atomic publication,
and the consumer (Phase B) runs it again before use: both trust boundaries validate identically.

Parser contract (all fail closed):
  * strict UTF-8; reject any NUL byte; reject CR / CRLF (LF-only); require a final LF;
  * no blank lines, no comments (`#...`), no line continuations (trailing `\\`);
  * every line must be `KEY=VALUE` with KEY matching ^[A-Z][A-Z0-9_]*$;
  * reject duplicate, foreign, or missing keys -- the key set must EQUAL the approved set;
  * per-field validation for digests, bare hashes, identity mode, capture transport, image tag,
    builder identity, and controlled absolute paths (no traversal, no shell metacharacters).

Public helpers from oci_manifest are reused where one exists (digest shape, identity-mode names).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# Import the shared manifest validator by adding release/ (the parent of this builder/ dir) to the
# path, so this stays runnable as a plain script from any cwd (Phase A/B, tests, RPi2 ceremony).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import oci_manifest as _ocim  # noqa: E402  (path set up above; stdlib-only module)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_KEYLINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")

_BUILDER_IDENTITY = "conduit-control-center-armv7-wheelhouse-builder"
_TRANSPORTS = ("oci-archive", "docker-archive")
_MODES = (_ocim.MODE_CONTAINERD, _ocim.MODE_LEGACY)   # index mode is smoke-only, never in this contract
_MAX_VALUE = 512   # generous bound; every legitimate field is far shorter


class InputError(ValueError):
    """Raised on any schema or field violation (fail closed)."""


def _v_digest(v: str) -> None:
    if not _ocim.is_sha256_digest(v):
        raise InputError("must be a lowercase sha256:<64hex> OCI digest")


def _v_hex64(v: str) -> None:
    if not _HEX64.match(v):
        raise InputError("must be a bare lowercase 64-hex sha256")


def _v_mode(v: str) -> None:
    if v not in _MODES:
        raise InputError(f"must be one of {_MODES}")


def _v_transport(v: str) -> None:
    if v not in _TRANSPORTS:
        raise InputError(f"must be one of {_TRANSPORTS}")


def _v_tag(v: str) -> None:
    if len(v) > _MAX_VALUE or not _TAG.match(v):
        raise InputError("must be a safe OCI tag reference (no spaces/metacharacters)")


def _v_identity(v: str) -> None:
    if v != _BUILDER_IDENTITY:
        raise InputError(f"must equal {_BUILDER_IDENTITY!r}")


def _path_validator(basename: str):
    def _v(v: str) -> None:
        if len(v) > _MAX_VALUE or not v.startswith("/") or not _PATH.match(v):
            raise InputError("must be an absolute path with a restricted [A-Za-z0-9._/-] charset")
        for seg in v.split("/")[1:]:            # skip the leading '' from the root slash
            if seg in ("", ".", ".."):
                raise InputError("must not contain empty, '.' or '..' path segments")
        if os.path.basename(v) != basename:
            raise InputError(f"must have basename {basename!r}")
    return _v


# The approved schema, in deterministic emit order. (CCC_SKOPEO_VERSION is intentionally NOT in the
# consumed contract -- it has no Phase-B reader and is retained only as free-form transcript audit
# evidence.)
SCHEMA = (
    ("CCC_BUILDER_IDENTITY", _v_identity),
    ("CCC_RECIPE", _path_validator("Containerfile")),
    ("CCC_RECIPE_SHA256", _v_hex64),
    ("CCC_BUILD_BACKENDS_LOCK", _path_validator("requirements-build-backends.lock")),
    ("CCC_APT_PACKAGES", _path_validator("apt-packages.list")),
    ("CCC_RUSTUP_SHA", _path_validator("rustup-init.sha256")),
    ("CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST", _path_validator("requirements-build-backends.source-allowlist")),
    ("CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST_SHA256", _v_hex64),
    ("CCC_BASE_IMAGE_DIGEST", _v_digest),
    ("CCC_IMAGE_TAG", _v_tag),
    ("CCC_RUNTIME_IMAGE_ID", _v_digest),
    ("CCC_IMAGE_MANIFEST", _path_validator("image-manifest.json")),
    ("CCC_IMAGE_MANIFEST_DIGEST", _v_digest),
    ("CCC_IMAGE_CONFIG_DIGEST", _v_digest),
    ("CCC_IMAGE_IDENTITY_MODE", _v_mode),
    ("CCC_MANIFEST_CAPTURE_TRANSPORT", _v_transport),
)
SCHEMA_KEYS = tuple(k for k, _ in SCHEMA)
_REQUIRED = frozenset(SCHEMA_KEYS)


def parse_builder_inputs(data: bytes):
    """Validate raw file bytes; return the records as a list of (key, value) in schema order.

    Raises InputError on any violation (fail closed). Never returns partial results."""
    if not isinstance(data, (bytes, bytearray)):
        raise InputError("input must be bytes")
    if b"\x00" in data:
        raise InputError("NUL byte present (binary / injection) -- rejected")
    if b"\r" in data:
        raise InputError("carriage return / CRLF rejected (LF-only)")
    if not data.endswith(b"\n"):
        raise InputError("file must end with a final LF")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"not valid UTF-8: {exc}") from exc

    lines = text.split("\n")[:-1]               # drop the empty element after the guaranteed final LF
    seen: dict = {}
    for i, line in enumerate(lines, 1):
        if line == "":
            raise InputError(f"line {i}: blank line rejected")
        if line[0] == "#":
            raise InputError(f"line {i}: comment rejected")
        if line.endswith("\\"):
            raise InputError(f"line {i}: line continuation rejected")
        m = _KEYLINE.match(line)
        if not m:
            raise InputError(f"line {i}: not a KEY=VALUE record")
        key, val = m.group(1), m.group(2)
        if key in seen:
            raise InputError(f"line {i}: duplicate key {key}")
        if key not in _REQUIRED:
            raise InputError(f"line {i}: foreign key {key}")
        seen[key] = val

    missing = _REQUIRED - set(seen)
    if missing:
        raise InputError(f"missing required keys: {sorted(missing)}")

    for key, validator in SCHEMA:              # per-field validation, deterministic order
        try:
            validator(seen[key])
        except InputError as exc:
            raise InputError(f"{key}: {exc}") from exc
    return [(k, seen[k]) for k in SCHEMA_KEYS]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="read_builder_inputs.py",
        description="Strict, data-only reader/validator for the Phase-A builder-inputs.kv handoff. "
                    "NEVER source this file -- it is data. Emits validated NUL-delimited KEY=VALUE "
                    "records on stdout ONLY when the whole file is valid.")
    ap.add_argument("--inputs", required=True, help="path to the builder-inputs.kv data file")
    a = ap.parse_args(argv)
    try:
        with open(a.inputs, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        sys.stderr.write(f"ERROR: cannot read builder inputs: {exc}\n")
        return 2
    try:
        records = parse_builder_inputs(data)
    except InputError as exc:
        sys.stderr.write(f"ERROR: builder inputs invalid: {exc}\n")
        return 1
    # Build the ENTIRE output first, then write once: a consumer can never observe partial output.
    blob = b"".join((f"{k}={v}".encode("utf-8") + b"\x00") for k, v in records)
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
