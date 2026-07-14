# SPDX-License-Identifier: MIT
"""ccc-verify-release bootstrap wrapper tests (finding #1).

Proves: NO public --platform override (removed); the real host platform is the only
source, exposed as the monkeypatchable `_host_platform` seam; a genuine release
verifies for the matching host, a cross-platform artifact is rejected, and an
unsupported host fails closed. Linux + ssh-keygen only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
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
from backend import update_verify as V  # noqa: E402


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
    repo = base / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env=env)
    g("init", "-q")
    (repo / "backend").mkdir()
    (repo / "backend" / "_version.py").write_text('APP_VERSION = "0.3.16"\n')
    (repo / "update.sh").write_text("#!/usr/bin/env bash\n")
    (repo / "requirements.txt").write_text("fastapi>=0.133.0,<1.0.0\n")
    (repo / "requirements-aarch64.lock").write_text("fastapi==0.133.0 --hash=sha256:%s\n" % ("a" * 64))
    (repo / "requirements-armv7-build.lock").write_text("fastapi==0.133.0 --hash=sha256:%s\n" % ("e" * 64))
    g("add", "-A")
    g("commit", "-q", "-m", "c")
    g("tag", "v0.3.16")
    wh = base / "wh"
    wh.mkdir()
    wname = "fastapi-0.133.0-py3-none-any.whl"
    wheel = b"WHEELBYTES"
    (wh / wname).write_bytes(wheel)
    ws = hashlib.sha256(wheel).hexdigest()
    (wh / "SHA256SUMS").write_text("%s  %s\n" % (ws, wname))
    bs = R.sha256_hex(R.pack_tree(R._wheelhouse_members(str(wh))))
    prov = base / "p.json"
    prov.write_text(json.dumps({"builder": {"identity": "b", "image_digest": "sha256:" + "a" * 64},
                                "bundle": {"sha256": bs},
                                "wheels": [{"sdist_name": "fastapi-0.133.0.tar.gz", "sdist_sha256": "e" * 64,
                                            "wheel_filename": wname, "wheel_sha256": ws}]}))
    runtime = base / "requirements-armv7.lock"
    runtime.write_text("fastapi==0.133.0 --hash=sha256:%s\n" % ws)
    res = R.produce_release(version="0.3.16", out_dir=str(base / "d"), key_path=str(key),
                            wheelhouse_armv7_dir=str(wh), provenance_armv7_path=str(prov),
                            armv7_runtime_lock_path=str(runtime),
                            git_ref="v0.3.16", repo_dir=str(repo))
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
