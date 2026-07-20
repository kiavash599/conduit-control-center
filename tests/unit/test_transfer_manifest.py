# SPDX-License-Identifier: MIT
"""Repository-owned Phase-B transfer manifest (release/transfer_manifest.py).

This tool carries load-bearing security evidence and was previously an unversioned Owner-side
script -- never reviewed, linted or tested. These tests prove it INDEPENDENTLY validates every
property it records, and keep the two demonstrated exploits as explicit regressions:

  EXPLOIT 1 -- a nested foreign path (`foreign/file.bin`) was ACCEPTED, because the exact-set check
               only compared top-level names (file_count 35 instead of 34).
  EXPLOIT 2 -- a FALSE provenance tree digest was copied verbatim into the manifest binding,
               because the tool read the claim instead of recomputing it."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from release import logical_tree as LT
from release import transfer_manifest as TM

BUILT = ["cffi", "httptools", "markupsafe", "psutil", "pyyaml", "uvloop"]
REUSED = ["reusepkg%02d" % i for i in range(1, 25)]


def _bundle(tmp_path, *, wheels=None, prov_mut=None, ev_mut=None, sums_text=None,
            lock_text=None, extra=None, name="bundle"):
    """A bundle that satisfies the FULL contract, so each test perturbs exactly one property."""
    b = tmp_path / name
    wh = b / TM.WHEELHOUSE_DIR
    wh.mkdir(parents=True)
    specs = wheels if wheels is not None else (
        [(n, "1.0", "built") for n in BUILT] + [(n, "2.0", "reused") for n in REUSED])
    members, recs, sums, lock = {}, [], [], []
    for pkg, ver, origin in specs:
        fn = f"{pkg}-{ver}-py3-none-any.whl"
        data = b"WHEEL:" + pkg.encode()
        (wh / fn).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        members[f"{TM.WHEELHOUSE_DIR}/{fn}"] = data
        sums.append((fn, digest))
        lock.append(f"{pkg}=={ver} --hash=sha256:{digest}")
        if origin == "built":
            recs.append({"origin": "built", "sdist_name": f"{pkg}-{ver}.tar.gz",
                         "sdist_sha256": "a" * 64, "wheel_filename": fn, "wheel_sha256": digest})
        else:
            recs.append({"origin": "reused", "name": pkg, "version": ver,
                         "wheel_filename": fn, "wheel_sha256": digest, "tags": ["py3-none-any"]})
    sums_data = (sums_text if sums_text is not None
                 else "".join(f"{d}  {n}\n" for n, d in sorted(sums)))
    (wh / "SHA256SUMS").write_text(sums_data, newline="")
    members[TM.SHA256SUMS] = sums_data.encode()
    digest = LT.tree_digest(members)
    prov = {"bundle": {"tree_digest": {"scheme": LT.SCHEME, "sha256": digest},
                       "member_count": len(members)}, "wheels": recs}
    if prov_mut:
        prov_mut(prov)
    (b / TM.PROVENANCE).write_text(json.dumps(prov, sort_keys=True))
    ev = {"bundle_tree_sha256": digest, "tree_scheme": LT.SCHEME, "member_count": len(members),
          "wheel_count": len(specs), "built": sorted(s[0] for s in specs if s[2] == "built"),
          "reused": sorted(s[0] for s in specs if s[2] == "reused"),
          "authorizers": {}, "partition_policy_enforced": True}
    if ev_mut:
        ev_mut(ev)
    (b / TM.BUILD_EVIDENCE).write_text(json.dumps(ev, sort_keys=True))
    (b / TM.RUNTIME_LOCK).write_text(
        lock_text if lock_text is not None
        else TM.RUNTIME_LOCK_HEADER + "\n" + "\n".join(sorted(lock)) + "\n", newline="")
    for rel, data in (extra or {}).items():
        p = b / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return b, digest


def _reject(bundle, match=None):
    with pytest.raises(TM.TransferManifestError, match=match):
        TM.build_manifest(str(bundle))


# --- positive + determinism ------------------------------------------------------------------ #

def test_generate_verify_roundtrip_and_recomputed_binding(tmp_path):
    b, digest = _bundle(tmp_path)
    out = tmp_path / "m.json"
    m = TM.generate(str(b), str(out))
    assert m["schema"] == TM.SCHEMA and m["file_count"] == 34
    assert m["bind"]["tree_sha256"] == digest == LT.tree_digest(
        {p: (b / p).read_bytes() for p in
         [f"{TM.WHEELHOUSE_DIR}/{f.name}" for f in (b / TM.WHEELHOUSE_DIR).iterdir()]})
    assert m["bind"]["built_count"] == 6 and m["bind"]["reused_count"] == 24
    TM.verify(str(b), str(out))


def test_output_is_deterministic(tmp_path):
    b, _ = _bundle(tmp_path)
    assert TM.canonical_bytes(TM.build_manifest(str(b))) == TM.canonical_bytes(TM.build_manifest(str(b)))
    m = TM.build_manifest(str(b))
    banned = {"timestamp", "generated_at", "created_at", "date", "utc", "host", "hostname"}
    assert not (set(m) & banned) and not (set(m["bind"]) & banned)
    assert all(set(f) == {"path", "size", "sha256"} for f in m["files"])


def test_top_level_evidence_files_are_bound_by_size_and_sha(tmp_path):
    b, _ = _bundle(tmp_path)
    files = {f["path"]: f for f in TM.build_manifest(str(b))["files"]}
    for name in (TM.PROVENANCE, TM.RUNTIME_LOCK, TM.BUILD_EVIDENCE):
        raw = (b / name).read_bytes()
        assert files[name]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert files[name]["size"] == len(raw)


# --- EXPLOIT REGRESSIONS ----------------------------------------------------------------------- #

def test_exploit1_nested_foreign_path_rejected(tmp_path):
    # REGRESSION: previously ACCEPTED with file_count=35 -- a top-level-only check cannot see this.
    b, _ = _bundle(tmp_path, extra={"foreign/file.bin": b"SMUGGLED"})
    _reject(b, "unexpected bundle entries")


def test_exploit2_false_provenance_tree_digest_rejected(tmp_path):
    # REGRESSION: previously COPIED verbatim into bind.tree_sha256 without verification.
    b, _ = _bundle(tmp_path, prov_mut=lambda p: p["bundle"]["tree_digest"].__setitem__("sha256", "de" * 32))
    _reject(b, "RECOMPUTED")


# --- exact set, every depth -------------------------------------------------------------------- #

@pytest.mark.parametrize("extra", [
    {"foreign/file.bin": b"x"},                                   # nested foreign dir
    {"provenance/wheelhouse-armv7.json": b"x"},                   # basename matches an expected file
    {f"{TM.WHEELHOUSE_DIR}/nested/deep.whl": b"x"},               # nested under the wheelhouse
    {f"{TM.WHEELHOUSE_DIR}/RECORD": b"x"},                        # extra wheelhouse metadata
    {"stray.whl": b"x"},                                          # wheel outside the wheelhouse
    {"stowaway.txt": b"x"},                                       # extra top-level file
])
def test_rejects_every_extra_entry_at_any_depth(tmp_path, extra):
    b, _ = _bundle(tmp_path, extra=extra)
    _reject(b, "unexpected bundle entries")


@pytest.mark.parametrize("missing", [TM.PROVENANCE, TM.RUNTIME_LOCK, TM.BUILD_EVIDENCE, TM.SHA256SUMS])
def test_rejects_missing_required_file(tmp_path, missing):
    b, _ = _bundle(tmp_path)
    (b / missing).unlink()
    _reject(b)


@pytest.mark.parametrize("n", [29, 31])
def test_rejects_wrong_wheel_count(tmp_path, n):
    specs = ([(f"b{i}", "1.0", "built") for i in range(6)] +
             [(f"r{i}", "2.0", "reused") for i in range(n - 6)])
    b, _ = _bundle(tmp_path, wheels=specs)
    _reject(b)


def test_rejects_symlink_entry(tmp_path):
    b, _ = _bundle(tmp_path)
    link = b / TM.WHEELHOUSE_DIR / "linked.whl"
    try:
        link.symlink_to(b / TM.RUNTIME_LOCK)
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            pytest.skip(f"symlink creation unavailable on this Windows host: {exc}")
        raise
    _reject(b, "symlink")


# --- SHA256SUMS ---------------------------------------------------------------------------------#

def _sums_of(b):
    return (b / TM.WHEELHOUSE_DIR / "SHA256SUMS").read_text()


@pytest.mark.parametrize("mutate,match", [
    (lambda t: t + t.splitlines()[0] + "\n", "exactly 30 entries"),          # duplicate line
    (lambda t: "\n".join(t.splitlines()[:-1]) + "\n", "exactly 30 entries"),  # missing entry
    (lambda t: t.replace("a", "A", 1), "64-hex"),                             # uppercase digest
    (lambda t: t[:10] + t[12:], "64-hex"),                                    # short digest
    (lambda t: t.replace("  ", "  sub/", 1), "bare filename"),                # path-bearing name
    (lambda t: t.rstrip("\n"), "single LF"),                                  # no trailing LF
    (lambda t: "".join(reversed(t.splitlines(keepends=True))), "canonical sorted-LF"),
])
def test_sha256sums_strictness(tmp_path, mutate, match):
    b, _ = _bundle(tmp_path)
    _bundle(tmp_path, sums_text=mutate(_sums_of(b)), name="b2")
    _reject(tmp_path / "b2", match)


def test_sha256sums_conflicting_duplicate_name_rejected(tmp_path):
    b, _ = _bundle(tmp_path)
    lines = _sums_of(b).splitlines(keepends=True)
    name = lines[0].split("  ")[1].strip()
    lines[1] = f"{'b' * 64}  {name}\n"                    # same name, conflicting digest
    _bundle(tmp_path, sums_text="".join(lines), name="b2")
    _reject(tmp_path / "b2")


def test_sha256sums_invalid_utf8_rejected(tmp_path):
    b, _ = _bundle(tmp_path)
    (b / TM.SHA256SUMS).write_bytes(b"\xff\xfe not utf-8\n")
    _reject(b, "not valid UTF-8")


# --- provenance / build evidence ----------------------------------------------------------------#

@pytest.mark.parametrize("mut,match", [
    (lambda p: p["bundle"]["tree_digest"].__setitem__("scheme", "nope"), "scheme"),
    (lambda p: p["bundle"]["tree_digest"].__setitem__("sha256", "ABC"), "64-hex"),
    (lambda p: p["bundle"].__setitem__("member_count", 99), "member_count"),
    (lambda p: p["bundle"].__setitem__("extra", 1), "EXACTLY"),
    (lambda p: p["bundle"]["tree_digest"].__setitem__("algorithm", "sha256"), "EXACTLY"),
    (lambda p: p.__setitem__("wheels", p["wheels"][:-1]), "exactly 30 records"),
    (lambda p: p["wheels"][0].__setitem__("wheel_sha256", "c" * 64), "mismatch"),
    (lambda p: p["wheels"][0].__setitem__("wheel_filename", "ghost.whl"), "foreign file"),
    (lambda p: p["wheels"][0].__setitem__("origin", "other"), "built|reused"),
    (lambda p: [r.__setitem__("origin", "reused") for r in p["wheels"] if r["origin"] == "built"], None),
])
def test_provenance_defects_rejected(tmp_path, mut, match):
    b, _ = _bundle(tmp_path, prov_mut=mut)
    _reject(b, match)


def test_provenance_must_be_json_object(tmp_path):
    b, _ = _bundle(tmp_path)
    (b / TM.PROVENANCE).write_text("[1,2,3]")
    _reject(b, "must be a JSON object")


@pytest.mark.parametrize("raw,match", [
    ('{"bundle": {"a": 1, "a": 2}}', "JSON rejected"),        # duplicate keys
    ('{"bundle": NaN}', "JSON rejected"),                      # NaN
    ('{"bundle": Infinity}', "JSON rejected"),                 # Infinity
    ("{not json", "JSON rejected"),
])
def test_provenance_strict_json(tmp_path, raw, match):
    b, _ = _bundle(tmp_path)
    (b / TM.PROVENANCE).write_text(raw)
    _reject(b, match)


@pytest.mark.parametrize("mut,match", [
    (lambda e: e.__setitem__("wheel_count", 29), "wheel_count"),
    (lambda e: e.__setitem__("member_count", 30), "member_count"),
    (lambda e: e.__setitem__("tree_scheme", "nope"), "tree_scheme"),
    (lambda e: e.__setitem__("bundle_tree_sha256", "f" * 64), "!= recomputed"),
    (lambda e: e.__setitem__("built", ["wrong"]), "'built' set"),
    (lambda e: e.__setitem__("reused", ["wrong"]), "'reused' set"),
    (lambda e: e.__setitem__("partition_policy_enforced", False), "partition_policy_enforced"),
])
def test_build_evidence_disagreement_rejected(tmp_path, mut, match):
    b, _ = _bundle(tmp_path, ev_mut=mut)
    _reject(b, match)


# --- runtime lock -------------------------------------------------------------------------------#

def _lock_of(b):
    return (b / TM.RUNTIME_LOCK).read_text()


@pytest.mark.parametrize("mutate,match", [
    (lambda t: "\n".join(t.splitlines()[:-1]) + "\n", "exactly 30"),            # missing pin
    (lambda t: t + t.splitlines()[0] + "\n", "exactly 30"),                     # extra line
    (lambda t: t.replace("cffi==1.0", "cffi==9.9", 1), "version"),              # version drift
    # hash drift: replace the FIRST PIN's digest (line 1; line 0 is the canonical header)
    (lambda t: t.replace(t.splitlines()[1].split("sha256:")[1], "d" * 64, 1), "hash"),
    (lambda t: t.replace("cffi==", "ghost==", 1), "package set"),               # foreign name
    (lambda t: t.replace(" --hash=sha256:", " ", 1), "malformed"),              # malformed line
])
def test_runtime_lock_bijection(tmp_path, mutate, match):
    b, _ = _bundle(tmp_path)
    _bundle(tmp_path, lock_text=mutate(_lock_of(b)), name="b2")
    _reject(tmp_path / "b2", match)


def test_runtime_lock_duplicate_pin_rejected(tmp_path):
    """Duplicate PACKAGE PIN -- not a duplicate header. lines[0] is the canonical header, so copying
    it would only re-test header strictness (covered separately) and would never reach the
    duplicate-pin branch. Overwrite one real pin with another real pin, preserving the line count."""
    b, _ = _bundle(tmp_path)
    lines = _lock_of(b).splitlines()
    assert lines[0] == TM.RUNTIME_LOCK_HEADER          # header, deliberately untouched
    pins = lines[1:]
    assert len(pins) == TM.EXPECTED_WHEELS and len(set(pins)) == TM.EXPECTED_WHEELS
    lines[2] = lines[1]                                 # pin -> duplicate of the previous pin
    assert len(lines) == TM.EXPECTED_WHEELS + 1         # line count preserved
    _bundle(tmp_path, lock_text="\n".join(lines) + "\n", name="b2")
    with pytest.raises(TM.TransferManifestError, match="duplicate"):
        TM.build_manifest(str(tmp_path / "b2"))


# --- manifest lifecycle ------------------------------------------------------------------------ #

def test_refuses_output_inside_bundle_and_collision(tmp_path):
    b, _ = _bundle(tmp_path)
    with pytest.raises(TM.TransferManifestError, match="OUTSIDE"):
        TM.generate(str(b), str(b / "m.json"))
    out = tmp_path / "m.json"
    TM.generate(str(b), str(out))
    with pytest.raises(TM.TransferManifestError, match="already exists"):
        TM.generate(str(b), str(out))


@pytest.mark.parametrize("mutate", [
    lambda b: (b / TM.WHEELHOUSE_DIR / "cffi-1.0-py3-none-any.whl").write_bytes(b"TAMPERED"),
    lambda b: (b / TM.WHEELHOUSE_DIR / "psutil-1.0-py3-none-any.whl").unlink(),
    lambda b: (b / TM.WHEELHOUSE_DIR / "extra.whl").write_bytes(b"x"),
    lambda b: (b / TM.RUNTIME_LOCK).write_text("mutated\n"),
])
def test_verify_detects_mutation(tmp_path, mutate):
    b, _ = _bundle(tmp_path)
    out = tmp_path / "m.json"
    TM.generate(str(b), str(out))
    mutate(b)
    with pytest.raises(TM.TransferManifestError):
        TM.verify(str(b), str(out))


def test_verify_rejects_substituted_and_unreadable_manifest(tmp_path):
    b, _ = _bundle(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "ccc-phase-b-transfer-manifest-v1"}))
    with pytest.raises(TM.TransferManifestError, match="schema"):
        TM.verify(str(b), str(bad))
    with pytest.raises(TM.TransferManifestError, match="unreadable"):
        TM.verify(str(b), str(tmp_path / "nope.json"))


def test_verify_rejects_wrong_bundle(tmp_path):
    b1, _ = _bundle(tmp_path, name="b1")
    b2, _ = _bundle(tmp_path, wheels=[(f"x{i}", "1.0", "built") for i in range(6)] +
                                     [(f"y{i}", "2.0", "reused") for i in range(24)], name="b2")
    out = tmp_path / "m.json"
    TM.generate(str(b1), str(out))
    with pytest.raises(TM.TransferManifestError):
        TM.verify(str(b2), str(out))


def test_cli_generate_verify_and_failure_exit(tmp_path, capsys):
    b, _ = _bundle(tmp_path)
    out = tmp_path / "m.json"
    assert TM.main(["generate", "--bundle", str(b), "--out", str(out)]) == 0
    assert "TRANSFER_MANIFEST=GENERATED" in capsys.readouterr().out
    assert TM.main(["verify", "--bundle", str(b), "--manifest", str(out)]) == 0
    assert "TRANSFER_MANIFEST=VERIFIED" in capsys.readouterr().out
    (b / TM.RUNTIME_LOCK).write_text("tampered\n")
    assert TM.main(["verify", "--bundle", str(b), "--manifest", str(out)]) == 1
    assert "fail closed" in capsys.readouterr().err


# --- production runtime-lock FORM (canonical header) ------------------------------------------- #
# Regression: the validator previously treated every non-empty line as a pin, so the REAL producer
# output -- which begins with a canonical header line -- would have been rejected as "31 pins".
# The fixtures now emit the exact production form and the header is required, not merely skipped.

def test_accepts_the_exact_production_runtime_lock_form(tmp_path):
    b, _ = _bundle(tmp_path)
    text = (b / TM.RUNTIME_LOCK).read_text()
    assert text.splitlines()[0] == TM.RUNTIME_LOCK_HEADER
    assert len(text.splitlines()) == 1 + 30
    TM.build_manifest(str(b))                       # must not raise


@pytest.mark.parametrize("mutate,match", [
    (lambda t: "\n".join(t.splitlines()[1:]) + "\n", "canonical header"),          # header missing
    (lambda t: t.replace(TM.RUNTIME_LOCK_HEADER, "# something else", 1), "canonical header"),
    (lambda t: TM.RUNTIME_LOCK_HEADER + "\n" + t, "exactly 30 packages"),          # duplicated header
    (lambda t: "\n".join(t.splitlines()[1:2] + [TM.RUNTIME_LOCK_HEADER]
                         + t.splitlines()[2:]) + "\n", "canonical header"),        # misplaced header
    (lambda t: t.replace("\n", "\n# stray comment\n", 2), "exactly 30 packages"),  # extra comment
])
def test_runtime_lock_header_strictness(tmp_path, mutate, match):
    b, _ = _bundle(tmp_path)
    _bundle(tmp_path, lock_text=mutate((b / TM.RUNTIME_LOCK).read_text()), name="b2")
    _reject(tmp_path / "b2", match)


# --- directory layout (empty foreign directories are invisible to a file-only collector) -------- #

def test_rejects_empty_foreign_directory(tmp_path):
    b, _ = _bundle(tmp_path)
    (b / "empty-foreign").mkdir()                   # contributes NO files
    _reject(b, "exactly one directory")


def test_rejects_empty_nested_directory_under_wheelhouse(tmp_path):
    b, _ = _bundle(tmp_path)
    (b / TM.WHEELHOUSE_DIR / "nested-empty").mkdir()
    _reject(b, "exactly one directory")


# --- malformed RECORDED manifests must fail closed (never an internal exception) ----------------- #
#
# The recorded document is UNTRUSTED input. Before this hardening, verify() built its mismatch
# diagnostic straight out of `recorded["files"]`, so a schema-correct manifest with `"files": 1`
# raised `TypeError: 'int' object is not iterable` instead of TransferManifestError, and the CLI
# would have surfaced a traceback rather than the controlled fail-closed message.

def _good_manifest(tmp_path):
    b, _ = _bundle(tmp_path)
    out = tmp_path / "m.json"
    TM.generate(str(b), str(out))
    return b, out, json.loads(out.read_text())


def _write(out, doc):
    out.write_bytes(TM.canonical_bytes(doc))


_MALFORMED = [
    ("files_int",            lambda d: d.update(files=1)),
    ("files_null",           lambda d: d.update(files=None)),
    ("files_object",         lambda d: d.update(files={"a": 1})),
    ("files_string",         lambda d: d.update(files="wheelhouse-armhf")),
    ("files_bool",           lambda d: d.update(files=True)),
    ("entry_not_object",     lambda d: d["files"].__setitem__(0, "nope")),
    ("entry_is_list",        lambda d: d["files"].__setitem__(0, [1, 2, 3])),
    ("entry_is_int",         lambda d: d["files"].__setitem__(0, 7)),
    ("missing_path",         lambda d: d["files"][0].pop("path")),
    ("missing_size",         lambda d: d["files"][0].pop("size")),
    ("missing_sha256",       lambda d: d["files"][0].pop("sha256")),
    ("path_is_int",          lambda d: d["files"][0].update(path=5)),
    ("path_is_list",         lambda d: d["files"][0].update(path=["a"])),      # unhashable
    ("path_is_object",       lambda d: d["files"][0].update(path={"a": 1})),   # unhashable
    ("path_is_null",         lambda d: d["files"][0].update(path=None)),
    ("size_is_string",       lambda d: d["files"][0].update(size="12")),
    ("size_is_bool",         lambda d: d["files"][0].update(size=True)),
    ("size_is_negative",     lambda d: d["files"][0].update(size=-1)),
    ("sha256_is_int",        lambda d: d["files"][0].update(sha256=1)),
    ("sha256_is_null",       lambda d: d["files"][0].update(sha256=None)),
    ("duplicate_path",       lambda d: d["files"].__setitem__(1, dict(d["files"][0]))),
    ("bind_int",             lambda d: d.update(bind=1)),
    ("bind_null",            lambda d: d.update(bind=None)),
    ("bind_list",            lambda d: d.update(bind=[])),
    ("bind_scheme_int",      lambda d: d["bind"].update(tree_scheme=1)),
    ("bind_digest_null",     lambda d: d["bind"].update(tree_sha256=None)),
    ("bind_count_string",    lambda d: d["bind"].update(wheel_count="30")),
    ("bind_count_bool",      lambda d: d["bind"].update(wheel_count=True)),
    ("bind_missing_key",     lambda d: d["bind"].pop("runtime_lock")),
    ("file_count_string",    lambda d: d.update(file_count="34")),
    ("file_count_null",      lambda d: d.update(file_count=None)),
]


@pytest.mark.parametrize("label,mutate", _MALFORMED, ids=[c[0] for c in _MALFORMED])
def test_malformed_recorded_manifest_fails_closed(tmp_path, label, mutate):
    b, out, doc = _good_manifest(tmp_path)
    mutate(doc)
    _write(out, doc)
    # API: TransferManifestError only -- no TypeError/KeyError/AttributeError may escape.
    with pytest.raises(TM.TransferManifestError):
        TM.verify(str(b), str(out))


@pytest.mark.parametrize("label,mutate", _MALFORMED, ids=[c[0] for c in _MALFORMED])
def test_malformed_recorded_manifest_cli_exit_1_no_traceback(tmp_path, capsys, label, mutate):
    b, out, doc = _good_manifest(tmp_path)
    mutate(doc)
    _write(out, doc)
    assert TM.main(["verify", "--bundle", str(b), "--manifest", str(out)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: transfer manifest failed (fail closed):")
    assert "Traceback" not in err


def test_the_exact_reported_files_is_one_case(tmp_path):
    """The literal reproduction from the finding: schema correct, files == 1."""
    b, out, doc = _good_manifest(tmp_path)
    doc["files"] = 1
    _write(out, doc)
    with pytest.raises(TM.TransferManifestError, match="'files' must be a list"):
        TM.verify(str(b), str(out))


def test_noncanonical_recorded_bytes_still_rejected(tmp_path):
    """Shape validation must NOT become a way to accept a non-byte-identical document."""
    b, out, doc = _good_manifest(tmp_path)
    out.write_bytes(json.dumps(doc, indent=2).encode())     # well-formed, correct shape, not canonical
    with pytest.raises(TM.TransferManifestError, match="does not match"):
        TM.verify(str(b), str(out))


def test_wellformed_manifest_with_one_changed_size_reports_a_clean_diagnostic(tmp_path):
    """The diagnostic path itself still works on a shape-valid but mismatched manifest."""
    b, out, doc = _good_manifest(tmp_path)
    doc["files"][0]["size"] = doc["files"][0]["size"] + 1
    _write(out, doc)
    with pytest.raises(TM.TransferManifestError, match="changed="):
        TM.verify(str(b), str(out))
