# SPDX-License-Identifier: MIT
"""
Tests for deployment/bin/ccc-personal-compartment (Personal Mode, C4).

Loads the extension-less helper via importlib, redirects its hardcoded paths to
a temp dir, and stubs _run_conduit so the create / status / restore-bak /
show-token behaviour, the divergence self-check, rollback, and the
no-ID/no-token leakage guarantees can be exercised on Linux without root,
conduit, or systemd. Pure tests (token build, validation, parsing, argv)
run on every platform.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import pathlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

_linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="POSIX fcntl/O_NOFOLLOW; helper is Linux-only"
)

_HELPER = (
    pathlib.Path(__file__).resolve().parents[2]
    / "deployment" / "bin" / "ccc-personal-compartment"
)

_VALID_ID = "A" * 43  # 32 zero bytes in unpadded standard base64


def _load():
    loader = SourceFileLoader("ccc_personal_compartment", str(_HELPER))
    spec = importlib.util.spec_from_loader("ccc_personal_compartment", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _setup(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "COMPARTMENT_FILE", str(tmp_path / "personal_compartment.json"))
    monkeypatch.setattr(mod, "BAK_FILE", str(tmp_path / "personal_compartment.json.bak"))
    monkeypatch.setattr(mod, "LOCK_FILE", str(tmp_path / ".pc.lock"))


def _fake_conduit(mod, monkeypatch, *, token=None, rc=0, the_id=_VALID_ID, write=True):
    def fake(args):
        name = args[args.index("--name") + 1]
        if write:
            with open(mod.COMPARTMENT_FILE, "w") as f:
                json.dump({"id": the_id}, f)
        tok = token if token is not None else mod._build_token(the_id, name)
        out = "Saved compartment ID to %s\nShare token:\n%s\n" % (mod.COMPARTMENT_FILE, tok)
        return (rc, out, "")
    monkeypatch.setattr(mod, "_run_conduit", fake)


class _Rec:
    def __init__(self):
        self.msgs = []

    def info(self, fmt, *a):
        self.msgs.append(fmt % a if a else fmt)

    def error(self, fmt, *a):
        self.msgs.append(fmt % a if a else fmt)

    def joined(self):
        return " ".join(self.msgs)


# ---------------------------------------------------------------------------
# Pure: token builder, validation, parsing (cross-platform)
# ---------------------------------------------------------------------------

def _decode_token(token):
    pad = "=" * (-len(token) % 4)
    return json.loads(base64.urlsafe_b64decode(token + pad))


def test_build_token_roundtrip():
    mod = _load()
    tok = mod._build_token(_VALID_ID, "raspberrypi")
    assert _decode_token(tok) == {"v": "1", "data": {"id": _VALID_ID, "name": "raspberrypi"}}


def test_build_token_deterministic():
    mod = _load()
    assert mod._build_token(_VALID_ID, "x") == mod._build_token(_VALID_ID, "x")


def test_build_token_html_escapes_ampersand():
    mod = _load()
    raw = base64.urlsafe_b64decode(
        mod._build_token(_VALID_ID, "a&b") + "=" * (-len(mod._build_token(_VALID_ID, "a&b")) % 4)
    )
    # Matches Go encoding/json default: & -> & (literal escape in the JSON).
    assert b"\\u0026" in raw and b"a&b" not in raw


def test_valid_id():
    mod = _load()
    assert mod._valid_id(_VALID_ID)
    assert not mod._valid_id("A" * 42)
    assert not mod._valid_id("A" * 44)
    assert not mod._valid_id("-" * 43)           # url-safe char rejected
    assert not mod._valid_id(None)


def test_validate_name():
    mod = _load()
    assert mod._validate_name("  hostname  ") == "hostname"
    for bad in ("", "a" * 33, "a\nb", "a\tb"):
        with pytest.raises(SystemExit):
            mod._validate_name(bad)


def test_parse_share_token():
    mod = _load()
    out = "Saved compartment ID to /x\nShare token:\nABC123\n"
    assert mod._parse_share_token(out) == "ABC123"
    assert mod._parse_share_token("no token here") is None


# ---------------------------------------------------------------------------
# argv / interface guarantees (cross-platform)
# ---------------------------------------------------------------------------

def test_show_token_rejects_name_argv():
    mod = _load()
    # The name must come via stdin -- there is no --name CLI option.
    with pytest.raises(SystemExit):
        mod.main(["show-token", "--name", "x"])


def test_create_rejects_name_argv():
    mod = _load()
    with pytest.raises(SystemExit):
        mod.main(["create", "--name", "x"])


def test_unknown_subcommand_rejected():
    mod = _load()
    with pytest.raises(SystemExit):
        mod.main(["show-id"])           # show-id does not exist (replaced by show-token)


def test_source_has_no_keyfile_or_compartment_id_or_show_id():
    src = _HELPER.read_text()
    assert "conduit_key" not in src              # never touches the private key
    assert "--compartment-id" not in src
    assert "show-id" not in src                  # replaced by show-token
    assert "show_id" not in src


# ---------------------------------------------------------------------------
# create (Linux: filesystem + flock)
# ---------------------------------------------------------------------------

@_linux_only
def test_create_valid_emits_token_only(tmp_path, monkeypatch, capsys):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    _fake_conduit(mod, monkeypatch)
    mod.cmd_create("raspberrypi")
    out = capsys.readouterr().out
    assert _decode_token(out.strip()) == {"v": "1", "data": {"id": _VALID_ID, "name": "raspberrypi"}}
    # the bare ID is NOT printed; only the token (which encodes it) is
    assert out.strip() != _VALID_ID
    assert _VALID_ID + "\n" not in out


@_linux_only
def test_create_divergence_mismatch_fails_closed(tmp_path, monkeypatch, capsys):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    _fake_conduit(mod, monkeypatch, token="bm90LXRoZS1zYW1lLXRva2Vu")  # wrong token
    with pytest.raises(SystemExit) as e:
        mod.cmd_create("raspberrypi")
    assert e.value.code == mod.EXIT_DIVERGENCE
    assert capsys.readouterr().out == ""          # no token emitted


@_linux_only
def test_create_conduit_failure_restores_bak(tmp_path, monkeypatch):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    old = "B" * 43
    with open(mod.COMPARTMENT_FILE, "w") as f:        # prior compartment
        json.dump({"id": old}, f)
    _fake_conduit(mod, monkeypatch, rc=1, the_id="C" * 43)  # conduit fails after writing
    with pytest.raises(SystemExit) as e:
        mod.cmd_create("raspberrypi")
    assert e.value.code == mod.EXIT_CONDUIT
    assert mod._read_validated_id_from(mod.COMPARTMENT_FILE) == old   # reverted


@_linux_only
def test_create_invalid_generated_file_reverts(tmp_path, monkeypatch):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    _fake_conduit(mod, monkeypatch, the_id="not-a-valid-id")   # rc=0 but invalid output
    with pytest.raises(SystemExit) as e:
        mod.cmd_create("raspberrypi")
    assert e.value.code == mod.EXIT_FS
    assert not pathlib.Path(mod.COMPARTMENT_FILE).exists()       # fresh create cleared


@_linux_only
def test_create_does_not_log_id_or_token(tmp_path, monkeypatch, capsys):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    _fake_conduit(mod, monkeypatch)
    rec = _Rec()
    monkeypatch.setattr(mod, "LOG", rec)
    mod.cmd_create("raspberrypi")
    token = capsys.readouterr().out.strip()
    assert _VALID_ID not in rec.joined()
    assert token not in rec.joined()


# ---------------------------------------------------------------------------
# status / restore-bak / show-token (Linux)
# ---------------------------------------------------------------------------

@_linux_only
def test_status_states(tmp_path, monkeypatch, capsys):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    mod.cmd_status()
    assert "exists=false" in capsys.readouterr().out
    with open(mod.COMPARTMENT_FILE, "w") as f:
        json.dump({"id": _VALID_ID}, f)
    mod.cmd_status()
    o = capsys.readouterr().out
    assert "exists=true" in o and "valid=true" in o
    with open(mod.COMPARTMENT_FILE, "w") as f:
        f.write("garbage")
    mod.cmd_status()
    assert "valid=false" in capsys.readouterr().out


@_linux_only
def test_restore_bak_valid_missing_invalid(tmp_path, monkeypatch, capsys):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    with pytest.raises(SystemExit):                       # missing backup
        mod.cmd_restore_bak()
    with open(mod.BAK_FILE, "w") as f:
        f.write("garbage")
    with pytest.raises(SystemExit):                       # invalid backup
        mod.cmd_restore_bak()
    with open(mod.BAK_FILE, "w") as f:
        json.dump({"id": _VALID_ID}, f)
    mod.cmd_restore_bak()
    assert "restored=true" in capsys.readouterr().out
    assert mod._read_validated_id_from(mod.COMPARTMENT_FILE) == _VALID_ID


@_linux_only
def test_show_token_outputs_token_not_bare_id(tmp_path, monkeypatch, capsys):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    with open(mod.COMPARTMENT_FILE, "w") as f:
        json.dump({"id": _VALID_ID}, f)
    mod.cmd_show_token("raspberrypi")
    out = capsys.readouterr().out.strip()
    assert _decode_token(out)["data"]["id"] == _VALID_ID   # token contains the id
    assert out != _VALID_ID                                # but is NOT the bare id


@_linux_only
def test_show_token_via_stdin(tmp_path, monkeypatch, capsys):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    with open(mod.COMPARTMENT_FILE, "w") as f:
        json.dump({"id": _VALID_ID}, f)
    monkeypatch.setattr("sys.stdin", io.StringIO("raspberrypi"))
    mod.main(["show-token"])
    assert _decode_token(capsys.readouterr().out.strip())["data"]["name"] == "raspberrypi"


@_linux_only
def test_show_token_missing_compartment(tmp_path, monkeypatch):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        mod.cmd_show_token("x")


@_linux_only
def test_lock_file_created_in_data_dir(tmp_path, monkeypatch):
    mod = _load()
    _setup(mod, tmp_path, monkeypatch)
    fd = mod._acquire_lock()
    import os
    os.close(fd)
    assert pathlib.Path(mod.LOCK_FILE).exists()
