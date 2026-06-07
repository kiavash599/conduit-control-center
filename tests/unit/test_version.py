# SPDX-License-Identifier: MIT
"""Unit test for backend/_version.py — just verifies the constant is importable."""
from __future__ import annotations

from backend._version import APP_VERSION


def test_app_version_is_string():
    assert isinstance(APP_VERSION, str)


def test_app_version_not_empty():
    assert APP_VERSION != ""


def test_app_version_matches_semver_pattern():
    parts = APP_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
