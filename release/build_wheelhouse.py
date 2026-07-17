#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/build_wheelhouse.py -- deterministic armv7 wheelhouse + provenance builder
(ADR-0003 Amendment A1). Consumes ONLY sdists authorized by
`requirements-armv7-build.lock`, verifies each sdist hash BEFORE build, builds each
in the recorded builder environment, records the sdist->wheel mapping, writes
SHA256SUMS, and emits the strict provenance JSON accepted by
`ccc_release._validate_provenance`. Refuses missing / extra / duplicate / ambiguous
build outputs. The BUILD step is injectable (`build_fn`) so the tool is fully
testable; the eventual real build remains Owner-gated (this module never runs it
automatically)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

try:
    from release import ccc_release as _R
except Exception:  # noqa: BLE001 - allow running as a script from repo root
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from release import ccc_release as _R

ReleaseError = _R.ReleaseError


def _default_build_fn(sdist_path, sdist_name, name, version):
    """Real builder: `pip wheel` the sdist with NO deps / NO build isolation / NO
    index (offline, from the authorized sdist only). Returns (wheel_filename, bytes).
    Runs ONLY when invoked as a real build (never in tests)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cp = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
             "--no-index", "-w", td, sdist_path],
            capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            raise ReleaseError(f"pip wheel failed for {sdist_name}: {cp.stderr.strip()}")
        whls = [f for f in os.listdir(td) if f.endswith(".whl")]
        if len(whls) != 1:
            raise ReleaseError(f"ambiguous build output for {sdist_name}: {whls}")
        with open(os.path.join(td, whls[0]), "rb") as fh:
            return whls[0], fh.read()


def _parse_dpkg_status_lines(text: str) -> dict:
    """Parse ``dpkg-query -W -f='${db:Status-Status}\t${binary:Package}\t${Version}'``
    output. Only rows whose status is exactly 'installed' enter the map (config-files /
    removed / half-configured packages are excluded, so they can never satisfy an
    authorized pin). Key is the architecture-qualified binary package identity."""
    apt = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        status, pkg, ver = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if status != "installed" or not pkg or not ver:
            continue
        apt[pkg] = ver
    return apt


def _default_env_probe() -> dict:
    """Capture the environment FROM THE EXECUTING image/runtime (ties the recorded
    environment to the image that actually runs the build). The Python interpreter is
    read IN-PROCESS (platform.python_version) -- it IS the interpreter executing this
    build, and is reliable across dev/test hosts (no external `python3` needed)."""
    import importlib.metadata as _md
    import os as _os
    import platform as _pf
    import subprocess as _sp

    def out(cmd):
        try:
            return _sp.run(cmd, capture_output=True, text=True).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""
    osr = {}
    try:
        for entry in open("/etc/os-release"):
            if "=" in entry:
                key, val = entry.split("=", 1)
                osr[key.strip()] = val.strip().strip('"')
    except Exception:  # noqa: BLE001
        pass
    try:
        glibc = (_os.confstr("CS_GNU_LIBC_VERSION") or "").replace("glibc ", "").strip()
    except Exception:  # noqa: BLE001
        glibc = ""
    # Installed-package identity: ${db:Status-Status} lets us keep ONLY packages whose
    # status is 'installed' (config-files-only / removed packages are excluded), and
    # ${binary:Package} preserves the architecture qualifier for foreign-arch packages.
    apt = _parse_dpkg_status_lines(
        out(["dpkg-query", "-W",
             "-f=${db:Status-Status}\t${binary:Package}\t${Version}\n"]))
    return {
        "os": osr.get("PRETTY_NAME", ""), "os_id": osr.get("ID", ""),
        "os_version_id": osr.get("VERSION_ID", ""), "arch": _pf.machine(),
        "apt_architecture": out(["dpkg", "--print-architecture"]),
        "python": "Python " + _pf.python_version(),   # the interpreter EXECUTING this build
        "rustc": out(["rustc", "--version"]), "cargo": out(["cargo", "--version"]),
        "gcc": (out(["gcc", "--version"]).splitlines() or [""])[0], "glibc": glibc,
        "apt": apt,
        "build_backends": {d.metadata["Name"].lower(): d.version for d in _md.distributions()},
    }


def build_wheelhouse(*, build_lock_path: str, sdist_dir: str, out_dir: str,
                     recipe_path: str, build_backends_lock_path: str, apt_packages_path: str,
                     rustup_sha_path: str, extractor_tools_lock_path: str,
                     build_backends_source_allowlist_path: str, builder_identity: str,
                     base_image_digest: str, image_manifest_path: str, runtime_image_id: str,
                     env_probe=None, build_fn=None) -> dict:
    """Build the armv7 wheelhouse + STRICT builder provenance. Binds the committed
    recipe + committed build-backends lock (by sha256), the pinned base image, and the
    STORE-AGNOSTIC runtime identity derived from the captured manifest + runtime_image_id
    (containerd: runtime_image_id == manifest digest; legacy: == config digest), recording
    runtime_image_id + image_manifest_digest + image_config_digest + image_identity_mode,
    plus the environment CAPTURED FROM THE EXECUTING IMAGE. Self-checked before return
    (fail closed)."""
    if not builder_identity:
        raise ReleaseError("builder identity required")
    if not recipe_path or not os.path.isfile(recipe_path):
        raise ReleaseError(f"committed builder recipe not found: {recipe_path!r}")
    with open(recipe_path, "rb") as fh:
        recipe_sha = _R.sha256_hex(_R._to_lf(fh.read()))
    if not build_backends_lock_path or not os.path.isfile(build_backends_lock_path):
        raise ReleaseError(f"committed build-backends lock not found: {build_backends_lock_path!r}")
    with open(build_backends_lock_path, "rb") as fh:
        bb_lock_raw = _R._to_lf(fh.read())
    bb_lock_text = bb_lock_raw.decode("utf-8", "replace")
    bb_lock_sha = _R.sha256_hex(bb_lock_raw)
    for _lbl, _pth in (("apt-packages.list", apt_packages_path), ("rustup-init.sha256", rustup_sha_path)):
        if not _pth or not os.path.isfile(_pth):
            raise ReleaseError(f"committed builder input not found: {_lbl} ({_pth!r})")
    with open(apt_packages_path, "rb") as fh:
        apt_pkgs_raw = _R._to_lf(fh.read())
    apt_pkgs_sha = _R.sha256_hex(apt_pkgs_raw)
    apt_pkgs_text = apt_pkgs_raw.decode("utf-8", "replace")
    with open(rustup_sha_path, "rb") as fh:
        rustup_file_sha = _R.sha256_hex(_R._to_lf(fh.read()))
    if not extractor_tools_lock_path or not os.path.isfile(extractor_tools_lock_path):
        raise ReleaseError(f"committed extractor-tools lock not found: {extractor_tools_lock_path!r}")
    with open(extractor_tools_lock_path, "rb") as fh:
        # Canonical-byte policy (matches recipe/backends/apt/rustup and canonicalize_tree):
        # LF-normalise before hashing so CRLF/LF working trees yield the same digest.
        extractor_tools_lock_sha = _R.sha256_hex(_R._to_lf(fh.read()))
    if not build_backends_source_allowlist_path or not os.path.isfile(build_backends_source_allowlist_path):
        raise ReleaseError(f"committed backend source-allowlist not found: "
                           f"{build_backends_source_allowlist_path!r}")
    with open(build_backends_source_allowlist_path, "rb") as fh:
        _allowlist_raw = _R._to_lf(fh.read())
    build_backends_source_allowlist_sha = _R.sha256_hex(_allowlist_raw)
    # SEMANTIC self-check (not merely a hash): the allowlist must be canonical, non-empty, and
    # every allowlisted backend must be pinned in THIS build-backends lock (exact use) BEFORE
    # provenance is emitted.
    _R.validate_backend_source_allowlist(_allowlist_raw.decode("utf-8", "replace"), bb_lock_text)
    if not _R._is_oci_digest(base_image_digest):
        raise ReleaseError("base_image_digest must be 'sha256:<64 lowercase hex>'")
    if not image_manifest_path or not os.path.isfile(image_manifest_path):
        raise ReleaseError(f"OCI image manifest file not found: {image_manifest_path!r}")
    with open(image_manifest_path, "rb") as fh:
        manifest_bytes = fh.read()
    if not manifest_bytes:
        raise ReleaseError("OCI image manifest is empty")
    if not _R._is_oci_digest(runtime_image_id):
        raise ReleaseError("runtime_image_id (docker .Id) is required as 'sha256:<64hex>'")
    # Store-agnostic identity: derive the mode + manifest/config digests from the captured
    # manifest and the runtime id (containerd: id == manifest digest; legacy: id == config digest).
    try:
        _idr = _R._ocim.validate_capture(manifest_bytes, runtime_image_id=runtime_image_id,
                                         allow_index=False)
    except _R._ocim.ManifestError as _exc:
        raise ReleaseError(f"builder manifest identity invalid: {_exc}") from _exc
    image_manifest_digest = _idr["manifest_digest"]
    image_config_digest = _idr["config_digest"]
    image_identity_mode = _idr["identity_mode"]
    environment = (env_probe or _default_env_probe)()
    build_fn = build_fn or _default_build_fn
    with open(build_lock_path, encoding="utf-8") as fh:
        build_lock_text = fh.read()
    build_pins = _R._parse_lock_pins(build_lock_text)
    if not build_pins:
        raise ReleaseError("empty/invalid armv7 build-input lock")

    # Collect + authorize sdists (verify each hash BEFORE build).
    sdists: dict = {}
    for fn in sorted(os.listdir(sdist_dir)):
        path = os.path.join(sdist_dir, fn)
        if not os.path.isfile(path):
            continue
        name, ver = _R._parse_sdist_name(fn)
        if name is None:
            raise ReleaseError(f"unrecognised file in sdist dir (not an sdist): {fn!r}")
        with open(path, "rb") as f:
            sha = _R.sha256_hex(f.read())
        if name in sdists:
            raise ReleaseError(f"duplicate sdist for package {name!r}")
        sdists[name] = (ver, fn, path, sha)
    if set(sdists) != set(build_pins):
        missing = sorted(set(build_pins) - set(sdists))
        extra = sorted(set(sdists) - set(build_pins))
        raise ReleaseError(f"sdists != build lock (missing={missing}, extra={extra})")
    for name, (ver, fn, _p, sha) in sdists.items():
        bver, bhashes = build_pins[name]
        if ver != bver:
            raise ReleaseError(f"sdist version mismatch for {name!r}: {ver} != {bver}")
        if sha not in bhashes:
            raise ReleaseError(f"sdist hash not authorized by build lock for {name!r}")

    # Build each authorized sdist -> exactly one wheel of the same name+version.
    os.makedirs(out_dir, exist_ok=True)
    wheels: list = []
    seen_wheel: set = set()
    for name, (ver, sfn, spath, ssha) in sorted(sdists.items()):
        wname, wbytes = build_fn(spath, sfn, name, ver)
        if not isinstance(wname, str) or not wname.endswith(".whl") or not isinstance(wbytes, (bytes, bytearray)):
            raise ReleaseError(f"invalid build output for {sfn!r}")
        wn, wv = _R._parse_wheel_name(wname)
        if wn != name or wv != ver:
            raise ReleaseError(f"build output {wname!r} does not match {name}=={ver}")
        if wname in seen_wheel:
            raise ReleaseError(f"duplicate/ambiguous build output: {wname!r}")
        seen_wheel.add(wname)
        with open(os.path.join(out_dir, wname), "wb") as fh:
            fh.write(wbytes)
        wheels.append({"sdist_name": sfn, "sdist_sha256": ssha,
                       "wheel_filename": wname, "wheel_sha256": _R.sha256_hex(bytes(wbytes))})

    wheels.sort(key=lambda w: w["wheel_filename"])
    with open(os.path.join(out_dir, "SHA256SUMS"), "w", encoding="utf-8") as fh:
        for w in wheels:
            fh.write("%s  %s\n" % (w["wheel_sha256"], w["wheel_filename"]))

    members = _R._wheelhouse_members(out_dir)
    bundle_sha = _R.sha256_hex(_R.pack_tree(members))
    env = dict(environment)
    builder = {
        "identity": builder_identity,
        "recipe_path": _R.CANONICAL_RECIPE_PATH,
        "recipe_sha256": recipe_sha,
        "build_backends_lock_sha256": bb_lock_sha,
        "apt_packages_sha256": apt_pkgs_sha,
        "rustup_init_file_sha256": rustup_file_sha,
        "extractor_tools_lock_sha256": extractor_tools_lock_sha,
        "build_backends_source_allowlist_sha256": build_backends_source_allowlist_sha,
        "base_image_digest": base_image_digest,
        "image_manifest_digest": image_manifest_digest,
        "image_config_digest": image_config_digest,
        "image_identity_mode": image_identity_mode,
        "runtime_image_id": runtime_image_id,
        "environment": env,
        "environment_sha256": _R.sha256_hex(_R._canonical_env_bytes(env)),
    }
    provenance = {"builder": builder, "bundle": {"sha256": bundle_sha}, "wheels": wheels}
    # Self-check: the emitted provenance MUST pass the strict producer-side validator,
    # INCLUDING the independent manifest-digest recompute from the raw OCI manifest.
    _R._validate_provenance(provenance, members, bundle_sha, build_lock_text, recipe_sha,
                            bb_lock_sha, bb_lock_text, apt_pkgs_sha, rustup_file_sha,
                            apt_pkgs_text, extractor_tools_lock_sha,
                            build_backends_source_allowlist_sha,
                            image_manifest_bytes=manifest_bytes)
    return {"provenance": provenance, "bundle_sha256": bundle_sha, "wheelhouse_dir": out_dir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="build_wheelhouse.py")
    ap.add_argument("--build-lock", required=True)
    ap.add_argument("--sdist-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--recipe", required=True, help="path to the committed release/builder/Containerfile")
    ap.add_argument("--build-backends-lock", required=True,
                    help="path to the committed release/builder/requirements-build-backends.lock")
    ap.add_argument("--apt-packages", required=True, help="committed release/builder/apt-packages.list")
    ap.add_argument("--rustup-sha", required=True, help="committed release/builder/rustup-init.sha256")
    ap.add_argument("--extractor-tools-lock", required=True,
                    help="committed release/builder/requirements-extractor-tools.lock")
    ap.add_argument("--build-backends-source-allowlist", required=True,
                    help="committed release/builder/requirements-build-backends.source-allowlist")
    ap.add_argument("--builder-identity", required=True)
    ap.add_argument("--base-image-digest", required=True, help="pinned base image OCI digest sha256:<64hex>")
    ap.add_argument("--image-manifest", required=True,
                    help="raw OCI image manifest file (skopeo inspect --raw); its sha256 is the manifest digest")
    ap.add_argument("--runtime-image-id", required=True, help="docker inspect .Id (store-agnostic runtime id)")
    ap.add_argument("--provenance-out", required=True)
    a = ap.parse_args(argv)
    res = build_wheelhouse(build_lock_path=a.build_lock, sdist_dir=a.sdist_dir, out_dir=a.out_dir,
                           recipe_path=a.recipe, build_backends_lock_path=a.build_backends_lock,
                           apt_packages_path=a.apt_packages, rustup_sha_path=a.rustup_sha,
                           extractor_tools_lock_path=a.extractor_tools_lock,
                           build_backends_source_allowlist_path=a.build_backends_source_allowlist,
                           builder_identity=a.builder_identity, base_image_digest=a.base_image_digest,
                           image_manifest_path=a.image_manifest, runtime_image_id=a.runtime_image_id)
    with open(a.provenance_out, "w", encoding="utf-8") as fh:
        json.dump(res["provenance"], fh)
    print(f"wheelhouse: {a.out_dir}  bundle_sha256={res['bundle_sha256']}  provenance={a.provenance_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
