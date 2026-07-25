# SPDX-License-Identifier: MIT
"""
Unit tests for backend.main.static_url — the static-asset cache-busting helper.

Verifies:
  - output format: /static/<path>?v=<content-hash>
  - a leading slash in the input is normalised
  - the token tracks the file's CONTENT (changes when the bytes change)
  - identical content at a different mtime yields the SAME token, and different
    content at the SAME mtime yields DIFFERENT tokens — the regression guard:
    deterministic release artifacts ship every file with mtime=0, so an mtime
    (or mtime+size) token would never change between releases and would miss a
    same-length change. The token must key on content alone.
  - a missing file falls back to APP_VERSION (page still renders)

_STATIC_DIR is monkeypatched to a temp dir so the test is hermetic.
"""
from __future__ import annotations

import hashlib
import os

import backend.main as main


def _tok(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _make(tmp_path, rel, content: bytes, mtime: int):
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)
    os.utime(f, (mtime, mtime))
    return f


def test_format_and_token(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    _make(tmp_path, "css/base.css", b"body{}", 1000)
    assert main.static_url("css/base.css") == "/static/css/base.css?v=" + _tok(b"body{}")


def test_leading_slash_normalised(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    _make(tmp_path, "js/app.js", b"x", 1234)
    assert main.static_url("/js/app.js") == "/static/js/app.js?v=" + _tok(b"x")


def test_token_changes_with_content(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    f = _make(tmp_path, "js/app.js", b"one", 1000)
    first = main.static_url("js/app.js")
    f.write_bytes(b"two")
    second = main.static_url("js/app.js")
    assert first != second
    assert second.endswith("?v=" + _tok(b"two"))


def test_same_content_different_mtime_same_token(tmp_path, monkeypatch):
    # Deterministic artifacts pin mtime=0, so the token must NOT depend on mtime:
    # identical bytes at two different mtimes -> identical token.
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    f = _make(tmp_path, "js/app.js", b"same", 0)
    tok_a = main.static_url("js/app.js")
    os.utime(f, (999999, 999999))
    tok_b = main.static_url("js/app.js")
    assert tok_a == tok_b == "/static/js/app.js?v=" + _tok(b"same")


def test_different_content_same_mtime_different_token(tmp_path, monkeypatch):
    # The exact defeat: two same-length payloads at the SAME (pinned) mtime must
    # still get DIFFERENT tokens — an mtime or mtime+size check would miss this.
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    f = _make(tmp_path, "js/app.js", b"aaaa", 0)
    tok_a = main.static_url("js/app.js")
    f.write_bytes(b"bbbb")
    os.utime(f, (0, 0))  # re-pin mtime=0 so only the content differs
    tok_b = main.static_url("js/app.js")
    assert tok_a != tok_b
    assert tok_a.endswith("?v=" + _tok(b"aaaa"))
    assert tok_b.endswith("?v=" + _tok(b"bbbb"))


def test_missing_file_falls_back_to_version(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    assert main.static_url("js/nope.js") == f"/static/js/nope.js?v={main.APP_VERSION}"
