# SPDX-License-Identifier: MIT
"""
Unit tests for backend/conduit/personal.py (Personal Mode adapter, C5).

The adapter is a stateless bridge to the C4 helper. Tests mock the subprocess
boundary (either _run_helper, or asyncio.create_subprocess_exec for the
argv/stdin guarantees) and verify: structural parsing, exit-code -> typed
exception mapping, that the name travels via stdin (never argv), and that the
token is never logged, cached, or placed in an exception message.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.conduit.personal as personal
from backend.conduit.errors import (
    ConduitPermissionError,
    PersonalCompartmentError,
    PersonalDivergenceError,
    PersonalValidationError,
)
from backend.conduit.models import PersonalCompartmentStatus

_TOKEN = "eyJ2IjoiMSIsImRhdGEiOnsiaWQiOiJYIiwibmFtZSI6InkifX0"  # opaque sample


def _fake_run(rc, out="", err=""):
    async def run(subcommand, stdin_text=None):
        return rc, out, err
    return run


class _FakeProc:
    def __init__(self, rc, out, err):
        self.returncode = rc
        self._out = out.encode()
        self._err = err.encode()
        self.captured_input = None

    async def communicate(self, input=None):
        self.captured_input = input
        return self._out, self._err

    def kill(self):  # pragma: no cover - only on timeout
        pass


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

async def test_status_parses(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper",
                        _fake_run(0, "exists=true\nvalid=true\nbackup=false\n"))
    s = await personal.personal_status()
    assert s == PersonalCompartmentStatus(exists=True, valid=True, backup=False)


async def test_status_malformed_output(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(0, "garbage"))
    with pytest.raises(PersonalCompartmentError):
        await personal.personal_status()


async def test_status_helper_failure(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(3, "", "boom"))
    with pytest.raises(PersonalCompartmentError):
        await personal.personal_status()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

async def test_create_returns_token(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(0, _TOKEN + "\n"))
    assert await personal.personal_create("raspberrypi") == _TOKEN


async def test_create_validation_error_from_helper(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(2, "", "display name is empty"))
    with pytest.raises(PersonalValidationError):
        await personal.personal_create("ok")


async def test_create_precheck_rejects_empty_without_subprocess(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("_run_helper must not be called on a pre-check failure")
    monkeypatch.setattr(personal, "_run_helper", boom)
    with pytest.raises(PersonalValidationError):
        await personal.personal_create("   ")


async def test_create_divergence_mapping(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(5, "", "mismatch"))
    with pytest.raises(PersonalDivergenceError):
        await personal.personal_create("ok")


async def test_create_permission_denied(monkeypatch):
    monkeypatch.setattr(
        personal, "_run_helper",
        _fake_run(1, "", "conduit-cc is not allowed to execute ... as conduit"))
    with pytest.raises(ConduitPermissionError):
        await personal.personal_create("ok")


# ---------------------------------------------------------------------------
# show-token / restore
# ---------------------------------------------------------------------------

async def test_show_token_returns_token(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(0, _TOKEN + "\n"))
    assert await personal.personal_show_token("raspberrypi") == _TOKEN


async def test_show_token_missing_compartment(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(3, "", "no valid personal compartment"))
    with pytest.raises(PersonalCompartmentError):
        await personal.personal_show_token("x")


async def test_restore_success(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(0, "restored=true\n"))
    assert await personal.personal_restore() is None


async def test_restore_helper_failure(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(3, "", "no backup to restore"))
    with pytest.raises(PersonalCompartmentError):
        await personal.personal_restore()


# ---------------------------------------------------------------------------
# argv / stdin guarantees (mock the real subprocess boundary)
# ---------------------------------------------------------------------------

async def test_name_via_stdin_not_argv(monkeypatch):
    seen = {}

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        seen["proc"] = _FakeProc(0, _TOKEN + "\n", "")
        return seen["proc"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await personal.personal_create("my-secret-label")

    # exact granted command; the name is NOT an argv element
    assert seen["args"] == ("sudo", "-u", "conduit",
                            "/opt/conduit-cc/bin/ccc-personal-compartment", "create")
    assert "my-secret-label" not in seen["args"]
    # the name arrived on stdin
    assert seen["proc"].captured_input == b"my-secret-label"


async def test_status_passes_no_stdin(monkeypatch):
    seen = {}

    async def fake_exec(*args, **kwargs):
        seen["proc"] = _FakeProc(0, "exists=false\nvalid=false\nbackup=false\n", "")
        return seen["proc"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await personal.personal_status()
    assert seen["proc"].captured_input is None


# ---------------------------------------------------------------------------
# security: no token in logs / exceptions; statelessness
# ---------------------------------------------------------------------------

async def test_token_never_logged(monkeypatch, caplog):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(0, _TOKEN + "\n"))
    with caplog.at_level("DEBUG", logger="backend.conduit.personal"):
        tok = await personal.personal_create("raspberrypi")
    assert tok == _TOKEN
    assert _TOKEN not in caplog.text


async def test_failure_logs_have_no_token(monkeypatch, caplog):
    # On failure there is no token, and the generic stderr is logged (not stdout).
    monkeypatch.setattr(personal, "_run_helper", _fake_run(4, "", "conduit failed"))
    with caplog.at_level("ERROR", logger="backend.conduit.personal"):
        with pytest.raises(PersonalCompartmentError) as e:
            await personal.personal_create("ok")
    assert _TOKEN not in caplog.text
    assert _TOKEN not in str(e.value)


async def test_divergence_exception_text_is_generic(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(5, "", "x"))
    with pytest.raises(PersonalDivergenceError) as e:
        await personal.personal_create("ok")
    msg = str(e.value).lower()
    assert "token" in msg and _TOKEN not in msg and "id" != msg  # generic, no secret


async def test_stateless_no_token_caching(monkeypatch):
    monkeypatch.setattr(personal, "_run_helper", _fake_run(0, "AAA\n"))
    assert await personal.personal_create("a") == "AAA"
    monkeypatch.setattr(personal, "_run_helper", _fake_run(0, "BBB\n"))
    assert await personal.personal_create("a") == "BBB"   # no memoization/caching
