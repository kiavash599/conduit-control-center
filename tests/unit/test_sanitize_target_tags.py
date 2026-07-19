# SPDX-License-Identifier: MIT
"""Target-tag derivation (release/builder/sanitize_target_tags): the SEPARATE controlled lifecycle
that turns raw RPi2 supported-tag evidence into the committed 495-tag artifact. Proves the raw-sha
gate, target-identity validation, marker extraction, exactly-495 unique/ordered/well-formed rule,
LF-canonical output, atomic bundle publish, and that it reproduces the committed artifact
byte-for-byte. Fully synthetic (no off-tree field evidence is read or modified)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib

import pytest

from release import reuse_authz as RA

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "san_mod", str(_ROOT / "release" / "builder" / "sanitize_target_tags.py"))
SAN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SAN)

_COMMITTED = _ROOT / "release" / "builder" / "target-supported-tags.txt"


def _raw(tags, *, count=None, identity=None):
    ident = identity if identity is not None else dict(SAN.TARGET_IDENTITY)
    cnt = count if count is not None else len(tags)
    lines = ["%s=%s" % (k, v) for k, v in ident.items()]
    lines += ["SUPPORTED_TAG_COUNT=%s" % cnt, SAN._BEGIN] + list(tags) + [SAN._END]
    return ("\n".join(lines) + "\n").encode()


_SYNTH = ["cp310-cp310-synth%03d" % i for i in range(SAN.EXPECTED_COUNT)]


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def test_sanitize_happy_495_lf_output_and_record():
    rb = _raw(_SYNTH)
    san, rec = SAN.sanitize(rb, expected_raw_sha256=_sha(rb))
    assert san == ("".join(t + "\n" for t in _SYNTH)).encode()   # LF, tags only, original order
    assert rec["tag_count"] == SAN.EXPECTED_COUNT
    assert rec["target_identity"] == dict(SAN.TARGET_IDENTITY)
    assert rec["sanitized_sha256"] == _sha(san)


def test_sanitize_reproduces_committed_artifact_byte_for_byte():
    committed_lf = RA.canonical_lf(_COMMITTED.read_bytes())
    tags = [ln for ln in committed_lf.decode().splitlines() if ln.strip()]
    assert len(tags) == SAN.EXPECTED_COUNT
    rb = _raw(tags)
    san, rec = SAN.sanitize(rb, expected_raw_sha256=_sha(rb))
    assert san == committed_lf                                    # exact committed bytes
    assert rec["sanitized_sha256"] == _sha(committed_lf)


def test_sanitize_rejects_crlf_mutated_raw_even_when_lf_equivalent():
    # The raw-evidence SHA identifies the EXACT acquired artifact, NOT an equivalence class. A
    # line-ending-transformed copy is a DIFFERENT artifact and must fail the byte-exact check even
    # though its LF-canonical content is identical. (Regression: this was previously ACCEPTED.)
    lf = _raw(_SYNTH)
    crlf = lf.replace(b"\n", b"\r\n")
    assert RA.canonical_lf(crlf) == lf                      # content is genuinely equivalent
    assert _sha(crlf) != _sha(lf)                           # but the artifacts are not the same bytes
    with pytest.raises(SAN.SanitizeError, match="exact bytes"):
        SAN.sanitize(crlf, expected_raw_sha256=_sha(lf))    # LF digest must NOT admit the CRLF file


def test_sanitize_records_exact_and_lf_identities_distinctly():
    rb = _raw(_SYNTH)
    san, rec = SAN.sanitize(rb, expected_raw_sha256=_sha(rb))
    assert rec["raw_evidence_sha256"] == _sha(rb)                       # exact acquired bytes
    assert rec["raw_evidence_lf_sha256"] == _sha(RA.canonical_lf(rb))   # audit reference
    assert rec["sanitized_sha256"] == _sha(san)                         # derived artifact
    # A CRLF artifact verified against its OWN exact digest is byte-honest: accepted, same content,
    # but a DIFFERENT recorded raw identity -- the evidence chain stays truthful either way.
    crlf = rb.replace(b"\n", b"\r\n")
    san2, rec2 = SAN.sanitize(crlf, expected_raw_sha256=_sha(crlf))
    assert san2 == san
    assert rec2["raw_evidence_sha256"] != rec["raw_evidence_sha256"]
    assert rec2["raw_evidence_lf_sha256"] == rec["raw_evidence_lf_sha256"]


def test_sanitize_rejects_wrong_raw_sha():
    rb = _raw(_SYNTH)
    with pytest.raises(SAN.SanitizeError):
        SAN.sanitize(rb, expected_raw_sha256="0" * 64)


@pytest.mark.parametrize("tags,count,identity,needle", [
    (_SYNTH[:494], 495, None, "expected exactly 495"),                       # 494 tags, declared 495
    (_SYNTH[:494] + [_SYNTH[0]], None, None, "duplicate"),                   # duplicate
    (_SYNTH[:494] + ["not_a_valid_tag"], None, None, "malformed"),           # malformed grammar
    (_SYNTH, 494, None, "SUPPORTED_TAG_COUNT"),                              # declared count wrong
    (_SYNTH, None, {**SAN.TARGET_IDENTITY, "MACHINE": "x86_64"}, "identity"),  # wrong target identity
])
def test_sanitize_fail_closed(tags, count, identity, needle):
    rb = _raw(tags, count=count, identity=identity)
    with pytest.raises(SAN.SanitizeError) as ei:
        SAN.sanitize(rb, expected_raw_sha256=_sha(rb))
    assert needle in str(ei.value)


def test_derive_atomic_bundle_and_refuse_existing(tmp_path):
    rb = _raw(_SYNTH)
    raw_path = tmp_path / "raw.txt"
    raw_path.write_bytes(rb)
    out = tmp_path / "bundle"
    rec = SAN.derive(str(raw_path), _sha(rb), str(out))
    assert (out / "target-supported-tags.txt").read_bytes() == ("".join(t + "\n" for t in _SYNTH)).encode()
    drec = json.loads((out / "derivation-record.json").read_text())
    assert drec["tag_count"] == SAN.EXPECTED_COUNT and drec["sanitized_sha256"] == rec["sanitized_sha256"]
    with pytest.raises(SAN.SanitizeError):                          # refuse-existing (no overwrite)
        SAN.derive(str(raw_path), _sha(rb), str(out))
