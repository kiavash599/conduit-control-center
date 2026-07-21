"""tests/unit/test_sudoers_contract.py -- A1: exact public sudo surface.

Cross-platform text contract over BOTH rendered sudoers sources (install.sh and
update.sh heredocs) plus a Linux behavioral `visudo -cf` gate, and the parser
contract for the one reviewed bare-path exception (ccc-apply-conduit-config).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _heredoc(script: str) -> str:
    """Extract the rendered sudoers heredoc block from a shell script."""
    s = (ROOT / script).read_text(encoding="utf-8")
    m = re.search(r"cat > \"\$\{_sudoers_tmp\}\" <<EOF\n(.*?)\nEOF", s, re.S)
    assert m, f"{script}: sudoers heredoc not found"
    return m.group(1)


def _grant_lines(block: str) -> list[str]:
    return [ln.strip() for ln in block.splitlines()
            if "NOPASSWD:" in ln and not ln.lstrip().startswith("#")]


UPDATE_EXACT = "${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-update-apply apply"
RESTORE_EXACT = "${APP_USER} ALL=(root) NOPASSWD: /opt/conduit-cc/bin/ccc-restore-apply apply"


@pytest.mark.parametrize("script", ["install.sh", "update.sh"])
def test_update_restore_grants_are_exact_apply(script):
    lines = _grant_lines(_heredoc(script))
    assert UPDATE_EXACT in lines
    assert RESTORE_EXACT in lines
    # No bare-path (argument-unrestricted) grant for either helper remains.
    for helper in ("ccc-update-apply", "ccc-restore-apply"):
        bare = [ln for ln in lines if ln.endswith(f"/opt/conduit-cc/bin/{helper}")]
        assert not bare, f"{script}: bare-path grant reintroduced for {helper}: {bare}"
    # The internal subcommand is never AUTHORIZED (comment lines may document
    # the policy; no grant line may carry it).
    assert not [ln for ln in lines if "__run-worker" in ln]


def test_install_and_update_render_identical_policy():
    assert _grant_lines(_heredoc("install.sh")) == _grant_lines(_heredoc("update.sh"))


def test_exception_is_documented_and_single():
    for script in ("install.sh", "update.sh"):
        block = _heredoc(script)
        lines = _grant_lines(block)
        # exactly one root bare-path helper grant: the reviewed exception
        bare_root = [ln for ln in lines
                     if ln.endswith("ccc-apply-conduit-config")]
        assert len(bare_root) == 1
        assert "REVIEWED EXCEPTION" in block


@pytest.mark.skipif(shutil.which("visudo") is None, reason="visudo required")
def test_rendered_policy_passes_visudo(tmp_path):
    for script in ("install.sh", "update.sh"):
        rendered = _heredoc(script).replace("${APP_USER}", "conduit-cc")
        f = tmp_path / f"{script}.sudoers"
        f.write_text(rendered + "\n")
        r = subprocess.run(["visudo", "-cf", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{script}: visudo rejected: {r.stderr}"


# --- the reviewed exception's parser IS the argument firewall ----------------- #

_HELPER = ROOT / "deployment" / "bin" / "ccc-apply-conduit-config"

pytestmark_linux = pytest.mark.skipif(sys.platform != "linux", reason="POSIX helper")


def _load_helper():
    loader = SourceFileLoader("ccc_apply_conduit_config", str(_HELPER))
    spec = importlib.util.spec_from_loader("ccc_apply_conduit_config", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytestmark_linux
def test_exception_parser_defines_no_path_or_string_arguments():
    """Static proof over the helper source: every option is a bounded integer or
    a fixed choice; no argument accepts a filesystem path, unit name, or free
    string."""
    src = _HELPER.read_text(encoding="utf-8")
    # every add_argument must use type=int or explicit choices
    calls = re.findall(r"add_argument\(([^)]*)\)", src)
    assert calls, "no argparse arguments found (parser moved?)"
    for c in calls:
        assert ("type=int" in c) or ("choices=" in c) or ("action=" in c), (
            f"free-form argument found in exception helper: add_argument({c})")
    # no option name suggests a path/unit/command
    for forbidden in ("--path", "--file", "--unit", "--exec", "--command", "--script"):
        assert forbidden not in src


@pytestmark_linux
@pytest.mark.parametrize("argv", [
    ["frobnicate"],                                  # unknown verb
    ["apply", "extra-positional"],                   # extra positional
    ["apply", "--max-common-clients", "notanint"],   # non-integer
    ["apply", "--max-common-clients", "999999999"],  # out of range
    ["apply", "--script", "/tmp/x"],                 # unknown/path option
])
def test_exception_parser_rejects_bad_input(argv):
    mod = _load_helper()
    with pytest.raises(SystemExit) as e:
        mod.main(argv)
    assert e.value.code not in (0, None)
