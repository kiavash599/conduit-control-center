# SPDX-License-Identifier: MIT
"""S1: unit tests for the Backup key-exclusion guard (backend/backup/exclusion.py).

Explicit-pattern path/name guard + precise content scanner (PEM / private-key
JSON field). Pure (no app import, no runtime)."""
from __future__ import annotations

import os

import pytest

from backend.backup.collector import _redact_env
from backend.backup.exclusion import (
    KeyExclusionError,
    assert_path_allowed,
    scan_content,
)


# --------------------------- path / name guard ---------------------------
def test_path_guard_rejects_var_lib_conduit():
    with pytest.raises(KeyExclusionError):
        assert_path_allowed("/var/lib/conduit/data/personal_compartment.json")


def test_path_guard_rejects_etc_conduit_cc_tls():
    with pytest.raises(KeyExclusionError):
        assert_path_allowed("/etc/conduit-cc/tls/origin.pem")


def test_path_guard_rejects_conduit_key_json():
    with pytest.raises(KeyExclusionError):
        assert_path_allowed("/etc/conduit-cc/conduit_key.json")


def test_path_guard_rejects_origin_key():
    with pytest.raises(KeyExclusionError):
        assert_path_allowed("/etc/conduit-cc/tls/origin.key")


def test_path_guard_rejects_pem_extension():
    with pytest.raises(KeyExclusionError):
        assert_path_allowed("/some/where/cert.pem")


def test_path_guard_rejects_private_and_secret_key_prefixes():
    with pytest.raises(KeyExclusionError):
        assert_path_allowed("/some/where/private_key.json")
    with pytest.raises(KeyExclusionError):
        assert_path_allowed("/some/where/secret_key.bin")


def test_path_guard_rejects_symlink_escape(tmp_path):
    # An innocently-named symlink that resolves to a key file must be rejected.
    secret = tmp_path / "conduit_key.json"
    secret.write_text("{}")
    link = tmp_path / "innocent.txt"
    os.symlink(str(secret), str(link))
    with pytest.raises(KeyExclusionError):
        assert_path_allowed(str(link))


def test_path_guard_allows_ccc_state_files(tmp_path):
    # The allowlisted CCC state files are NOT key-grade and must pass.
    for name in ("ccc.db", ".env", "config.json"):
        p = tmp_path / name
        p.write_text("x")
        assert_path_allowed(str(p))   # no raise


def test_path_guard_allows_keyboard_config_no_false_positive(tmp_path):
    # The narrowed guard must NOT reject a benign name that merely contains "key".
    p = tmp_path / "keyboard_config.json"
    p.write_text("{}")
    assert_path_allowed(str(p))       # no raise


# --------------------------- content scanner ---------------------------
def test_content_scanner_rejects_pem_private_key():
    pem = b"-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJB\n-----END RSA PRIVATE KEY-----\n"
    with pytest.raises(KeyExclusionError):
        scan_content(pem)


def test_content_scanner_rejects_conduit_key_like_json():
    blob = b'{"privateKey":"QUJDREVG","publicKey":"R0hJSktM"}'
    with pytest.raises(KeyExclusionError):
        scan_content(blob)


def test_content_scanner_allows_long_base64():
    # S1 scanner is signature-based only; a long base64 string is NOT rejected
    # (no entropy heuristic in S1 -- documented decision).
    scan_content(b"value=" + (b"A2b9X/k1" * 64))   # no raise


def test_content_scanner_allows_benign_config():
    scan_content(b'{"traffic": {"collector_enabled": false}}')
    scan_content(b"ADMIN_PASSWORD_HASH=$2b$12$abcdefghijklmnopqrstuv\nAPP_PORT=8000\n")


# --------------------------- .env redaction ---------------------------
def test_env_redaction_drops_session_secret(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SESSION_SECRET=deadbeef\nADMIN_PASSWORD_HASH=$2b$12$x\n")
    out = _redact_env(str(env)).decode("utf-8")
    assert "SESSION_SECRET" not in out
    assert "ADMIN_PASSWORD_HASH" in out


def test_env_redaction_drops_cf_api_token(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CF_API_TOKEN=cf_secret_token\nCF_ZONE_NAME=example.net\n")
    out = _redact_env(str(env)).decode("utf-8")
    assert "CF_API_TOKEN" not in out
    assert "CF_ZONE_NAME" in out


def test_env_redaction_drops_tls_paths(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "TLS_CERT_PATH=/etc/conduit-cc/tls/origin.pem\n"
        "TLS_KEY_PATH=/etc/conduit-cc/tls/origin.key\n"
        "ADMIN_USERNAME=admin\n"
    )
    out = _redact_env(str(env)).decode("utf-8")
    assert "TLS_KEY_PATH" not in out
    assert "TLS_CERT_PATH" not in out
    assert "ADMIN_USERNAME" in out
