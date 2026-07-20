# SPDX-License-Identifier: MIT
"""Active-input co-producer (release/builder/gen_active_inputs): hash-gated inputs, deterministic
6+24=30 generation, ordered-tag selection, the REAL six-sdist LIST schema
({package, filename, sha256, size, url}), and fail-closed paths. Uses SYNTHETIC metadata/locks
(no network, no real PyPI)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib

import pytest

from release import ccc_release as R

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("gen_mod", str(_ROOT / "release" / "builder" / "gen_active_inputs.py"))
GEN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GEN)
_TAGS_PATH = _ROOT / "release" / "builder" / "target-supported-tags.txt"

BUILT = sorted(R.WHEELHOUSE_SOURCE_BUILD_PACKAGES)
REUSED = ["reusepkg%02d" % i for i in range(1, 25)]
_FILE_HOST = "files.pythonhosted.org"


def _sdist_url(name):
    return f"https://{_FILE_HOST}/packages/src/{name}-1.0.tar.gz"


def _pkg_meta(name, *, sdist=None, sdist_size=None, sdist_url=None, wheels=()):
    urls = []
    if sdist is not None:
        urls.append({"filename": "%s-1.0.tar.gz" % name, "packagetype": "sdist",
                     "digests": {"sha256": sdist}, "size": sdist_size, "url": sdist_url,
                     "yanked": False, "requires_python": ">=3.9"})
    for wf, sha, rp, yanked in wheels:
        urls.append({"filename": wf, "packagetype": "bdist_wheel", "digests": {"sha256": sha},
                     "requires_python": rp, "yanked": yanked, "url": "https://" + _FILE_HOST + "/x/" + wf})
    return {"raw_metadata_json": json.dumps({"info": {"name": name, "version": "1.0"}, "urls": urls})}


def _six_list():
    """The REAL six-record shape: a JSON LIST of {package, filename, sha256, size, url}."""
    recs = []
    for n in BUILT:
        recs.append({"package": n, "filename": "%s-1.0.tar.gz" % n,
                     "sha256": hashlib.sha256(("SD:" + n).encode()).hexdigest(),
                     "size": 1000 + len(n), "url": _sdist_url(n)})
    return recs


def _inputs(tmp_path, *, reused_wheels=None, six_list=None):
    """Build synthetic metadata + solution lock + six-record LIST. Returns paths + expected shas."""
    six = six_list if six_list is not None else _six_list()
    by_pkg = {r["package"]: r for r in six}
    packages = {}
    for n in BUILT:
        r = by_pkg[n]
        packages[n] = _pkg_meta(n, sdist=r["sha256"], sdist_size=r["size"], sdist_url=r["url"])
    for n in REUSED:
        wf = "%s-1.0-py3-none-any.whl" % n
        wsha = hashlib.sha256(("W:" + n).encode()).hexdigest()
        wheels = reused_wheels(n, wf, wsha) if reused_wheels else [(wf, wsha, ">=3.9", False)]
        packages[n] = _pkg_meta(n, wheels=wheels)
    md = tmp_path / "meta.json"
    md.write_text(json.dumps({"packages": packages}))
    sol_lines = ["%s==1.0 --hash=sha256:%s" % (r["package"], r["sha256"]) for r in six]
    sol_lines += ["%s==1.0 --hash=sha256:%s" % (n, "d" * 64) for n in REUSED]
    sol = tmp_path / "solution.lock"
    sol.write_text("\n".join(sol_lines) + "\n")
    six_rec = tmp_path / "six.json"
    six_rec.write_text(json.dumps(six))

    def h(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    return {"meta": str(md), "meta_sha": h(md), "sol": str(sol), "sol_sha": h(sol),
            "six": str(six_rec), "six_sha": h(six_rec),
            "tags": str(_TAGS_PATH), "tags_sha": h(_TAGS_PATH)}


def _run(tmp_path, ip, out="bundle"):
    return GEN.generate(metadata_path=ip["meta"], metadata_sha=ip["meta_sha"],
                        tags_path=ip["tags"], tags_sha=ip["tags_sha"],
                        six_record_path=ip["six"], six_record_sha=ip["six_sha"],
                        solution_lock_path=ip["sol"], solution_lock_sha=ip["sol_sha"],
                        out_bundle=str(tmp_path / out))


def test_generate_deterministic_6_24_30(tmp_path):
    ip = _inputs(tmp_path)
    rec = _run(tmp_path, ip)
    assert rec["partition"]["counts"] == {"built": 6, "reused": 24, "total": 30}
    bundle = tmp_path / "bundle"
    bl = [ln for ln in (bundle / "requirements-armv7-build.lock").read_text().splitlines()
          if ln and not ln.startswith("#")]
    assert sorted(x.split("==")[0] for x in bl) == BUILT
    authz = json.loads((bundle / "armv7-reuse-authz.json").read_text())
    assert len(authz["wheels"]) == 24
    assert (bundle / "generation-record.json").is_file()


def test_input_hash_mismatch_fails_closed(tmp_path):
    ip = _inputs(tmp_path)
    ip["meta_sha"] = "0" * 64
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


def test_six_record_must_be_list_not_dict(tmp_path):
    # regression F1: a DICT-shaped six-record (the old, wrong schema) must fail closed.
    ip = _inputs(tmp_path)
    as_dict = {r["package"]: r for r in _six_list()}
    pathlib.Path(ip["six"]).write_text(json.dumps(as_dict))
    ip["six_sha"] = hashlib.sha256(pathlib.Path(ip["six"]).read_bytes()).hexdigest()
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


def test_six_record_sha_mismatch_fails_closed(tmp_path):
    ip = _inputs(tmp_path)
    six = json.loads(pathlib.Path(ip["six"]).read_text())
    for r in six:
        if r["package"] == "cffi":
            r["sha256"] = "9" * 64                       # not authorized by the solution lock
    pathlib.Path(ip["six"]).write_text(json.dumps(six))
    ip["six_sha"] = hashlib.sha256(pathlib.Path(ip["six"]).read_bytes()).hexdigest()
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


def test_six_record_size_mismatch_fails_closed(tmp_path):
    ip = _inputs(tmp_path)
    six = json.loads(pathlib.Path(ip["six"]).read_text())
    for r in six:
        if r["package"] == "psutil":
            r["size"] = r["size"] + 1                    # disagrees with official metadata size
    pathlib.Path(ip["six"]).write_text(json.dumps(six))
    ip["six_sha"] = hashlib.sha256(pathlib.Path(ip["six"]).read_bytes()).hexdigest()
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


def test_six_record_url_off_origin_fails_closed(tmp_path):
    six = _six_list()
    for r in six:
        if r["package"] == "uvloop":
            r["url"] = "https://evil.example.com/x/uvloop-1.0.tar.gz"
    ip = _inputs(tmp_path, six_list=six)                 # metadata url tracks the record url
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


@pytest.mark.parametrize("value", [
    "CFFI",          # noncanonical case
    "c_ffi",         # noncanonical separator that normalizes into an approved name
    "cffi ",         # trailing whitespace
    123,             # non-string (previously coerced via str())
    None,            # non-string
    "",              # empty
])
def test_six_record_package_must_be_canonical(tmp_path, value):
    # The acquisition record is security-relevant evidence: it must have exactly ONE serialized
    # identity, not merely normalize into an approved one. Metadata + solution stay canonical, so the
    # ONLY defect under test is the package spelling/type in the record itself.
    ip = _inputs(tmp_path)
    six = json.loads(pathlib.Path(ip["six"]).read_text())
    for r in six:
        if r["package"] == "cffi":
            r["package"] = value
    pathlib.Path(ip["six"]).write_text(json.dumps(six))
    ip["six_sha"] = hashlib.sha256(pathlib.Path(ip["six"]).read_bytes()).hexdigest()
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


def test_zero_candidate_fails_closed(tmp_path):
    # a reused package whose only wheel is x86_64 -> no target-compatible candidate
    def rw(n, wf, wsha):
        if n == "reusepkg05":
            bad = "%s-1.0-cp310-cp310-manylinux_2_31_x86_64.whl" % n
            return [(bad, hashlib.sha256(bad.encode()).hexdigest(), ">=3.9", False)]
        return [(wf, wsha, ">=3.9", False)]
    ip = _inputs(tmp_path, reused_wheels=rw)
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


def test_yanked_and_incompatible_requires_python_rejected(tmp_path):
    def rw(n, wf, wsha):
        if n == "reusepkg07":                            # only candidate is yanked
            return [(wf, wsha, ">=3.9", True)]
        if n == "reusepkg08":                            # only candidate excludes 3.10
            return [(wf, wsha, ">=3.11", False)]
        return [(wf, wsha, ">=3.9", False)]
    ip = _inputs(tmp_path, reused_wheels=rw)
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)


def test_refuses_preexisting_out_bundle(tmp_path):
    ip = _inputs(tmp_path)
    (tmp_path / "bundle").mkdir()
    with pytest.raises(GEN.GenError):
        _run(tmp_path, ip)
