#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/preflight.py -- the REAL release-readiness gate, run by CI and by the Owner before a tag.

This is a thin CLI over ``ccc_release.release_preflight`` so that CI validates the release inputs with
the *exact same* validators ``produce_release`` uses -- CI can never report "ready" while the producer
would reject. Two modes:

  * default (dev)         -- ``python -m release.preflight``
        Validates whatever is committed and allows the legitimate pre-generation state in which the
        derived active inputs (six-entry build lock, 24-entry reuse authorization) are not yet present.
        Suitable for every branch/PR build.

  * release (``--require-present``) -- ``python -m release.preflight --require-present``
        Requires the full release-ready set (durable solution, target tags, builder inputs, six-entry
        build lock, 24-entry reuse authorization) and validates the complete dual-origin partition.
        Wired to tag / manual-dispatch CI so a tag cannot be cut against an incomplete input set.

Exit status: 0 = ready under the selected mode; 1 = any validation failure (fail closed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from release import ccc_release as _R  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="release-preflight",
                                 description="Authoritative CCC release-readiness gate (dual-origin armv7).")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    help="repository root (defaults to the checkout containing this file)")
    ap.add_argument("--require-present", action="store_true",
                    help="require the full release-ready input set (tag / release path)")
    a = ap.parse_args(argv)
    try:
        status = _R.release_preflight(a.repo, require_present=a.require_present)
    except _R.ReleaseError as exc:
        sys.stderr.write(f"RELEASE PREFLIGHT FAILED (fail closed): {exc}\n")
        return 1
    mode = "release" if a.require_present else "dev"
    print(f"release preflight OK [{mode}]: {json.dumps(status, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
