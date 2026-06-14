# SPDX-License-Identifier: MIT
"""
Unit tests for backend.main.static_url — the static-asset cache-busting helper.

Verifies:
  - output format: /static/<path>?v=<mtime>
  - a leading slash in the input is normalised
  - the token tracks the file's mtime (changes when the file changes)
  - a missing file falls back to APP_VERSION (page still renders)

_STATIC_DIR is monkeypatched to a temp dir so the test is hermetic.
"""
from __future__ import annotations

import os

import backend.main as main


def _make(tmp_path, rel, mtime):
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x")
    os.utime(f, (mtime, mtime))
    return f


def test_format_and_token(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    _make(tmp_path, "css/base.css", 1000)
    assert main.static_url("css/base.css") == "/static/css/base.css?v=1000"


def test_leading_slash_normalised(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    _make(tmp_path, "js/app.js", 1234)
    assert main.static_url("/js/app.js") == "/static/js/app.js?v=1234"


def test_token_changes_with_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    f = _make(tmp_path, "js/app.js", 1000)
    first = main.static_url("js/app.js")
    os.utime(f, (2000, 2000))
    second = main.static_url("js/app.js")
    assert first != second
    assert second.endswith("?v=2000")


def test_missing_file_falls_back_to_version(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    assert main.static_url("js/nope.js") == f"/static/js/nope.js?v={main.APP_VERSION}"
