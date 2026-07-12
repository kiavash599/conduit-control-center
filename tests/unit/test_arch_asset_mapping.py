# SPDX-License-Identifier: MIT
"""
tests/unit/test_arch_asset_mapping.py
-------------------------------------
BL-0002 (Raspberry Pi 2 / armhf support) contract tests for the shared
architecture -> Conduit-asset mapping and armhf wheelhouse provisioning in
``install.sh`` and ``update.sh``.

These scripts run privileged/root end-to-end, so we assert the safely testable
pieces: (1) the pure ``conduit_asset_for_arch`` function is *executed* behaviorally
by extracting and sourcing just that function; (2) install/update parity of the
shared functions; (3) static guarantees that fail-closed, verification, and
wheelhouse behaviour are present and that the aarch64 path is preserved.

Runnable with the stdlib: ``python -m unittest tests.unit.test_arch_asset_mapping``
(also pytest-compatible).
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_INSTALL = (_ROOT / "install.sh").read_text(encoding="utf-8")
_UPDATE = (_ROOT / "update.sh").read_text(encoding="utf-8")

_HAS_BASH = shutil.which("bash") is not None

def _run_provision(arch, wh_dir, pip_rc=0):
    """Execute the extracted install_python_deps() with a fake uname (forcing
    ``arch``) and a fake pip (records args, exits ``pip_rc``). Returns
    (return_code, pip_args_text, stderr)."""
    d = tempfile.mkdtemp()
    bind = os.path.join(d, "bin")
    os.makedirs(bind)
    with open(os.path.join(bind, "uname"), "w") as f:
        f.write('#!/bin/sh\necho "%s"\n' % arch)
    os.chmod(os.path.join(bind, "uname"), 0o755)
    pip_args = os.path.join(d, "pip-args.txt")
    with open(os.path.join(bind, "pip"), "w") as f:
        f.write('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\nexit %d\n' % (pip_args, pip_rc))
    os.chmod(os.path.join(bind, "pip"), 0o755)
    req = os.path.join(d, "requirements.txt")
    open(req, "w").close()
    func = _extract_func(_INSTALL, "install_python_deps")
    script = (
        'export PATH="%s:$PATH"\n' % bind
        + 'warn() { printf "WARN: %%s\\n" "$*" >&2; }\n'
        + func + "\n"
        + 'install_python_deps "%s/bin/pip" "%s" "%s"; echo "RC=$?"\n' % (d, req, wh_dir)
    )
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    rc = next((int(ln[3:]) for ln in p.stdout.splitlines() if ln.startswith("RC=")), None)
    args = open(pip_args).read() if os.path.exists(pip_args) else ""
    return rc, args, p.stderr


def _make_wheelhouse(valid=True, with_sums=True):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "foo-1.0-py3-none-any.whl"), "wb") as f:
        f.write(b"dummy-wheel-bytes")
    if with_sums:
        digest = hashlib.sha256(b"dummy-wheel-bytes").hexdigest()
        if not valid:
            digest = "0" * 64
        with open(os.path.join(d, "SHA256SUMS"), "w") as f:
            f.write("%s  foo-1.0-py3-none-any.whl\n" % digest)
    return d



def _extract_func(src: str, name: str) -> str:
    lines = src.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.rstrip().startswith(f"{name}() {{")), None)
    assert start is not None, f"function {name} not found"
    end = next((j for j in range(start + 1, len(lines)) if lines[j] == "}"), None)
    assert end is not None, f"closing brace for {name} not found"
    return "\n".join(lines[start:end + 1]) + "\n"


def _run_mapping(arch: str):
    func = _extract_func(_INSTALL, "conduit_asset_for_arch")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(func)
        path = fh.name
    try:
        p = subprocess.run(
            ["bash", "-c", f'source "{path}"; conduit_asset_for_arch "{arch}"'],
            capture_output=True, text=True,
        )
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    return p.returncode, p.stdout.strip()


class ArchAssetMappingTests(unittest.TestCase):
    @unittest.skipUnless(_HAS_BASH, "bash is not available")
    def test_aarch64_maps_to_arm64(self):
        rc, out = _run_mapping("aarch64")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "conduit-linux-arm64")

    @unittest.skipUnless(_HAS_BASH, "bash is not available")
    def test_armv7l_maps_to_armv7(self):
        rc, out = _run_mapping("armv7l")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "conduit-linux-armv7")

    @unittest.skipUnless(_HAS_BASH, "bash is not available")
    def test_unsupported_arch_fails_closed(self):
        for bad in ("armv6l", "x86_64", "i686", "riscv64", ""):
            rc, out = _run_mapping(bad)
            self.assertNotEqual(rc, 0, f"{bad!r} should fail closed")
            self.assertEqual(out, "", f"{bad!r} must emit no asset name")

    def test_armv6_not_mapped(self):
        # v1 scope excludes armv6/Pi Zero.
        self.assertNotIn("conduit-linux-armv6", _INSTALL)
        self.assertNotIn("conduit-linux-armv6", _UPDATE)


class ParityTests(unittest.TestCase):
    def test_mapping_function_identical(self):
        self.assertEqual(_extract_func(_INSTALL, "conduit_asset_for_arch"),
                         _extract_func(_UPDATE, "conduit_asset_for_arch"))

    def test_provisioning_function_identical(self):
        self.assertEqual(_extract_func(_INSTALL, "install_python_deps"),
                         _extract_func(_UPDATE, "install_python_deps"))

    def test_both_use_the_mapping_for_asset(self):
        for src in (_INSTALL, _UPDATE):
            self.assertIn('conduit_asset_for_arch "$(uname -m)"', src)
        # the hardcoded arm64-only asset must be gone from both
        self.assertNotIn('local _asset="conduit-linux-arm64"', _INSTALL)
        self.assertNotIn('local _asset="conduit-linux-arm64"', _UPDATE)


class PreflightAndVerificationTests(unittest.TestCase):
    def test_install_arch_gate_allowlist_and_failclosed(self):
        self.assertIn("aarch64|armv7l) : ;;", _INSTALL)
        # unsupported arch path still calls die
        self.assertIn('*) die "Unsupported architecture: ${os_arch}.', _INSTALL)
        # the old aarch64-only hard gate is gone
        self.assertNotIn('[[ "${os_arch}" == "aarch64" ]] || die', _INSTALL)

    def test_checksum_and_version_verification_preserved(self):
        for src in (_INSTALL, _UPDATE):
            self.assertIn("checksums.txt", src)
            self.assertIn("sha256sum", src)
            self.assertIn('grep -q "${CONDUIT_VERSION}"', src)  # binary --version gate
            self.assertIn("--version", src)


class WheelhouseTests(unittest.TestCase):
    def test_armhf_uses_wheelhouse_and_fails_closed(self):
        for src in (_INSTALL, _UPDATE):
            self.assertIn("--no-index", src)                   # install from wheelhouse (no index)
            self.assertIn("--find-links", src)
            self.assertIn("wheelhouse-armhf", src)
            self.assertIn("SHA256SUMS", src)                    # verifiable
            self.assertIn("armhf wheelhouse not found", src)    # fail-closed message

    def test_armhf_does_not_source_build(self):
        # No source-build fallback for armhf: --no-binary must not appear.
        for src in (_INSTALL, _UPDATE):
            self.assertNotIn("--no-binary", src)

    def test_aarch64_provisioning_preserved(self):
        # aarch64 branch installs from the index exactly as before (no --no-index).
        for src in (_INSTALL, _UPDATE):
            func = _extract_func(src, "install_python_deps")
            aarch = func.split("aarch64)")[1].split(";;")[0]
            self.assertIn('install --quiet -r "${_req}"', aarch)
            self.assertNotIn("--no-index", aarch)


class SyntaxTests(unittest.TestCase):
    @unittest.skipUnless(_HAS_BASH, "bash is not available")
    def test_scripts_parse(self):
        for name in ("install.sh", "update.sh"):
            p = subprocess.run(["bash", "-n", str(_ROOT / name)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)


class UpdateFailureMessageTests(unittest.TestCase):
    """Issue 2: the update dependency-install failure must still tell the
    operator the service is still running and to re-run update.sh."""

    def test_update_preserves_operator_failure_message(self):
        self.assertRegex(
            _UPDATE,
            r'install_python_deps [^\n]*\|\| die "pip install failed\. '
            r'Service is still running version \$\{CURRENT_VERSION\}\. '
            r'Resolve the dependency issue and re-run update\.sh\."',
        )

    def test_install_dependency_failure_is_failclosed(self):
        self.assertRegex(_INSTALL, r'install_python_deps [^\n]*\|\| die ')


@unittest.skipUnless(_HAS_BASH, "bash is not available")
class WheelhouseBehaviorTests(unittest.TestCase):
    """Behavioral verification of the armhf wheelhouse acquisition contract."""

    def test_missing_wheelhouse_fails_closed(self):
        rc, args, _ = _run_provision("armv7l", "/no/such/wheelhouse")
        self.assertEqual(rc, 1)
        self.assertEqual(args, "")  # pip never invoked

    def test_missing_sha256sums_fails_closed(self):
        rc, args, _ = _run_provision("armv7l", _make_wheelhouse(with_sums=False))
        self.assertEqual(rc, 1)
        self.assertEqual(args, "")

    def test_checksum_mismatch_fails_closed(self):
        rc, args, _ = _run_provision("armv7l", _make_wheelhouse(valid=False))
        self.assertEqual(rc, 1)
        self.assertEqual(args, "")

    def test_valid_wheelhouse_passes_and_wheels_only(self):
        wh = _make_wheelhouse(valid=True)
        rc, args, err = _run_provision("armv7l", wh, pip_rc=0)
        self.assertEqual(rc, 0, err)
        self.assertIn("--no-index", args)
        self.assertIn("--only-binary=:all:", args)
        self.assertIn("--find-links", args)
        self.assertIn(wh, args)

    def test_local_override_dir_is_honored(self):
        rc, _, _ = _run_provision("armv7l", _make_wheelhouse(valid=True))
        self.assertEqual(rc, 0)

    def test_valid_wheelhouse_but_pip_failure_fails_closed(self):
        rc, args, _ = _run_provision("armv7l", _make_wheelhouse(valid=True), pip_rc=7)
        self.assertEqual(rc, 1)
        self.assertIn("--no-index", args)  # pip ran, then failed -> fail closed

    def test_aarch64_uses_index_not_wheelhouse(self):
        rc, args, err = _run_provision("aarch64", "/ignored", pip_rc=0)
        self.assertEqual(rc, 0, err)
        self.assertIn(" -r ", " " + args + " ")
        self.assertNotIn("--no-index", args)
        self.assertNotIn("--find-links", args)

    def test_unsupported_arch_fails_closed(self):
        rc, args, _ = _run_provision("x86_64", "/ignored")
        self.assertEqual(rc, 1)
        self.assertEqual(args, "")


class WheelhouseContractStaticTests(unittest.TestCase):
    def test_armhf_only_binary_present_arm64_absent(self):
        for src in (_INSTALL, _UPDATE):
            func = _extract_func(src, "install_python_deps")
            armv7 = func.split("armv7l)")[1].split(";;")[0]
            aarch = func.split("aarch64)")[1].split(";;")[0]
            self.assertIn("--only-binary=:all:", armv7)
            self.assertNotIn("--only-binary", aarch)

    def test_callsites_use_ccc_wheelhouse_override(self):
        self.assertIn("${CCC_WHEELHOUSE_DIR:-${SCRIPT_DIR}/wheelhouse-armhf}", _INSTALL)
        self.assertIn("${CCC_WHEELHOUSE_DIR:-${SOURCE_DIR}/wheelhouse-armhf}", _UPDATE)


@unittest.skipUnless(_HAS_BASH, "bash is not available")
class CrlfUnitFileTests(unittest.TestCase):
    """Issue 2: installed systemd unit files must be LF-normalized so the
    $-anchored validation cannot fail on CRLF, without weakening validation."""

    _GREP = 'grep -qE "^Environment=CCC_MAX_PERSONAL_CLIENTS=0$"'

    def test_crlf_fails_before_and_passes_after_normalization(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "conduit.service")
        with open(src, "wb") as f:
            f.write(b"[Service]\r\nEnvironment=CCC_MAX_PERSONAL_CLIENTS=0\r\n")
        # reproduces the field bug: $-anchored grep fails on the trailing CR
        r = subprocess.run(["bash", "-c", f'{self._GREP} "{src}"'])
        self.assertNotEqual(r.returncode, 0)
        # the installer's normalization makes it pass deterministically
        dst = os.path.join(d, "out.service")
        r = subprocess.run(["bash", "-c",
                            f"sed 's/\\r$//' '{src}' > '{dst}'; {self._GREP} '{dst}'"])
        self.assertEqual(r.returncode, 0)

    def test_validation_not_weakened_when_default_missing(self):
        d = tempfile.mkdtemp()
        dst = os.path.join(d, "out.service")
        with open(dst, "w") as f:
            f.write("[Service]\nEnvironment=SOMETHING_ELSE=1\n")
        r = subprocess.run(["bash", "-c", f'{self._GREP} "{dst}"'])
        self.assertNotEqual(r.returncode, 0)


class CrlfStaticTests(unittest.TestCase):
    def test_scripts_lf_normalize_units_not_bare_cp(self):
        for src in (_INSTALL, _UPDATE):
            self.assertIn("sed 's/\\r$//'", src)      # LF normalization present
        # no bare cp of a deployment unit into an install destination
        self.assertNotIn('cp "${APP_DIR}/deployment/conduit.service"', _INSTALL)
        self.assertNotIn('cp "${APP_DIR}/deployment/conduit-cc.service"', _INSTALL)
        self.assertNotIn('cp "${SOURCE_DIR}/deployment/conduit.service"', _UPDATE)
        self.assertNotIn('cp "${APP_DIR}/deployment/conduit-cc.service"', _UPDATE)


class WheelhouseClosureTests(unittest.TestCase):
    """Issue 3: the wheelhouse must satisfy the FULL requirements.txt closure;
    an incomplete wheelhouse must fail closed."""

    def test_wheelhouse_installs_full_requirements_closure(self):
        for src in (_INSTALL, _UPDATE):
            func = _extract_func(src, "install_python_deps")
            armv7 = func.split("armv7l)")[1].split(";;")[0]
            self.assertIn('-r "${_req}"', armv7)          # full requirements closure
            self.assertIn("--no-index", armv7)
            self.assertIn("--only-binary=:all:", armv7)

    @unittest.skipUnless(_HAS_BASH, "bash is not available")
    def test_incomplete_wheelhouse_fails_closed(self):
        # Verified but INCOMPLETE wheelhouse -> pip exits non-zero under
        # --no-index --only-binary -> installer fails closed (pip_rc models this).
        rc, args, _ = _run_provision("armv7l", _make_wheelhouse(valid=True), pip_rc=1)
        self.assertEqual(rc, 1)
        self.assertIn("--no-index", args)


class ConduitUdpFirewallGuardTests(unittest.TestCase):
    """Issue 4: CCC must neither open inbound UDP nor instruct the operator to.
    The validated reference deployment (arm64 Pi 4 and the armv7l RPi2 field
    install) runs with only TCP 22/80 + the selected HTTPS port; Conduit's dynamic UDP ports change at
    runtime, so per-port rules are ineffective and only widen attack surface."""

    def test_scripts_never_open_or_instruct_udp(self):
        self.assertNotIn("/udp", _INSTALL)
        self.assertNotIn("/udp", _UPDATE)
        for token in ("ACTION REQUIRED: Conduit needs UFW",
                      "Then for each UDP port listed",
                      "re-sync your UFW rules"):
            self.assertNotIn(token, _INSTALL)

    def test_installer_only_opens_fixed_tcp_ports(self):
        allows = [ln for ln in _INSTALL.splitlines() if "ufw allow" in ln]
        self.assertTrue(allows)                      # 2j block still present
        for ln in allows:
            self.assertIn("/tcp", ln)                # TCP only; no dynamic UDP openings

    def test_installer_retains_informational_guidance(self):
        self.assertIn("ss -ulnp | grep conduit", _INSTALL)   # optional inspect retained
        self.assertIn("selected HTTPS port", _INSTALL)       # posture, not hardcoded 443
        self.assertIn("docs/pre-install.md", _INSTALL)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
