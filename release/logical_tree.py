#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/logical_tree.py -- THE Logical Tree Digest (LTD) v1.

STDLIB ONLY, deliberately: this module is imported on the RPi2 Phase-B host and inside the offline
builder image as well as by the Owner-PC release producer. It has exactly one job -- turn a
{logical_path -> exact_bytes} mapping into a digest that is identical on every runtime.

WHY THIS EXISTS
    The wheelhouse identity used to be ``sha256(pack_tree(members))`` -- a digest over *gzip* bytes.
    The tar layer was canonicalised (sorted members, mtime=0, mode 0644, uid/gid 0) but the DEFLATE
    stream is an implementation detail of whatever zlib the runtime links. zlib 1.2.11 (RPi2,
    CPython 3.10) and zlib-ng 1.3.1 (Windows, CPython 3.14) produce different compressed bytes from
    byte-identical input, so Phase B and the release producer could never agree. LTD never
    compresses and never uses ``tarfile``, so no compressor or archive-format behaviour can affect
    it. (Raw tar was considered and rejected: ``tarfile`` emits a PAX extended header for paths
    over 100 bytes -- which the 113-byte websockets wheel triggers -- and ``DEFAULT_FORMAT`` itself
    changed in CPython 3.8, so raw tar only narrows the drift class instead of removing it.)

ENCODING (v1, normative)

    stream  := PREFIX || u64be(member_count) || member_1 || ... || member_N
    PREFIX  := b"ccc-logical-tree-v1\\n"     # 20 bytes:
                                             # 63 63 63 2d 6c 6f 67 69 63 61 6c
                                             # 2d 74 72 65 65 2d 76 31 0a
    member  := u64be(len(path_utf8)) || path_utf8
               || u64be(len(content)) || content
    digest  := lowercase hex SHA-256(stream)

Members are emitted in ascending order of their ENCODED UTF-8 path bytes (byte comparison, never
locale- or str-aware collation). The domain prefix separates this hash from any other SHA-256 use;
the explicit member count plus the length prefix on every field make the encoding injective, so two
different mappings cannot produce the same stream (no boundary-confusion collisions).

DELIBERATELY NOT DONE
    No Unicode normalisation (it would make the digest depend on a Unicode version -- reintroducing
    the very drift class this module removes; ambiguous paths are REJECTED instead). No line-ending
    or content normalisation: wheel bytes are binary and are digested exactly as they are.

LAYERING
    This module validates ENCODING only, over an in-memory mapping. Filesystem properties
    (symlinks, non-regular entries, unreadable files) are the COLLECTOR's responsibility --
    see ``ccc_release._wheelhouse_members``.
"""
from __future__ import annotations

import hashlib
import re
import struct

__all__ = ["SCHEME", "PREFIX", "MAX_U64", "LogicalTreeError",
           "encode_tree", "tree_digest", "validate_path"]

SCHEME = "ccc-logical-tree-v1"                 # the exact scheme identifier recorded in provenance
PREFIX = b"ccc-logical-tree-v1\n"              # 20 bytes; see the module docstring for the hex form
MAX_U64 = 1 << 64                              # every length/count must be strictly below this

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class LogicalTreeError(ValueError):
    """Raised on any Logical Tree encoding violation (fail closed)."""


def _u64be(value: int) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool):
        raise LogicalTreeError(f"length/count must be an int, got {type(value).__name__}")
    if value < 0 or value >= MAX_U64:
        raise LogicalTreeError(f"length/count {value} outside unsigned 64-bit range")
    return struct.pack(">Q", value)


def validate_path(path: object) -> bytes:
    """Validate a logical path and return its single strict-UTF-8 encoding.

    Rejects: non-str, empty, non-UTF-8-encodable (incl. lone surrogates), NUL, backslash, absolute
    paths, Windows drive paths, UNC forms, empty segments (leading/trailing/doubled separators),
    ``.`` and ``..`` segments, and paths whose encoded length does not fit u64."""
    if not isinstance(path, str):
        raise LogicalTreeError(f"path must be str, got {type(path).__name__}")
    if not path:
        raise LogicalTreeError("path must not be empty")
    if "\x00" in path:
        raise LogicalTreeError(f"path contains NUL: {path!r}")
    if "\\" in path:
        raise LogicalTreeError(f"path contains a backslash (use '/' only): {path!r}")
    if path.startswith("/"):
        raise LogicalTreeError(f"path must be relative, got absolute: {path!r}")
    if path.startswith("//"):
        raise LogicalTreeError(f"path must not be a UNC form: {path!r}")
    if _WINDOWS_DRIVE.match(path):
        raise LogicalTreeError(f"path must not carry a Windows drive: {path!r}")
    for segment in path.split("/"):
        if segment == "":
            raise LogicalTreeError(f"path has an empty segment: {path!r}")
        if segment in (".", ".."):
            raise LogicalTreeError(f"path has a {segment!r} segment: {path!r}")
    try:
        encoded = path.encode("utf-8", "strict")     # rejects lone surrogates
    except UnicodeEncodeError as exc:
        raise LogicalTreeError(f"path is not strictly UTF-8 encodable: {path!r} ({exc})") from exc
    if len(encoded) >= MAX_U64:
        raise LogicalTreeError("encoded path length outside unsigned 64-bit range")
    return encoded


def encode_tree(mapping: dict) -> bytes:
    """Return the complete normative v1 encoded stream for ``mapping``.

    Content must be immutable ``bytes`` -- ``bytearray`` is refused so the bytes that are digested
    cannot be mutated before they are packaged. An EMPTY mapping is permitted at this generic layer;
    domain policy (e.g. the 31-member wheelhouse rule) is enforced by the caller."""
    if not isinstance(mapping, dict):
        raise LogicalTreeError(f"mapping must be a dict, got {type(mapping).__name__}")
    encoded_members = []
    seen = set()
    for path, content in mapping.items():
        encoded_path = validate_path(path)
        if encoded_path in seen:
            raise LogicalTreeError(f"duplicate encoded path: {path!r}")
        seen.add(encoded_path)
        if type(content) is not bytes:               # noqa: E721 -- bytearray/memoryview refused
            raise LogicalTreeError(
                f"content for {path!r} must be immutable bytes, got {type(content).__name__}")
        if len(content) >= MAX_U64:
            raise LogicalTreeError(f"content length for {path!r} outside unsigned 64-bit range")
        encoded_members.append((encoded_path, content))
    encoded_members.sort(key=lambda item: item[0])   # ascending ENCODED path bytes
    out = [PREFIX, _u64be(len(encoded_members))]
    for encoded_path, content in encoded_members:
        out.append(_u64be(len(encoded_path)))
        out.append(encoded_path)
        out.append(_u64be(len(content)))
        out.append(content)
    return b"".join(out)


def tree_digest(mapping: dict) -> str:
    """Lowercase hex SHA-256 over the complete v1 encoded stream."""
    return hashlib.sha256(encode_tree(mapping)).hexdigest()
