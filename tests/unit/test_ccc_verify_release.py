# SPDX-License-Identifier: MIT
"""ccc-verify-release bootstrap wrapper tests (finding #1).

Proves: NO public --platform override (removed); the real host platform is the only
source, exposed as the monkeypatchable `_host_platform` seam; a genuine release
verifies for the matching host, a cross-platform artifact is rejected, and an
unsupported host fails closed. Linux + ssh-keygen only.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import stat
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("ssh-keygen") is None or shutil.which("git") is None,
    reason="wrapper uses os.uname; genuine release needs ssh-keygen",
)

_WRAP = pathlib.Path(__file__).resolve().parents[2] / "deployment" / "bin" / "ccc-verify-release"
from release import ccc_release as R  # noqa: E402
from tests.unit import _hybrid_release_fixture as _HF  # noqa: E402

_EXT_IN = "tomli==2.0.1\n"
_EXT_LOCK = "tomli==2.0.1 --hash=sha256:%s\n" % ("7" * 64)
_EXT_LOCK_SHA = R.sha256_hex(_EXT_LOCK.encode())
_ALLOWLIST = "maturin\n"
_ALLOWLIST_SHA = R.sha256_hex(_ALLOWLIST.encode())
from backend import update_verify as V  # noqa: E402



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

def _load():
    loader = SourceFileLoader("ccc_verify_release", str(_WRAP))
    spec = importlib.util.spec_from_loader("ccc_verify_release", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _release(base):
    key = base / "k"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "", "-f", str(key)],
                   check=True, capture_output=True)
    r = _HF.make_release(base)                                         # full 6+24=30 dual-origin repo
    res = R.produce_release(version="0.3.16", out_dir=str(base / "d"), key_path=str(key),
                            wheelhouse_armv7_dir=r["wheelhouse_dir"], provenance_armv7_path=r["provenance_path"],
                            armv7_runtime_lock_path=r["runtime_lock_path"],
                            image_manifest_path=r["image_manifest_path"],
                            transfer_manifest_path=r["transfer_manifest_path"],
                            git_ref="v0.3.16", repo_dir=r["repo"])
    store = base / "as"
    store.write_text(R.public_allowed_signers_line(str(key), V.PUBLISHER_IDENTITY) + "\n")
    return res, str(store)


def test_no_platform_flag_in_source():
    assert '"--platform"' not in _WRAP.read_text(encoding="utf-8")


def test_accepts_matching_host_and_rejects_mismatch(tmp_path, monkeypatch):
    mod = _load()
    res, store = _release(tmp_path)
    args = ["--manifest", res["manifest"], "--signature", res["signature"],
            "--trust-store", store]

    monkeypatch.setattr(mod, "_host_platform", lambda: "aarch64")
    assert mod.main(args + ["--artifact", res["artifacts"]["aarch64"]]) == 0     # genuine, matching
    assert mod.main(args + ["--artifact", res["artifacts"]["armv7l"]]) == 2      # wrong-platform bytes

    monkeypatch.setattr(mod, "_host_platform", lambda: "armv7l")
    assert mod.main(args + ["--artifact", res["artifacts"]["armv7l"]]) == 0

    monkeypatch.setattr(mod, "_host_platform", lambda: "x86_64")                 # unsupported host
    assert mod.main(args + ["--artifact", res["artifacts"]["aarch64"]]) == 2


def test_verified_wrapper_writes_install_identity_once(tmp_path, monkeypatch):
    mod = _load()
    res, store = _release(tmp_path)
    identity = tmp_path / "verified-install-identity.json"
    monkeypatch.setattr(mod, "_host_platform", lambda: "aarch64")
    args = [
        "--manifest", res["manifest"],
        "--signature", res["signature"],
        "--artifact", res["artifacts"]["aarch64"],
        "--trust-store", store,
        "--write-install-identity", str(identity),
    ]
    assert mod.main(args) == 0
    doc = json.loads(identity.read_text())
    manifest = json.loads(pathlib.Path(res["manifest"]).read_text())
    assert doc["schema"] == 1
    assert doc["source_commit"] == manifest["source"]["commit"]
    assert doc["source_tag"] == "v0.3.16"
    assert doc["artifact_name"] == pathlib.Path(
        res["artifacts"]["aarch64"]).name
    assert stat.S_IMODE(identity.stat().st_mode) == 0o600
    # O_EXCL: a second verification cannot replace evidence selected earlier.
    assert mod.main(args) == 3
