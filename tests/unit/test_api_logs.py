# SPDX-License-Identifier: MIT
"""
Unit tests for backend/api/logs.py

Coverage targets:
  - _should_redact()    — psi:// / psiphon:// / https://psiphon / clean line
  - _redact_stderr()    — mix of clean and redacted lines, truncation
  - _parse_line()       — blank / separator / malformed / 4-token / level detection
  - GET /api/logs       — via TestClient with mocked _run_journalctl
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.logs import (
    _parse_line,
    _redact_stderr,
    _should_redact,
    router,
)
from backend.dependencies import AuthenticatedUser, get_current_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logs_client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    return TestClient(app)


# ---------------------------------------------------------------------------
# _should_redact()
# ---------------------------------------------------------------------------


class TestShouldRedact:
    def test_psi_scheme_is_redacted(self):
        assert _should_redact("pairing link: psi://some-data") is True

    def test_psiphon_scheme_is_redacted(self):
        assert _should_redact("link=psiphon://abc") is True

    def test_https_psiphon_is_redacted(self):
        assert _should_redact("visit https://psiphon.ca") is True

    def test_case_insensitive_match(self):
        assert _should_redact("PSI://data") is True
        assert _should_redact("PSIPHON://data") is True

    def test_clean_line_not_redacted(self):
        assert _should_redact("2026-06-01 conduit started successfully") is False

    def test_empty_line_not_redacted(self):
        assert _should_redact("") is False

    def test_unrelated_url_not_redacted(self):
        assert _should_redact("https://example.com/path") is False


# ---------------------------------------------------------------------------
# _redact_stderr()
# ---------------------------------------------------------------------------


class TestRedactStderr:
    def test_clean_lines_returned_as_is(self):
        raw = "line one\nline two"
        result = _redact_stderr(raw)
        assert "line one" in result
        assert "line two" in result

    def test_pairing_line_replaced(self):
        raw = "normal line\npsi://secret-data"
        result = _redact_stderr(raw)
        assert "[REDACTED]" in result
        assert "psi://" not in result

    def test_output_truncated_to_200_chars(self):
        raw = "x" * 500
        result = _redact_stderr(raw)
        assert len(result) <= 200

    def test_empty_input_returns_empty(self):
        assert _redact_stderr("") == ""


# ---------------------------------------------------------------------------
# _parse_line()
# ---------------------------------------------------------------------------


class TestParseLine:
    def test_blank_line_returns_none(self):
        assert _parse_line("") is None
        assert _parse_line("   \n") is None

    def test_separator_line_returns_none(self):
        assert _parse_line("-- Logs begin at Mon 2026-01-01") is None

    def test_well_formed_line_parsed(self):
        raw = "2026-06-01T14:30:00+0000 hostname conduit[123]: INFO: started"
        result = _parse_line(raw)
        assert result is not None
        assert result.timestamp == "2026-06-01T14:30:00+0000"
        assert result.level == "INFO"

    def test_level_error_detected(self):
        raw = "2026-06-01T14:30:00+0000 host conduit[1]: ERROR: something failed"
        result = _parse_line(raw)
        assert result is not None
        assert result.level == "ERROR"

    def test_level_warn_normalised_to_warning(self):
        raw = "2026-06-01T14:30:00+0000 host conduit[1]: WARN: disk low"
        result = _parse_line(raw)
        assert result is not None
        assert result.level == "WARNING"

    def test_level_debug_detected(self):
        raw = "2026-06-01T14:30:00+0000 host conduit[1]: DEBUG: verbose message"
        result = _parse_line(raw)
        assert result is not None
        assert result.level == "DEBUG"

    def test_no_level_keyword_defaults_to_info(self):
        raw = "2026-06-01T14:30:00+0000 host conduit[1]: just a message"
        result = _parse_line(raw)
        assert result is not None
        assert result.level == "INFO"

    def test_short_line_surfaces_as_message(self):
        """Lines with fewer than 4 tokens return a LogLine with timestamp=None."""
        # split(" ", 3) on 3 words produces 3 parts — len < 4 → raw-message path
        raw = "only two words"
        result = _parse_line(raw)
        assert result is not None
        assert result.timestamp is None
        assert result.message == raw

    def test_pairing_link_not_parsed_specially(self):
        """_parse_line does NOT redact; the route handler does that."""
        raw = "2026-06-01T14:30:00+0000 host conduit[1]: psi://secret"
        result = _parse_line(raw)
        assert result is not None


# ---------------------------------------------------------------------------
# GET /api/logs — TestClient with mocked _run_journalctl
# ---------------------------------------------------------------------------


class TestGetLogsRoute:
    _SAMPLE_OUTPUT = (
        "2026-06-01T14:30:00+0000 host conduit[1]: INFO: started\n"
        "2026-06-01T14:30:01+0000 host conduit[1]: ERROR: something failed\n"
    )

    def test_returns_200_with_log_lines(self, logs_client):
        with patch(
            "backend.api.logs._run_journalctl",
            new=AsyncMock(return_value=(0, self._SAMPLE_OUTPUT, "")),
        ):
            response = logs_client.get("/api/logs")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_pairing_link_redacted_in_response(self, logs_client):
        output = "2026-06-01T00:00:00+0000 host conduit[1]: psi://secret-link\n"
        with patch(
            "backend.api.logs._run_journalctl",
            new=AsyncMock(return_value=(0, output, "")),
        ):
            response = logs_client.get("/api/logs")
        assert response.status_code == 200
        body = response.text
        assert "psi://" not in body
        assert "[REDACTED]" in body

    def test_journalctl_not_found_returns_503(self, logs_client):
        with patch(
            "backend.api.logs._run_journalctl",
            new=AsyncMock(side_effect=FileNotFoundError),
        ):
            response = logs_client.get("/api/logs")
        assert response.status_code == 503

    def test_journalctl_nonzero_exit_returns_503(self, logs_client):
        with patch(
            "backend.api.logs._run_journalctl",
            new=AsyncMock(return_value=(1, "", "some journalctl error")),
        ):
            response = logs_client.get("/api/logs")
        assert response.status_code == 503

    def test_empty_output_returns_empty_list(self, logs_client):
        with patch(
            "backend.api.logs._run_journalctl",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            response = logs_client.get("/api/logs")
        assert response.status_code == 200
        assert response.json() == []

    def test_limit_query_param_accepted(self, logs_client):
        with patch(
            "backend.api.logs._run_journalctl",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            response = logs_client.get("/api/logs?limit=50")
        assert response.status_code == 200
