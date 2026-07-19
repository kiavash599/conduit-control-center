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

from release import reuse_authz as _reuse_authz  # noqa: E402 (release importable above)

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


def _effective_build_backends(paths=None):
    """Record each installed distribution's EFFECTIVE version -- the one the executing Python
    environment actually resolves (finder / sys.path precedence) -- NOT a last-wins enumeration.

    Returns (effective:{norm_name->version}, shadows:{norm_name->[all versions in search order]}).
    Fails closed (ReleaseError) if a name's effective resolution cannot be established, or is
    GENUINELY AMBIGUOUS (the canonical resolver `importlib.metadata.version` disagrees with the
    finder search order). A lower-precedence, non-effective Ubuntu distribution existing under a
    later sys.path entry is recorded only as an audit shadow and does NOT fail."""
    import importlib.metadata as _md
    import re as _re

    def _norm(n):
        return _re.sub(r"[-_.]+", "-", str(n)).strip("-").lower()

    dists = _md.distributions() if paths is None else _md.distributions(path=list(paths))
    order: dict = {}
    for d in dists:
        try:
            nm = d.metadata["Name"]
        except Exception:  # noqa: BLE001
            nm = None
        if not nm:
            continue
        order.setdefault(_norm(nm), []).append(d.version)
    effective, shadows = {}, {}
    for norm, versions in order.items():
        first = versions[0]                         # earliest in finder/search order == effective
        if paths is None:
            # Testable correspondence to what the interpreter resolves: the canonical resolver API
            # must agree with the search order; disagreement => ambiguous => fail closed.
            try:
                resolved = _md.version(norm)
            except _md.PackageNotFoundError as exc:
                raise ReleaseError(f"effective backend resolution failed for {norm!r}: {exc}") from exc
            if resolved != first:
                raise ReleaseError(f"ambiguous effective resolution for {norm!r}: "
                                   f"resolver={resolved!r} search-order-first={first!r}")
            first = resolved
        effective[norm] = first
        uniq = list(dict.fromkeys(versions))
        if len(uniq) > 1:
            shadows[norm] = uniq                    # audit only (non-effective lower-precedence dupes)
    return effective, shadows


def _default_env_probe() -> dict:
    """Capture the environment FROM THE EXECUTING image/runtime (ties the recorded
    environment to the image that actually runs the build). The Python interpreter is
    read IN-PROCESS (platform.python_version) -- it IS the interpreter executing this
    build, and is reliable across dev/test hosts (no external `python3` needed)."""
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
    _eff_backends, _bb_shadows = _effective_build_backends()   # effective (not last-wins) + audit
    return {
        "os": osr.get("PRETTY_NAME", ""), "os_id": osr.get("ID", ""),
        "os_version_id": osr.get("VERSION_ID", ""), "arch": _pf.machine(),
        "apt_architecture": out(["dpkg", "--print-architecture"]),
        "python": "Python " + _pf.python_version(),   # the interpreter EXECUTING this build
        "rustc": out(["rustc", "--version"]), "cargo": out(["cargo", "--version"]),
        "gcc": (out(["gcc", "--version"]).splitlines() or [""])[0], "glibc": glibc,
        "apt": apt,
        "build_backends": _eff_backends,
        "build_backends_shadows": _bb_shadows,
    }


IMAGE_CONTEXT_ROOT = "/opt/ccc"      # PRODUCTION location of the in-image build-context copies


def _verify_image_context(*, recipe_sha: str, partition_backends_path: str, committed: dict,
                          image_context_root: str = IMAGE_CONTEXT_ROOT) -> dict:
    """Prove the executing image was built from the committed build context, then return the exact
    six-entry {canonical_path: sha256} map.

    For each of the five files COPYed into the image, the in-image copy under ``image_context_root``
    must exist, be a REGULAR file (no symlink/device/directory), be readable, and hash identically
    (LF-canonical) to the corresponding committed file. Any missing, unreadable, non-regular, or
    mismatching entry fails closed and names the exact canonical path. ``image_context_root`` is a
    narrow seam for unit tests only; production is fixed to ``/opt/ccc``."""
    ctx = {"release/builder/Containerfile": recipe_sha}
    pairs = dict(committed)
    pairs["release/builder/partition_backends.py"] = partition_backends_path
    for canonical_path, committed_path in sorted(pairs.items()):
        if not committed_path or not os.path.isfile(committed_path):
            raise ReleaseError(f"committed image-context file not found: {canonical_path} "
                               f"({committed_path!r})")
        with open(committed_path, "rb") as fh:
            committed_sha = _R.canonical_file_sha256(fh.read())
        in_image = os.path.join(image_context_root, os.path.basename(canonical_path))
        if os.path.islink(in_image) or not os.path.exists(in_image):
            raise ReleaseError(f"image-context proof failed for {canonical_path}: in-image copy "
                               f"missing or is a symlink: {in_image!r}")
        if not os.path.isfile(in_image):
            raise ReleaseError(f"image-context proof failed for {canonical_path}: in-image path is "
                               f"not a regular file: {in_image!r}")
        try:
            with open(in_image, "rb") as fh:
                in_image_sha = _R.canonical_file_sha256(fh.read())
        except OSError as exc:
            raise ReleaseError(f"image-context proof failed for {canonical_path}: cannot read "
                               f"{in_image!r}: {exc}") from exc
        if in_image_sha != committed_sha:
            raise ReleaseError(
                f"image-context proof FAILED for {canonical_path}: the executing image was built "
                f"from different bytes (in-image {in_image_sha}, committed {committed_sha})")
        ctx[canonical_path] = committed_sha
    return ctx


def build_wheelhouse(*, build_lock_path: str, sdist_dir: str, out_dir: str,
                     recipe_path: str, build_backends_lock_path: str, apt_packages_path: str,
                     rustup_sha_path: str, extractor_tools_lock_path: str,
                     build_backends_source_allowlist_path: str, partition_backends_path: str,
                     builder_identity: str,
                     base_image_digest: str, image_manifest_path: str, runtime_image_id: str,
                     reuse_authz_path: str = None, reuse_wheels_dir: str = None,
                     target_tags=None, target_tags_sha256: str = None, requirements_text: str = None,
                     enforce_partition_policy: bool = False,
                     image_context_root: str = IMAGE_CONTEXT_ROOT,
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
    # --- IMAGE-CONTEXT PROOF (before env probing, reuse ingestion, or ANY source build) ---------
    # Five of the six context files were COPYed into this image by the Containerfile and are still
    # present at /opt/ccc. Reading them back from INSIDE the executing image and comparing them
    # byte-for-byte (LF-canonical) with the committed files proves the running image was built from
    # exactly these committed bytes -- something the environment checks cannot do, since identical
    # installed state can arise from different source bytes. The recipe is the sixth entry; it is
    # not copied into the image and is bound by Phase A's recorded CCC_RECIPE_SHA256, which
    # build-wheelhouse-offline.sh compares against this same committed file before Docker runs.
    image_context = _verify_image_context(
        recipe_sha=recipe_sha, partition_backends_path=partition_backends_path,
        committed={"release/builder/apt-packages.list": apt_packages_path,
                   "release/builder/rustup-init.sha256": rustup_sha_path,
                   "release/builder/requirements-build-backends.lock": build_backends_lock_path,
                   "release/builder/requirements-build-backends.source-allowlist":
                       build_backends_source_allowlist_path},
        image_context_root=image_context_root)
    image_context_sha = _R.image_context_digest(image_context)

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

    # --- Build the COMPLETE Phase-B bundle in a SIBLING STAGING dir, validate EVERYTHING, then
    # publish with ONE atomic rename. The Python builder owns this transaction (no shell-side atomic
    # logic). The final bundle must NOT pre-exist -- a valid prior result is never silently replaced;
    # a failure removes staging and publishes nothing. Bundle layout:
    #   <bundle>/wheelhouse-armhf/{*.whl, SHA256SUMS}  wheelhouse-armv7.json  requirements-armv7.lock
    #   <bundle>/build-evidence.json
    import shutil as _shutil
    import tempfile as _tf
    final_bundle = out_dir
    if os.path.exists(final_bundle):
        raise ReleaseError(f"output bundle must not pre-exist (no overwrite): {final_bundle!r}")
    _parent = os.path.dirname(os.path.abspath(final_bundle)) or "."
    os.makedirs(_parent, exist_ok=True)
    staging = _tf.mkdtemp(prefix=".whb-", dir=_parent)
    try:
        wh_dir = os.path.join(staging, "wheelhouse-armhf")
        os.makedirs(wh_dir)

        # ================= CHEAP PREFLIGHT (before the first expensive source build) =================
        # In production mode every input is REQUIRED (not semantically optional). All reuse-store and
        # partition-feasibility validation happens here so a bad store never costs six source builds.
        if enforce_partition_policy:
            _missing = [n for n, v in (("reuse-authz", reuse_authz_path), ("reuse-store", reuse_wheels_dir),
                                       ("target-tags", target_tags), ("target-tags-sha256", target_tags_sha256),
                                       ("requirements", requirements_text)) if not v]
            if _missing:
                raise ReleaseError(f"production partition policy requires inputs: {_missing}")
            if set(sdists) != set(_R.V0317_SOURCE_BUILD_PACKAGES):
                raise ReleaseError(f"source sdists != approved six {sorted(_R.V0317_SOURCE_BUILD_PACKAGES)}; "
                                   f"got {sorted(sdists)}")
        authz = None
        reuse_authz_bytes = None
        reused_records = []                                 # [(authz_wheel, bytes)] pre-verified offline
        if reuse_authz_path is not None:
            with open(reuse_authz_path, "rb") as fh:
                reuse_authz_bytes = fh.read()
            authz = _reuse_authz.load_and_validate(reuse_authz_bytes, target_tags=target_tags)
            if enforce_partition_policy and len(authz["wheels"]) != _R.V0317_REUSED_COUNT:
                raise ReleaseError(f"reuse authorization must have exactly {_R.V0317_REUSED_COUNT} wheels; "
                                   f"got {len(authz['wheels'])}")
            if reuse_wheels_dir is None or not os.path.isdir(reuse_wheels_dir):
                raise ReleaseError(f"reuse wheels dir not found: {reuse_wheels_dir!r}")
            # EXACT-SET store: entries == authz filenames; regular files ONLY (no dirs/symlinks/foreign).
            authz_files = {a["filename"] for a in authz["wheels"]}
            store_names = set()
            for de in os.scandir(reuse_wheels_dir):
                if de.is_symlink():
                    raise ReleaseError(f"symlink in reuse store rejected: {de.name!r}")
                if de.is_dir(follow_symlinks=False):
                    raise ReleaseError(f"subdirectory in reuse store rejected: {de.name!r}")
                if not de.is_file(follow_symlinks=False):
                    raise ReleaseError(f"non-regular entry in reuse store rejected: {de.name!r}")
                store_names.add(de.name)
            if store_names != authz_files:
                raise ReleaseError(f"reuse store != authorization (missing={sorted(authz_files - store_names)}, "
                                   f"foreign={sorted(store_names - authz_files)})")
            for a in authz["wheels"]:                       # re-verify all 24 hashes OFFLINE before building
                with open(os.path.join(reuse_wheels_dir, a["filename"]), "rb") as fh:
                    rbytes = fh.read()
                if _R.sha256_hex(rbytes) != a["sha256"]:
                    raise ReleaseError(f"reused wheel {a['filename']!r} sha256 mismatch (store vs authz)")
                reused_records.append((a, rbytes))
        # 6/24/30 partition feasibility (by name) BEFORE building.
        _built_expected = set(sdists)
        _reused_expected = {a["name"] for a, _ in reused_records}
        if _built_expected & _reused_expected:
            raise ReleaseError(f"built/reused overlap: {sorted(_built_expected & _reused_expected)}")
        if enforce_partition_policy and (len(_built_expected) != _R.V0317_BUILT_COUNT
                or len(_reused_expected) != _R.V0317_REUSED_COUNT
                or len(_built_expected | _reused_expected) != _R.V0317_TOTAL_COUNT):
            raise ReleaseError("6/24/30 partition not feasible before build")

        # ================= FIELD-PROVEN six-wheel build (UNCHANGED) -- only after preflight =========
        wheels: list = []
        seen_wheel: set = set()
        built_names: set = set()
        reused_names: set = set()
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
            with open(os.path.join(wh_dir, wname), "wb") as fh:
                fh.write(wbytes)
            wheels.append({"origin": "built", "sdist_name": sfn, "sdist_sha256": ssha,
                           "wheel_filename": wname, "wheel_sha256": _R.sha256_hex(bytes(wbytes))})
            built_names.add(name)

        # ---- ingest the PRE-VERIFIED reuse wheels (bytes already checked in preflight) ----
        authorizers: dict = {}
        if authz is not None:
            authorizers["reuse_authz_sha256"] = _reuse_authz.sha256_hex(_reuse_authz.canonical_bytes(authz))
            for a, rbytes in reused_records:
                if a["filename"] in seen_wheel:
                    raise ReleaseError(f"duplicate/ambiguous wheel across origins: {a['filename']!r}")
                seen_wheel.add(a["filename"])
                with open(os.path.join(wh_dir, a["filename"]), "wb") as fh:
                    fh.write(rbytes)
                wheels.append({"origin": "reused", "name": a["name"], "version": a["version"],
                               "wheel_filename": a["filename"], "wheel_sha256": a["sha256"], "tags": a["tags"]})
                reused_names.add(a["name"])
        if target_tags_sha256 is not None:
            authorizers["target_tags_sha256"] = target_tags_sha256

        # ---- ALL 30 final wheels (built + reused) must be target-compatible against the committed set ----
        if target_tags is not None:
            _tt = set(target_tags)
            for w in wheels:
                _, _, _fntags = _reuse_authz.parse_wheel_filename(w["wheel_filename"])
                if not (_fntags & _tt):
                    raise ReleaseError(f"final wheel {w['wheel_filename']!r} has no target-compatible tag")

        # PRODUCTION PARTITION POLICY (exact, before publication).
        if enforce_partition_policy:
            if built_names != set(_R.V0317_SOURCE_BUILD_PACKAGES):
                raise ReleaseError(f"built packages != approved six {sorted(_R.V0317_SOURCE_BUILD_PACKAGES)}; "
                                   f"got {sorted(built_names)}")
            if built_names & reused_names:
                raise ReleaseError(f"built/reused overlap: {sorted(built_names & reused_names)}")
            if (len(built_names) != _R.V0317_BUILT_COUNT or len(reused_names) != _R.V0317_REUSED_COUNT
                    or len(wheels) != _R.V0317_TOTAL_COUNT):
                raise ReleaseError(f"partition counts must be {_R.V0317_BUILT_COUNT}/"
                                   f"{_R.V0317_REUSED_COUNT}/{_R.V0317_TOTAL_COUNT}; got "
                                   f"{len(built_names)}/{len(reused_names)}/{len(wheels)}")

        wheels.sort(key=lambda w: w["wheel_filename"])
        with open(os.path.join(wh_dir, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as fh:
            for w in wheels:
                fh.write("%s  %s\n" % (w["wheel_sha256"], w["wheel_filename"]))

        members = _R._wheelhouse_members(wh_dir)
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
            # Byte-level proof of the build context (five in-image copies + the recipe hash).
            "image_context": dict(image_context),
            "image_context_sha256": image_context_sha,
        }
        provenance = {"builder": builder, "bundle": {"sha256": bundle_sha}, "wheels": wheels,
                      "authorizers": authorizers}
        _reuse_text = reuse_authz_bytes if reuse_authz_path is not None else None
        _R._validate_provenance(provenance, members, bundle_sha, build_lock_text, recipe_sha,
                                bb_lock_sha, bb_lock_text, apt_pkgs_sha, rustup_file_sha,
                                apt_pkgs_text, extractor_tools_lock_sha,
                                build_backends_source_allowlist_sha,
                                image_manifest_bytes=manifest_bytes, reuse_authz_text=_reuse_text,
                                target_tags=target_tags,
                                expected_target_tags_sha256=(target_tags_sha256 if enforce_partition_policy else None),
                                # Self-check against the SAME committed bytes just proven in-image.
                                image_context_expected=image_context)

        # Runtime lock generated ONLY from the final validated wheelhouse, checked as an exact
        # 30-way name/version/hash bijection with the embedded wheels. MANDATORY under production
        # policy: the bundle cannot publish without it.
        runtime_lock_text = None
        if requirements_text is not None:
            lines = ["# GENERATED from the final validated wheelhouse -- DO NOT hand-edit."]
            for w in wheels:
                _n, _v = _R._parse_wheel_name(w["wheel_filename"])
                lines.append(f"{_n}=={_v} --hash=sha256:{w['wheel_sha256']}")
            runtime_lock_text = "\n".join(lines) + "\n"
            _R._validate_runtime_lock_against_wheelhouse(runtime_lock_text, members, requirements_text)
            with open(os.path.join(staging, "requirements-armv7.lock"), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write(runtime_lock_text)
        if enforce_partition_policy and runtime_lock_text is None:
            raise ReleaseError("runtime lock is mandatory under production policy (requirements required)")

        with open(os.path.join(staging, "wheelhouse-armv7.json"), "w", encoding="utf-8") as fh:
            json.dump(provenance, fh)
        evidence = {"bundle_sha256": bundle_sha, "wheel_count": len(wheels),
                    "built": sorted(built_names), "reused": sorted(reused_names),
                    "authorizers": authorizers, "partition_policy_enforced": bool(enforce_partition_policy)}
        with open(os.path.join(staging, "build-evidence.json"), "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, indent=2, sort_keys=True)

        os.replace(staging, final_bundle)                    # ONE atomic publish of the complete bundle
    except BaseException:
        _shutil.rmtree(staging, ignore_errors=True)          # no partial/stale final on failure
        raise
    return {"provenance": provenance, "bundle_sha256": bundle_sha, "bundle_dir": final_bundle,
            "wheelhouse_dir": os.path.join(final_bundle, "wheelhouse-armhf"),
            "runtime_lock_text": runtime_lock_text}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="build_wheelhouse.py")
    ap.add_argument("--build-lock", required=True)
    ap.add_argument("--sdist-dir", required=True)
    # NOTE: there is deliberately no --out-dir. `--out-bundle` is the SINGLE atomic publication
    # boundary and is what build-wheelhouse-offline.sh passes; it is bound to the internal
    # build_wheelhouse(out_dir=...) parameter below. A stale required --out-dir made argparse exit 2
    # before the builder ever ran.
    ap.add_argument("--recipe", required=True, help="path to the committed release/builder/Containerfile")
    ap.add_argument("--build-backends-lock", required=True,
                    help="path to the committed release/builder/requirements-build-backends.lock")
    ap.add_argument("--apt-packages", required=True, help="committed release/builder/apt-packages.list")
    ap.add_argument("--rustup-sha", required=True, help="committed release/builder/rustup-init.sha256")
    ap.add_argument("--extractor-tools-lock", required=True,
                    help="committed release/builder/requirements-extractor-tools.lock")
    ap.add_argument("--partition-backends", required=True,
                    help="committed release/builder/partition_backends.py (image-context entry; "
                         "MANDATORY -- never inferred from a mutable path)")
    ap.add_argument("--build-backends-source-allowlist", required=True,
                    help="committed release/builder/requirements-build-backends.source-allowlist")
    ap.add_argument("--builder-identity", required=True)
    ap.add_argument("--base-image-digest", required=True, help="pinned base image OCI digest sha256:<64hex>")
    ap.add_argument("--image-manifest", required=True,
                    help="raw OCI image manifest file (skopeo inspect --raw); its sha256 is the manifest digest")
    ap.add_argument("--runtime-image-id", required=True, help="docker inspect .Id (store-agnostic runtime id)")
    ap.add_argument("--reuse-authz", default=None,
                    help="committed reused-wheel authorization JSON (24 official wheels)")
    ap.add_argument("--reuse-wheels-dir", default=None,
                    help="read-only reuse store (acquisition bundle's wheels/; re-verified offline)")
    ap.add_argument("--target-tags", default=None,
                    help="committed release/builder/target-supported-tags.txt (mandatory target policy)")
    ap.add_argument("--requirements", default=None,
                    help="committed requirements.txt (runtime lock is generated + bijection-checked)")
    ap.add_argument("--out-bundle", required=True, help="Phase-B bundle dir (must NOT pre-exist)")
    ap.add_argument("--enforce-partition-policy", action="store_true",
                    help="enforce the v0.3.17 6/24/30 approved-six policy before publication (production)")
    a = ap.parse_args(argv)
    _tags = _reqs = None
    _tags_sha = None
    if a.target_tags:
        _t, _tags, _tags_sha = _reuse_authz.load_target_tags(a.target_tags)
    if a.requirements:
        with open(a.requirements, encoding="utf-8") as fh:
            _reqs = fh.read()
    res = build_wheelhouse(build_lock_path=a.build_lock, sdist_dir=a.sdist_dir, out_dir=a.out_bundle,
                           recipe_path=a.recipe, build_backends_lock_path=a.build_backends_lock,
                           apt_packages_path=a.apt_packages, rustup_sha_path=a.rustup_sha,
                           extractor_tools_lock_path=a.extractor_tools_lock,
                           build_backends_source_allowlist_path=a.build_backends_source_allowlist,
                           partition_backends_path=a.partition_backends,
                           builder_identity=a.builder_identity, base_image_digest=a.base_image_digest,
                           image_manifest_path=a.image_manifest, runtime_image_id=a.runtime_image_id,
                           reuse_authz_path=a.reuse_authz, reuse_wheels_dir=a.reuse_wheels_dir,
                           target_tags=_tags, target_tags_sha256=_tags_sha, requirements_text=_reqs,
                           enforce_partition_policy=a.enforce_partition_policy)
    print(f"phase-b bundle: {res['bundle_dir']}  bundle_sha256={res['bundle_sha256']}")
    print(f"  wheelhouse={res['wheelhouse_dir']}  provenance={res['bundle_dir']}/wheelhouse-armv7.json")
    print(f"  runtime_lock={res['bundle_dir']}/requirements-armv7.lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
