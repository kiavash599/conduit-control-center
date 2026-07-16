# SPDX-License-Identifier: MIT
"""Tests for the controlled builder scripts + recipe.

Three layers:
  * STATIC contract assertions on the two Owner-gated shell scripts + the Containerfile
    (their Docker/skopeo runtime is validated on the RPi2, not here).
  * LIFECYCLE-AWARE validation of the three active builder inputs (finding 6): absent is
    fine pre-gate, placeholders/malformed are always rejected, present must pass strict
    semantic validation, .example templates are never active, and a release requires all
    three. This replaces the old "the active lock must not exist" assertion, which would
    have failed the day a real valid lock is committed.
  * BEHAVIORAL script-contract tests (finding 7): run build-wheelhouse-offline.sh against
    fake sudo/docker/skopeo on PATH and prove TOCTOU rejection, immutable-image-id target,
    copy-failure exit, same-file and distinct-file success, and that an invalid RAM/swap
    contract fails BEFORE Docker is ever invoked.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

from release import ccc_release as R

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_B = _ROOT / "release" / "builder"
_PHASE_A = (_B / "build-builder-image.sh").read_text(encoding="utf-8")
_PHASE_B = (_B / "build-wheelhouse-offline.sh").read_text(encoding="utf-8")
_RECIPE = (_B / "Containerfile").read_text(encoding="utf-8")
_bash = shutil.which("bash")
_needs_bash = pytest.mark.skipif(_bash is None, reason="bash required")


# --------------------------------------------------------------------------- #
#  Static contract                                                             #
# --------------------------------------------------------------------------- #
@_needs_bash
def test_scripts_parse():
    for s in ("build-builder-image.sh", "build-wheelhouse-offline.sh"):
        r = subprocess.run([_bash, "-n", str(_B / s)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_phase_b_runs_immutable_image_id_and_reverifies():
    assert '"${CCC_IMAGE_ID}" \\' in _PHASE_B
    assert 'python3 /repo/release/build_wheelhouse.py' in _PHASE_B
    assert '{{.Id}}' in _PHASE_B and '"${CUR_ID}" == "${CCC_IMAGE_ID}"' in _PHASE_B
    assert 'CUR_MANIFEST_DIGEST' in _PHASE_B and '== "${CCC_IMAGE_MANIFEST_DIGEST}"' in _PHASE_B


def test_phase_b_offline_and_hardening_flags():
    for flag in ("--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                 "--user 1000:1000", "--read-only"):
        assert flag in _PHASE_B


def test_phase_b_binds_apt_and_rustup_inputs():
    # The offline build passes the committed apt list + rustup hash so build_wheelhouse
    # can bind their sha256 in provenance (findings 4/6).
    assert "--apt-packages /repo/release/builder/apt-packages.list" in _PHASE_B
    assert "--rustup-sha /repo/release/builder/rustup-init.sha256" in _PHASE_B


def test_finding3_distinct_ram_and_swap_contract():
    # RAM, swap and host-reserve are DISTINCT mandatory inputs; swap==0 => --memory-swap ==
    # --memory (no swap); the contract is host-validated before Docker.
    assert '--memory "${RAM}" --memory-swap "${MEMORY_SWAP}"' in _PHASE_B
    assert 'MEMORY_SWAP="${RAM}"' in _PHASE_B                      # swap disabled path
    assert "RAM_B + SWAP_B" in _PHASE_B                            # total path
    assert "--host-reserve is required" in _PHASE_B                # mandatory reserve
    assert "RAM_B + HR_B <= MT_B" in _PHASE_B                      # reserve-protecting math
    assert "RAM_B <= MA_B" in _PHASE_B                             # MemAvailable gate
    assert "_determine_swap_capability" in _PHASE_B                # positive swap-capability evidence
    assert "MEMINFO_PATH" in _PHASE_B and "SWAPS_PATH" in _PHASE_B  # test indirection


def test_finding8_skopeo_is_explicit_preflight_not_autoinstalled():
    for sh in (_PHASE_A, _PHASE_B):
        assert "require_tool skopeo" in sh
        assert "never installs prerequisites" in sh
    # No script silently installs skopeo.
    assert "apt-get install skopeo" not in _PHASE_A.split("Install it out-of-band", 1)[0]


def test_finding9_provenance_copy_checked_no_suppression():
    assert "2>/dev/null || true" not in _PHASE_B
    assert 'readlink -f' in _PHASE_B
    assert '[[ -s "${PROV_OUT}" ]] ||' in _PHASE_B


def test_recipe_pins_and_jammy():
    assert "# syntax=" not in _RECIPE
    assert "slim-bookworm" not in _RECIPE and "ubuntu:22.04" in _RECIPE
    assert "sha256sum -c -" in _RECIPE
    assert "apt-packages.list" in _RECIPE and "UNPINNED apt package" in _RECIPE
    assert "empty/comment-only build-backends lock" in _RECIPE


# --------------------------------------------------------------------------- #
#  Finding 6: lifecycle-aware builder-input validation                        #
# --------------------------------------------------------------------------- #
_VALID_APT = "libssl-dev=3.0.2-0ubuntu1.15\npkg-config=0.29.2-1ubuntu3\n"
_VALID_RUSTUP = "a" * 64 + "  rustup-init\n"
_VALID_BB = "maturin==1.5.1 --hash=sha256:%s\n" % ("7" * 64)
_VALID_ALLOWLIST = "maturin\n"


def _write_inputs(d, apt=None, rustup=None, bb=None, allowlist=None):
    if apt is not None:
        (d / "apt-packages.list").write_text(apt)
    if rustup is not None:
        (d / "rustup-init.sha256").write_text(rustup)
    if bb is not None:
        (d / "requirements-build-backends.lock").write_text(bb)
    if allowlist is not None:
        (d / "requirements-build-backends.source-allowlist").write_text(allowlist)


def test_absent_inputs_ok_pre_gate(tmp_path):
    # Nothing committed yet -> valid during development (require_present=False).
    status = R.validate_builder_inputs(str(tmp_path), require_present=False)
    assert status == {"apt-packages.list": "absent", "rustup-init.sha256": "absent",
                      "requirements-build-backends.lock": "absent",
                      "requirements-build-backends.source-allowlist": "absent"}


def test_absent_inputs_rejected_at_release_gate(tmp_path):
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=True)


def test_present_valid_inputs_pass(tmp_path):
    _write_inputs(tmp_path, _VALID_APT, _VALID_RUSTUP, _VALID_BB, _VALID_ALLOWLIST)
    status = R.validate_builder_inputs(str(tmp_path), require_present=True)
    assert set(status.values()) == {"present"}


def test_example_templates_are_not_active_inputs(tmp_path):
    # Only .example templates present -> the ACTIVE files are still absent.
    (tmp_path / "apt-packages.list.example").write_text(_VALID_APT)
    (tmp_path / "rustup-init.sha256.example").write_text(_VALID_RUSTUP)
    (tmp_path / "requirements-build-backends.lock.example").write_text(_VALID_BB)
    (tmp_path / "requirements-build-backends.source-allowlist.example").write_text(_VALID_ALLOWLIST)
    status = R.validate_builder_inputs(str(tmp_path), require_present=False)
    assert set(status.values()) == {"absent"}
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=True)


def test_placeholder_rustup_all_zeros_rejected(tmp_path):
    _write_inputs(tmp_path, _VALID_APT, "0" * 64 + "\n", _VALID_BB)
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=False)


def test_unpinned_apt_entry_rejected(tmp_path):
    _write_inputs(tmp_path, "libssl-dev\n", _VALID_RUSTUP, _VALID_BB)
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=False)


def test_comment_only_backend_lock_rejected(tmp_path):
    _write_inputs(tmp_path, _VALID_APT, _VALID_RUSTUP, "# nothing here\n")
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=False)


def test_placeholder_version_backend_lock_rejected(tmp_path):
    _write_inputs(tmp_path, _VALID_APT, _VALID_RUSTUP,
                  "maturin==0.0.0 --hash=sha256:%s\n" % ("7" * 64))
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=False)


def test_no_committed_active_inputs_yet_but_lifecycle_valid():
    # The repository is PRE-GATE: the three active inputs are intentionally absent.
    # This asserts the *state* without a brittle "must not exist" that a real lock breaks.
    status = R.validate_builder_inputs(str(_B), require_present=False)
    assert set(status) == set(R.BUILDER_INPUT_FILES)


# --------------------------------------------------------------------------- #
#  Finding 7: behavioral script-contract tests (fake sudo/docker/skopeo)      #
# --------------------------------------------------------------------------- #
_MANIFEST_CONTENT = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json"}'
_MANIFEST_DIGEST = "sha256:" + hashlib.sha256(_MANIFEST_CONTENT).hexdigest()
_IMG_ID = "sha256:" + "1" * 64
_FAKE_SUDO = "#!/usr/bin/env bash\nexec \"$@\"\n"
_FAKE_SKOPEO = (
    "#!/usr/bin/env bash\n"
    "if [[ \"$1\" == \"--version\" ]]; then echo 'skopeo version 1.4.1'; exit 0; fi\n"
    "cat \"$FAKE_MANIFEST_FILE\"\n"
)
_FAKE_DOCKER = (
    "#!/usr/bin/env bash\n"
    "echo \"docker $*\" >> \"$DOCKER_LOG\"\n"
    "sub=\"$1\"; shift || true\n"
    "case \"$sub\" in\n"
    "  image) echo \"$FAKE_IMAGE_ID\";;\n"
    "  info) exit 0;;\n"
    "  run)\n"
    "    out=\"\"\n"
    "    while [[ $# -gt 0 ]]; do\n"
    "      if [[ \"$1\" == \"-v\" ]]; then\n"
    "        case \"$2\" in *:/out:rw) out=\"${2%%:/out:rw}\";; esac\n"
    "        shift 2; continue\n"
    "      fi\n"
    "      shift\n"
    "    done\n"
    "    if [[ -n \"$out\" && \"${FAKE_SKIP_OUTPUT:-0}\" != \"1\" ]]; then\n"
    "      echo '{\"schemaVersion\":1}' > \"$out/wheelhouse-armhf-provenance\" 2>/dev/null || true\n"
    "      echo '{\"ok\":true}' > \"$out/wheelhouse-armv7.json\"\n"
    "    fi;;\n"
    "  *) : ;;\n"
    "esac\n"
)


def _mkbin(binp, name, body):
    p = binp / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _write_meminfo(path, *, mem_total=4000000, mem_available=3500000,
                   swap_total=4000000, swap_free=4000000):
    # values in kB, matching /proc/meminfo
    path.write_text(
        "MemTotal:       %d kB\n"
        "MemAvailable:   %d kB\n"
        "SwapTotal:      %d kB\n"
        "SwapFree:       %d kB\n" % (mem_total, mem_available, swap_total, swap_free))


def _prep(tmp_path, *, image_id=_IMG_ID, skip_output=False, swap_capable=True):
    binp = tmp_path / "bin"
    binp.mkdir()
    _mkbin(binp, "sudo", _FAKE_SUDO)
    _mkbin(binp, "docker", _FAKE_DOCKER)
    _mkbin(binp, "skopeo", _FAKE_SKOPEO)
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    evid = tmp_path / "evidence"
    evid.mkdir()
    inputs = evid / "builder-inputs.env"
    inputs.write_text(
        "CCC_BUILDER_IDENTITY=id\n"
        "CCC_BASE_IMAGE_DIGEST=sha256:%s\n" % ("b" * 64) +
        "CCC_IMAGE_TAG=ccc:local\n"
        "CCC_IMAGE_ID=%s\n" % _IMG_ID +
        "CCC_IMAGE_MANIFEST=%s\n" % manifest +
        "CCC_IMAGE_MANIFEST_DIGEST=%s\n" % _MANIFEST_DIGEST)
    sdist = tmp_path / "sdists"
    sdist.mkdir()
    lock = tmp_path / "build.lock"
    lock.write_text("x==1 --hash=sha256:%s\n" % ("2" * 64))
    outd = tmp_path / "out"
    outd.mkdir()
    log = tmp_path / "docker.log"
    meminfo = tmp_path / "meminfo"
    _write_meminfo(meminfo)
    swaps = tmp_path / "swaps"
    swaps.write_text("Filename\tType\tSize\tUsed\tPriority\n/swapfile\tfile\t4000000\t0\t-2\n")
    env = dict(os.environ)
    env["PATH"] = str(binp) + os.pathsep + env["PATH"]
    env["DOCKER_LOG"] = str(log)
    env["FAKE_IMAGE_ID"] = image_id
    env["FAKE_MANIFEST_FILE"] = str(manifest)
    env["CCC_MEMINFO_PATH"] = str(meminfo)
    env["CCC_SWAPS_PATH"] = str(swaps)
    env["CCC_CGROUP2_SWAP_MAX"] = str(tmp_path / "nocg2")   # nonexistent -> no positive cgroup evidence
    env["CCC_CGROUP1_MEMSW"] = str(tmp_path / "nocg1")
    if swap_capable is not None:
        env["CCC_SWAP_LIMIT_CAPABLE"] = "1" if swap_capable else "0"
    if skip_output:
        env["FAKE_SKIP_OUTPUT"] = "1"
    return binp, inputs, sdist, lock, outd, log, env


def _res_args(tmp_path, *, ram="800m", swap="0", reserve="200m"):
    return ["--ram", ram, "--swap", swap, "--host-reserve", reserve,
            "--resource-evidence", str(tmp_path / "res.env")]


def _run_phase_b(env, inputs, sdist, outd, prov_out, *extra):
    cmd = [_bash, str(_B / "build-wheelhouse-offline.sh"),
           "--inputs", str(inputs), "--sdist-dir", str(sdist),
           "--out-dir", str(outd), "--provenance-out", str(prov_out), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(_ROOT))


def _no_docker_run(log):
    return not log.exists() or not any(
        ln.startswith("docker run") for ln in log.read_text().splitlines())


@_needs_bash
def test_phase_b_distinct_file_success(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode == 0, r.stderr
    assert prov.exists() and prov.read_text().strip()
    runline = [ln for ln in log.read_text().splitlines() if ln.startswith("docker run")][0]
    assert _IMG_ID in runline and "ccc:local" not in runline
    # external resource evidence written, swap disabled path
    ev = (tmp_path / "res.env").read_text()
    assert "docker_memory_swap=800m" in ev and "cgroup_mode=" in ev


@_needs_bash
def test_phase_b_same_file_success(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    prov = outd / "wheelhouse-armv7.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode == 0, r.stderr
    assert prov.exists() and prov.read_text().strip()


@_needs_bash
def test_phase_b_swap_capable_success_sets_total(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path, swap_capable=True)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, ram="800m", swap="512m"))
    assert r.returncode == 0, r.stderr
    ev = (tmp_path / "res.env").read_text()
    total = (800 + 512) * 1024 * 1024
    assert ("docker_memory_swap=%d" % total) in ev
    assert "swap_limit_capable=yes" in ev
    assert "swap_limit_capable_source=explicit-override" in ev


@_needs_bash
def test_phase_b_toctou_image_id_drift_rejected(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path, image_id="sha256:" + "9" * 64)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode != 0
    assert "no longer maps" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_copy_failure_exits_nonzero(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path, skip_output=True)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode != 0
    assert not prov.exists()


@_needs_bash
def test_phase_b_invalid_ram_fails_before_docker(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, ram="notasize"))
    assert r.returncode != 0 and "invalid --ram" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_zero_ram_fails_before_docker(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, ram="0"))
    assert r.returncode != 0
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_ram_plus_reserve_exceeds_memtotal(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    _write_meminfo(tmp_path / "meminfo", mem_total=900000, mem_available=800000)  # ~880 MiB
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, ram="800m", reserve="300m"))
    assert r.returncode != 0 and "exceeds MemTotal" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_ram_exceeds_memavailable(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    _write_meminfo(tmp_path / "meminfo", mem_total=4000000, mem_available=100000)  # ~98 MiB free
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, ram="800m"))
    assert r.returncode != 0 and "MemAvailable" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_swap_exceeds_swaptotal(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    _write_meminfo(tmp_path / "meminfo", swap_total=100000, swap_free=100000)  # ~98 MiB swap
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, swap="512m"))
    assert r.returncode != 0 and "SwapTotal" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_swap_exceeds_swapfree(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    _write_meminfo(tmp_path / "meminfo", swap_total=4000000, swap_free=100000)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, swap="512m"))
    assert r.returncode != 0 and "SwapFree" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_swap_without_cgroup_support_rejected(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path, swap_capable=False)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, swap="512m"))
    assert r.returncode != 0 and "capability unproven" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_missing_host_reserve_rejected(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    prov = tmp_path / "prov.json"
    # omit --host-reserve
    r = _run_phase_b(env, inputs, sdist, outd, prov,
                     "--ram", "800m", "--swap", "0",
                     "--resource-evidence", str(tmp_path / "res.env"))
    assert r.returncode != 0 and "--host-reserve is required" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_swap_capability_unprovable_fails_closed(tmp_path):
    # No override, no readable cgroup swap-control file, docker info empty -> capability
    # cannot be positively established -> swap request fails closed (never fall-open).
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path, swap_capable=None)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, swap="512m"))
    assert r.returncode != 0 and "capability unproven" in r.stderr
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_swap_capability_via_readable_cgroup_file(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path, swap_capable=None)
    cg = tmp_path / "cg2_swap_max"
    cg.write_text("max\n")
    env["CCC_CGROUP2_SWAP_MAX"] = str(cg)     # positive, attributable evidence
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path, swap="512m"))
    assert r.returncode == 0, r.stderr
    ev = (tmp_path / "res.env").read_text()
    assert "swap_limit_capable=yes" in ev
    assert "swap_limit_capable_source=cgroup-v2" in ev
    assert "active_swap_devices=" in ev


@_needs_bash
def test_phase_b_evidence_path_collision_rejected(tmp_path):
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    prov = tmp_path / "prov.json"
    # point --resource-evidence at the provenance-out path (collision)
    r = _run_phase_b(env, inputs, sdist, outd, prov,
                     "--ram", "800m", "--swap", "0", "--host-reserve", "200m",
                     "--resource-evidence", str(prov))
    assert r.returncode != 0 and "collides" in r.stderr
    assert _no_docker_run(log)


# --------------------------------------------------------------------------- #
#  Backend source-allowlist: Containerfile two-pass ordered install contract   #
# --------------------------------------------------------------------------- #


def _norm(text):
    # join shell line-continuations and collapse whitespace (whitespace-robust matching)
    return " ".join(text.replace("\\\n", " ").split())


def test_containerfile_two_pass_backend_install_ordering():
    r = _RECIPE
    n = _norm(r)
    assert "partition_backends.py" in r
    assert "requirements-build-backends.source-allowlist" in r
    wheel_pass = ("pip install --no-cache-dir --require-hashes --only-binary=:all: "
                  "--no-deps -r /opt/ccc/backends.wheel.txt")
    source_pass = ("pip install --no-cache-dir --require-hashes --no-binary=:all: "
                   "--no-build-isolation --no-deps -r /opt/ccc/backends.source.txt")
    assert wheel_pass in n, "wheel pass flags/target wrong"
    assert source_pass in n, "source pass flags/target wrong"
    # ORDERING: wheel partition install must precede source partition install
    assert n.index(wheel_pass) < n.index(source_pass)


def test_containerfile_backend_install_is_hash_and_isolation_locked():
    n = _norm(_RECIPE)
    assert "--no-build-isolation" in n                 # source pass disables build isolation
    assert n.count("--no-deps") >= 2                    # both passes disable implicit deps
    # every backend-partition pip install is hash-pinned
    for seg in n.split("pip install")[1:]:
        head = seg[:220]
        if "backends.wheel.txt" in head or "backends.source.txt" in head:
            assert "--require-hashes" in head


def test_allowlist_lifecycle_absent_present_required(tmp_path):
    # absent pre-gate is fine; required at the release gate; present+valid passes.
    st = R.validate_builder_inputs(str(tmp_path), require_present=False)
    assert st["requirements-build-backends.source-allowlist"] == "absent"
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=True)
    _write_inputs(tmp_path, _VALID_APT, _VALID_RUSTUP, _VALID_BB, _VALID_ALLOWLIST)
    st2 = R.validate_builder_inputs(str(tmp_path), require_present=True)
    assert st2["requirements-build-backends.source-allowlist"] == "present"


def test_allowlist_noncanonical_entry_rejected_in_lifecycle(tmp_path):
    _write_inputs(tmp_path, _VALID_APT, _VALID_RUSTUP, _VALID_BB, "CFFI\n")   # not normalized
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=False)
    _write_inputs(tmp_path, _VALID_APT, _VALID_RUSTUP, _VALID_BB, "c_ffi\n")  # underscore
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=False)


def test_allowlist_lifecycle_exact_use_when_lock_present(tmp_path):
    # allowlist canonical but its name is NOT pinned in the PRESENT backend lock (maturin) ->
    # exact-use fails at the lifecycle gate, INCLUDING the required release gate.
    _write_inputs(tmp_path, _VALID_APT, _VALID_RUSTUP, _VALID_BB, "cffi\n")
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=False)
    with pytest.raises(R.ReleaseError):
        R.validate_builder_inputs(str(tmp_path), require_present=True)


def test_allowlist_lifecycle_grammar_only_when_lock_absent(tmp_path):
    # allowlist committed first (pre-tag dev), backend lock not yet present -> grammar-only passes
    (tmp_path / "requirements-build-backends.source-allowlist").write_text("cffi\n")
    st = R.validate_builder_inputs(str(tmp_path), require_present=False)
    assert st["requirements-build-backends.source-allowlist"] == "present"
    assert st["requirements-build-backends.lock"] == "absent"
