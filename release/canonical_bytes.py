#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/canonical_bytes.py -- THE canonical text-byte normalisation and digest primitive.

STDLIB ONLY, deliberately. This module is imported on the RPi2 Phase-B **host** (outside the
builder image) by `build-wheelhouse-offline.sh` to compute the recipe digest before Docker runs.
The rest of the host path is stdlib-only too (`read_builder_inputs.py` imports only
`oci_manifest`), and computing an LF-canonical SHA-256 needs nothing beyond `hashlib`. Importing
`release.ccc_release` there would drag in `packaging` (via `release.reuse_authz`, for PEP 440
specifiers that have nothing to do with hashing) and add an installation/availability failure
mode for no security or correctness benefit.

There is exactly ONE implementation of the normalisation rule -- here. `release.ccc_release`
re-exports `_to_lf` and `canonical_file_sha256` from this module, and the CLI below calls the same
functions, so the producer, the builder, the shell, and the tests can never disagree.

CLI (fail closed):
    python3 -m release.canonical_bytes sha256-file <path>
prints exactly one lowercase 64-hex digest on success; on any failure prints nothing on stdout and
exits non-zero with a diagnostic on stderr.
"""
from __future__ import annotations

import hashlib
import os
import sys

__all__ = ["to_lf", "canonical_file_sha256", "sha256_file", "main"]


def to_lf(data: bytes) -> bytes:
    """Normalise CRLF and lone CR to LF. THE canonical text-byte rule."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def canonical_file_sha256(data: bytes) -> str:
    """sha256 over LF-normalised bytes, so CRLF/LF/lone-CR working trees agree."""
    return hashlib.sha256(to_lf(data)).hexdigest()


def sha256_file(path: str) -> str:
    """Canonical digest of a REGULAR file. Raises OSError/ValueError (fail closed)."""
    if os.path.islink(path):
        raise ValueError(f"refusing to hash a symlink: {path!r}")
    if not os.path.isfile(path):
        raise ValueError(f"not a regular file: {path!r}")
    with open(path, "rb") as fh:
        return canonical_file_sha256(fh.read())


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "sha256-file":
        sys.stderr.write("usage: python3 -m release.canonical_bytes sha256-file <path>\n")
        return 2
    try:
        digest = sha256_file(argv[1])
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"ERROR: canonical sha256 failed: {exc}\n")
        return 1
    sys.stdout.write(digest + "\n")          # exactly one lowercase 64-hex digest
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
