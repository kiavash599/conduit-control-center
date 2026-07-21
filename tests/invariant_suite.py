#!/usr/bin/env python3
"""tests/invariant_suite.py -- THE cumulative release/lifecycle invariant gate.

Marker semantics (A7 -- structurally enforced, not conventional):

  INVARIANT_SUITE=PASS   only a platform-complete, unweakened, conftest-enabled
                         authoritative execution can emit this.
  INVARIANT_SUITE=SMOKE  ANY weakening -- --noconftest, subsets, wrong-platform
                         cross-check, dependency-limited execution -- yields at
                         most SMOKE. SMOKE can never satisfy a release gate.
  INVARIANT_SUITE=FAIL   collection, inventory, test, lint or shell-gate failure.

Platform authority:
  * windows : real Ruff (`python -m ruff check .`) + the documented exact
              Windows-compatible module list (curation forced by platform
              incompatibility, documented per module).
  * linux   : COMPLETE unweakened pytest discovery of tests/unit/ and
              tests/integration/ with real conftest, plus `bash -n` on the
              builder scripts and the exact reviewed ShellCheck baseline gate.
              There is NO curated Linux list to rot.

Deletion guard: pytest discovery catches additions but is blind to deletions,
so a committed, sorted, deduplicated REQUIRED-INVARIANT INVENTORY
(tests/invariant_inventory.txt) covers every authoritative test_*.py under both
trees. The gate fails if a listed file is missing, a discovered test file is
unlisted (same-commit inventory entries are mandatory), or the inventory is
malformed. Removing an entry requires an explicit, separately reviewed
lifecycle record. The inventory controls deletion/coverage; discovery controls
execution.

The final marker records platform, interpreter, conftest mode, inventory/file
counts, collected/passed/skipped/failed counts and exit status. CI and the
v0.3.19 qualification runbook invoke exactly this entry point. No device,
Docker, git or evidence mutation is ever performed here.
"""
from __future__ import annotations

import argparse
import pathlib
import platform as _platform
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "tests" / "invariant_inventory.txt"

# Windows-compatible exact list (per-module documented exclusions: the four
# legacy POSIX modules with bash-only gates and every module whose pytestmark
# requires linux are covered by the Linux full run instead).
WINDOWS_MODULES = [
    "tests/unit/test_logical_tree.py",
    "tests/unit/test_transfer_manifest.py",
    "tests/unit/test_phase_b_to_release_e2e.py",
    "tests/unit/test_build_wheelhouse.py",
    "tests/unit/test_release_manifest.py",
    "tests/unit/test_update_verify.py",
    "tests/unit/test_builder_provenance.py",
    "tests/unit/test_ccc_verify_release.py",
    "tests/unit/test_release_canonical.py",
    "tests/unit/test_release_lock_drift.py",
    "tests/unit/test_release_preflight.py",
    "tests/unit/test_builder_scripts.py",
    "tests/unit/test_reuse_authz.py",
    "tests/unit/test_sanitize_target_tags.py",
    "tests/unit/test_acquire_reuse_wheels.py",
    "tests/unit/test_gen_active_inputs.py",
    "tests/unit/test_canonical_bytes.py",
    "tests/unit/test_update_taxonomy.py",
    "tests/unit/test_version.py",
    "tests/unit/test_api_update_pure.py",
    "tests/unit/test_epic1_ownership_contract.py",
    "tests/unit/test_sudoers_contract.py",
    "tests/unit/test_lifecycle_filter_contract.py",
    "tests/unit/test_invariant_suite.py",
    # Cross-platform static wiring contracts run on Windows; the three
    # executable Bash-recovery cases inside this module self-skip and are
    # exercised authoritatively by the complete Linux gate.
    "tests/unit/test_update_lifecycle_contract.py",
]

ZERO_WARNING_SCRIPTS = [
    "install.sh",
    "uninstall.sh",
    "update.sh",
    "scripts/cloudflare-ddns.sh",
    "deployment/bootstrap/ccc-bootstrap.sh",
]
BUILDER_SCRIPTS = [
    "release/builder/build-builder-image.sh",
    "release/builder/build-wheelhouse-offline.sh",
    "release/builder/manifest-capture.lib.sh",
]
# Exact reviewed ShellCheck baseline (pre-existing, individually justified debt
# at aa6ae50; any OTHER finding fails). See the v0.3.18 runbook shell gate.
SHELLCHECK_BASELINE = [
    "release/builder/build-builder-image.sh:29:SC1091",
    "release/builder/build-wheelhouse-offline.sh:102:SC1091",
    "release/builder/manifest-capture.lib.sh:66:SC2024",
    "release/builder/manifest-capture.lib.sh:76:SC2024",
    "release/builder/manifest-capture.lib.sh:91:SC2015",
]


def _host_matches_authority(requested: str, host: str) -> bool:
    """Only the exact matching OS may emit an authoritative PASS marker."""
    return host == {"windows": "windows", "linux": "linux"}[requested]


def _emit(kind: str, **fields) -> None:
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"INVARIANT_SUITE={kind} {kv}")


def _load_inventory() -> "tuple[list[str], str | None]":
    if not INVENTORY.is_file():
        return [], "inventory file missing"
    lines = INVENTORY.read_text(encoding="utf-8").splitlines()
    entries = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
    if entries != sorted(entries):
        return entries, "inventory not sorted"
    if len(entries) != len(set(entries)):
        return entries, "inventory contains duplicates"
    bad = [e for e in entries
           if not re.match(r"^tests/(unit|integration)/([\w]+/)*test_[\w]+\.py$", e)]
    if bad:
        return entries, f"malformed inventory entries: {bad[:3]}"
    return entries, None


def _inventory_check() -> "tuple[int, int, str | None]":
    entries, err = _load_inventory()
    if err:
        return len(entries), 0, err
    missing = [e for e in entries if not (ROOT / e).is_file()]
    if missing:
        return len(entries), 0, f"missing required invariant file(s): {missing[:5]}"
    # RECURSIVE discovery: a nested authoritative test module can never escape
    # the inventory contract.
    discovered = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for tree in ("tests/unit", "tests/integration")
        for p in (ROOT / tree).rglob("test_*.py"))
    unlisted = [d for d in discovered if d not in entries]
    if unlisted:
        return len(entries), len(discovered), (
            f"unlisted test file(s) (add to inventory in the same commit): {unlisted[:5]}")
    return len(entries), len(discovered), None


def _pytest_counts(output: str) -> dict[str, int]:
    """Extract the final pytest summary without hiding setup/collection errors."""
    labels = ("failed", "passed", "skipped", "errors", "error")
    found: dict[str, int] = {}
    for label in labels:
        matches = re.findall(rf"(?<!\w)(\d+) {label}(?!\w)", output)
        if matches:
            found[label] = int(matches[-1])
    if "error" in found and "errors" not in found:
        found["errors"] = found["error"]
    if "passed" not in found:
        return {"failed": -1, "passed": -1, "skipped": -1, "errors": -1}
    return {
        "failed": found.get("failed", 0),
        "passed": found["passed"],
        "skipped": found.get("skipped", 0),
        "errors": found.get("errors", 0),
    }


def _shell_gates() -> "str | None":
    import shutil as _sh
    for script in [*ZERO_WARNING_SCRIPTS, *BUILDER_SCRIPTS]:
        r = subprocess.run(["bash", "-n", str(ROOT / script)], capture_output=True)
        if r.returncode != 0:
            return f"bash -n failed: {script}"
    if _sh.which("shellcheck") is None:
        return "shellcheck not installed (authoritative Linux gate requires it)"
    zero = subprocess.run(
        ["shellcheck", "--format=gcc",
         *[str(ROOT / s) for s in ZERO_WARNING_SCRIPTS]],
        capture_output=True, text=True,
    )
    if zero.returncode != 0:
        findings = (zero.stdout or zero.stderr).strip().splitlines()
        return f"zero-warning lifecycle ShellCheck failed: {findings[:5]}"
    raw = subprocess.run(["shellcheck", "--format=gcc",
                          *[str(ROOT / s) for s in BUILDER_SCRIPTS]],
                         capture_output=True, text=True)
    if raw.returncode not in (0, 1):
        return f"shellcheck error exit {raw.returncode}"
    findings = []
    for ln in raw.stdout.splitlines():
        m = re.match(r"^(.*?):(\d+):\d+: +[a-z]+: .*\[(SC\d+)\]$", ln)
        if not m:
            return f"unparseable shellcheck output: {ln!r}"
        rel = str(pathlib.Path(m.group(1)).resolve().relative_to(ROOT)).replace("\\", "/")
        findings.append(f"{rel}:{m.group(2)}:{m.group(3)}")
    if sorted(findings) != sorted(SHELLCHECK_BASELINE):
        return (f"shellcheck findings differ from the reviewed baseline: "
                f"got {sorted(findings)}")
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="invariant_suite")
    ap.add_argument("--platform", choices=["windows", "linux"], required=True)
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--noconftest", action="store_true",
                    help="WEAKENED execution: emits SMOKE, never PASS")
    ap.add_argument("--smoke-subset", default=None,
                    help="comma-separated module subset: emits SMOKE, never PASS")
    a = ap.parse_args(argv)

    host = _platform.system().lower()
    authoritative = True
    weakeners = []
    if a.noconftest:
        authoritative, weakeners = False, weakeners + ["noconftest"]
    if a.smoke_subset:
        authoritative, weakeners = False, weakeners + ["subset"]
    if not _host_matches_authority(a.platform, host):
        authoritative, weakeners = False, weakeners + [f"cross-platform(host={host})"]

    base = dict(platform=a.platform, host=host,
                interpreter=_platform.python_version(),
                conftest=("no" if a.noconftest else "yes"))

    # Inventory + deletion guard runs in EVERY mode (cheap, always meaningful).
    inv_count, disc_count, inv_err = _inventory_check()
    base.update(inventory=inv_count, discovered=disc_count)
    if inv_err:
        _emit("FAIL", reason="inventory", detail=f"\"{inv_err}\"", **base)
        return 1

    # Select execution set.
    if a.smoke_subset:
        modules = [m for m in a.smoke_subset.split(",") if m]
    elif a.platform == "windows":
        missing = [m for m in WINDOWS_MODULES if not (ROOT / m).is_file()]
        if missing:
            _emit("FAIL", reason="missing_windows_modules", detail=missing[:5], **base)
            return 1
        modules = list(WINDOWS_MODULES)
    else:
        modules = ["tests/unit/", "tests/integration/"]     # FULL discovery

    # Windows authority additionally requires real Ruff.
    if a.platform == "windows" and authoritative:
        ruff = subprocess.run([sys.executable, "-m", "ruff", "check", "."],
                              cwd=str(ROOT), capture_output=True, text=True)
        if ruff.returncode != 0:
            tail = (ruff.stdout or ruff.stderr).strip().splitlines()[-1:]
            _emit("FAIL", reason="ruff", detail=tail, **base)
            return 1

    # Linux authority additionally requires bash/ShellCheck over every
    # lifecycle/bootstrap script plus the exact reviewed builder baseline.
    if a.platform == "linux" and authoritative:
        shell_err = _shell_gates()
        if shell_err:
            _emit("FAIL", reason="shell_gate", detail=f"\"{shell_err}\"", **base)
            return 1

    # -p no:warnings: display-only; warnings are not part of this gate. pytest's
    # own -q is NOT used (some environments then omit the count summary the
    # marker must record); this runner manages verbosity itself.
    # Never inherit pytest's shared per-user temp root. A stale/ACL-damaged
    # pytest-of-<user> directory must not turn a clean candidate into hundreds
    # of setup errors or make one gate attempt interfere with another.
    with tempfile.TemporaryDirectory(prefix="ccc-invariant-") as base_temp:
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
               "-p", "no:warnings", "--tb=short", "--basetemp", base_temp,
               *(["--noconftest"] if a.noconftest else []), *modules]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    counts = _pytest_counts(out)
    if not a.quiet or proc.returncode != 0:
        sys.stdout.write(out)
    ok = proc.returncode == 0
    base.update(**counts, exit=proc.returncode)
    if not ok:
        _emit("FAIL", reason="tests", **base)
        return 1
    if counts["passed"] <= 0:
        # UNPARSEABLE or empty collection: authoritative evidence is missing --
        # a PASS marker without verifiable counts is vacuous. Fail closed.
        _emit("FAIL", reason="unparseable_counts", **base)
        return 1
    if authoritative:
        _emit("PASS", **base)
        return 0
    _emit("SMOKE", weakened=",".join(weakeners), **base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
