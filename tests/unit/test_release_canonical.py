# SPDX-License-Identifier: MIT
"""ADR-0003 Epic A — Release canonicalization tests.

Proves the Canonical Release Artifact is a property of the ARTIFACT, not of the
producing OS/checkout:

  * text files are normalised to LF (the 0.3.13 CRLF-contamination class of bug);
  * binary / uncertain files are left BYTE-EXACT (never corrupted);
  * `.gitattributes` rules are honoured (explicit binary stays CRLF; eol=lf wins);
  * packing is deterministic (same content -> identical bytes);
  * a Git-ref build and a CRLF-contaminated --source build of the same content
    CONVERGE to byte-identical artifacts (the platform-independence proof).

Git-dependent cases are skipped only if `git` is unavailable.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from release import ccc_release as R

_HAS_GIT = shutil.which("git") is not None
_needs_git = pytest.mark.skipif(not _HAS_GIT, reason="git not available")


# --- .gitattributes parsing & classification ------------------------------- #

def test_parse_gitattributes_subset():
    rules = R.parse_gitattributes(
        "# comment\n"
        "* text=auto\n"
        "*.sh text eol=lf\n"
        "deployment/* text eol=lf\n"
        "*.png binary\n"
        "notes.txt -text\n"
    )
    assert R.attrs_for("x.sh", rules).get("text") is True
    assert R.attrs_for("x.sh", rules).get("eol") == "lf"
    assert R.attrs_for("deployment/conduit.service", rules).get("text") is True
    assert R.attrs_for("deployment/conduit.service", rules).get("eol") == "lf"
    assert R.attrs_for("img/logo.png", rules).get("text") is False
    assert R.attrs_for("notes.txt", rules).get("text") is False
    # a plain undeclared file only matches the `* text=auto` catch-all
    assert R.attrs_for("backend/main.py", rules).get("text") == "auto"


def test_is_text_explicit_and_sniff():
    rules = R.parse_gitattributes("* text=auto\ndeployment/* text eol=lf\n*.png binary\n")
    # explicit eol=lf -> text even though bytes contain CRLF
    assert R.is_text("deployment/conduit.service", b"A=0\r\n", rules) is True
    # explicit binary -> never text, even if it looks textual
    assert R.is_text("img/logo.png", b"plain-text", rules) is False
    # text=auto + NUL byte -> binary (left alone)
    assert R.is_text("data.bin", b"\x00\x01\x02", rules) is False
    # text=auto + no NUL -> text (LF-normalised)
    assert R.is_text("backend/main.py", b"x = 1\r\n", rules) is True


def test_to_lf_normalises_crlf_and_cr():
    assert R._to_lf(b"a\r\nb\rc\n") == b"a\nb\nc\n"


# --- canonicalize_tree ----------------------------------------------------- #

def test_canonicalize_text_becomes_lf():
    raw = {
        ".gitattributes": b"* text=auto\ndeployment/* text eol=lf\n",
        "deployment/conduit.service": b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\r\n",
        "backend/main.py": b"x = 1\r\n",
    }
    canon = R.canonicalize_tree(raw)
    assert canon["deployment/conduit.service"] == b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\n"
    assert b"\r" not in canon["deployment/conduit.service"]
    assert canon["backend/main.py"] == b"x = 1\n"


def test_canonicalize_leaves_binary_byte_exact():
    # a binary blob that happens to contain 0d0a must NOT be rewritten
    blob = b"\x89PNG\r\n\x1a\n\x00\x01\r\n\x02"
    raw = {".gitattributes": b"* text=auto\n*.png binary\n", "img/logo.png": blob}
    canon = R.canonicalize_tree(raw)
    assert canon["img/logo.png"] == blob            # untouched


def test_canonicalize_undeclared_binary_sniff_preserved():
    # no rule at all for a NUL-bearing file -> sniff -> binary -> untouched
    blob = b"MZ\x00\x00\r\n\r\n"
    raw = {"tool.exe": blob}
    canon = R.canonicalize_tree(raw)
    assert canon["tool.exe"] == blob


def test_pack_tree_is_deterministic():
    mapping = {"a.txt": b"hello\n", "b/c.txt": b"world\n"}
    a1 = R.pack_tree(mapping)
    a2 = R.pack_tree(dict(reversed(list(mapping.items()))))  # insertion order differs
    assert a1 == a2
    assert a1[:2] == b"\x1f\x8b"
    assert R.sha256_hex(a1) == R.sha256_hex(a2)


# --- Git object-DB producer + convergence ---------------------------------- #

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo):
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


@_needs_git
def test_git_ref_build_normalises_lf(tmp_path):
    repo = tmp_path / "repo"
    (repo / "deployment").mkdir(parents=True)
    (repo / ".gitattributes").write_bytes(b"* text=auto\ndeployment/* text eol=lf\n")
    (repo / "deployment" / "conduit.service").write_bytes(
        b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\n"
    )
    _init_repo(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    # corrupt the WORKING TREE to CRLF; the canonical git-ref build must ignore it
    (repo / "deployment" / "conduit.service").write_bytes(
        b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\r\n"
    )
    raw = R._raw_from_git_ref("HEAD", str(repo))
    assert b"\r" not in raw["deployment/conduit.service"]  # object DB is LF
    canon = R.canonicalize_tree(raw)
    assert canon["deployment/conduit.service"] == b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\n"


@_needs_git
def test_gitref_and_crlf_source_converge(tmp_path):
    """The platform-independence proof: a canonical --git-ref build and a
    CRLF-contaminated --source build of the same commit yield byte-identical
    artifacts and the same content digest."""
    repo = tmp_path / "repo"
    (repo / "deployment").mkdir(parents=True)
    (repo / "img").mkdir(parents=True)
    (repo / ".gitattributes").write_bytes(b"* text=auto\ndeployment/* text eol=lf\n*.png binary\n")
    (repo / "deployment" / "conduit.service").write_bytes(
        b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\n--max-personal-clients ${CCC_MAX_PERSONAL_CLIENTS}\n"
    )
    (repo / "backend_version.py").write_bytes(b'APP_VERSION = "0.3.13"\n')
    png = b"\x89PNG\r\n\x1a\n\x00binary\r\ncontent\x00"
    (repo / "img" / "logo.png").write_bytes(png)
    _init_repo(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    # Simulate a Windows CRLF checkout of the text file in the working tree.
    (repo / "deployment" / "conduit.service").write_bytes(
        b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\r\n--max-personal-clients ${CCC_MAX_PERSONAL_CLIENTS}\r\n"
    )

    from_ref = R.build_canonical_artifact_from_git_ref("HEAD", str(repo))
    from_src = R.build_deterministic_artifact(str(repo))  # excludes .git, reads CRLF working tree

    assert from_ref == from_src                      # byte-identical -> platform independent
    assert R.sha256_hex(from_ref) == R.sha256_hex(from_src)

    # and the binary survived intact through both paths
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(from_ref)) as tar:
        got = tar.extractfile("img/logo.png").read()
    assert got == png


# --- real-repository pattern regression ------------------------------------ #

def test_real_repo_deployment_rule_normalises_conduit_service():
    """Regression guard using the ACTUAL repository `.gitattributes` rule.

    The project pins `deployment/* text eol=lf`; a parser regression that stopped
    honouring it would silently reintroduce the 0.3.13 CRLF contamination of
    `deployment/conduit.service`. This asserts the rule exists in the repo file
    and that it LF-normalises the real unit path.
    """
    import os

    root = os.path.dirname(os.path.abspath(__file__))
    cand = None
    for _ in range(6):
        c = os.path.join(root, ".gitattributes")
        if os.path.isfile(c):
            cand = c
            break
        root = os.path.dirname(root)
    assert cand, "repository .gitattributes not found from test location"

    gitattributes = open(cand, "rb").read()
    assert b"deployment/* text eol=lf" in gitattributes  # the real project rule

    raw = {
        ".gitattributes": gitattributes,
        "deployment/conduit.service": b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\r\n",
    }
    canon = R.canonicalize_tree(raw)
    assert canon["deployment/conduit.service"] == b"Environment=CCC_MAX_PERSONAL_CLIENTS=0\n"
    assert b"\r" not in canon["deployment/conduit.service"]
