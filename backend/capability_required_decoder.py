"""
backend/capability_required_decoder.py
--------------------------------------
Batch D (IDD-0002): Layer 1 required-declaration decoder -- REQUIRED side only.

Decodes a concrete required-capability declaration into Batch B's frozen
three-state ``DecodeResult`` (Absent / Malformed / Declared(collection)). Pure,
deterministic, side-effect-free, and UNWIRED: no runtime component imports it.

Encoding (approved, §12-conformant, representation-only -- no naming policy):
  * Input is ``None`` (artifact absent) or ``bytes`` (artifact present, possibly
    empty). The decoder guards the trust boundary of payload **content**, not
    Python API misuse: a non-``None``, non-bytes argument is a trusted-caller
    contract violation and raises ``TypeError`` (it is NOT malformed payload
    data).
  * ``None``  -> Absent.
  * ``b""``   -> Declared(empty): an intentional "no requirements" declaration.
  * Present, non-empty bytes are LF-delimited **opaque byte identities**:
      - an identity is a non-empty byte sequence that must not contain LF;
      - equality is exact byte equality; the decoder does NOT interpret identity
        content (no UTF-8 requirement, no case folding, no Unicode normalization,
        no trimming, no character/content validation);
      - each record ends with LF, and non-empty content requires a final LF;
      - a blank line / empty identity is Malformed;
      - identities must be in strictly ascending byte-lexicographic order
        (which also rejects duplicates);
      - any CR byte (including CRLF) is Malformed;
      - any other framing/canonical deviation is Malformed.
  * The decoder performs NO normalization; non-canonical input fails closed
    (Malformed), it is never repaired.

Output is a Batch B ``DecodeResult`` built via the imported constructors
``absent`` / ``malformed`` / ``declared`` (read-only). This module does not
decode the provided side, does not enumerate capabilities, and is not wired into
any runtime flow.
"""
from __future__ import annotations

from backend.capability_extraction import absent, declared, malformed

_LF = b"\n"
_CR = 0x0D  # carriage return byte


def decode_required(data):
    """Decode a concrete required-capability declaration to a ``DecodeResult``.

    ``data`` is ``None`` (artifact absent) or ``bytes``/``bytearray`` (artifact
    present). Pure and side-effect-free; see the module docstring for the
    encoding. ``None`` maps to ``Absent``; any framing/canonical deviation in the
    payload content maps to ``Malformed`` (fail-closed). A non-``None``,
    non-bytes argument is a trusted-caller contract violation and raises
    ``TypeError`` -- it is NOT treated as malformed payload data.
    """
    if data is None:
        return absent()
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(
            "decode_required expects None or bytes/bytearray; got "
            f"{type(data).__name__}. A non-None, non-bytes argument is a "
            "trusted-caller contract violation, not malformed payload data."
        )
    data = bytes(data)

    if data == b"":
        return declared(())  # Declared(empty): intentional no-requirements

    if _CR in data:
        return malformed()  # any CR byte (including CRLF) is malformed
    if not data.endswith(_LF):
        return malformed()  # non-empty content requires a final LF

    tokens = data.split(_LF)[:-1]  # drop the empty element from the trailing LF
    if any(token == b"" for token in tokens):
        return malformed()  # blank line / empty identity

    for prev, cur in zip(tokens, tokens[1:]):
        if not prev < cur:  # strictly ascending; rejects out-of-order and duplicates
            return malformed()

    return declared(tuple(tokens))
