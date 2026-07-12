"""
tests/unit/test_update_sh_contract.py
-------------------------------------
Contract + regression tests for the trusted installed engine script ``update.sh``.

``update.sh`` performs a full privileged, root-only system update (stop service,
backup, rsync, restart), so it cannot be run end-to-end without root and a real
deployed host. These tests therefore cover the pieces that ARE safely testable
without root:

  * static syntax            -- ``bash -n update.sh``
  * the public CLI contract  -- ``--help`` text and unknown-option handling, both
                                of which run in ``_parse_args`` BEFORE the root
                                check, so they execute as an unprivileged user
  * Phase-0g structure       -- the non-interactive / no-TTY fail-closed gate is
                                deep inside the root-gated preflight, so it is
                                asserted statically against the source
  * the deploy rsync         -- a STATIC assert that the deploy excludes the
                                top-level bin/, plus a BEHAVIORAL regression that
                                runs a real rsync with update.sh's own exclude
                                list and proves ${APP_DIR}/bin survives --delete

ADR-0001: update.sh is the trusted, installed *engine*. These tests pin its
externally observable contract; they do not exercise or weaken privileged flow.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="bash/rsync POSIX only")

_REPO = pathlib.Path(__file__).resolve().parents[2]
_UPDATE_SH = _REPO / "update.sh"
_BASH = shutil.which("bash")
_RSYNC = shutil.which("rsync")

_needs_bash = pytest.mark.skipif(_BASH is None, reason="bash not available")
_needs_rsync = pytest.mark.skipif(_RSYNC is None, reason="rsync not available")


def _src() -> str:
    return _UPDATE_SH.read_text(encoding="utf-8")


def _run_sh(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, str(_UPDATE_SH), *args],
        cwd=str(_REPO), capture_output=True, text=True, check=False,
        stdin=subprocess.DEVNULL,
    )


def _deploy_excludes() -> list[str]:
    """Parse the EXACT --exclude list from the phase3_deploy rsync, so the
    behavioral regression cannot drift from the production command."""
    text = _src()
    body = text.split("phase3_deploy()", 1)[1]
    # the deploy rsync runs from `rsync -a --delete` to the `${SOURCE_DIR}/` line
    block = body.split("rsync -a --checksum --delete", 1)[1].split("${SOURCE_DIR}/", 1)[0]
    return re.findall(r"--exclude '([^']*)'", block)


# --------------------------------------------------------------------------- #
#  Static syntax                                                               #
# --------------------------------------------------------------------------- #
@_needs_bash
def test_bash_syntax_ok():
    r = subprocess.run(
        [_BASH, "-n", str(_UPDATE_SH)], capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- #
#  Public CLI contract (parsed before the root check -> runnable unprivileged) #
# --------------------------------------------------------------------------- #
@_needs_bash
def test_help_exits_zero_and_documents_non_interactive():
    r = _run_sh("--help")
    assert r.returncode == 0, r.stderr
    assert "--non-interactive" in r.stdout


@_needs_bash
def test_help_documents_core_flags():
    r = _run_sh("--help")
    assert "--ccc-only" in r.stdout
    assert "--source" in r.stdout


@_needs_bash
def test_unknown_option_exits_one_with_usage():
    r = _run_sh("--definitely-not-a-flag")
    assert r.returncode == 1
    assert "Unknown option" in r.stderr
    assert "Usage:" in r.stderr


# --------------------------------------------------------------------------- #
#  Phase-0g fail-closed structure (root-gated -> asserted statically)         #
# --------------------------------------------------------------------------- #
def test_phase0g_non_interactive_gate_is_fail_closed():
    text = _src()
    # The confirmation block must honour the explicit --non-interactive contract
    # and fail closed when there is no TTY (never silently proceed).
    assert '"${NONINTERACTIVE}" == true' in text
    assert "[[ ! -t 0 ]]" in text
    # The no-TTY branch must DIE (fail closed), and must mention --non-interactive.
    m = re.search(r"elif \[\[ ! -t 0 \]\]; then\s*\n\s*die ([^\n]+)", text)
    assert m is not None, "no-TTY branch must call die()"
    assert "--non-interactive" in m.group(1)


def test_phase0g_ordering_noninteractive_before_tty_check():
    text = _src()
    i_ni = text.index('"${NONINTERACTIVE}" == true')
    i_tty = text.index("[[ ! -t 0 ]]")
    assert i_ni < i_tty, "explicit --non-interactive must be checked before TTY presence"


def test_phase0g_interaction_decoupled_from_ccc_only():
    # Interaction mode is governed by NONINTERACTIVE, not by the update SCOPE
    # (CCC_ONLY). Guard against re-coupling them in the confirm gate.
    text = _src()
    confirm = text.split('"${NONINTERACTIVE}" == true', 1)[1].split('Phase 1', 1)[0]
    assert "CCC_ONLY" not in confirm


# --------------------------------------------------------------------------- #
#  Deploy rsync: top-level bin/ must be excluded (the "cannot delete bin" bug) #
# --------------------------------------------------------------------------- #
def test_deploy_rsync_excludes_top_level_bin():
    excludes = _deploy_excludes()
    # ANCHORED exclude only: '/bin/' protects ${APP_DIR}/bin; an unanchored
    # 'bin/' would wrongly also exclude deployment/bin (the helper SOURCE).
    assert "/bin/" in excludes, f"deploy rsync must exclude '/bin/'; got {excludes}"
    assert "bin/" not in excludes, "exclude must be anchored ('/bin/'), not 'bin/'"


def test_3b2_reprovisions_bin_dir():
    # Scope item 4: bin/ remains owned/re-provisioned by step 3b2, so excluding
    # it from the deploy rsync does not leave helpers stale or missing.
    text = _src()
    block = text.split('3b2 - Re-provisioning', 1)[1].split('Phase', 1)[0]
    assert "install -d" in block and "/opt/conduit-cc/bin" in block
    assert "ccc-update-apply" in block
    assert "deployment/bin" in block  # helpers are (re)installed FROM deployment/bin


@_needs_rsync
def test_deploy_rsync_preserves_bin_regression(tmp_path):
    """Run a real rsync with update.sh's OWN exclude list and prove:
      * the top-level ${APP_DIR}/bin (running helpers) survives --delete,
      * --delete still removes genuinely stale files elsewhere,
      * deployment/bin/ (the helper source) IS still deployed.
    """
    excludes = _deploy_excludes()

    source = tmp_path / "source"           # the new release tree (no top-level bin/)
    dest = tmp_path / "app"                # ${APP_DIR} == /opt/conduit-cc
    new_version_text = 'APP_VERSION = "9.9.9"\n# new release marker (distinct size)\n'
    (source / "backend").mkdir(parents=True)
    (source / "backend" / "_version.py").write_text(new_version_text)
    (source / "deployment" / "bin").mkdir(parents=True)
    (source / "deployment" / "bin" / "ccc-update-apply").write_text("# helper source\n")

    (dest / "bin").mkdir(parents=True)     # installed privileged helpers (3b2-owned)
    (dest / "bin" / "ccc-update-apply").write_text("# RUNNING helper\n")
    (dest / "bin" / "ccc-restore-apply").write_text("# other helper\n")
    (dest / "backend").mkdir(parents=True)
    (dest / "backend" / "_version.py").write_text('APP_VERSION = "0.0.1"\n')
    (dest / "stale_old_module.py").write_text("# removed by --delete\n")

    cmd = [_RSYNC, "-a", "--delete"]
    for ex in excludes:
        cmd += ["--exclude", ex]
    cmd += [f"{source}/", f"{dest}/"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr

    # top-level bin/ preserved untouched (NOT deleted, NOT overwritten)
    assert (dest / "bin" / "ccc-update-apply").read_text() == "# RUNNING helper\n"
    assert (dest / "bin" / "ccc-restore-apply").exists()
    # --delete still works for non-excluded paths
    assert not (dest / "stale_old_module.py").exists()
    # new code deployed (size differs from the old file, so rsync copies it)
    assert (dest / "backend" / "_version.py").read_text() == new_version_text
    # deployment/bin (the helper SOURCE) is still deployed (anchored exclude only)
    assert (dest / "deployment" / "bin" / "ccc-update-apply").exists()
