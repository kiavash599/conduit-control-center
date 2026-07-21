"""Regression contracts for the authoritative invariant-suite harness."""
from __future__ import annotations

import inspect

from tests import invariant_suite as gate


def test_pytest_summary_parser_reports_setup_errors_truthfully():
    summary = "233 passed, 96 skipped, 279 errors in 13.65s\n"
    assert gate._pytest_counts(summary) == {
        "failed": 0,
        "passed": 233,
        "skipped": 96,
        "errors": 279,
    }


def test_pytest_summary_parser_fails_closed_without_pass_count():
    assert gate._pytest_counts("collection interrupted: 3 errors\n") == {
        "failed": -1,
        "passed": -1,
        "skipped": -1,
        "errors": -1,
    }


def test_authoritative_runner_owns_a_unique_pytest_basetemp():
    source = inspect.getsource(gate.main)
    assert 'TemporaryDirectory(prefix="ccc-invariant-")' in source
    assert '"--basetemp", base_temp' in source


def test_platform_authority_requires_the_exact_host_os():
    assert gate._host_matches_authority("windows", "windows") is True
    assert gate._host_matches_authority("linux", "linux") is True
    assert gate._host_matches_authority("linux", "darwin") is False
    assert gate._host_matches_authority("linux", "windows") is False
    assert gate._host_matches_authority("windows", "linux") is False
