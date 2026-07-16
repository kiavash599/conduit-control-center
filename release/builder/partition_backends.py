#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/builder/partition_backends.py -- split the committed build-backends lock into
two DISJOINT, EXACT install partitions driven by the committed source-allowlist:

  * WHEEL  partition = every NON-allowlisted pin (installed --only-binary, pre-built wheels);
  * SOURCE partition = every allowlisted pin (installed --no-binary --no-build-isolation).

Fail-closed invariants (finding: authorized backend-sdist allowlist):
  * every allowlisted name is pinned in the lock (exact use; unused entry -> fail);
  * the SOURCE partition names equal the allowlist EXACTLY (no missing/unauthorized);
  * WHEEL and SOURCE are disjoint and their union is EVERY non-comment pin in the lock;
  * no duplicate pins or malformed lines.

Used by the builder Containerfile (to emit the two requirement files consumed by the two
ordered pip passes) and by the unit tests. Standard-library only (runs inside the image)."""
from __future__ import annotations

import argparse
import re
import sys

_LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)=="
    r"(?P<ver>[^\s]+)(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})+)\s*$")
_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


class PartitionError(RuntimeError):
    pass


def normalize(name: str) -> str:
    """PEP 503 name normalization (runs of -_. collapse to a single -, lowercased)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_allowlist(text: str) -> list:
    names: list = []
    seen: set = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _NAME_RE.fullmatch(line):
            raise PartitionError(f"malformed allowlist entry: {raw!r}")
        norm = normalize(line)
        if line != norm:
            raise PartitionError(f"non-canonical allowlist entry {line!r} (must be PEP 503-normalized: {norm!r})")
        if norm in seen:
            raise PartitionError(f"duplicate allowlist entry: {norm}")
        seen.add(norm)
        names.append(norm)
    if not names:
        raise PartitionError("source-allowlist is empty")
    return names


def _parse_lock(text: str):
    pins: dict = {}
    order: list = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = _LOCK_LINE.match(s)
        if not m:
            raise PartitionError(f"unrecognized backend lock line: {raw!r}")
        norm = normalize(m.group("name"))
        if norm in pins:
            raise PartitionError(f"duplicate backend lock pin: {norm}")
        pins[norm] = s
        order.append(norm)
    if not pins:
        raise PartitionError("backend lock has no pins")
    return pins, order


def partition(lock_text: str, allowlist_text: str):
    """Return (wheel_lines, source_lines) as exact subsets of the lock's pin lines."""
    allow = parse_allowlist(allowlist_text)
    allow_set = set(allow)
    pins, order = _parse_lock(lock_text)
    missing = [n for n in allow if n not in pins]
    if missing:
        raise PartitionError(f"allowlisted backend(s) not pinned in the lock: {sorted(missing)}")
    wheel = [pins[n] for n in order if n not in allow_set]
    source = [pins[n] for n in order if n in allow_set]
    # Disjoint + exact cover of every non-comment pin.
    if len(wheel) + len(source) != len(pins):
        raise PartitionError("partition is not an exact disjoint cover of the lock")
    if len(source) != len(allow_set):
        raise PartitionError("source partition does not equal the allowlist exactly")
    return wheel, source


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="partition_backends.py")
    ap.add_argument("--lock", required=True)
    ap.add_argument("--allowlist", required=True)
    ap.add_argument("--wheel-out", required=True)
    ap.add_argument("--source-out", required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.lock, "r", encoding="utf-8") as fh:
            lock_text = fh.read()
        with open(a.allowlist, "r", encoding="utf-8") as fh:
            allow_text = fh.read()
        wheel, source = partition(lock_text, allow_text)
    except (OSError, PartitionError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    with open(a.wheel_out, "w", encoding="utf-8") as fh:
        fh.write("".join(ln + "\n" for ln in wheel))
    with open(a.source_out, "w", encoding="utf-8") as fh:
        fh.write("".join(ln + "\n" for ln in source))
    sys.stderr.write(f"partitioned backend lock: {len(wheel)} wheel, {len(source)} source\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
