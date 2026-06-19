# SPDX-License-Identifier: MIT
"""R2a: unit tests for the Ryve claim adapter (backend/conduit/ryve.py).

Frame parsing is pure and tested directly; the runner / error mapping / timeout
paths are tested with a monkeypatched subprocess (the real `ryve-claim` binary
is not present in CI). Leakage guards assert that no stdout/stderr/PNG/field
material reaches the logs, and a source scan asserts no shell / no base64.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib

import pytest

import backend.conduit.ryve as ryve
from backend.conduit.errors import ConduitPermissionError, RyveClaimError

ROOT = pathlib.Path(__file__).resolve().parents[2]
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03payload"


def _frame(station: str, proxy: str, png: bytes) -> bytes:
    header = (
        "CCC-RYVE-CLAIM/1\n"
        "station_name: " + station + "\n"
        "proxy_id: " + proxy + "\n"
        "png_len: " + str(len(png)) + "\n"
        "\n"
    ).encode("ascii")
    return header + png


# --------------------------- frame parsing (pure) ---------------------------
def test_parse_valid_frame():
    claim = ryve._parse_frame(_frame("alirpi4", "AbC123", _PNG))
    assert claim.station_name == "alirpi4"
    assert claim.proxy_id == "AbC123"
    assert claim.png == _PNG


def test_parse_png_with_double_newline_is_binary_safe():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\n\n\x02\x03 tail bytes"
    claim = ryve._parse_frame(_frame("n", "p", png))
    assert claim.png == png          # internal b"\n\n" preserved exactly


def test_parse_bad_version():
    hdr = ("CCC-RYVE-CLAIM/2\npng_len: " + str(len(_PNG)) + "\n\n").encode("ascii")
    with pytest.raises(RyveClaimError):
        ryve._parse_frame(hdr + _PNG)


def test_parse_missing_png_len():
    hdr = ("CCC-RYVE-CLAIM/1\nstation_name: n\nproxy_id: p\n\n").encode("ascii")
    with pytest.raises(RyveClaimError):
        ryve._parse_frame(hdr + _PNG)


def test_parse_png_len_mismatch_short_body():
    hdr = ("CCC-RYVE-CLAIM/1\nstation_name: n\nproxy_id: p\npng_len: 9999\n\n").encode("ascii")
    with pytest.raises(RyveClaimError):
        ryve._parse_frame(hdr + _PNG)


def test_parse_trailing_bytes_rejected():
    with pytest.raises(RyveClaimError):
        ryve._parse_frame(_frame("n", "p", _PNG) + b"EXTRA")


def test_parse_bad_png_magic():
    not_png = b"NOTPNG__" + b"\x00" * 8
    with pytest.raises(RyveClaimError):
        ryve._parse_frame(_frame("n", "p", not_png))


def test_parse_no_separator():
    with pytest.raises(RyveClaimError):
        ryve._parse_frame(b"CCC-RYVE-CLAIM/1 no blank line " + _PNG)


# --------------------------- runner / mapping ---------------------------
async def test_generate_success(monkeypatch):
    out = _frame("alirpi4", "AbC123", _PNG)

    async def _fake():
        return (0, out, "")

    monkeypatch.setattr(ryve, "_run_helper", _fake)
    claim = await ryve.generate_ryve_claim()
    assert (claim.station_name, claim.proxy_id, claim.png) == ("alirpi4", "AbC123", _PNG)


async def test_generate_permission_error(monkeypatch):
    async def _fake():
        return (1, b"", "sudo: a password is required")

    monkeypatch.setattr(ryve, "_run_helper", _fake)
    with pytest.raises(ConduitPermissionError):
        await ryve.generate_ryve_claim()


async def test_generate_generic_error(monkeypatch):
    async def _fake():
        return (4, b"", "boom")

    monkeypatch.setattr(ryve, "_run_helper", _fake)
    with pytest.raises(RyveClaimError):
        await ryve.generate_ryve_claim()


async def test_run_helper_timeout_maps_to_ryve_error(monkeypatch):
    class _FakeProc:
        returncode = None

        async def communicate(self, input=None):
            await asyncio.sleep(1)

        def kill(self):
            pass

    async def _fake_exec(*a, **k):
        return _FakeProc()

    monkeypatch.setattr(ryve, "_TIMEOUT_S", 0.01)
    monkeypatch.setattr(ryve.asyncio, "create_subprocess_exec", _fake_exec)
    with pytest.raises(RyveClaimError):
        await ryve._run_helper()


async def test_run_helper_oserror_maps_to_ryve_error(monkeypatch):
    async def _boom(*a, **k):
        raise OSError("no such file")

    monkeypatch.setattr(ryve.asyncio, "create_subprocess_exec", _boom)
    with pytest.raises(RyveClaimError):
        await ryve._run_helper()


# --------------------------- leakage guards ---------------------------
async def test_no_secret_material_in_logs(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    out = _frame("SECRETSTATION", "SECRETPROXY", _PNG)

    async def _ok():
        return (0, out, "")

    monkeypatch.setattr(ryve, "_run_helper", _ok)
    await ryve.generate_ryve_claim()
    assert "SECRETSTATION" not in caplog.text
    assert "SECRETPROXY" not in caplog.text

    caplog.clear()

    async def _fail():
        return (4, b"STDOUTSECRET", "STDERRSECRET")

    monkeypatch.setattr(ryve, "_run_helper", _fail)
    with pytest.raises(RyveClaimError):
        await ryve.generate_ryve_claim()
    assert "STDOUTSECRET" not in caplog.text
    assert "STDERRSECRET" not in caplog.text


def test_source_no_shell_no_base64():
    src = (ROOT / "backend" / "conduit" / "ryve.py").read_text(encoding="utf-8")
    assert "shell=True" not in src
    assert "os.system" not in src
    assert "base64" not in src
    assert "create_subprocess_exec" in src
