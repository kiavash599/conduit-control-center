# SPDX-License-Identifier: MIT
"""S2B: unit tests for the Backup encryption envelope (backend/backup/crypto.py).

AES-256-GCM + scrypt envelope. Requires the `cryptography` dependency."""
from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from backend.backup.crypto import (
    HEADER_LEN,
    MAGIC,
    MIN_BLOB_LEN,
    NONCE_LEN,
    SALT_LEN,
    BackupCryptoError,
    decrypt_archive,
    encrypt_archive,
)

_PW = "correct horse battery staple"
_PLAIN = b"tar.gz-archive-bytes-\x00\x01\x02-content"


# 1
def test_round_trip():
    assert decrypt_archive(encrypt_archive(_PLAIN, _PW), _PW) == _PLAIN


# 2
def test_empty_plaintext_round_trip():
    blob = encrypt_archive(b"", _PW)
    assert len(blob) == MIN_BLOB_LEN          # 39 header + 16 tag + 0 ciphertext
    assert decrypt_archive(blob, _PW) == b""


# 3
def test_wrong_password_rejected():
    blob = encrypt_archive(_PLAIN, _PW)
    with pytest.raises(BackupCryptoError):
        decrypt_archive(blob, "wrong-passphrase")


# 4
def test_tampered_ciphertext_rejected():
    blob = bytearray(encrypt_archive(_PLAIN, _PW))
    blob[HEADER_LEN + 1] ^= 0x01              # flip a ciphertext byte
    with pytest.raises(BackupCryptoError):
        decrypt_archive(bytes(blob), _PW)


# 5
def test_tampered_header_rejected_via_aad():
    # Any header byte alteration is rejected: the header is AAD-authenticated and
    # its fields (salt here) also bind the derived key.
    blob = bytearray(encrypt_archive(_PLAIN, _PW))
    blob[11] ^= 0x01                          # flip a salt byte
    with pytest.raises(BackupCryptoError):
        decrypt_archive(bytes(blob), _PW)


# 6
def test_bad_magic_rejected():
    blob = bytearray(encrypt_archive(_PLAIN, _PW))
    blob[0] ^= 0x01
    with pytest.raises(BackupCryptoError):
        decrypt_archive(bytes(blob), _PW)


# 7
def test_unsupported_envelope_version_rejected():
    blob = bytearray(encrypt_archive(_PLAIN, _PW))
    blob[6] = 2
    with pytest.raises(BackupCryptoError):
        decrypt_archive(bytes(blob), _PW)


# 8
def test_unsupported_kdf_id_rejected():
    blob = bytearray(encrypt_archive(_PLAIN, _PW))
    blob[7] = 2
    with pytest.raises(BackupCryptoError):
        decrypt_archive(bytes(blob), _PW)


# 9
def test_truncated_header_rejected():
    blob = encrypt_archive(_PLAIN, _PW)
    with pytest.raises(BackupCryptoError):
        decrypt_archive(blob[:HEADER_LEN - 1], _PW)
    with pytest.raises(BackupCryptoError):
        decrypt_archive(blob[:MIN_BLOB_LEN - 1], _PW)


# 10
def test_truncated_tag_ciphertext_rejected():
    blob = encrypt_archive(_PLAIN, _PW)
    with pytest.raises(BackupCryptoError):
        decrypt_archive(blob[:-1], _PW)        # drop the last tag byte


# 11
def test_header_offsets_for_salt_and_nonce():
    blob = encrypt_archive(_PLAIN, _PW)
    assert blob[:6] == MAGIC
    assert blob[6] == 1 and blob[7] == 1
    salt = blob[11:11 + SALT_LEN]
    nonce = blob[11 + SALT_LEN:HEADER_LEN]
    assert len(salt) == SALT_LEN == 16
    assert len(nonce) == NONCE_LEN == 12
    assert HEADER_LEN == 39


# 12
def test_two_encryptions_differ():
    b1 = encrypt_archive(_PLAIN, _PW)
    b2 = encrypt_archive(_PLAIN, _PW)
    assert b1 != b2                            # fresh salt + nonce each time
    assert decrypt_archive(b1, _PW) == decrypt_archive(b2, _PW) == _PLAIN


# 13
def test_non_default_kdf_params_parse_and_decrypt():
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    header = MAGIC + bytes([1, 1, 14, 8, 1]) + salt + nonce   # log2_n=14 (supported)
    key = Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1).derive(_PW.encode())
    ct = AESGCM(key).encrypt(nonce, _PLAIN, header)
    assert decrypt_archive(header + ct, _PW) == _PLAIN


# 14
def test_no_plaintext_in_blob():
    marker = b"UNIQUE-PLAINTEXT-MARKER-9f3a"
    assert marker not in encrypt_archive(marker, _PW)


# 15
def test_error_message_has_no_secret():
    plain = b"SECRET-PLAINTEXT-7q"
    blob = encrypt_archive(plain, _PW)
    with pytest.raises(BackupCryptoError) as ei:
        decrypt_archive(blob, "the-wrong-password-zzz")
    msg = str(ei.value)
    assert "the-wrong-password-zzz" not in msg
    assert "SECRET-PLAINTEXT-7q" not in msg


# 16: a non-InvalidTag crypto-layer failure also collapses to BackupCryptoError.
def test_non_invalidtag_crypto_failure_is_generic(monkeypatch):
    plain = b"SECRET-PLAINTEXT-7q"
    blob = encrypt_archive(plain, _PW)   # built before the monkeypatch (real encrypt)

    def _boom(self, *args, **kwargs):
        raise OverflowError("data too large")

    monkeypatch.setattr(AESGCM, "decrypt", _boom)
    with pytest.raises(BackupCryptoError) as ei:
        decrypt_archive(blob, _PW)
    msg = str(ei.value)
    assert _PW not in msg
    assert "SECRET-PLAINTEXT-7q" not in msg
