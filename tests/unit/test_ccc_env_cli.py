"""tests/unit/test_ccc_env_cli.py -- A3: the canonical .env CLI.

Real `python -I` subprocess execution of the shipped tool from an installed-
style fake root (self-location bootstrap resolves backend/ beside bin/).
Includes process-level secret-hygiene proofs (argv + environment capture) and
live/dangling symlink victim-preservation regressions.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="POSIX semantics")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SECRET = "$2b$12$" + "A" * 53


@pytest.fixture
def cli(tmp_path):
    """Installed-style fake root with the exact shipped bytes."""
    app = tmp_path / "opt-x"
    (app / "bin").mkdir(parents=True)
    (app / "backend").mkdir()
    shutil.copyfile(ROOT / "deployment" / "bin" / "ccc-env", app / "bin" / "ccc-env")
    (app / "backend" / "__init__.py").write_text("")
    shutil.copyfile(ROOT / "backend" / "env_file.py", app / "backend" / "env_file.py")
    return app / "bin" / "ccc-env"


def _run(cli_path, args, stdin: bytes, tmp_path):
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-I", str(cli_path), *args],
                          input=stdin, capture_output=True,
                          cwd=str(cwd), env={"PATH": "/usr/bin:/bin"}, timeout=60)


def test_init_and_set_key_roundtrip(cli, tmp_path):
    env = tmp_path / ".env"
    r = _run(cli, ["init", str(env)], b"A=1\nADMIN_USERNAME=old\n", tmp_path)
    assert r.returncode == 0, r.stderr
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    r = _run(cli, ["set-key", str(env), "ADMIN_USERNAME"], b"newname", tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ADMIN_USERNAME='newname'\n" in env.read_text()
    assert "A=1\n" in env.read_text()                       # other lines preserved


@pytest.mark.parametrize(
    "key,value",
    [
        ("ADMIN_USERNAME", b"bad'name"),
        ("ADMIN_USERNAME", b"$(touch-pwned)"),
        ("ADMIN_PASSWORD_HASH", b"not-a-bcrypt-hash"),
    ],
)
def test_set_key_rejects_shell_breakout_and_invalid_key_grammar(cli, tmp_path,
                                                                 key, value):
    env = tmp_path / ".env"
    assert _run(cli, ["init", str(env)], b"ADMIN_USERNAME=old\n", tmp_path).returncode == 0
    before = env.read_bytes()
    result = _run(cli, ["set-key", str(env), key], value, tmp_path)
    assert result.returncode != 0
    assert env.read_bytes() == before


def test_get_key_is_nonsecret_allowlisted_and_contract_checked(cli, tmp_path):
    env = tmp_path / ".env"
    content = b"CF_RECORD_NAME=ccc.example.test\nADMIN_PASSWORD_HASH=hidden\n"
    assert _run(cli, ["init", str(env)], content, tmp_path).returncode == 0
    r = _run(cli, ["get-key", str(env), "CF_RECORD_NAME"], b"", tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"ccc.example.test\n"

    secret = _run(cli, ["get-key", str(env), "ADMIN_PASSWORD_HASH"], b"", tmp_path)
    assert secret.returncode == 2
    assert b"hidden" not in secret.stdout + secret.stderr

    env.chmod(0o640)
    noncanonical = _run(cli, ["get-key", str(env), "CF_RECORD_NAME"], b"", tmp_path)
    assert noncanonical.returncode == 2


def test_get_key_rejects_duplicate_assignment(cli, tmp_path):
    env = tmp_path / ".env"
    first = b"secret-value-alpha-7f31"
    second = b"secret-value-beta-9c42"
    assert _run(cli, ["init", str(env)],
                b"CF_RECORD_NAME=" + first + b"\nCF_RECORD_NAME=" + second + b"\n",
                tmp_path).returncode == 0
    r = _run(cli, ["get-key", str(env), "CF_RECORD_NAME"], b"", tmp_path)
    assert r.returncode == 2
    assert first not in r.stdout + r.stderr
    assert second not in r.stdout + r.stderr


def test_get_key_rejects_terminal_control_or_non_dns_value_without_echo(cli, tmp_path):
    env = tmp_path / ".env"
    bad = b"ccc.example.test\x1b[31m"
    assert _run(cli, ["init", str(env)], b"CF_RECORD_NAME=" + bad + b"\n",
                tmp_path).returncode == 0
    r = _run(cli, ["get-key", str(env), "CF_RECORD_NAME"], b"", tmp_path)
    assert r.returncode == 2
    assert bad not in r.stdout + r.stderr


def test_secret_never_in_argv_or_environment(cli, tmp_path):
    """REAL process-level observation: while the CLI blocks on its bounded
    stdin read, the test samples the LIVE /proc/<pid>/cmdline and
    /proc/<pid>/environ of the actual child -- not its own constructed lists --
    then delivers the secret and asserts success."""
    env = tmp_path / ".env"
    _run(cli, ["init", str(env)], b"ADMIN_PASSWORD_HASH=\n", tmp_path)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-I", str(cli), "set-key", str(env), "ADMIN_PASSWORD_HASH"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(cwd), env={"PATH": "/usr/bin:/bin"})
    try:
        # child is alive, blocked reading stdin: OBSERVE its real kernel state
        cmdline = pathlib.Path(f"/proc/{proc.pid}/cmdline").read_bytes()
        environ = pathlib.Path(f"/proc/{proc.pid}/environ").read_bytes()
        assert SECRET.encode() not in cmdline
        assert SECRET.encode() not in environ
    finally:
        out, err = proc.communicate(SECRET.encode(), timeout=60)
    assert proc.returncode == 0, err
    assert SECRET in env.read_text()                     # only the FILE has it
    assert SECRET not in out.decode() + err.decode()


def test_error_text_never_contains_the_value(cli, tmp_path):
    env = tmp_path / ".env"
    _run(cli, ["init", str(env)], b"", tmp_path)
    r = _run(cli, ["set-key", str(env), "NOT_ALLOWLISTED"], SECRET.encode(), tmp_path)
    assert r.returncode == 2
    assert SECRET not in r.stdout.decode() + r.stderr.decode()


def test_live_symlink_victim_preserved(cli, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    link = tmp_path / ".env"
    link.symlink_to(victim)
    r = _run(cli, ["init", str(link)], b"EVIL=1\n", tmp_path)
    assert r.returncode != 0
    assert victim.read_text() == "precious"                 # not modified
    r = _run(cli, ["set-key", str(link), "ADMIN_USERNAME"], b"x", tmp_path)
    assert r.returncode != 0
    assert victim.read_text() == "precious"                 # not read into output either
    r = _run(cli, ["get-key", str(link), "CF_RECORD_NAME"], b"", tmp_path)
    assert r.returncode != 0
    assert b"precious" not in r.stdout + r.stderr


def test_dangling_symlink_victim_never_created(cli, tmp_path):
    """The install.sh F2-class shape: dangling symlink -> the target must NOT
    be created by the writer."""
    target = tmp_path / "would-be-created.txt"
    link = tmp_path / ".env"
    link.symlink_to(target)                                  # dangling
    r = _run(cli, ["init", str(link)], b"EVIL=1\n", tmp_path)
    assert r.returncode != 0
    assert not target.exists()                               # never created


def test_assert_and_write_reject_hardlinked_env(cli, tmp_path):
    env = tmp_path / ".env"
    assert _run(cli, ["init", str(env)], b"CF_RECORD_NAME=ccc.example.test\n",
                tmp_path).returncode == 0
    alias = tmp_path / "env-alias"
    alias.hardlink_to(env)
    for args, stdin in (
        (["assert-contract", str(env)], b""),
        (["get-key", str(env), "CF_RECORD_NAME"], b""),
        (["set-key", str(env), "ADMIN_USERNAME"], b"owner"),
        (["init", str(env)], b"ADMIN_USERNAME=owner\n"),
    ):
        result = _run(cli, args, stdin, tmp_path)
        assert result.returncode != 0
    assert alias.read_text() == "CF_RECORD_NAME=ccc.example.test\n"


def test_unknown_operation_rejected(cli, tmp_path):
    r = _run(cli, ["delete-key", str(tmp_path / ".env"), "A"], b"", tmp_path)
    assert r.returncode == 2


def test_install_sh_uses_canonical_writer_only():
    """Text contract: install.sh performs no direct .env writes anymore."""
    s = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'cat > "${CONF_DIR}/.env"' not in s
    assert 'sed -i "s|^ADMIN_PASSWORD_HASH' not in s
    assert 'sed -i "s|^ADMIN_USERNAME' not in s
    assert s.count("/opt/conduit-cc/bin/ccc-env") >= 3       # init + 2x set-key
    assert "builtin printf" in s                             # builtin, never external
    assert "ADMIN_USERNAME='${ADMIN_USERNAME}'" in s
    assert "CF_API_TOKEN='${CF_API_TOKEN}'" in s
    assert "_require_env_scalar" in s
    reinstall = s[s.index('if [[ -f "${CONF_DIR}/.env" ]]'):s.index("    else", s.index('if [[ -f "${CONF_DIR}/.env" ]]'))]
    assert 'assert-contract "${CONF_DIR}/.env"' in reinstall
    assert 'chown "${APP_USER}:${APP_USER}" "${CONF_DIR}/.env"' not in reinstall
    assert 'chmod 600 "${CONF_DIR}/.env"' not in reinstall
    # pre-branch object-type gate present (dangling symlink coverage)
    assert '-L "${CONF_DIR}/.env"' in s
