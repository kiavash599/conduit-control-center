# SPDX-License-Identifier: MIT
"""
backend/backup/exclusion.py
---------------------------
Key-exclusion guard for Backup & Restore (Epic #4, S1). Defense-in-depth via
TECHNICAL controls -- not operator discipline. Two independent layers:

  * Path/name guard: a path (resolved with symlinks followed) is rejected if it
    lies under an excluded location (/var/lib/conduit, /etc/conduit-cc/tls) or
    its basename is key-grade by an EXPLICIT pattern set (conduit_key.json,
    *.key, *.pem, private_key*, secret_key*). Explicit patterns avoid false
    positives such as keyboard_config.json.
  * Content scanner: staged bytes are rejected on a precise, low-false-positive
    signature -- a PEM private-key marker or a private-key JSON field. (A
    high-entropy/base64 heuristic was deliberately NOT used in S1: it both
    misses small raw keys and risks false positives on legitimate long base64.)

Any hit raises KeyExclusionError; the caller MUST fail closed. Pure stdlib; this
module never reads keys and never logs content.
"""
from __future__ import annotations

import os


class KeyExclusionError(Exception):
    """A staged path or byte stream looked key-grade; the backup must fail closed.
    The message is generic and never contains the offending content."""


# Locations whose contents are NEVER eligible for backup (resolved-prefix match).
EXCLUDED_PATH_PREFIXES = ("/var/lib/conduit", "/etc/conduit-cc/tls")
# Explicit key-grade basename patterns (basename compared lowercased).
_EXCLUDED_NAMES = ("conduit_key.json",)
_EXCLUDED_SUFFIXES = (".key", ".pem")
_EXCLUDED_BASENAME_PREFIXES = ("private_key", "secret_key")

# Content signatures (precise; no entropy heuristic in S1).
_PEM_PRIVATE_MARKER = b"PRIVATE KEY-----"
_KEY_JSON_FIELDS = (
    b'"privatekey"', b'"private_key"', b'"secretkey"', b'"secret_key"', b'"seed"',
)


def assert_path_allowed(path: str) -> None:
    """Raise KeyExclusionError if `path` (after symlink resolution) is key-grade
    or under an excluded location."""
    rp = os.path.realpath(path)
    for pre in EXCLUDED_PATH_PREFIXES:
        if rp == pre or rp.startswith(pre + os.sep):
            raise KeyExclusionError("path under an excluded location")
    base = os.path.basename(rp).lower()
    if base in _EXCLUDED_NAMES:
        raise KeyExclusionError("excluded filename")
    if base.endswith(_EXCLUDED_SUFFIXES):
        raise KeyExclusionError("excluded key/cert extension")
    if base.startswith(_EXCLUDED_BASENAME_PREFIXES):
        raise KeyExclusionError("private/secret key filename")


def scan_content(data: bytes) -> None:
    """Raise KeyExclusionError if `data` contains key-grade material (PEM private
    key or a private-key JSON field)."""
    if _PEM_PRIVATE_MARKER in data:
        raise KeyExclusionError("PEM private-key marker detected")
    low = data.lower()
    for tok in _KEY_JSON_FIELDS:
        if tok in low:
            raise KeyExclusionError("private-key JSON field detected")
