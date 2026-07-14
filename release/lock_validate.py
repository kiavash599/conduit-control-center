#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/lock_validate.py -- SEMANTIC validation of a pip --require-hashes lock
against the bounds-based requirements.txt (ADR-0003 Amendment A1, finding #4).

Proves (as far as is checkable without a full resolver) that a lock is a VALID
SOLUTION of requirements.txt:
  * every direct requirement is present and pinned EXACTLY (`name==version`);
  * the pinned version SATISFIES every specifier in requirements.txt;
  * every lock entry carries at least one `--hash=sha256:<64hex>`;
  * no duplicate pinned names.
Transitive-closure COMPLETENESS is additionally enforced at install time by pip
`--require-hashes` (a missing/unhashed transitive dep fails closed); this module
covers the statically-checkable properties. Returns a list of problem strings
(empty == valid)."""
from __future__ import annotations

import re

_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")
_SPEC = re.compile(r"(==|>=|<=|!=|~=|>|<)\s*([0-9][0-9A-Za-z.\-]*)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _ver_tuple(v: str):
    out = []
    for part in v.split("."):
        m = re.match(r"^(\d+)", part)
        out.append(int(m.group(1)) if m else 0)
    return tuple(out)


def _cmp(a: str, b: str) -> int:
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


def _satisfies(version: str, op: str, bound: str) -> bool:
    c = _cmp(version, bound)
    return {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0,
            "==": c == 0, "!=": c != 0, "~=": c >= 0}.get(op, False)


def parse_requirements(text: str) -> dict:
    reqs: dict = {}
    for ln in text.splitlines():
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        m = _NAME.match(ln)
        if not m:
            continue
        reqs[_norm(m.group(1))] = _SPEC.findall(ln)
    return reqs


# CLOSED lock grammar (finding 2): a non-empty, non-comment line MUST be exactly one
# generated pin -- `name==version` followed by ONE OR MORE `--hash=sha256:<64hex>` and
# nothing else. Any other content (index URLs, --find-links, -r/-c includes, editable
# requirements, environment markers, continuations, trailing tokens, unpinned or
# unhashed requirements, malformed hashes) is a PROBLEM (fail closed).
_LOCK_LINE_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9A-Za-z.+\-]*)"
    r"((?:[ \t]+--hash=sha256:[0-9a-f]{64})+)[ \t]*$")


def parse_lock(text: str):
    """STRICT parse. Returns (pins, problems). `pins` maps normalized name ->
    (version, {sha256,...}); `problems` lists every line that is not exactly one
    accepted pin, plus duplicate package pins."""
    pins: dict = {}
    problems: list = []
    for i, line in enumerate(text.splitlines(), 1):
        body = line.rstrip("\r\n")
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if body.endswith("\\"):
            problems.append(f"line {i}: line continuation not allowed")
            continue
        m = _LOCK_LINE_RE.match(body)
        if not m:
            problems.append(f"line {i}: unrecognized lock line: {stripped[:48]!r}")
            continue
        name = _norm(m.group(1))
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group(3)))
        if name in pins:
            problems.append(f"duplicate pinned package: {name}")
        pins[name] = (m.group(2), hashes)
    return pins, problems


def validate(requirements_text: str, lock_text: str) -> list:
    """Return a list of problem strings (empty == valid). Enforces the closed lock
    grammar (every line an accepted pin), no duplicates, every requirement pinned +
    version-satisfying + hashed."""
    reqs = parse_requirements(requirements_text)
    pins, problems = parse_lock(lock_text)
    problems = list(problems)
    for name, specs in reqs.items():
        if name not in pins:
            problems.append(f"requirement not pinned in lock: {name}")
            continue
        version, hashes = pins[name]
        if not hashes:
            problems.append(f"{name}=={version} has no --hash=sha256")
        for op, bound in specs:
            if not _satisfies(version, op, bound):
                problems.append(f"{name}=={version} violates {op}{bound}")
    return problems
