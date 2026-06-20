# SPDX-License-Identifier: MIT
"""
backend/backup/crypto.py
------------------------
Encryption envelope for Backup & Restore (Epic #4, S2B). Wraps the S2A tar.gz
bytes in an authenticated envelope: AES-256-GCM with a key derived from an
operator passphrase via scrypt; the full cleartext header is authenticated as
AES-GCM associated data (AAD).

S2B scope: encrypt/decrypt of BYTES only -- NO restore-to-disk, NO API, NO UI.
The passphrase is used transiently and is never logged, stored, written to disk,
or placed in an exception message.

Envelope layout (fixed 39-byte header, then ciphertext+tag):
    off  size  field
    0    6     magic = b"CCCBAK"
    6    1     envelope_version = 1
    7    1     kdf_id = 1 (scrypt)
    8    1     scrypt_log2_n
    9    1     scrypt_r
    10   1     scrypt_p
    11   16    salt
    27   12    nonce
    39   N     AES-256-GCM ciphertext || 16-byte tag   (AAD = header[0:39])
Minimum valid blob = 39 + 16 = 55 bytes.
"""
from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# --- envelope constants (locked S2B parameters) ---
MAGIC = b"CCCBAK"
ENVELOPE_VERSION = 1
KDF_SCRYPT = 1

SCRYPT_LOG2_N = 15            # n = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

KEY_LEN = 32                 # AES-256
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
HEADER_LEN = 39
MIN_BLOB_LEN = HEADER_LEN + TAG_LEN   # 55

# Accepted scrypt parameter ranges on DECRYPT. The producer always writes the
# locked defaults above; the ranges bound resource use from a crafted header
# (e.g. a huge log2_n that would otherwise allocate gigabytes) while leaving
# room to retune the defaults later.
_LOG2_N_MIN, _LOG2_N_MAX = 10, 20
_R_MIN, _R_MAX = 1, 64
_P_MIN, _P_MAX = 1, 16

# Generic, non-leaking message for ANY cryptographic verdict (wrong password,
# tampered ciphertext, tampered header, ciphertext/tag truncation). These cases
# are cryptographically indistinguishable and are reported identically.
_GENERIC = ("could not decrypt the backup: incorrect password, or the file is "
            "corrupted or has been altered")


class BackupCryptoError(Exception):
    """A backup envelope is malformed/unsupported, or decryption failed. The
    message never contains the passphrase, the key, or any plaintext."""


def _to_bytes(passphrase) -> bytes:
    if isinstance(passphrase, bytes):
        return passphrase
    if isinstance(passphrase, str):
        return passphrase.encode("utf-8")
    raise TypeError("passphrase must be str or bytes")


def _derive_key(pw: bytes, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=KEY_LEN, n=(1 << log2_n), r=r, p=p).derive(pw)


def encrypt_archive(plain: bytes, passphrase) -> bytes:
    """Encrypt `plain` (the S2A tar.gz bytes) into the CCC backup envelope."""
    pw = _to_bytes(passphrase)
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    header = (
        MAGIC
        + bytes([ENVELOPE_VERSION, KDF_SCRYPT, SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P])
        + salt + nonce
    )
    key = _derive_key(pw, salt, SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P)
    ct = AESGCM(key).encrypt(nonce, plain, header)   # ciphertext || 16-byte tag
    return header + ct


def decrypt_archive(blob: bytes, passphrase) -> bytes:
    """Decrypt a CCC backup envelope back into the S2A tar.gz bytes."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < MIN_BLOB_LEN:
        raise BackupCryptoError("backup file is corrupted or truncated")
    blob = bytes(blob)
    header = blob[:HEADER_LEN]
    if header[:6] != MAGIC:
        raise BackupCryptoError("not a CCC backup file")
    if header[6] != ENVELOPE_VERSION:
        raise BackupCryptoError("backup was created by a newer version of CCC")
    if header[7] != KDF_SCRYPT:
        raise BackupCryptoError("unsupported key-derivation function in backup")
    log2_n, r, p = header[8], header[9], header[10]
    if not (_LOG2_N_MIN <= log2_n <= _LOG2_N_MAX
            and _R_MIN <= r <= _R_MAX and _P_MIN <= p <= _P_MAX):
        raise BackupCryptoError("unsupported KDF parameters in backup")
    salt = header[11:11 + SALT_LEN]
    nonce = header[11 + SALT_LEN:HEADER_LEN]
    pw = _to_bytes(passphrase)
    key = _derive_key(pw, salt, log2_n, r, p)
    try:
        return AESGCM(key).decrypt(nonce, blob[HEADER_LEN:], header)
    except (InvalidTag, ValueError, OverflowError):
        # Collapse ANY cryptographic verdict -- auth failure (InvalidTag) and
        # malformed/oversized AEAD input (ValueError / OverflowError from the
        # parameter checks) -- to one generic, non-leaking message.
        raise BackupCryptoError(_GENERIC) from None
