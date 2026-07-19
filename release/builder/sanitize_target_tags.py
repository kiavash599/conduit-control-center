#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/builder/sanitize_target_tags.py -- the CONTROLLED derivation of the committed sanitized
target-tag artifact (`release/builder/target-supported-tags.txt`) from the raw RPi2 supported-tag
evidence.

This is a SEPARATE lifecycle from the per-release active-input co-producer: the target tags change
only when the target ABI/RPi changes (rare), so they are derived here, committed durably, and
consumed by `gen_active_inputs.py` and every trust boundary as an input (never regenerated per
release). The tool:

  * verifies the raw evidence sha256 against an explicit expected value BEFORE trusting content;
  * validates the target identity (CPython 3.10.12 / armv7l / armhf / glibc 2.35);
  * extracts ONLY the content between the SUPPORTED_TAGS_BEGIN/END markers;
  * requires exactly 495 ordered, unique, well-formed tags (no header/foreign content);
  * writes the sanitized artifact deterministically (LF, tags only);
  * records raw hash, sanitized canonical hash, count, target identity, and the extraction rule in a
    derivation record; publishes both atomically into an off-tree bundle (refuse-existing; cleanup).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from release import reuse_authz as _authz  # noqa: E402  (canonical_lf + tag grammar)

EXPECTED_COUNT = 495
_BEGIN, _END = "SUPPORTED_TAGS_BEGIN", "SUPPORTED_TAGS_END"
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+]*-[A-Za-z0-9][A-Za-z0-9_.+]*-[A-Za-z0-9][A-Za-z0-9_.+]*$")
TARGET_IDENTITY = {"PYTHON_VERSION": "3.10.12", "PYTHON_IMPLEMENTATION": "CPython",
                   "MACHINE": "armv7l", "LIBC": "glibc-2.35"}
EXTRACTION_RULE = ("LF-canonical raw; lines strictly between SUPPORTED_TAGS_BEGIN and "
                   "SUPPORTED_TAGS_END; strip whitespace; each must match the wheel-tag grammar; "
                   "exactly 495 unique tags in original order; no header/foreign content.")


class SanitizeError(RuntimeError):
    """Raised on any target-tag derivation violation (fail closed)."""


def _kv(raw_text: str, key: str):
    m = re.search(rf"^{re.escape(key)}=(.+)$", raw_text, re.MULTILINE)
    return m.group(1).strip() if m else None


def sanitize(raw_bytes: bytes, *, expected_raw_sha256: str):
    """Return (sanitized_bytes, derivation_record). Raises SanitizeError on any violation.

    TRUST BOUNDARY ORDER (strict): the raw evidence SHA identifies the EXACT acquired artifact, not
    an equivalence class. We therefore verify sha256(raw_bytes) BYTE-EXACTLY *before* any parsing or
    normalization; a CRLF-mutated (or otherwise line-ending-transformed) file is a DIFFERENT artifact
    and must fail here even though its LF-canonical content is equivalent. Only after that check
    passes do we normalize the now-trusted content and compute the sanitized artifact's canonical
    digest separately. Both identities are recorded distinctly."""
    raw_sha = hashlib.sha256(bytes(raw_bytes)).hexdigest()          # EXACT acquired artifact identity
    if raw_sha != expected_raw_sha256:
        raise SanitizeError(f"raw evidence sha256 mismatch (exact bytes): expected "
                            f"{expected_raw_sha256}, got {raw_sha}")
    raw_lf = _authz.canonical_lf(raw_bytes)               # normalize only AFTER the byte-exact check
    text = raw_lf.decode("utf-8")                         # strict UTF-8 (raises on invalid)
    for key, want in TARGET_IDENTITY.items():
        got = _kv(text, key)
        if got != want:
            raise SanitizeError(f"target identity {key} must be {want!r}; got {got!r}")
    declared = _kv(text, "SUPPORTED_TAG_COUNT")
    if declared != str(EXPECTED_COUNT):
        raise SanitizeError(f"SUPPORTED_TAG_COUNT must be {EXPECTED_COUNT}; got {declared!r}")
    lines = text.splitlines()
    if lines.count(_BEGIN) != 1 or lines.count(_END) != 1:
        raise SanitizeError("exactly one SUPPORTED_TAGS_BEGIN and one SUPPORTED_TAGS_END required")
    b, e = lines.index(_BEGIN), lines.index(_END)
    if not b < e:
        raise SanitizeError("SUPPORTED_TAGS_BEGIN must precede SUPPORTED_TAGS_END")
    section = lines[b + 1:e]
    tags = [ln.strip() for ln in section]
    if any(ln != ln.strip() or ln == "" for ln in section):
        raise SanitizeError("blank/whitespace or foreign line inside the tag section")
    if len(tags) != EXPECTED_COUNT:
        raise SanitizeError(f"expected exactly {EXPECTED_COUNT} tags; got {len(tags)}")
    if len(set(tags)) != EXPECTED_COUNT:
        raise SanitizeError("tag section contains duplicates")
    if not all(_TAG.match(t) for t in tags):
        bad = [t for t in tags if not _TAG.match(t)][:3]
        raise SanitizeError(f"malformed wheel tag(s): {bad}")
    sanitized = ("".join(t + "\n" for t in tags)).encode("utf-8")   # LF, tags only, original order
    # Distinct identities, never conflated: the exact acquired-artifact digest, the LF-canonical
    # digest of that same raw input (audit only), and the derived sanitized artifact's digest.
    record = {"raw_evidence_sha256": raw_sha,                       # EXACT bytes as acquired
              "raw_evidence_lf_sha256": hashlib.sha256(raw_lf).hexdigest(),   # audit/reference only
              "sanitized_sha256": hashlib.sha256(sanitized).hexdigest(),
              "tag_count": len(tags), "target_identity": dict(TARGET_IDENTITY),
              "extraction_rule": EXTRACTION_RULE}
    return sanitized, record


def derive(raw_path: str, expected_raw_sha256: str, out_bundle: str) -> dict:
    with open(raw_path, "rb") as fh:
        raw = fh.read()
    sanitized, record = sanitize(raw, expected_raw_sha256=expected_raw_sha256)
    if os.path.exists(out_bundle):
        raise SanitizeError(f"output bundle must not pre-exist (no overwrite): {out_bundle!r}")
    import tempfile
    parent = os.path.dirname(os.path.abspath(out_bundle)) or "."
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".tags-", dir=parent)
    try:
        with open(os.path.join(staging, "target-supported-tags.txt"), "wb") as fh:
            fh.write(sanitized)
        with open(os.path.join(staging, "derivation-record.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
        os.replace(staging, out_bundle)                  # ONE atomic publish
    except BaseException:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sanitize_target_tags.py",
                                 description="Controlled derivation of the sanitized target-tag artifact.")
    ap.add_argument("--raw-evidence", required=True, help="raw RPi2 supported-wheel-tag evidence file")
    ap.add_argument("--expected-raw-sha256", required=True, help="expected sha256 of the raw evidence")
    ap.add_argument("--out-bundle", required=True, help="off-tree output bundle dir (must NOT pre-exist)")
    a = ap.parse_args(argv)
    try:
        rec = derive(a.raw_evidence, a.expected_raw_sha256, a.out_bundle)
    except (SanitizeError, OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"ERROR: target-tag derivation failed (fail closed): {exc}\n")
        return 1
    print(f"derived {rec['tag_count']} target tags -> {a.out_bundle}/target-supported-tags.txt")
    print(f"raw_evidence_sha256={rec['raw_evidence_sha256']}  sanitized_sha256={rec['sanitized_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
