# SPDX-License-Identifier: MIT
"""release/build_wheelhouse.py tests (hardened builder block). Verifies the manifest
digest is recomputed from the raw OCI manifest file, the store-agnostic runtime identity
the committed build-backends lock is bound + cross-checked, the environment is CAPTURED
from the executing runtime (injectable env_probe), and the whole thing self-checks
through the strict producer validator. Portable (no ssh/Linux/Docker)."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import tempfile

import pytest

from release import build_wheelhouse as B
from release import ccc_release as R

_TT = set((pathlib.Path(__file__).resolve().parents[2] / "release" / "builder"
           / "target-supported-tags.txt").read_text(encoding="utf-8").split())

_BB_LOCK = "maturin==1.5.1 --hash=sha256:%s\n" % ("7" * 64)
_ENV = {"os": "Ubuntu 22.04.5 LTS", "python": "Python 3.10.12", "rustc": "rustc 1.75.0",
        "cargo": "cargo 1.75.0", "gcc": "gcc 11.4.0", "glibc": "2.35",
        "os_id": "ubuntu", "os_version_id": "22.04", "arch": "armv7l", "apt_architecture": "armhf",
        "apt": {"build-essential": "12.9ubuntu3"}, "build_backends": {"maturin": "1.5.1", "wheel": "0.43.0"}}
_APT = "build-essential=12.9ubuntu3\n"
_RUSTUP = "f" * 64 + "  rustup-init\n"
_APT_SHA = R.sha256_hex(R._to_lf(_APT.encode()))
_RUSTUP_SHA = R.sha256_hex(R._to_lf(_RUSTUP.encode()))
_EXT_IN = "tomli==2.0.1\n"
_EXT_LOCK = "tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
_EXT_LOCK_SHA = R.sha256_hex(_EXT_LOCK.encode())
_ALLOWLIST = "maturin\n"
_PARTITION_BACKENDS = "# synthetic partition_backends.py stand-in (image-context entry)\n"
_ALLOWLIST_SHA = R.sha256_hex(_ALLOWLIST.encode())
_BASE = "sha256:" + "b" * 64
_CONFIG_DIGEST = "sha256:" + "c" * 64
def _manifest_bytes(config_digest):
    import json as _json
    return _json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                   "digest": config_digest, "size": 1234},
        "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "digest": "sha256:" + "a" * 64, "size": 5678}],
    }).encode()
_MANIFEST_BYTES = _manifest_bytes(_CONFIG_DIGEST)
_RUNTIME_ID = "sha256:" + hashlib.sha256(_MANIFEST_BYTES).hexdigest()   # containerd: .Id == manifest digest


def _sdist(d, name, data):
    with open(os.path.join(d, name), "wb") as fh:
        fh.write(data)
    return hashlib.sha256(data).hexdigest()


def _good_build_fn(spath, sfn, name, ver):
    return "%s-%s-py3-none-any.whl" % (name, ver), b"WHEEL:" + name.encode()


def _probe():
    return dict(_ENV)


def _setup(tmp_path, *, lock=None, sdists=None, bb_lock=_BB_LOCK, manifest=_MANIFEST_BYTES,
           allowlist=_ALLOWLIST):
    base = pathlib.Path(tempfile.mkdtemp(dir=str(tmp_path)))
    sdir = base / "sdists"
    sdir.mkdir()
    (base / "Containerfile").write_text("FROM base\nRUN true\n")
    (base / "requirements-build-backends.lock").write_text(bb_lock)
    (base / "apt-packages.list").write_text(_APT)
    (base / "rustup-init.sha256").write_text(_RUSTUP)
    (base / "requirements-extractor-tools.lock").write_text(_EXT_LOCK)
    (base / "requirements-build-backends.source-allowlist").write_text(allowlist)
    (base / "partition_backends.py").write_text(_PARTITION_BACKENDS)
    (base / "image-manifest.json").write_bytes(manifest)
    # Synthetic stand-in for the image's /opt/ccc: the five build-context files COPYed into the
    # real builder image. `image_context_root` is the narrow test seam; production is /opt/ccc.
    optccc = base / "optccc"
    optccc.mkdir()
    for _n in ("apt-packages.list", "rustup-init.sha256", "requirements-build-backends.lock",
               "requirements-build-backends.source-allowlist", "partition_backends.py"):
        (optccc / _n).write_bytes((base / _n).read_bytes())
    sh = {}
    for nm, data in (sdists or {"fastapi-0.133.0.tar.gz": b"SDIST"}).items():
        sh[nm] = _sdist(str(sdir), nm, data)
    lockp = base / "requirements-armv7-build.lock"
    lockp.write_text(lock or "fastapi==0.133.0 --hash=sha256:%s\n" % sh["fastapi-0.133.0.tar.gz"])
    return base, sdir, sh


def _run(tmp_path, *, build_fn=_good_build_fn, identity="ccc-builder", base=_BASE,
         runtime_image_id=_RUNTIME_ID, env_probe=None, **kw):
    d, sdir, sh = _setup(tmp_path, **kw)
    res = B.build_wheelhouse(
        build_lock_path=str(d / "requirements-armv7-build.lock"), sdist_dir=str(sdir),
        out_dir=str(d / "wh"), recipe_path=str(d / "Containerfile"),
        build_backends_lock_path=str(d / "requirements-build-backends.lock"),
        apt_packages_path=str(d / "apt-packages.list"), rustup_sha_path=str(d / "rustup-init.sha256"),
        extractor_tools_lock_path=str(d / "requirements-extractor-tools.lock"),
        build_backends_source_allowlist_path=str(d / "requirements-build-backends.source-allowlist"),
        partition_backends_path=str(d / "partition_backends.py"),
        image_context_root=str(d / "optccc"),
        builder_identity=identity, base_image_digest=base,
        image_manifest_path=str(d / "image-manifest.json"), runtime_image_id=runtime_image_id,
        env_probe=env_probe or _probe, build_fn=build_fn)
    return res, d, sh


def test_build_ok_and_round_trips(tmp_path):
    res, d, _sh = _run(tmp_path)
    b = res["provenance"]["builder"]
    assert b["image_manifest_digest"] == "sha256:" + hashlib.sha256(_MANIFEST_BYTES).hexdigest()
    assert b["runtime_image_id"] == _RUNTIME_ID and b["image_identity_mode"] == "containerd"
    assert b["image_config_digest"] == _CONFIG_DIGEST
    assert b["runtime_image_id"] == b["image_manifest_digest"]        # containerd binding
    assert "image_id" not in b
    assert b["build_backends_lock_sha256"] == R.sha256_hex(_BB_LOCK.encode())
    assert b["environment"]["glibc"] == "2.35"
    R._validate_provenance(res["provenance"], R._wheelhouse_members(res["wheelhouse_dir"]), res["bundle_tree_sha256"],
                           open(d / "requirements-armv7-build.lock").read(),
                           R.sha256_hex(b"FROM base\nRUN true\n"), R.sha256_hex(_BB_LOCK.encode()), _BB_LOCK,
                           _APT_SHA, _RUSTUP_SHA, _APT, _EXT_LOCK_SHA, _ALLOWLIST_SHA,
                           image_manifest_bytes=_MANIFEST_BYTES)


# --------------------------------------------------------------------------- #
#  IMAGE-CONTEXT BINDING: byte-level proof that the executing image was built   #
#  from the committed build context (five in-image copies + the recipe hash).   #
# --------------------------------------------------------------------------- #
_IN_IMAGE = sorted(set(R.IMAGE_CONTEXT_FILES) - {"release/builder/Containerfile"})


def test_image_context_digest_is_order_independent_and_exact_six():
    m = {p: "%064x" % i for i, p in enumerate(R.IMAGE_CONTEXT_FILES)}
    shuffled = dict(reversed(list(m.items())))
    assert R.image_context_digest(m) == R.image_context_digest(shuffled)
    with pytest.raises(R.ReleaseError, match="EXACTLY the six"):      # missing key
        R.image_context_digest({k: v for k, v in list(m.items())[:5]})
    with pytest.raises(R.ReleaseError, match="EXACTLY the six"):      # unknown key
        R.image_context_digest(dict(m, **{"release/builder/EXTRA": "a" * 64}))
    with pytest.raises(R.ReleaseError, match="64-hex"):               # malformed hash
        R.image_context_digest(dict(m, **{"release/builder/Containerfile": "nothex"}))


def test_image_context_recorded_in_provenance(tmp_path):
    res, _d, _sh = _run(tmp_path)
    b = res["provenance"]["builder"]
    assert set(b["image_context"]) == set(R.IMAGE_CONTEXT_FILES)
    assert b["image_context_sha256"] == R.image_context_digest(b["image_context"])


@pytest.mark.parametrize("canonical", _IN_IMAGE)
def test_image_context_rejects_each_tampered_in_image_file(tmp_path, canonical):
    # The in-image copy differing from the committed bytes must fail closed and NAME the file.
    d, sdir, _sh = _setup(tmp_path)
    tgt = d / "optccc" / os.path.basename(canonical)
    tgt.write_bytes(tgt.read_bytes() + b"\n# tampered\n")
    with pytest.raises(R.ReleaseError) as ei:
        _run_with(d, sdir)
    assert canonical in str(ei.value) and "different bytes" in str(ei.value)


def test_image_context_rejects_missing_in_image_file(tmp_path):
    d, sdir, _sh = _setup(tmp_path)
    (d / "optccc" / "partition_backends.py").unlink()
    with pytest.raises(R.ReleaseError, match="missing or is a symlink"):
        _run_with(d, sdir)


def test_image_context_rejects_symlinked_in_image_file(tmp_path):
    # Production symlink rejection is NOT weakened. Only the test's ability to CREATE a symlink is
    # environment-dependent: Windows needs Developer Mode / SeCreateSymbolicLinkPrivilege. On POSIX
    # a symlink failure is a real error and is never skipped.
    d, sdir, _sh = _setup(tmp_path)
    tgt = d / "optccc" / "apt-packages.list"
    tgt.unlink()
    try:
        tgt.symlink_to(d / "apt-packages.list")
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            pytest.skip("cannot create a symlink on this Windows host (requires Developer Mode or "
                        f"SeCreateSymbolicLinkPrivilege); POSIX/WSL covers this case: {exc}")
        raise
    with pytest.raises(R.ReleaseError, match="symlink"):
        _run_with(d, sdir)


def test_image_context_verified_before_env_probe_and_build(tmp_path):
    # Ordering guarantee: the proof runs BEFORE environment probing and before any build starts.
    d, sdir, _sh = _setup(tmp_path)
    (d / "optccc" / "rustup-init.sha256").write_text("deadbeef\n")
    calls = {"env": 0, "build": 0}

    def _probe_counting():
        calls["env"] += 1
        return dict(_ENV)

    def _bfn_counting(sp, sf, n, v):
        calls["build"] += 1
        return _good_build_fn(sp, sf, n, v)

    with pytest.raises(R.ReleaseError, match="image-context proof FAILED"):
        _run_with(d, sdir, env_probe=_probe_counting, build_fn=_bfn_counting)
    assert calls == {"env": 0, "build": 0}


def _run_with(d, sdir, *, env_probe=None, build_fn=_good_build_fn):
    return B.build_wheelhouse(
        build_lock_path=str(d / "requirements-armv7-build.lock"), sdist_dir=str(sdir),
        out_dir=str(d / "wh"), recipe_path=str(d / "Containerfile"),
        build_backends_lock_path=str(d / "requirements-build-backends.lock"),
        apt_packages_path=str(d / "apt-packages.list"), rustup_sha_path=str(d / "rustup-init.sha256"),
        extractor_tools_lock_path=str(d / "requirements-extractor-tools.lock"),
        build_backends_source_allowlist_path=str(d / "requirements-build-backends.source-allowlist"),
        partition_backends_path=str(d / "partition_backends.py"),
        image_context_root=str(d / "optccc"),
        builder_identity="ccc-builder", base_image_digest=_BASE,
        image_manifest_path=str(d / "image-manifest.json"), runtime_image_id=_RUNTIME_ID,
        env_probe=env_probe or _probe, build_fn=build_fn)


def _expected_ctx(d):
    ctx = {"release/builder/Containerfile": R.canonical_file_sha256((d / "Containerfile").read_bytes())}
    for canonical in _IN_IMAGE:
        ctx[canonical] = R.canonical_file_sha256((d / os.path.basename(canonical)).read_bytes())
    return ctx


def _validate_prov(res, d, *, expected):
    R._validate_provenance(res["provenance"], R._wheelhouse_members(res["wheelhouse_dir"]),
                           res["bundle_tree_sha256"], (d / "requirements-armv7-build.lock").read_text(),
                           R.sha256_hex(b"FROM base\nRUN true\n"), R.sha256_hex(_BB_LOCK.encode()),
                           _BB_LOCK, _APT_SHA, _RUSTUP_SHA, _APT, _EXT_LOCK_SHA, _ALLOWLIST_SHA,
                           image_manifest_bytes=_MANIFEST_BYTES, image_context_expected=expected)


def test_producer_accepts_matching_image_context(tmp_path):
    res, d, _sh = _run(tmp_path)
    _validate_prov(res, d, expected=_expected_ctx(d))


def test_producer_rejects_absent_context_map(tmp_path):
    res, d, _sh = _run(tmp_path)
    del res["provenance"]["builder"]["image_context"]
    with pytest.raises(R.ReleaseError, match="image_context is required"):
        _validate_prov(res, d, expected=_expected_ctx(d))


def test_producer_rejects_absent_aggregate_digest(tmp_path):
    res, d, _sh = _run(tmp_path)
    del res["provenance"]["builder"]["image_context_sha256"]
    with pytest.raises(R.ReleaseError, match="image_context_sha256 is required"):
        _validate_prov(res, d, expected=_expected_ctx(d))


@pytest.mark.parametrize("canonical", list(R.IMAGE_CONTEXT_FILES))
def test_producer_rejects_each_wrong_per_file_hash(tmp_path, canonical):
    res, d, _sh = _run(tmp_path)
    b = res["provenance"]["builder"]
    b["image_context"][canonical] = "e" * 64                 # disagrees with the committed bytes
    b["image_context_sha256"] = R.image_context_digest(b["image_context"])   # self-consistent!
    with pytest.raises(R.ReleaseError, match="does not match the committed bytes"):
        _validate_prov(res, d, expected=_expected_ctx(d))


def test_producer_rejects_aggregate_digest_mismatch(tmp_path):
    res, d, _sh = _run(tmp_path)
    res["provenance"]["builder"]["image_context_sha256"] = "d" * 64
    with pytest.raises(R.ReleaseError, match="image_context_sha256 mismatch"):
        _validate_prov(res, d, expected=_expected_ctx(d))


# --------------------------------------------------------------------------- #
#  CLI CONTRACT: the real argparse entry point must accept the EXACT argument    #
#  shape build-wheelhouse-offline.sh produces.                                   #
#                                                                                #
#  INTEGRATION-TEST ESCAPE this closes: unit tests exercised build_wheelhouse()  #
#  directly, and separate tests inspected the shell as TEXT, but nothing fed the #
#  shell's actual argument contract to the real argparse parser. A stale         #
#  required --out-dir therefore survived to hardware, where Phase B exited 2     #
#  before any wheel was built.                                                   #
# --------------------------------------------------------------------------- #
_PHASE_B_SH = (pathlib.Path(__file__).resolve().parents[2]
               / "release" / "builder" / "build-wheelhouse-offline.sh").read_text(encoding="utf-8")


def _shell_reuse_flags():
    """The long options the shell splices in DYNAMICALLY via "${REUSE_ARGS[@]}".

    Derived from the committed REUSE_ARGS definition itself, so shell drift cannot stay invisible:
    merely observing that the array is spliced (the previous behaviour) would leave the contract
    test green even if --reuse-authz or --reuse-wheels-dir vanished from the parser."""
    defs = re.findall(r"REUSE_ARGS=\(([^)]*)\)", _PHASE_B_SH)
    assert defs, "could not locate any REUSE_ARGS definition in the committed shell"
    flags = []
    for body in defs:                                     # REUSE_ARGS=() is the empty init
        for tok in body.split():
            if tok.startswith("--") and tok not in flags:
                flags.append(tok)
    assert flags, "REUSE_ARGS defines no long options; the hybrid reuse path would be untested"
    return flags


def _shell_producer_flags():
    """Every long option the committed shell passes to build_wheelhouse.py -- the statically
    written ones AND the two expanded through REUSE_ARGS."""
    body = _PHASE_B_SH.split("python3 /repo/release/build_wheelhouse.py", 1)[1]
    flags, spliced = [], False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("--"):
            flags.append(line.split()[0])
        if "REUSE_ARGS" in line:
            spliced = True
        if line and not line.startswith(("--", '"', "\\")) and flags and not line.endswith("\\"):
            break
    assert spliced, "expected the shell to splice REUSE_ARGS into the producer invocation"
    return flags + [f for f in _shell_reuse_flags() if f not in flags]


def test_shell_producer_flags_are_all_accepted_by_the_real_parser():
    # Every flag the shell emits must exist in the production parser -- and --out-dir must not be
    # among them. Derived from the committed shell text so it cannot drift out of sync.
    flags = _shell_producer_flags()
    assert "--out-bundle" in flags
    assert "--out-dir" not in flags
    # The dynamically spliced hybrid-reuse flags are part of the contract, not incidental.
    assert "--reuse-authz" in flags and "--reuse-wheels-dir" in flags
    parser_flags = {a for line in open(B.__file__, encoding="utf-8").read().splitlines()
                    if 'add_argument("--' in line
                    for a in [line.split('add_argument("')[1].split('"')[0]]}
    unknown = [f for f in flags if f not in parser_flags]
    assert not unknown, f"shell passes flags the parser does not define: {unknown}"


def _reuse_paths(d):
    """Fixture-local values for the two dynamically spliced reuse flags."""
    return str(d / "armv7-reuse-authz.json"), str(d / "reuse-store")


def _production_argv(d, sdir, out_bundle):
    """EXACTLY the shell's producer argument shape -- the v0.3.17 hybrid path, INCLUDING the two
    flags the shell expands through "${REUSE_ARGS[@]}" (6 built + 24 reused), whose names are taken
    from the committed REUSE_ARGS definition rather than hard-coded here."""
    reuse_authz, reuse_store = _reuse_paths(d)
    reuse_flags = _shell_reuse_flags()
    assert reuse_flags == ["--reuse-authz", "--reuse-wheels-dir"], reuse_flags
    reuse_argv = [reuse_flags[0], reuse_authz, reuse_flags[1], reuse_store]
    b = str(d)
    return reuse_argv + ["--build-lock", str(d / "requirements-armv7-build.lock"),
            "--sdist-dir", str(sdir),
            "--out-bundle", out_bundle,
            "--recipe", b + "/Containerfile",
            "--build-backends-lock", b + "/requirements-build-backends.lock",
            "--apt-packages", b + "/apt-packages.list",
            "--rustup-sha", b + "/rustup-init.sha256",
            "--extractor-tools-lock", b + "/requirements-extractor-tools.lock",
            "--build-backends-source-allowlist", b + "/requirements-build-backends.source-allowlist",
            "--partition-backends", b + "/partition_backends.py",
            "--builder-identity", "ccc-builder",
            "--base-image-digest", _BASE,
            "--image-manifest", b + "/image-manifest.json",
            "--runtime-image-id", _RUNTIME_ID,
            "--target-tags", str(pathlib.Path(__file__).resolve().parents[2]
                                 / "release" / "builder" / "target-supported-tags.txt"),
            "--requirements", b + "/requirements.txt",
            "--enforce-partition-policy"]


def test_cli_main_accepts_production_argv_and_binds_out_dir_to_out_bundle(tmp_path, monkeypatch):
    # Drives the REAL main()/argparse path. On the defective HEAD this raised SystemExit(2)
    # ("the following arguments are required: --out-dir") before build_wheelhouse was reached --
    # exactly the hardware failure.
    d, sdir, _sh = _setup(tmp_path)
    (d / "requirements.txt").write_text("fastapi>=0\n")
    out_bundle = str(d / "bundle")
    reuse_authz, reuse_store = _reuse_paths(d)
    captured = {}

    def _fake_build_wheelhouse(**kw):
        captured.update(kw)
        return {"provenance": {}, "bundle_tree_sha256": "0" * 64, "bundle_dir": kw["out_dir"],
                "wheelhouse_dir": kw["out_dir"] + "/wheelhouse-armhf", "runtime_lock_text": "x\n"}

    monkeypatch.setattr(B, "build_wheelhouse", _fake_build_wheelhouse)
    rc = B.main(_production_argv(d, sdir, out_bundle))
    assert rc == 0
    assert captured, "argparse must reach build_wheelhouse with the production argument shape"
    assert captured["out_dir"] == out_bundle          # --out-bundle binds the internal out_dir
    assert captured["enforce_partition_policy"] is True
    # The dynamically spliced hybrid-reuse paths must reach build_wheelhouse UNCHANGED, otherwise
    # the 6-built + 24-reused production path is not what this test exercises.
    assert captured["reuse_authz_path"] == reuse_authz
    assert captured["reuse_wheels_dir"] == reuse_store


def test_cli_main_rejects_the_obsolete_out_dir_flag(tmp_path):
    # --out-dir is gone from the producer CLI; passing it is an unrecognised argument.
    d, sdir, _sh = _setup(tmp_path)
    (d / "requirements.txt").write_text("fastapi>=0\n")
    argv = _production_argv(d, sdir, str(d / "bundle")) + ["--out-dir", str(d / "wh")]
    with pytest.raises(SystemExit) as ei:
        B.main(argv)
    assert ei.value.code == 2


def test_default_env_probe_shape():
    env = B._default_env_probe()               # captured from THIS runtime; structure must be complete
    for k in ("os", "os_id", "os_version_id", "arch", "python", "rustc", "cargo", "gcc",
              "glibc", "apt", "build_backends"):
        assert k in env
    assert env["python"]                        # python is present in any runtime we run in
    assert isinstance(env["build_backends"], dict)


def test_runtime_image_id_required_and_bound(tmp_path):
    with pytest.raises(R.ReleaseError):        # not an OCI digest
        _run(tmp_path, runtime_image_id="")
    with pytest.raises(R.ReleaseError):        # equals neither manifest nor config digest -> no mode
        _run(tmp_path, runtime_image_id="sha256:" + "7" * 64)


def test_legacy_mode_runtime_image_id_is_config(tmp_path):
    # legacy store: .Id == config digest (!= manifest digest) is also accepted
    res, d, _sh = _run(tmp_path, runtime_image_id=_CONFIG_DIGEST)
    assert res["provenance"]["builder"]["image_identity_mode"] == "legacy"


def test_missing_or_empty_manifest_file(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, manifest=b"")


def test_bad_base_digest(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, base="not-a-digest")


def test_empty_backend_lock_rejected(tmp_path):
    with pytest.raises(R.ReleaseError):        # finding 1
        _run(tmp_path, bb_lock="# only comments\n")


def test_environment_glibc_and_backend_binding(tmp_path):
    with pytest.raises(R.ReleaseError):        # glibc newer than target
        _run(tmp_path, env_probe=lambda: {**_ENV, "glibc": "2.38"})
    with pytest.raises(R.ReleaseError):        # authorized backend not captured in env
        _run(tmp_path, env_probe=lambda: {**_ENV, "build_backends": {"wheel": "0.43.0"}})


def test_sdist_authorization_and_build_output(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, lock="fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64))   # unauthorized hash
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, sdists={"fastapi-0.133.0.tar.gz": b"S", "extra-1.0.0.tar.gz": b"x"})  # extra

    def _bad(spath, sfn, name, ver):
        return "wrongname-9.9.9-py3-none-any.whl", b"W"
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, build_fn=_bad)          # ambiguous build output


def test_dpkg_status_parser_excludes_non_installed():
    # ${db:Status-Status} filtering: only 'installed' rows survive; config-files/removed excluded.
    lines = ("installed\tbuild-essential\t12.9ubuntu3\n"
             "config-files\told-pkg\t1.0\n"
             "not-installed\tghost\t2.0\n"
             "installed\tlibssl-dev:armhf\t3.0.2\n")
    apt = B._parse_dpkg_status_lines(lines)
    assert apt == {"build-essential": "12.9ubuntu3", "libssl-dev:armhf": "3.0.2"}
    assert "old-pkg" not in apt and "ghost" not in apt


def test_build_wheelhouse_rejects_unused_allowlist(tmp_path):
    # 'evilpkg' is not pinned in the backend lock (maturin) -> semantic self-check fails
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, allowlist="evilpkg\n")


def test_build_wheelhouse_rejects_noncanonical_allowlist(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, allowlist="MATURIN\n")             # noncanonical spelling


def test_build_wheelhouse_rejects_empty_allowlist(tmp_path):
    with pytest.raises(R.ReleaseError):
        _run(tmp_path, allowlist="# only a comment\n")


# --------------------------------------------------------------------------- #
#  Effective backend recorder (shadowed/duplicate metadata)                   #
# --------------------------------------------------------------------------- #
def _mk_distinfo(root, name, version):
    di = pathlib.Path(root) / f"{name}-{version}.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (name, version))


def test_effective_backend_prefers_search_order_not_last_wins(tmp_path):
    early, late = tmp_path / "early", tmp_path / "late"
    _mk_distinfo(early, "setuptools", "82.0.1")     # effective (earlier path entry)
    _mk_distinfo(late, "setuptools", "59.6.0")      # shadow (later)
    eff, shadows = B._effective_build_backends(paths=[str(early), str(late)])
    assert eff["setuptools"] == "82.0.1"            # NOT the last-enumerated 59.6.0
    assert shadows["setuptools"] == ["82.0.1", "59.6.0"]   # shadow retained as audit only
    eff2, _ = B._effective_build_backends(paths=[str(late), str(early)])
    assert eff2["setuptools"] == "59.6.0"           # resolution follows search order, not last-wins


# --------------------------------------------------------------------------- #
#  Dual-origin build: 1 source-built + 1 reused, one merged wheelhouse         #
# --------------------------------------------------------------------------- #
def _reused(tmp_path, filename, data):
    # The reuse store mirrors the real acquisition bundle's `wheels/` dir: authorized wheels ONLY.
    # The authorization file lives OUTSIDE the store (any foreign entry is rejected by the
    # exact-set store validation), so these fixtures must not co-locate it with the wheels.
    from release import reuse_authz as RA
    rdir = pathlib.Path(tempfile.mkdtemp(dir=str(tmp_path)))
    (rdir / filename).write_bytes(data)
    authz = {"schema": RA.SCHEMA_ID, "origin": "pypi",
             "target": {"python": "cp310", "platform": "armv7l", "glibc": "2.35"},
             "wheels": [{"name": "bcrypt", "version": "4.3.0", "filename": filename,
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "tags": ["cp39-abi3-manylinux_2_31_armv7l"], "requires_python": ">=3.8"}]}
    ap = pathlib.Path(tempfile.mkdtemp(dir=str(tmp_path))) / "armv7-reuse-authz.json"
    ap.write_text(json.dumps(authz))
    return str(ap), str(rdir)


def _run_dual(tmp_path, *, reuse_authz_path, reuse_wheels_dir):
    d, sdir, sh = _setup(tmp_path)
    return B.build_wheelhouse(
        build_lock_path=str(d / "requirements-armv7-build.lock"), sdist_dir=str(sdir),
        out_dir=str(d / "wh"), recipe_path=str(d / "Containerfile"),
        build_backends_lock_path=str(d / "requirements-build-backends.lock"),
        apt_packages_path=str(d / "apt-packages.list"), rustup_sha_path=str(d / "rustup-init.sha256"),
        extractor_tools_lock_path=str(d / "requirements-extractor-tools.lock"),
        build_backends_source_allowlist_path=str(d / "requirements-build-backends.source-allowlist"),
        partition_backends_path=str(d / "partition_backends.py"),
        image_context_root=str(d / "optccc"),
        builder_identity="ccc-builder", base_image_digest=_BASE,
        image_manifest_path=str(d / "image-manifest.json"), runtime_image_id=_RUNTIME_ID,
        reuse_authz_path=reuse_authz_path, reuse_wheels_dir=reuse_wheels_dir, target_tags=_TT,
        env_probe=_probe, build_fn=_good_build_fn), d


def test_dual_origin_build_merges_and_self_validates(tmp_path):
    from release import reuse_authz as RA
    wfn = "bcrypt-4.3.0-cp39-abi3-manylinux_2_31_armv7l.whl"
    ap, rdir = _reused(tmp_path, wfn, b"REUSED-BYTES")
    res, d = _run_dual(tmp_path, reuse_authz_path=ap, reuse_wheels_dir=rdir)
    prov = res["provenance"]
    origins = {w["wheel_filename"]: w["origin"] for w in prov["wheels"]}
    assert origins["fastapi-0.133.0-py3-none-any.whl"] == "built"
    assert origins[wfn] == "reused"
    by = {w["wheel_filename"]: w for w in prov["wheels"]}
    assert by["fastapi-0.133.0-py3-none-any.whl"]["sdist_name"] == "fastapi-0.133.0.tar.gz"
    assert "sdist_name" not in by[wfn]              # reused wheel carries no sdist fields
    authz = RA.load_and_validate(pathlib.Path(ap).read_bytes(), target_tags=_TT)
    assert prov["authorizers"]["reuse_authz_sha256"] == RA.sha256_hex(RA.canonical_bytes(authz))


def test_dual_origin_rejects_tampered_reused_wheel(tmp_path):
    # Must fail for the TAMPER reason specifically. (Regression: this previously passed for the wrong
    # reason -- the fixture put the authz file inside the store, so it failed the foreign-entry check
    # before ever hashing the wheel, masking whether tamper detection worked at all.)
    wfn = "bcrypt-4.3.0-cp39-abi3-manylinux_2_31_armv7l.whl"
    ap, rdir = _reused(tmp_path, wfn, b"REUSED-BYTES")
    (pathlib.Path(rdir) / wfn).write_bytes(b"TAMPERED-DIFFERENT-BYTES")   # store != authz sha
    with pytest.raises(R.ReleaseError, match="sha256"):
        _run_dual(tmp_path, reuse_authz_path=ap, reuse_wheels_dir=rdir)


def test_dual_origin_rejects_missing_reused_wheel(tmp_path):
    # Must fail for the MISSING reason specifically (see the note above about the masked assertion).
    wfn = "bcrypt-4.3.0-cp39-abi3-manylinux_2_31_armv7l.whl"
    ap, rdir = _reused(tmp_path, wfn, b"REUSED-BYTES")
    (pathlib.Path(rdir) / wfn).unlink()            # authorized wheel absent from the store
    with pytest.raises(R.ReleaseError, match="missing"):
        _run_dual(tmp_path, reuse_authz_path=ap, reuse_wheels_dir=rdir)


# --------------------------------------------------------------------------- #
#  Production 6/24/30 policy enforced by Phase B BEFORE publication            #
# --------------------------------------------------------------------------- #
def _policy_inputs(tmp_path, *, built_names):
    from release import reuse_authz as RA
    reused = ["reusepkg%02d" % i for i in range(1, 25)]
    sd, lock_lines = {}, []
    for n in built_names:
        data = ("SD:" + n).encode()
        sd["%s-1.0.tar.gz" % n] = data
        lock_lines.append("%s==1.0 --hash=sha256:%s" % (n, hashlib.sha256(data).hexdigest()))
    d, sdir, _sh = _setup(tmp_path, sdists=sd, lock="\n".join(lock_lines) + "\n")
    rdir = pathlib.Path(tempfile.mkdtemp(dir=str(tmp_path)))
    wheels = []
    for n in reused:
        wf = "%s-1.0-py3-none-any.whl" % n
        wb = ("W:" + n).encode()
        (rdir / wf).write_bytes(wb)
        wheels.append({"name": n, "version": "1.0", "filename": wf,
                       "sha256": hashlib.sha256(wb).hexdigest(),
                       "tags": ["py3-none-any"], "requires_python": ">=3.9"})
    authz = {"schema": RA.SCHEMA_ID, "origin": "pypi", "target": dict(RA.TARGET_PROFILE), "wheels": wheels}
    ap = d / "reuse-authz.json"
    ap.write_text(json.dumps(authz))
    reqs = "".join("%s>=0\n" % n for n in list(built_names) + reused)
    return d, sdir, rdir, ap, reqs


def _bfn(sp, sf, n, v):
    return ("%s-%s-py3-none-any.whl" % (n, v), ("BUILT:" + n).encode())


def _run_policy(tmp_path, *, built_names):
    d, sdir, rdir, ap, reqs = _policy_inputs(tmp_path, built_names=built_names)
    return B.build_wheelhouse(
        build_lock_path=str(d / "requirements-armv7-build.lock"), sdist_dir=str(sdir),
        out_dir=str(d / "bundle"), recipe_path=str(d / "Containerfile"),
        build_backends_lock_path=str(d / "requirements-build-backends.lock"),
        apt_packages_path=str(d / "apt-packages.list"), rustup_sha_path=str(d / "rustup-init.sha256"),
        extractor_tools_lock_path=str(d / "requirements-extractor-tools.lock"),
        build_backends_source_allowlist_path=str(d / "requirements-build-backends.source-allowlist"),
        partition_backends_path=str(d / "partition_backends.py"),
        image_context_root=str(d / "optccc"),
        builder_identity="ccc-builder", base_image_digest=_BASE,
        image_manifest_path=str(d / "image-manifest.json"), runtime_image_id=_RUNTIME_ID,
        reuse_authz_path=str(ap), reuse_wheels_dir=str(rdir), target_tags=_TT,
        target_tags_sha256="ab" * 32, requirements_text=reqs, enforce_partition_policy=True,
        env_probe=_probe, build_fn=_bfn)


def test_phase_b_6_24_30_policy_success_and_bundle(tmp_path):
    res = _run_policy(tmp_path, built_names=sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES))
    bundle = pathlib.Path(res["bundle_dir"])
    whl = [p.name for p in (bundle / "wheelhouse-armhf").iterdir() if p.name.endswith(".whl")]
    assert len(whl) == 30
    assert (bundle / "requirements-armv7.lock").is_file()
    assert (bundle / "wheelhouse-armv7.json").is_file()
    ev = json.loads((bundle / "build-evidence.json").read_text())
    assert ev["wheel_count"] == 30 and len(ev["built"]) == 6 and len(ev["reused"]) == 24
    assert res["provenance"]["authorizers"]["target_tags_sha256"] == "ab" * 32


def test_phase_b_policy_rejects_wrong_built_member(tmp_path):
    # a non-approved built package (7-set) fails the approved-six/count gate BEFORE publication
    wrong = sorted(set(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES) - {"uvloop"} | {"notapproved"})
    with pytest.raises(R.ReleaseError):
        _run_policy(tmp_path, built_names=wrong)


def test_phase_b_refuses_preexisting_bundle(tmp_path):
    d, sdir, rdir, ap, reqs = _policy_inputs(tmp_path, built_names=sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES))
    (d / "bundle").mkdir()
    with pytest.raises(R.ReleaseError):
        B.build_wheelhouse(
            build_lock_path=str(d / "requirements-armv7-build.lock"), sdist_dir=str(sdir),
            out_dir=str(d / "bundle"), recipe_path=str(d / "Containerfile"),
            build_backends_lock_path=str(d / "requirements-build-backends.lock"),
            apt_packages_path=str(d / "apt-packages.list"), rustup_sha_path=str(d / "rustup-init.sha256"),
            extractor_tools_lock_path=str(d / "requirements-extractor-tools.lock"),
            build_backends_source_allowlist_path=str(d / "requirements-build-backends.source-allowlist"),
            partition_backends_path=str(d / "partition_backends.py"),
            image_context_root=str(d / "optccc"),
            builder_identity="ccc-builder", base_image_digest=_BASE,
            image_manifest_path=str(d / "image-manifest.json"), runtime_image_id=_RUNTIME_ID,
            reuse_authz_path=str(ap), reuse_wheels_dir=str(rdir), target_tags=_TT,
            requirements_text=reqs, enforce_partition_policy=True, env_probe=_probe, build_fn=_bfn)


# --------------------------------------------------------------------------- #
#  FAIL-FAST Phase B: the whole reuse preflight runs BEFORE the first source   #
#  build, so no expensive RPi2 compilation starts against a bad reuse input.   #
# --------------------------------------------------------------------------- #
def _counting_bfn(bad_tag_for=None):
    """A build_fn that counts invocations; optionally emits an incompatible tag for one package."""
    calls = {"n": 0}

    def fn(sp, sf, n, v):
        calls["n"] += 1
        if bad_tag_for is not None and n == bad_tag_for:
            return ("%s-%s-cp310-cp310-manylinux_2_31_x86_64.whl" % (n, v), ("BUILT:" + n).encode())
        return ("%s-%s-py3-none-any.whl" % (n, v), ("BUILT:" + n).encode())
    return fn, calls


def _call_policy(d, sdir, rdir, ap, *, build_fn, requirements_text, target_tags_sha256="ab" * 32):
    return B.build_wheelhouse(
        build_lock_path=str(d / "requirements-armv7-build.lock"), sdist_dir=str(sdir),
        out_dir=str(d / "bundle"), recipe_path=str(d / "Containerfile"),
        build_backends_lock_path=str(d / "requirements-build-backends.lock"),
        apt_packages_path=str(d / "apt-packages.list"), rustup_sha_path=str(d / "rustup-init.sha256"),
        extractor_tools_lock_path=str(d / "requirements-extractor-tools.lock"),
        build_backends_source_allowlist_path=str(d / "requirements-build-backends.source-allowlist"),
        partition_backends_path=str(d / "partition_backends.py"),
        image_context_root=str(d / "optccc"),
        builder_identity="ccc-builder", base_image_digest=_BASE,
        image_manifest_path=str(d / "image-manifest.json"), runtime_image_id=_RUNTIME_ID,
        reuse_authz_path=str(ap), reuse_wheels_dir=str(rdir), target_tags=_TT,
        target_tags_sha256=target_tags_sha256, requirements_text=requirements_text,
        enforce_partition_policy=True, env_probe=_probe, build_fn=build_fn)


def test_phase_b_fail_fast_foreign_store_starts_no_build(tmp_path):
    # A foreign subdirectory in the reuse store must be rejected in the CHEAP preflight, BEFORE any
    # source build is attempted (0 build_fn calls) -- the core fail-fast guarantee.
    d, sdir, rdir, ap, reqs = _policy_inputs(tmp_path, built_names=sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES))
    (pathlib.Path(rdir) / "foreign_sub").mkdir()
    fn, calls = _counting_bfn()
    with pytest.raises(R.ReleaseError) as ei:
        _call_policy(d, sdir, rdir, ap, build_fn=fn, requirements_text=reqs)
    assert "reuse store" in str(ei.value)
    assert calls["n"] == 0                       # NO source build started after the preflight failure


def test_phase_b_fail_fast_missing_required_input_starts_no_build(tmp_path):
    # Under the production policy, a missing mandatory input (requirements text) fails in the cheap
    # preflight before any build starts.
    d, sdir, rdir, ap, _reqs = _policy_inputs(tmp_path, built_names=sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES))
    fn, calls = _counting_bfn()
    with pytest.raises(R.ReleaseError):
        _call_policy(d, sdir, rdir, ap, build_fn=fn, requirements_text=None)
    assert calls["n"] == 0


def test_phase_b_all_30_target_tag_check_rejects_incompatible_built_wheel(tmp_path):
    # Every one of the final 30 wheels (built AND reused) must carry a tag in the committed 495-set;
    # a source-built wheel emitted with an x86_64 tag fails the all-30 compatibility gate.
    d, sdir, rdir, ap, reqs = _policy_inputs(tmp_path, built_names=sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES))
    built0 = sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES)[0]
    fn, _calls = _counting_bfn(bad_tag_for=built0)
    with pytest.raises(R.ReleaseError) as ei:
        _call_policy(d, sdir, rdir, ap, build_fn=fn, requirements_text=reqs)
    assert "final wheel" in str(ei.value) or "target" in str(ei.value)


def test_phase_b_success_generates_mandatory_runtime_lock(tmp_path):
    # The runtime lock is MANDATORY under policy: the successful bundle contains an exact 30-line
    # runtime lock (one hashed pin per final wheel), generated before publication.
    res = _run_policy(tmp_path, built_names=sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES))
    bundle = pathlib.Path(res["bundle_dir"])
    lock = (bundle / "requirements-armv7.lock").read_text().splitlines()
    pins = [ln for ln in lock if ln and not ln.startswith("#")]
    assert len(pins) == 30
    assert all("--hash=sha256:" in ln for ln in pins)
