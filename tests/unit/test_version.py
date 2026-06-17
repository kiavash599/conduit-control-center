# SPDX-License-Identifier: MIT
"""Unit tests for backend/_version.py.

Verifies the constant is importable and well-formed, and — critically — that
APP_VERSION stays in lock-step with the CHANGELOG. The cross-check exists so a
future milestone closure cannot silently leave APP_VERSION stale: stamping a new
``## [X.Y.Z] — <date>`` heading in CHANGELOG.md without bumping APP_VERSION (or
vice-versa) fails CI.
"""
from __future__ import annotations

import pathlib
import re

from backend._version import APP_VERSION

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CHANGELOG = _ROOT / "CHANGELOG.md"

# Matches a dated release heading like "## [0.2.0] — 2026-06-17".
# The "[Unreleased]" heading has no semver and is intentionally skipped.
_RELEASE_HEADING = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]\s*[—\-]", re.MULTILINE)


def test_app_version_is_string():
    assert isinstance(APP_VERSION, str)


def test_app_version_not_empty():
    assert APP_VERSION != ""


def test_app_version_matches_semver_pattern():
    parts = APP_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_app_version_matches_latest_changelog_release():
    """APP_VERSION must equal the topmost dated release heading in CHANGELOG.md.

    This is the staleness guard: it ties the application version to the most
    recent stamped release so the two cannot drift apart at milestone closure.
    """
    text = _CHANGELOG.read_text(encoding="utf-8")
    matches = _RELEASE_HEADING.findall(text)
    assert matches, "no dated '## [X.Y.Z] — <date>' release heading found in CHANGELOG.md"
    latest = matches[0]
    assert APP_VERSION == latest, (
        f"APP_VERSION ({APP_VERSION}) != latest CHANGELOG release ({latest}). "
        f"On milestone closure, bump backend/_version.py and stamp CHANGELOG together "
        f"(see docs/release-checklist.md)."
    )
