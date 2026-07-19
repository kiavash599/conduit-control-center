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
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys

import pytest

from release import ccc_release as R

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_B = _ROOT / "release" / "builder"
_PHASE_A = (_B / "build-builder-image.sh").read_text(encoding="utf-8")
_PHASE_B = (_B / "build-wheelhouse-offline.sh").read_text(encoding="utf-8")
_RECIPE = (_B / "Containerfile").read_text(encoding="utf-8")
# Require a NATIVE POSIX bash. On Windows, shutil.which("bash") can resolve to the WindowsApps
# WSL launcher (bash.exe), which then receives Windows paths through /bin/bash and mangles the
# separators; the POSIX-only reader fixtures also (correctly) reject Windows absolute paths. So
# these bash-backed tests must skip on Windows even when a bash.exe launcher exists.
_bash = None if os.name == "nt" else shutil.which("bash")
_needs_bash = pytest.mark.skipif(_bash is None, reason="native POSIX bash required")


# --------------------------------------------------------------------------- #
#  Static contract                                                             #
# --------------------------------------------------------------------------- #
@_needs_bash
def test_scripts_parse():
    for s in ("build-builder-image.sh", "build-wheelhouse-offline.sh", "manifest-capture.lib.sh"):
        r = subprocess.run([_bash, "-n", str(_B / s)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_phase_b_runs_immutable_image_id_and_reverifies():
    assert '"${CCC_RUNTIME_IMAGE_ID}" \\' in _PHASE_B
    assert 'python3 /repo/release/build_wheelhouse.py' in _PHASE_B
    assert '{{.Id}}' in _PHASE_B and '"${CUR_ID}" == "${CCC_RUNTIME_IMAGE_ID}"' in _PHASE_B
    assert 'CUR_MANIFEST_DIGEST' in _PHASE_B and '== "${CCC_IMAGE_MANIFEST_DIGEST}"' in _PHASE_B


def test_phase_b_offline_and_hardening_flags():
    for flag in ("--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                 "--user 1000:1000", "--read-only"):
        assert flag in _PHASE_B


def test_phase_b_field_proven_exec_scratch_not_work():
    # the FIELD-PROVEN executable scratch is /tmp:...,exec,...; no untested /work scratch exists.
    assert "--tmpfs /tmp:rw,exec,nosuid,nodev,size=512m" in _PHASE_B
    assert "/work" not in _PHASE_B


def test_phase_b_dual_origin_reuse_mounts_are_readonly():
    # the reuse authorization + store are mounted READ-ONLY and passed to the builder.
    assert ':/in/reuse-authz.json:ro' in _PHASE_B
    assert ':/in/reuse:ro' in _PHASE_B
    assert '--reuse-authz /in/reuse-authz.json' in _PHASE_B
    assert '--reuse-wheels-dir /in/reuse' in _PHASE_B


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
    assert '[[ -s "${SRC}" ]] ||' in _PHASE_B          # bundle provenance is checked


def test_phase_b_atomic_bundle_and_target_policy():
    # Python owns the atomic bundle; shell no longer direct-writes the wheelhouse or provenance.
    assert "--out-bundle /out/bundle" in _PHASE_B
    assert "--target-tags /repo/release/builder/target-supported-tags.txt" in _PHASE_B
    assert "--requirements /repo/requirements.txt" in _PHASE_B
    assert "--enforce-partition-policy" in _PHASE_B
    assert "--out-dir /out/wheelhouse-armhf" not in _PHASE_B
    assert "--provenance-out /out/wheelhouse-armv7.json" not in _PHASE_B


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
# Full, valid single-image manifest with a DISTINCT config digest; the runtime id equals the
# manifest digest -> containerd identity mode (the real RPi2 Docker 29 containerd-store mode).
_CONFIG_DIGEST = "sha256:" + "c" * 64
_MANIFEST_CONTENT = json.dumps({
    "schemaVersion": 2,
    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
    "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
               "digest": _CONFIG_DIGEST, "size": 1234},
    "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                "digest": "sha256:" + "a" * 64, "size": 5678}],
}).encode()
_MANIFEST_DIGEST = "sha256:" + hashlib.sha256(_MANIFEST_CONTENT).hexdigest()
_IMG_ID = _MANIFEST_DIGEST                       # containerd: docker .Id == manifest digest
_FAKE_SUDO = "#!/usr/bin/env bash\nexec \"$@\"\n"
_FAKE_SKOPEO = (
    "#!/usr/bin/env bash\n"
    "if [[ \"$1\" == \"--version\" ]]; then echo 'skopeo version 1.4.1'; exit 0; fi\n"
    "cat \"$FAKE_MANIFEST_FILE\"\n"          # inspect --raw <transport>:<tar> -> raw manifest
)
# `tar -tf <archive>` -> a single listing line so the lib can detect the archive transport.
_FAKE_TAR = (
    "#!/usr/bin/env bash\n"
    "if [[ \"$1\" == \"-tf\" && -n \"${MC_ARCHIVE_STAT_FILE:-}\" && -f \"$2\" ]]; then\n"
    "  stat -c '%a' \"$2\" > \"$MC_ARCHIVE_STAT_FILE\"\n"      # record the created-archive mode
    "fi\n"
    "echo \"${FAKE_ARCHIVE_KIND:-manifest.json}\"\n"
)
_FAKE_DOCKER = (
    "#!/usr/bin/env bash\n"
    "echo \"docker $*\" >> \"$DOCKER_LOG\"\n"
    "sub=\"$1\"; shift || true\n"
    "case \"$sub\" in\n"
    "  image) echo \"$FAKE_IMAGE_ID\";;\n"
    "  info) exit 0;;\n"
    "  pull) exit 0;;\n"
    "  save)\n"                                  # streams to STDOUT; the root '-o' form is refused
    "    for a in \"$@\"; do [[ \"$a\" == \"-o\" ]] && { echo 'FAKE: refusing docker save -o' >&2; exit 3; }; done\n"
    "    [[ \"${FAKE_SAVE_FAIL:-0}\" == \"1\" ]] && exit 1\n"
    "    printf 'FAKE_DOCKER_OCI_ARCHIVE';;\n"
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
    # The Python builder publishes ONE atomic bundle at /out/bundle; the shell validates that
    # layout (provenance + runtime lock), so the mock must reproduce it, not the old flat files.
    "      mkdir -p \"$out/bundle/wheelhouse-armhf\"\n"
    "      echo '{\"ok\":true}' > \"$out/bundle/wheelhouse-armv7.json\"\n"
    "      echo 'x==1 --hash=sha256:2222222222222222222222222222222222222222222222222222222222222222' \\\n"
    "        > \"$out/bundle/requirements-armv7.lock\"\n"
    "      echo '{\"wheel_count\":0}' > \"$out/bundle/build-evidence.json\"\n"
    "    fi;;\n"
    "  *) : ;;\n"
    "esac\n"
)


_READER = _B / "read_builder_inputs.py"
# The real committed Containerfile's canonical sha256, via THE shared implementation (never a local
# re-implementation): Phase B compares CCC_RECIPE_SHA256 against it before Docker runs.
_REAL_RECIPE_SHA = R.canonical_file_sha256((_B / "Containerfile").read_bytes())
_py = shutil.which("python3") or sys.executable


def _valid_kv(base, manifest_path, **over):
    """Full, schema-valid builder-inputs.kv content as bytes; override any key for negatives.
    Paths point under `base` (an absolute tmp dir) with the exact basenames the reader pins; they
    need not exist (the reader validates shape, not existence)."""
    d = {
        "CCC_BUILDER_IDENTITY": "conduit-control-center-armv7-wheelhouse-builder",
        "CCC_RECIPE": str(base / "Containerfile"),
        # Phase B now BINDS this to the real committed Containerfile (image-context recipe check),
        # so the fixture must carry that file's true LF-canonical hash, not a placeholder.
        "CCC_RECIPE_SHA256": _REAL_RECIPE_SHA,
        "CCC_BUILD_BACKENDS_LOCK": str(base / "requirements-build-backends.lock"),
        "CCC_APT_PACKAGES": str(base / "apt-packages.list"),
        "CCC_RUSTUP_SHA": str(base / "rustup-init.sha256"),
        "CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST": str(base / "requirements-build-backends.source-allowlist"),
        "CCC_BUILD_BACKENDS_SOURCE_ALLOWLIST_SHA256": "4" * 64,
        "CCC_BASE_IMAGE_DIGEST": "sha256:" + "b" * 64,
        "CCC_IMAGE_TAG": "ccc:local",
        "CCC_RUNTIME_IMAGE_ID": _IMG_ID,
        "CCC_IMAGE_MANIFEST": str(manifest_path),
        "CCC_IMAGE_MANIFEST_DIGEST": _MANIFEST_DIGEST,
        "CCC_IMAGE_CONFIG_DIGEST": _CONFIG_DIGEST,
        "CCC_IMAGE_IDENTITY_MODE": "containerd",
        "CCC_MANIFEST_CAPTURE_TRANSPORT": "docker-archive",
    }
    d.update(over)
    return "".join("%s=%s\n" % (k, v) for k, v in d.items()).encode("utf-8")


def _run_reader(path):
    # invoke the reader as a plain script (never sourced); stdout is NUL-delimited bytes
    return subprocess.run([_py, str(_READER), "--inputs", str(path)], capture_output=True)


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
    # (FAKE_ARCHIVE_KIND / FAKE_MANIFEST_FILE may be overridden by callers via the returned env)
    binp = tmp_path / "bin"
    binp.mkdir()
    _mkbin(binp, "sudo", _FAKE_SUDO)
    _mkbin(binp, "docker", _FAKE_DOCKER)
    _mkbin(binp, "skopeo", _FAKE_SKOPEO)
    _mkbin(binp, "tar", _FAKE_TAR)
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    evid = tmp_path / "evidence"
    evid.mkdir()
    inputs = evid / "builder-inputs.kv"
    inputs.write_bytes(_valid_kv(tmp_path, manifest))
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
    env["FAKE_ARCHIVE_KIND"] = "manifest.json"      # -> docker-archive transport
    env["MC_ARCHIVE_STAT_FILE"] = str(tmp_path / "archive.mode")
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
def test_phase_b_rejects_wrong_recipe_hash_before_docker(tmp_path):
    """IMAGE-CONTEXT recipe binding: a syntactically VALID but WRONG CCC_RECIPE_SHA256 (i.e. the
    executing image was built from a different Containerfile) must fail closed BEFORE the expensive
    Docker path, naming expected vs actual and the Phase-A-rerun instruction."""
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    wrong = "0123456789abcdef" * 4                       # valid lowercase 64-hex, != real recipe
    assert len(wrong) == 64 and wrong != _REAL_RECIPE_SHA
    inputs.write_bytes(_valid_kv(tmp_path, tmp_path / "image-manifest.json",
                                 CCC_RECIPE_SHA256=wrong))
    r = _run_phase_b(env, inputs, sdist, outd, tmp_path / "prov.json", *_res_args(tmp_path))
    assert r.returncode != 0, r.stdout
    assert "recipe mismatch" in r.stderr
    assert wrong in r.stderr and _REAL_RECIPE_SHA in r.stderr      # expected AND actual reported
    assert "Phase A must be re-run" in r.stderr
    assert _no_docker_run(log)                                     # never reached the build
    assert not (outd / "bundle").exists()                          # nothing published


def test_phase_b_uses_shared_canonical_module_not_a_local_reimplementation():
    """WIRING PROOF: the shell must delegate the recipe digest to the stdlib-only shared module and
    must not carry its own normalisation. A local `.replace(b"\\r\\n", ...)` would silently handle
    only CRLF and diverge from the shared rule (which also folds lone CR), so its ABSENCE is
    asserted here -- the equivalence tests alone would stay green if it were reintroduced."""
    src = (_B / "build-wheelhouse-offline.sh").read_text(encoding="utf-8")
    assert "python3 -m release.canonical_bytes" in src
    assert "sha256-file" in src
    assert 'PYTHONPATH="${REPO}"' in src
    assert "release.ccc_release" not in src          # host path stays stdlib-only (no `packaging`)
    assert ".replace(" not in src                    # no local normalisation implementation
    assert "hashlib" not in src                      # no local digest implementation


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


# --------------------------------------------------------------------------- #
#  Manifest-capture correction: docker save -> archive transport -> skopeo     #
#  (no docker-daemon transport), fail-fast preflight, atomic evidence.         #
# --------------------------------------------------------------------------- #
_LIB = (_B / "manifest-capture.lib.sh").read_text(encoding="utf-8")
_BASE = "docker.io/arm32v7/ubuntu:22.04@sha256:" + "f" * 64


def test_no_docker_daemon_transport_in_executable_paths():
    # req 8: the executable docker-daemon capture path is gone everywhere; only the local
    # archive flow remains (lib comments may mention the old transport for context).
    for sh in (_PHASE_A, _PHASE_B, _LIB):
        assert "docker-daemon:${" not in sh
        assert 'skopeo inspect --raw "docker-daemon:' not in sh
    assert "docker save" in _LIB and 'skopeo inspect --raw "${transport}:' in _LIB


def _prep_phase_a(tmp_path, *, manifest=_MANIFEST_CONTENT, archive_kind="manifest.json",
                  image_id=_IMG_ID):
    binp = tmp_path / "bin"
    binp.mkdir()
    _mkbin(binp, "sudo", _FAKE_SUDO)
    _mkbin(binp, "docker", _FAKE_DOCKER)
    _mkbin(binp, "skopeo", _FAKE_SKOPEO)
    _mkbin(binp, "tar", _FAKE_TAR)
    man = tmp_path / "fake-manifest.json"
    man.write_bytes(manifest)
    evid = tmp_path / "evidence"
    evid.mkdir()
    log = tmp_path / "docker.log"
    env = dict(os.environ)
    env["PATH"] = str(binp) + os.pathsep + env["PATH"]
    env["DOCKER_LOG"] = str(log)
    env["FAKE_IMAGE_ID"] = image_id
    env["FAKE_MANIFEST_FILE"] = str(man)
    env["FAKE_ARCHIVE_KIND"] = archive_kind
    env["MC_ARCHIVE_STAT_FILE"] = str(tmp_path / "archive.mode")
    return evid, log, env


def _run_phase_a(env, evid, *, base=_BASE, tag="ccc:t"):
    cmd = [_bash, str(_B / "build-builder-image.sh"),
           "--base-image", base, "--tag", tag, "--evidence-dir", str(evid)]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(_ROOT))


def _no_temp_left(evid):
    names = [p.name for p in evid.iterdir()]
    return not any(n.startswith(".mc.") or n.startswith(".smoke-manifest")
                   or ".builder-inputs.kv.tmp" in n or n.endswith(".tmp") for n in names)


@_needs_bash
def test_phase_a_success_records_transport_and_atomic_evidence(tmp_path):
    evid, log, env = _prep_phase_a(tmp_path)
    r = _run_phase_a(env, evid)
    assert r.returncode == 0, r.stderr
    assert (evid / "image-manifest.json").read_bytes() == _MANIFEST_CONTENT
    inp = (evid / "builder-inputs.kv").read_text()
    assert "CCC_MANIFEST_CAPTURE_TRANSPORT=docker-archive" in inp
    assert "CCC_IMAGE_IDENTITY_MODE=containerd" in inp
    assert ("CCC_RUNTIME_IMAGE_ID=%s" % _IMG_ID) in inp
    assert ("CCC_IMAGE_CONFIG_DIGEST=%s" % _CONFIG_DIGEST) in inp
    assert "CCC_SKOPEO_VERSION" not in inp        # removed from the consumed contract
    assert '"' not in inp                         # raw data values, not shell literals
    # the published data file passes the SAME strict reader Phase B uses
    assert _run_reader(evid / "builder-inputs.kv").returncode == 0
    assert _no_temp_left(evid)


@_needs_bash
def test_phase_a_preflight_capture_runs_before_docker_build(tmp_path):
    evid, log, env = _prep_phase_a(tmp_path)
    r = _run_phase_a(env, evid)
    assert r.returncode == 0, r.stderr
    lines = log.read_text().splitlines()
    first_save = next(i for i, ln in enumerate(lines) if ln.startswith("docker save"))
    first_build = next(i for i, ln in enumerate(lines) if ln.startswith("docker build"))
    assert first_save < first_build   # interop smoke capture precedes the expensive build


@_needs_bash
def test_phase_a_oci_archive_transport_detected_and_recorded(tmp_path):
    evid, log, env = _prep_phase_a(tmp_path, archive_kind="oci-layout")
    r = _run_phase_a(env, evid)
    assert r.returncode == 0, r.stderr
    assert "CCC_MANIFEST_CAPTURE_TRANSPORT=oci-archive" in (evid / "builder-inputs.kv").read_text()


@_needs_bash
def test_phase_a_unrecognized_archive_fails_before_build(tmp_path):
    evid, log, env = _prep_phase_a(tmp_path, archive_kind="not-an-image.txt")
    r = _run_phase_a(env, evid)
    assert r.returncode != 0
    assert not (evid / "image-manifest.json").exists()
    assert not (evid / "builder-inputs.kv").exists()
    assert "docker build" not in log.read_text()      # aborted at preflight
    assert _no_temp_left(evid)


@_needs_bash
def test_phase_a_identity_relationship_mismatch_fails_atomic_no_evidence(tmp_path):
    # runtime id equals neither the manifest digest nor the config digest -> no identity mode ->
    # shared gate fails at the smoke test -> abort before build, no artifacts.
    evid, log, env = _prep_phase_a(tmp_path, image_id="sha256:" + "9" * 64)
    r = _run_phase_a(env, evid)
    assert r.returncode != 0
    assert not (evid / "image-manifest.json").exists()
    assert not (evid / "builder-inputs.kv").exists()
    assert "docker build" not in log.read_text()
    assert _no_temp_left(evid)


@_needs_bash
def test_phase_a_zero_byte_capture_rejected_atomic(tmp_path):
    evid, log, env = _prep_phase_a(tmp_path, manifest=b"")
    r = _run_phase_a(env, evid)
    assert r.returncode != 0
    assert not (evid / "image-manifest.json").exists()
    assert not (evid / "builder-inputs.kv").exists()
    assert _no_temp_left(evid)


@_needs_bash
def test_phase_b_reuses_recorded_transport_and_rejects_drift(tmp_path):
    # builder-inputs.kv recorded docker-archive; a Phase-B capture that detects oci-archive
    # (representation drift) must fail rather than silently accept a different representation.
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    env["FAKE_ARCHIVE_KIND"] = "oci-layout"           # detected transport now differs
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode != 0 and "transport drift" in r.stderr
    assert _no_docker_run(log)


def test_capture_uses_protected_user_owned_redirection_not_root_o():
    # req: no root-created `docker save -o`; use the umask-protected user-owned redirection.
    assert 'docker save "${tag}" -o' not in _LIB
    assert '-o "${tar}"' not in _LIB
    assert '( umask 077; sudo docker save "${tag}" > "${tar}" )' in _LIB
    # tar's own diagnostic is preserved (real permission/format cause is not discarded)
    assert 'tar -tf "${tar}" 2>/dev/null' not in _LIB
    assert 'listing="$(tar -tf "${tar}")"' in _LIB


@_needs_bash
def test_capture_archive_is_user_owned_mode_600(tmp_path):
    # The lib creates the archive via `(umask 077; sudo docker save ... > tar)`, so it is owned
    # by the unprivileged ceremony user and mode 0600 (never a root-created 0600 file).
    evid, log, env = _prep_phase_a(tmp_path)
    r = _run_phase_a(env, evid)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "archive.mode").read_text().strip() == "600"


@_needs_bash
def test_capture_rejects_root_o_form_via_fake(tmp_path):
    # Defensive: if the production command still used `docker save -o`, the fake refuses it and
    # capture fails. A SUCCESSFUL run therefore proves the lib uses stdout redirection, not -o.
    evid, log, env = _prep_phase_a(tmp_path)
    r = _run_phase_a(env, evid)
    assert r.returncode == 0, r.stderr
    assert "refusing docker save -o" not in r.stderr


@_needs_bash
def test_phase_a_docker_save_failure_fails_closed(tmp_path):
    evid, log, env = _prep_phase_a(tmp_path)
    env["FAKE_SAVE_FAIL"] = "1"
    r = _run_phase_a(env, evid)
    assert r.returncode != 0
    assert not (evid / "image-manifest.json").exists()
    assert not (evid / "builder-inputs.kv").exists()
    assert "docker build" not in log.read_text()      # aborted at the preflight capture
    assert _no_temp_left(evid)


@_needs_bash
def test_phase_b_rejects_identity_mode_drift(tmp_path):
    # Phase-A recorded containerd, but rewrite the evidence to claim legacy; the recaptured
    # manifest still derives containerd, so --expect-mode legacy fails closed.
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    txt = inputs.read_text().replace("CCC_IMAGE_IDENTITY_MODE=containerd",
                                     "CCC_IMAGE_IDENTITY_MODE=legacy")
    inputs.write_text(txt)
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode != 0
    assert _no_docker_run(log)


# --------------------------------------------------------------------------- #
#  Data-boundary: builder-inputs.kv is DATA, parsed by read_builder_inputs.py  #
#  and NEVER sourced/eval'd. Adversarial + round-trip coverage.                #
# --------------------------------------------------------------------------- #
def test_no_source_or_eval_of_builder_inputs_data_file():
    # builder-inputs.kv is data: never source/./eval'd anywhere; both phases parse via the reader.
    assert "read_builder_inputs.py" in _PHASE_A and "read_builder_inputs.py" in _PHASE_B
    assert 'source "${INPUTS}"' not in _PHASE_B
    assert '. "${INPUTS}"' not in _PHASE_B
    assert "builder-inputs.kv" in _PHASE_A and "builder-inputs.kv" in _PHASE_B
    assert "builder-inputs.env" not in _PHASE_A and "builder-inputs.env" not in _PHASE_B
    # No `eval` command, and the ONLY `source`/`.` is the shell LIBRARY -- never the data file.
    # (comment lines may mention "eval"/"source" descriptively; ignore them.)
    for sh in (_PHASE_A, _PHASE_B):
        for ln in sh.splitlines():
            s = ln.strip()
            if s.startswith("#"):
                continue
            assert "eval" not in s, s
            if s.startswith("source ") or s.startswith(". "):
                assert "manifest-capture.lib.sh" in s, s


@_needs_bash
def test_reader_valid_round_trip(tmp_path):
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    kv = tmp_path / "builder-inputs.kv"
    kv.write_bytes(_valid_kv(tmp_path, manifest))
    r = _run_reader(kv)
    assert r.returncode == 0, r.stderr
    recs = r.stdout.split(b"\x00")
    assert recs[-1] == b""                                  # NUL-terminated stream
    pairs = [rec.split(b"=", 1) for rec in recs[:-1]]
    assert len(pairs) == 16
    keys = [k.decode() for k, _ in pairs]
    assert keys[0] == "CCC_BUILDER_IDENTITY"                # deterministic schema order
    assert keys[-1] == "CCC_MANIFEST_CAPTURE_TRANSPORT"
    got = {k.decode(): v.decode() for k, v in pairs}
    assert got["CCC_IMAGE_IDENTITY_MODE"] == "containerd"
    assert got["CCC_RUNTIME_IMAGE_ID"] == _IMG_ID


@_needs_bash
def test_reader_real_multiword_skopeo_version_is_foreign(tmp_path):
    # the real offender: a space-bearing skopeo version is now OUTSIDE the consumed contract, so its
    # presence is rejected as a foreign key -- harmless, never parsed into Phase-B state.
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    body = _valid_kv(tmp_path, manifest) + b"CCC_SKOPEO_VERSION=skopeo version 1.4.1\n"
    kv = tmp_path / "builder-inputs.kv"
    kv.write_bytes(body)
    r = _run_reader(kv)
    assert r.returncode != 0
    assert b"foreign key" in r.stderr and r.stdout == b""


@_needs_bash
def test_reader_rejects_command_substitution_and_backticks_no_marker(tmp_path):
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    marker = tmp_path / "pwned"
    for tag in ("$(touch %s)" % marker, "`touch %s`" % marker):
        kv = tmp_path / "bad.kv"
        kv.write_bytes(_valid_kv(tmp_path, manifest, CCC_IMAGE_TAG=tag))
        r = _run_reader(kv)
        assert r.returncode != 0 and r.stdout == b""
        assert not marker.exists()                          # never executed (reader is a parser)


@_needs_bash
@pytest.mark.parametrize("tag", ['a"b', "a'b", "a b", "a;b", "a|b", "a&b", "a>b"])
def test_reader_rejects_embedded_quotes_and_metacharacters(tmp_path, tag):
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    kv = tmp_path / "bad.kv"
    kv.write_bytes(_valid_kv(tmp_path, manifest, CCC_IMAGE_TAG=tag))
    r = _run_reader(kv)
    assert r.returncode != 0 and r.stdout == b""


@_needs_bash
def test_reader_rejects_newline_smuggled_assignment(tmp_path):
    # a smuggled extra record (what a newline in a value would create) fails closed.
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    body = _valid_kv(tmp_path, manifest).replace(
        b"CCC_IMAGE_TAG=ccc:local\n",
        b"CCC_IMAGE_TAG=ccc:local\nCCC_IMAGE_IDENTITY_MODE=legacy\n")   # duplicate/foreign smuggle
    kv = tmp_path / "bad.kv"
    kv.write_bytes(body)
    r = _run_reader(kv)
    assert r.returncode != 0 and r.stdout == b""


@_needs_bash
@pytest.mark.parametrize("mutate,needle", [
    (lambda b: b.replace(b"containerd\n", b"container\x00d\n"), b"NUL"),
    (lambda b: b.replace(b"\n", b"\r\n"), b"CRLF"),
    (lambda b: b.rstrip(b"\n"), b"final LF"),
    (lambda b: b + b"\n", b"blank line"),
    (lambda b: b"# comment\n" + b, b"comment"),
    (lambda b: b.replace(b"CCC_IMAGE_TAG=ccc:local\n",
                         b"CCC_IMAGE_TAG=ccc:local\nCCC_IMAGE_TAG=ccc:local\n"), b"duplicate key"),
    (lambda b: b + b"CCC_EVIL=1\n", b"foreign key"),
    (lambda b: b.replace(b"CCC_IMAGE_IDENTITY_MODE=containerd\n", b""), b"missing required"),
])
def test_reader_structural_violations_fail_closed(tmp_path, mutate, needle):
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    kv = tmp_path / "bad.kv"
    kv.write_bytes(mutate(_valid_kv(tmp_path, manifest)))
    r = _run_reader(kv)
    assert r.returncode != 0 and r.stdout == b""
    assert needle in r.stderr


@_needs_bash
@pytest.mark.parametrize("over", [
    {"CCC_RUNTIME_IMAGE_ID": "sha256:short"},
    {"CCC_RUNTIME_IMAGE_ID": "sha256:" + "A" * 64},          # uppercase hex
    {"CCC_BASE_IMAGE_DIGEST": "sha512:" + "a" * 64},         # wrong algo
    {"CCC_RECIPE_SHA256": "xyz"},                            # bad bare hash
    {"CCC_IMAGE_IDENTITY_MODE": "bogus"},
    {"CCC_IMAGE_IDENTITY_MODE": "index"},                    # index is smoke-only, not in contract
    {"CCC_MANIFEST_CAPTURE_TRANSPORT": "docker-daemon"},     # removed transport
    {"CCC_BUILDER_IDENTITY": "somethingelse"},
    {"CCC_RECIPE": "relative/Containerfile"},                # not absolute
    {"CCC_RECIPE": "/etc/../Containerfile"},                 # traversal
    {"CCC_IMAGE_MANIFEST": "/tmp/wrong-basename.json"},      # basename not pinned
])
def test_reader_field_validation_fails_closed(tmp_path, over):
    manifest = tmp_path / "image-manifest.json"
    manifest.write_bytes(_MANIFEST_CONTENT)
    kv = tmp_path / "bad.kv"
    kv.write_bytes(_valid_kv(tmp_path, manifest, **over))
    r = _run_reader(kv)
    assert r.returncode != 0 and r.stdout == b""


@_needs_bash
def test_phase_a_invalid_producer_output_not_published(tmp_path):
    # producer-side gate (no prior artifact): a value the reader rejects (tag with a space) must
    # abort Phase A BEFORE the atomic publish, so no builder-inputs.kv is ever published.
    evid, log, env = _prep_phase_a(tmp_path)
    r = _run_phase_a(env, evid, tag="ccc bad:local")
    assert r.returncode != 0
    assert "refusing to publish" in r.stderr
    assert not (evid / "builder-inputs.kv").exists()
    assert _no_temp_left(evid)


@_needs_bash
def test_phase_a_invalid_producer_does_not_replace_existing_valid_artifact(tmp_path):
    # A failed Phase A must never clobber an ALREADY-published valid builder-inputs.kv: producer
    # validation runs BEFORE the atomic rename and writes only a distinct temp, so an existing
    # artifact is preserved byte-for-byte (not replaced, not deleted).
    evid, log, env = _prep_phase_a(tmp_path)
    published = evid / "builder-inputs.kv"
    prior = _valid_kv(tmp_path, tmp_path / "image-manifest.json", CCC_IMAGE_TAG="prior:artifact")
    published.write_bytes(prior)
    before = published.read_bytes()                       # exact bytes of the existing valid artifact
    assert _run_reader(published).returncode == 0         # the pre-existing artifact is genuinely valid
    r = _run_phase_a(env, evid, tag="ccc bad:local")      # invalid producer output (tag with a space)
    assert r.returncode != 0
    assert "refusing to publish" in r.stderr
    assert published.exists()                             # existing artifact NOT deleted
    assert published.read_bytes() == before               # bytes EXACTLY unchanged (NOT replaced)
    assert _no_temp_left(evid)                             # producer temp cleaned


@_needs_bash
def test_phase_b_rejects_injected_inputs_before_docker_no_marker(tmp_path):
    # a malicious KV never reaches Docker: the reader rejects it, Phase B dies, no marker executes.
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    marker = tmp_path / "pwned"
    inputs.write_bytes(_valid_kv(tmp_path, tmp_path / "image-manifest.json",
                                 CCC_IMAGE_TAG="$(touch %s)" % marker))
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode != 0
    assert not marker.exists()
    assert _no_docker_run(log)


@_needs_bash
def test_phase_b_partial_or_failed_reader_does_not_populate_state(tmp_path):
    # if the reader fails, Phase B must not proceed with any builder-input state (fail closed).
    _binp, inputs, sdist, _lock, outd, log, env = _prep(tmp_path)
    inputs.write_bytes(_valid_kv(tmp_path, tmp_path / "image-manifest.json").replace(
        b"CCC_IMAGE_IDENTITY_MODE=containerd\n", b""))          # missing required key
    prov = tmp_path / "prov.json"
    r = _run_phase_b(env, inputs, sdist, outd, prov, *_res_args(tmp_path))
    assert r.returncode != 0
    assert "strict validation" in r.stderr
    assert _no_docker_run(log)
