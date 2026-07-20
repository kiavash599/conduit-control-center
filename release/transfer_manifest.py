#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""release/transfer_manifest.py -- repository-owned Phase-B bundle transfer manifest.

STDLIB ONLY, deliberately: `generate` runs on the RPi2 Phase-B host (whose python path is
stdlib-only by contract -- `read_builder_inputs.py` imports only `oci_manifest`) and `verify` runs
at the Owner-PC release-production boundary. Both ends execute the SAME committed code instead of
an unversioned external script carrying load-bearing security evidence.

    python3 -m release.transfer_manifest generate --bundle <dir> --out <manifest.json>
    python  -m release.transfer_manifest verify   --bundle <dir> --manifest <manifest.json>

SECURITY BOUNDARY -- what this tool INDEPENDENTLY PROVES from the transferred bytes alone
  * EXACT bundle set at EVERY depth: exactly 30 direct `wheelhouse-armhf/*.whl`, exactly
    `wheelhouse-armhf/SHA256SUMS`, and exactly the three top-level evidence files. Any extra file
    or directory at any depth (`foreign/file.bin`, nested content under an expected name, extra
    wheelhouse metadata, wheels outside the wheelhouse) is rejected, as are symlinks and every
    non-regular entry.
  * The Logical Tree Digest is RECOMPUTED here with the committed `release.logical_tree` over the
    exact 31-member mapping built from collected bytes. The value recorded in this manifest is the
    RECOMPUTED one; a provenance claim is never copied through unverified.
  * SHA256SUMS is a canonical exact bijection with the 30 wheels (count, lowercase 64-hex digests,
    one entry per wheel, no duplicates/foreign/path-bearing names, digests recomputed from bytes,
    and the exact canonical sorted-LF representation Phase B emits).
  * Provenance format-3 bundle shape; its wheel records describe exactly the 30 wheels with
    matching hashes; the 6-built / 24-reused partition is disjoint and totals 30.
  * build-evidence agrees with provenance on digest, scheme, member count, wheel count, and the
    built/reused sets, and records the partition policy as enforced.
  * The runtime lock is an exact (name, version, wheel-hash) bijection with the 30 wheels.
  * All malformed input fails CLOSED as TransferManifestError -- strict UTF-8, JSON objects only,
    duplicate JSON keys and NaN/Infinity rejected at every depth (via the committed stdlib-only
    `oci_manifest.strict_json_loads`). No traceback escapes.

DELIBERATELY NOT PROVED HERE (owned by the release producer, which can see the committed repo):
  agreement with the committed locks/requirements, the image-context binding, and tag/source
  binding. This tool describes and binds the TRANSFER; it does not replace producer validation.

Output is DETERMINISTIC canonical bytes -- no timestamp, host, or free-running field -- so
`generate` is reproducible and `verify` is a single byte-for-byte comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from release import logical_tree as _ltree  # noqa: E402  (stdlib-only; recomputes the identity)
from release import oci_manifest as _ocim  # noqa: E402  (stdlib-only strict JSON: dup keys + NaN)

SCHEMA = "ccc-phase-b-transfer-manifest-v2"
WHEELHOUSE_DIR = "wheelhouse-armhf"
SHA256SUMS = f"{WHEELHOUSE_DIR}/SHA256SUMS"
PROVENANCE = "wheelhouse-armv7.json"
RUNTIME_LOCK = "requirements-armv7.lock"
BUILD_EVIDENCE = "build-evidence.json"
EXPECTED_WHEELS = 30
EXPECTED_BUILT = 6
EXPECTED_REUSED = 24
EXPECTED_MEMBERS = EXPECTED_WHEELS + 1              # 30 wheels + SHA256SUMS
EXPECTED_TOP_LEVEL = ("build-evidence.json", "requirements-armv7.lock", "wheelhouse-armv7.json")
EXPECTED_FILES = EXPECTED_MEMBERS + len(EXPECTED_TOP_LEVEL)      # 34
# THE canonical runtime-lock header. Defined here (stdlib-only) and imported by
# release/build_wheelhouse.py, so the producer and this validator cannot drift apart.
RUNTIME_LOCK_HEADER = "# GENERATED from the final validated wheelhouse -- DO NOT hand-edit."

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LOCK_LINE = re.compile(r"^([^\s=]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$")


class TransferManifestError(RuntimeError):
    """Raised on any transfer-manifest violation (fail closed)."""


def _text(data: bytes, what: str) -> str:
    try:
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise TransferManifestError(f"{what} is not valid UTF-8: {exc}") from exc


def _json_object(data: bytes, what: str) -> dict:
    """Strict JSON: duplicate keys at ANY depth and NaN/Infinity rejected; must be an object."""
    try:
        obj = _ocim.strict_json_loads(_text(data, what))
    except _ocim.ManifestError as exc:
        raise TransferManifestError(f"{what} JSON rejected: {exc}") from exc
    if not isinstance(obj, dict):
        raise TransferManifestError(f"{what} must be a JSON object, got {type(obj).__name__}")
    return obj


def _hex64(value: object, what: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise TransferManifestError(f"{what} must be a canonical lowercase 64-hex sha256: {value!r}")
    return value


def _collect(bundle: str):
    """Return (files, dirs): {relative '/'-path -> exact bytes} and the set of relative directory
    paths. Directories are returned so exact-set validation can reject EMPTY foreign directories,
    which record no files and would otherwise be invisible. Rejects every non-regular entry.

    os.scandir with follow_symlinks=False makes BOTH predicates false for symlinks and for every
    non-regular entry (FIFO/socket/device), portably on Windows and Linux."""
    base = os.path.abspath(bundle)
    if os.path.islink(base) or not os.path.isdir(base):
        raise TransferManifestError(f"bundle dir not found or is a symlink: {bundle!r}")
    out: dict = {}
    dirs: set = set()

    def scan(directory: str, prefix: str) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError as exc:
            raise TransferManifestError(f"unreadable directory {directory!r}: {exc}") from exc
        for entry in entries:
            rel = f"{prefix}{entry.name}"
            try:
                if entry.is_symlink():
                    raise TransferManifestError(f"symlink rejected: {rel}")
                if entry.is_dir(follow_symlinks=False):
                    dirs.add(rel)                      # recorded even when it contains no files
                    scan(entry.path, f"{rel}/")
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise TransferManifestError(f"non-regular entry rejected: {rel}")
                with open(entry.path, "rb") as fh:
                    out[rel] = fh.read()
            except OSError as exc:
                raise TransferManifestError(f"unreadable entry {rel}: {exc}") from exc

    scan(base, "")
    if not out:
        raise TransferManifestError("bundle is empty")
    return out, dirs


def _exact_set(members: dict, dirs: set) -> list:
    """Enforce the EXACT bundle shape at every depth. Returns the sorted 30 wheel paths.

    Every collected path must be either one of the three top-level evidence files, or a DIRECT
    child of wheelhouse-armhf/. This is what rejects nested foreign paths ('foreign/file.bin'),
    nested content under an expected name, extra wheelhouse metadata, and wheels stored elsewhere
    -- a top-level-only comparison silently admits all of them."""
    wheels, unexpected = [], []
    wh_prefix = f"{WHEELHOUSE_DIR}/"
    for path in sorted(members):
        if path in EXPECTED_TOP_LEVEL:
            continue
        if path.startswith(wh_prefix):
            leaf = path[len(wh_prefix):]
            if "/" in leaf:                                   # nested under the wheelhouse
                unexpected.append(path)
            elif leaf == "SHA256SUMS":
                continue
            elif leaf.endswith(".whl"):
                wheels.append(path)
            else:
                unexpected.append(path)                       # extra wheelhouse metadata
        else:
            unexpected.append(path)                           # foreign top-level OR nested path
    if unexpected:
        raise TransferManifestError(
            f"unexpected bundle entries (exact set required): {sorted(unexpected)}")
    missing = [p for p in (*EXPECTED_TOP_LEVEL, SHA256SUMS) if p not in members]
    if missing:
        raise TransferManifestError(f"missing required bundle files: {missing}")
    if len(wheels) != EXPECTED_WHEELS:
        raise TransferManifestError(f"expected exactly {EXPECTED_WHEELS} wheels, got {len(wheels)}")
    if len(members) != EXPECTED_FILES:
        raise TransferManifestError(
            f"bundle must contain EXACTLY {EXPECTED_FILES} files, got {len(members)}")
    # The ONLY directory in the bundle is the wheelhouse. Any other directory -- including an EMPTY
    # one, which contributes no files -- is rejected.
    if dirs != {WHEELHOUSE_DIR}:
        raise TransferManifestError(
            f"bundle must contain exactly one directory ({WHEELHOUSE_DIR!r}); "
            f"unexpected={sorted(dirs - {WHEELHOUSE_DIR})}, missing={sorted({WHEELHOUSE_DIR} - dirs)}")
    return wheels


def _check_sha256sums(members: dict, wheels: list) -> None:
    """Canonical exact bijection between SHA256SUMS and the 30 wheel files."""
    text = _text(members[SHA256SUMS], SHA256SUMS)
    lines = text.split("\n")
    if not lines or lines[-1] != "":
        raise TransferManifestError(f"{SHA256SUMS} must end with a single LF (canonical form)")
    lines = lines[:-1]
    if len(lines) != EXPECTED_WHEELS:
        raise TransferManifestError(
            f"{SHA256SUMS} must have exactly {EXPECTED_WHEELS} entries, got {len(lines)}")
    seen, pairs = set(), []
    for line in lines:
        parts = line.split("  ")
        if len(parts) != 2 or not parts[1]:
            raise TransferManifestError(f"malformed {SHA256SUMS} line: {line!r}")
        digest, name = _hex64(parts[0], f"{SHA256SUMS} digest"), parts[1]
        if "/" in name or "\\" in name or name != name.strip():
            raise TransferManifestError(f"{SHA256SUMS} name must be a bare filename: {name!r}")
        if name in seen:
            raise TransferManifestError(f"duplicate {SHA256SUMS} entry: {name!r}")
        seen.add(name)
        pairs.append((digest, name))
    wheel_names = [p[len(WHEELHOUSE_DIR) + 1:] for p in wheels]
    if sorted(seen) != sorted(wheel_names):
        raise TransferManifestError(
            f"{SHA256SUMS} does not describe exactly the {EXPECTED_WHEELS} wheels "
            f"(missing={sorted(set(wheel_names) - seen)}, foreign={sorted(seen - set(wheel_names))})")
    by_name = {n: d for d, n in pairs}
    for path, name in zip(wheels, wheel_names):
        actual = hashlib.sha256(members[path]).hexdigest()
        if by_name[name] != actual:
            raise TransferManifestError(f"{SHA256SUMS} digest mismatch for {name}")
    # Exact canonical representation Phase B emits: sorted by filename, "<sha>  <name>\n".
    canonical = "".join(f"{by_name[n]}  {n}\n" for n in sorted(wheel_names))
    if text != canonical:
        raise TransferManifestError(
            f"{SHA256SUMS} is not in canonical sorted-LF form")


def _check_provenance(members: dict, wheels: list, recomputed: str) -> dict:
    """Format-3 bundle shape + wheel records describing exactly the 30 wheels + 6/24 partition."""
    prov = _json_object(members[PROVENANCE], PROVENANCE)
    bundle = prov.get("bundle")
    if not isinstance(bundle, dict):
        raise TransferManifestError("provenance.bundle must be an object")
    if set(bundle) != {"tree_digest", "member_count"}:
        raise TransferManifestError(
            f"provenance.bundle must have EXACTLY {{'tree_digest','member_count'}}; got {sorted(bundle)}")
    td = bundle["tree_digest"]
    if not isinstance(td, dict) or set(td) != {"scheme", "sha256"}:
        raise TransferManifestError(
            "provenance.bundle.tree_digest must have EXACTLY {'scheme','sha256'}")
    if td["scheme"] != _ltree.SCHEME:
        raise TransferManifestError(f"unsupported tree_digest scheme: {td['scheme']!r}")
    value = _hex64(td["sha256"], "provenance tree_digest.sha256")
    if value != recomputed:
        raise TransferManifestError(
            f"provenance tree_digest.sha256 {value} != RECOMPUTED {recomputed} "
            "(the transferred bytes do not produce the claimed identity)")
    count = bundle["member_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count != EXPECTED_MEMBERS:
        raise TransferManifestError(
            f"provenance member_count must be {EXPECTED_MEMBERS}; got {count!r}")

    records = prov.get("wheels")
    if not isinstance(records, list) or len(records) != EXPECTED_WHEELS:
        raise TransferManifestError(
            f"provenance.wheels must be a list of exactly {EXPECTED_WHEELS} records")
    wheel_names = {p[len(WHEELHOUSE_DIR) + 1:] for p in wheels}
    built, reused, seen, triples = set(), set(), set(), {}
    for rec in records:
        if not isinstance(rec, dict):
            raise TransferManifestError("provenance wheel record must be an object")
        fn, origin = rec.get("wheel_filename"), rec.get("origin")
        if fn not in wheel_names:
            raise TransferManifestError(f"provenance wheel record names a foreign file: {fn!r}")
        if fn in seen:
            raise TransferManifestError(f"duplicate provenance wheel record: {fn!r}")
        seen.add(fn)
        digest = _hex64(rec.get("wheel_sha256"), f"provenance wheel_sha256 for {fn!r}")
        actual = hashlib.sha256(members[f"{WHEELHOUSE_DIR}/{fn}"]).hexdigest()
        if digest != actual:
            raise TransferManifestError(f"provenance wheel_sha256 mismatch for {fn!r}")
        if origin == "built":
            sdist = rec.get("sdist_name")
            if not isinstance(sdist, str) or not sdist.endswith(".tar.gz") or "-" not in sdist:
                raise TransferManifestError(f"built record has a malformed sdist_name: {sdist!r}")
            stem = sdist[: -len(".tar.gz")]
            name, _, version = stem.rpartition("-")
            built.add(name)
        elif origin == "reused":
            name, version = rec.get("name"), rec.get("version")
            if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
                raise TransferManifestError(f"reused record has a malformed name/version: {fn!r}")
            reused.add(name)
        else:
            raise TransferManifestError(f"provenance wheel record origin must be built|reused: {origin!r}")
        triples[name] = (version, digest)
    if seen != wheel_names:
        raise TransferManifestError("provenance wheel records do not describe exactly the 30 wheels")
    if built & reused:
        raise TransferManifestError(f"built/reused overlap: {sorted(built & reused)}")
    if len(built) != EXPECTED_BUILT or len(reused) != EXPECTED_REUSED:
        raise TransferManifestError(
            f"partition must be {EXPECTED_BUILT} built + {EXPECTED_REUSED} reused; "
            f"got {len(built)} + {len(reused)}")
    if len(built | reused) != EXPECTED_WHEELS:
        raise TransferManifestError("built|reused union is not the exact 30-package closure")
    return {"built": sorted(built), "reused": sorted(reused), "triples": triples}


def _check_build_evidence(members: dict, recomputed: str, partition: dict) -> None:
    ev = _json_object(members[BUILD_EVIDENCE], BUILD_EVIDENCE)
    if ev.get("tree_scheme") != _ltree.SCHEME:
        raise TransferManifestError(f"build evidence tree_scheme: {ev.get('tree_scheme')!r}")
    if _hex64(ev.get("bundle_tree_sha256"), "build evidence bundle_tree_sha256") != recomputed:
        raise TransferManifestError("build evidence bundle_tree_sha256 != recomputed digest")
    if ev.get("member_count") != EXPECTED_MEMBERS:
        raise TransferManifestError(f"build evidence member_count: {ev.get('member_count')!r}")
    if ev.get("wheel_count") != EXPECTED_WHEELS:
        raise TransferManifestError(f"build evidence wheel_count: {ev.get('wheel_count')!r}")
    if ev.get("built") != partition["built"]:
        raise TransferManifestError("build evidence 'built' set disagrees with provenance")
    if ev.get("reused") != partition["reused"]:
        raise TransferManifestError("build evidence 'reused' set disagrees with provenance")
    if ev.get("partition_policy_enforced") is not True:
        raise TransferManifestError("build evidence must record partition_policy_enforced=true")


def _check_runtime_lock(members: dict, partition: dict) -> None:
    """Exact (name, version, wheel-hash) bijection between the runtime lock and the 30 wheels."""
    text = _text(members[RUNTIME_LOCK], RUNTIME_LOCK)
    raw = text.split("\n")
    if not raw or raw[-1] != "":
        raise TransferManifestError(f"{RUNTIME_LOCK} must end with a single LF (canonical form)")
    raw = raw[:-1]
    # EXACT canonical production form: one header line, then exactly the 30 pins. Arbitrary comments
    # are NOT skipped -- a lock that does not match what build_wheelhouse.py emits is rejected.
    if not raw or raw[0] != RUNTIME_LOCK_HEADER:
        raise TransferManifestError(
            f"{RUNTIME_LOCK} must begin with the canonical header {RUNTIME_LOCK_HEADER!r}")
    lines = raw[1:]
    if len(lines) != EXPECTED_WHEELS:
        raise TransferManifestError(
            f"{RUNTIME_LOCK} must pin exactly {EXPECTED_WHEELS} packages, got {len(lines)}")
    if any(ln.lstrip().startswith("#") for ln in lines):
        raise TransferManifestError(f"{RUNTIME_LOCK} must carry exactly ONE header line")
    seen = {}
    for line in lines:
        m = _LOCK_LINE.match(line)
        if not m:
            raise TransferManifestError(f"malformed {RUNTIME_LOCK} line: {line!r}")
        name, version, digest = m.group(1), m.group(2), m.group(3)
        if name in seen:
            raise TransferManifestError(f"duplicate {RUNTIME_LOCK} pin: {name!r}")
        seen[name] = (version, digest)
    expected = partition["triples"]
    if set(seen) != set(expected):
        raise TransferManifestError(
            f"{RUNTIME_LOCK} package set != the 30 wheels "
            f"(missing={sorted(set(expected) - set(seen))}, extra={sorted(set(seen) - set(expected))})")
    for name, (version, digest) in sorted(seen.items()):
        exp_version, exp_digest = expected[name]
        if version != exp_version:
            raise TransferManifestError(
                f"{RUNTIME_LOCK} version for {name!r}: {version!r} != {exp_version!r}")
        if digest != exp_digest:
            raise TransferManifestError(f"{RUNTIME_LOCK} hash for {name!r} != the wheel sha256")


def build_manifest(bundle: str) -> dict:
    """Collect, INDEPENDENTLY VALIDATE, and describe a Phase-B bundle. Fails closed on any defect."""
    members, dirs = _collect(bundle)
    wheels = _exact_set(members, dirs)
    _check_sha256sums(members, wheels)
    # RECOMPUTE the identity from the exact 31-member mapping -- never copy a provenance claim.
    tree_members = {p: members[p] for p in members if p.startswith(f"{WHEELHOUSE_DIR}/")}
    if len(tree_members) != EXPECTED_MEMBERS:
        raise TransferManifestError(
            f"wheelhouse must contain EXACTLY {EXPECTED_MEMBERS} members, got {len(tree_members)}")
    try:
        recomputed = _ltree.tree_digest(tree_members)
    except _ltree.LogicalTreeError as exc:
        raise TransferManifestError(f"logical-tree encoding rejected: {exc}") from exc
    partition = _check_provenance(members, wheels, recomputed)
    _check_build_evidence(members, recomputed, partition)
    _check_runtime_lock(members, partition)
    files = [{"path": p, "size": len(members[p]),
              "sha256": hashlib.sha256(members[p]).hexdigest()} for p in sorted(members)]
    return {"schema": SCHEMA, "file_count": len(files),
            "bind": {"wheel_count": EXPECTED_WHEELS,
                     "wheelhouse_member_count": EXPECTED_MEMBERS,
                     "built_count": EXPECTED_BUILT, "reused_count": EXPECTED_REUSED,
                     "tree_scheme": _ltree.SCHEME,
                     "tree_sha256": recomputed,          # RECOMPUTED here, not copied
                     "provenance": PROVENANCE, "runtime_lock": RUNTIME_LOCK,
                     "build_evidence": BUILD_EVIDENCE},
            "files": files}


def canonical_bytes(manifest: dict) -> bytes:
    """Canonical serialization: sorted keys, fixed separators, LF terminator, UTF-8."""
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def _assert_outside(bundle: str, out_path: str) -> None:
    base, target = os.path.abspath(bundle), os.path.abspath(out_path)
    if target == base or target.startswith(base + os.sep):
        raise TransferManifestError(
            f"manifest must live OUTSIDE the bundle it describes (self-reference): {out_path!r}")


def generate(bundle: str, out_path: str) -> dict:
    _assert_outside(bundle, out_path)
    if os.path.exists(out_path):
        raise TransferManifestError(f"output already exists (refusing to overwrite): {out_path!r}")
    manifest = build_manifest(bundle)
    tmp = out_path + ".tmp.%d" % os.getpid()
    try:
        with open(tmp, "wb") as fh:
            fh.write(canonical_bytes(manifest))
        os.replace(tmp, out_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return manifest


def _validate_recorded_shape(recorded: dict) -> None:
    """Fully validate the UNTRUSTED recorded manifest BEFORE any of its values are used -- including
    in mismatch diagnostics. Every malformed shape must surface as TransferManifestError, never as an
    internal TypeError/KeyError escaping to the caller or the CLI."""
    files = recorded.get("files")
    if not isinstance(files, list):
        raise TransferManifestError(
            f"manifest 'files' must be a list, got {type(files).__name__}")
    seen = set()
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise TransferManifestError(
                f"manifest files[{i}] must be an object, got {type(f).__name__}")
        for key, want, wname in (("path", str, "string"),
                                 ("size", int, "integer"),
                                 ("sha256", str, "string")):
            if key not in f:
                raise TransferManifestError(f"manifest files[{i}] missing {key!r}")
            val = f[key]
            # bool is an int subclass; a boolean size is malformed, not a size.
            if not isinstance(val, want) or isinstance(val, bool):
                raise TransferManifestError(
                    f"manifest files[{i}][{key!r}] must be a {wname}, got {type(val).__name__}")
        if f["size"] < 0:
            raise TransferManifestError(f"manifest files[{i}]['size'] must not be negative")
        if f["path"] in seen:
            raise TransferManifestError(f"manifest records a duplicate path: {f['path']!r}")
        seen.add(f["path"])
    bind = recorded.get("bind")
    if not isinstance(bind, dict):
        raise TransferManifestError(
            f"manifest 'bind' must be an object, got {type(bind).__name__}")
    for key in ("tree_scheme", "tree_sha256", "provenance", "runtime_lock", "build_evidence"):
        if not isinstance(bind.get(key), str):
            raise TransferManifestError(f"manifest bind[{key!r}] must be a string")
    for key in ("wheel_count", "wheelhouse_member_count", "built_count", "reused_count"):
        val = bind.get(key)
        if not isinstance(val, int) or isinstance(val, bool):
            raise TransferManifestError(f"manifest bind[{key!r}] must be an integer")
    if not isinstance(recorded.get("file_count"), int) or isinstance(recorded.get("file_count"), bool):
        raise TransferManifestError("manifest 'file_count' must be an integer")


def verify(bundle: str, manifest_path: str) -> dict:
    """Re-validate the bundle from scratch and require BYTE-FOR-BYTE equality with the recorded
    document. Deterministic canonical output is what makes this one exact comparison."""
    _assert_outside(bundle, manifest_path)
    try:
        with open(manifest_path, "rb") as fh:
            recorded_bytes = fh.read()
    except OSError as exc:
        raise TransferManifestError(f"transfer manifest unreadable: {manifest_path!r}: {exc}") from exc
    recorded = _json_object(recorded_bytes, "transfer manifest")
    if recorded.get("schema") != SCHEMA:
        raise TransferManifestError(f"unsupported manifest schema: {recorded.get('schema')!r}")
    _validate_recorded_shape(recorded)
    recomputed = build_manifest(bundle)
    if canonical_bytes(recomputed) != recorded_bytes:
        rec = {f["path"]: (f["size"], f["sha256"]) for f in recorded["files"]}
        new = {f["path"]: (f["size"], f["sha256"]) for f in recomputed["files"]}
        raise TransferManifestError(
            f"bundle does not match the manifest (missing={sorted(set(rec) - set(new))}, "
            f"extra={sorted(set(new) - set(rec))}, "
            f"changed={sorted(p for p in set(rec) & set(new) if rec[p] != new[p])})")
    return recomputed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="transfer_manifest",
                                 description="Phase-B bundle transfer manifest (generate/verify).")
    sub = ap.add_subparsers(dest="mode", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--bundle", required=True)
    g.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--bundle", required=True)
    v.add_argument("--manifest", required=True)
    a = ap.parse_args(argv)
    try:
        if a.mode == "generate":
            m = generate(a.bundle, a.out)
            print(f"TRANSFER_MANIFEST=GENERATED file_count={m['file_count']} "
                  f"tree_sha256={m['bind']['tree_sha256']}")
        else:
            m = verify(a.bundle, a.manifest)
            print(f"TRANSFER_MANIFEST=VERIFIED file_count={m['file_count']} "
                  f"tree_sha256={m['bind']['tree_sha256']}")
    except (TransferManifestError, OSError) as exc:
        sys.stderr.write(f"ERROR: transfer manifest failed (fail closed): {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
