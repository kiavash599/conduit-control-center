"""
tests/unit/test_capability_required_decoder.py
----------------------------------------------
Batch D (IDD-0002): behavioural contracts for the Layer 1 required-declaration
decoder (backend/capability_required_decoder.py), using the approved
representation-only encoding.

Pure, dependency-light, platform-independent -> no skip markers. Batch B is
imported READ-ONLY: DecodeState for state assertions, and extract_required for a
read-only seam check (not runtime wiring).
"""
from __future__ import annotations

from backend.capability_extraction import DecodeState, extract_required
from backend.capability_required_decoder import decode_required


def _state(data):
    return decode_required(data).state


def _collection(data):
    return tuple(decode_required(data).collection)


# --------------------------------------------------------------------------- #
#  Absent / Declared(empty)                                                    #
# --------------------------------------------------------------------------- #
def test_none_is_absent():
    assert _state(None) is DecodeState.ABSENT


def test_empty_bytes_is_declared_empty():
    r = decode_required(b"")
    assert r.state is DecodeState.DECLARED
    assert tuple(r.collection) == ()


# --------------------------------------------------------------------------- #
#  Declared(non-empty) -- opaque byte identities, no naming policy             #
# --------------------------------------------------------------------------- #
def test_single_identity():
    assert _state(b"cap.a\n") is DecodeState.DECLARED
    assert _collection(b"cap.a\n") == (b"cap.a",)


def test_multiple_sorted_unique_identities():
    assert _collection(b"cap.a\ncap.b\ncap.c\n") == (b"cap.a", b"cap.b", b"cap.c")


def test_previously_invalid_identities_are_now_valid_opaque():
    # Under the representation-only encoding these are ordinary opaque identities.
    # Ascending byte order: 'T'(0x54) < 'e'(0x65) < 't'(0x74).
    data = b"TLS\nengine/update\ntls:v2\n"
    assert _state(data) is DecodeState.DECLARED
    assert _collection(data) == (b"TLS", b"engine/update", b"tls:v2")


def test_hyphen_and_digit_identities_are_valid():
    assert _collection(b"mqtt-bridge\nx25519\n") == (b"mqtt-bridge", b"x25519")


def test_uppercase_identity_is_valid_no_case_folding():
    assert _collection(b"TLS\n") == (b"TLS",)


# --------------------------------------------------------------------------- #
#  Malformed -- framing / canonical deviations only (no content policy)        #
# --------------------------------------------------------------------------- #
def test_non_ascending_order_is_malformed():
    assert _state(b"cap.b\ncap.a\n") is DecodeState.MALFORMED


def test_duplicate_is_malformed():
    assert _state(b"cap.a\ncap.a\n") is DecodeState.MALFORMED


def test_blank_line_is_malformed():
    assert _state(b"cap.a\n\ncap.b\n") is DecodeState.MALFORMED


def test_lone_lf_is_malformed():
    assert _state(b"\n") is DecodeState.MALFORMED


def test_trailing_blank_line_is_malformed():
    assert _state(b"cap.a\n\n") is DecodeState.MALFORMED


def test_missing_final_lf_is_malformed():
    assert _state(b"cap.a") is DecodeState.MALFORMED
    assert _state(b"cap.a\ncap.b") is DecodeState.MALFORMED


def test_crlf_is_malformed():
    assert _state(b"cap.a\r\n") is DecodeState.MALFORMED


def test_stray_cr_is_malformed():
    assert _state(b"ca\rp\n") is DecodeState.MALFORMED


def test_non_bytes_input_raises_type_error():
    # Boundary clarification: a non-None, non-bytes argument is a trusted-caller
    # contract violation (a programming error), NOT malformed payload data.
    for bad in ("cap.a\n", 123, object(), ["cap.a"]):
        raised = False
        try:
            decode_required(bad)
        except TypeError:
            raised = True
        assert raised, f"expected TypeError for {bad!r}"


# --------------------------------------------------------------------------- #
#  bytearray accepted; round-trip; determinism; no mutation                    #
# --------------------------------------------------------------------------- #
def test_bytearray_is_accepted_and_not_mutated():
    buf = bytearray(b"cap.a\ncap.b\n")
    original = bytes(buf)
    r = decode_required(buf)
    assert r.state is DecodeState.DECLARED
    assert tuple(r.collection) == (b"cap.a", b"cap.b")
    assert bytes(buf) == original  # input not mutated


def test_round_trip_of_canonical_forms():
    for data in (b"", b"cap.a\n", b"cap.a\ncap.b\ncap.c\n", b"TLS\nengine/update\ntls:v2\n"):
        r = decode_required(data)
        assert r.state is DecodeState.DECLARED
        # Re-serialise the decoded canonical set and compare to the input.
        reserialised = b"".join(token + b"\n" for token in r.collection)
        assert reserialised == data


def test_decode_is_deterministic():
    data = b"cap.a\ncap.b\n"
    first = decode_required(data)
    for _ in range(5):
        assert decode_required(data) == first


# --------------------------------------------------------------------------- #
#  Read-only seam with Batch B extract_required (not runtime wiring)           #
# --------------------------------------------------------------------------- #
def test_seam_declared_feeds_extraction():
    result = decode_required(b"cap.a\ncap.b\n")
    extraction = extract_required(result)
    assert extraction.fail_closed is False
    assert tuple(extraction.required) == (b"cap.a", b"cap.b")


def test_seam_declared_empty_feeds_extraction():
    extraction = extract_required(decode_required(b""))
    assert extraction.fail_closed is False
    assert tuple(extraction.required) == ()


def test_seam_absent_feeds_extraction_fail_closed():
    assert extract_required(decode_required(None)).fail_closed is True


def test_seam_malformed_feeds_extraction_fail_closed():
    assert extract_required(decode_required(b"cap.b\ncap.a\n")).fail_closed is True
