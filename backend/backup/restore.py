# SPDX-License-Identifier: MIT
"""
backend/backup/restore.py
-------------------------
Restore primitive for Backup & Restore (Epic #4, S3). Applies an OpenedBackup to
the CCC config directory transactionally, with a raw local rollback checkpoint.

This is a PRIMITIVE: it performs NO service control (no systemctl/sudo) and does
NOT hot-swap a live database. The caller MUST guarantee the target is non-live
(conduit-cc stopped, or a fresh target) and is responsible for restarting
conduit-cc afterward (the result flags restart_required).

Policy (locked):
  * ccc.db / config.json -> atomic replace; .env -> MERGE (preserve live secrets;
    overwrite only the keys the backup's env.subset provides; generate a fresh
    SESSION_SECRET only if absent; never restore CF_API_TOKEN or TLS paths).
  * Re-run the key-exclusion guard (content + target path) before any disk write.
  * Reject a manifest_version newer than supported; accept an older app_version.
  * On any apply/validation failure, roll back to the pre-apply checkpoint.
No secrets (passphrase, .env contents, admin hash, file bytes) are ever logged or
placed in an error/result message.
"""
from __future__ import annotations

import os
import secrets
import shutil
import stat
import sqlite3
import tempfile
from dataclasses import dataclass, field

from backend.backup.archiver import OpenedBackup
from backend.backup.collector import CCC_DIR
from backend.backup.exclusion import assert_path_allowed, scan_content
from backend.backup.manifest import FORMAT, MANIFEST_VERSION, BackupArchiveError

# Logical item name -> target filename within ccc_dir.
_TARGET_NAME = {"ccc.db": "ccc.db", "env.subset": ".env", "config.json": "config.json"}
_REQUIRED_ITEMS = ("ccc.db", "env.subset")
# config.json -> disk; conduit_settings.json (S4B-2.6) is allowed in the archive
# but is NOT a filesystem target: it is consumed out-of-band by the restore
# worker (ccc-restore-apply) and never written under ccc_dir. It is therefore
# absent from _TARGET_NAME, and the path-guard / apply loops skip non-target names.
_OPTIONAL_ITEMS = ("config.json", "conduit_settings.json")
# Epic-1 (F7): .env is canonically 0600 -- install already wrote 0600 and a
# restore must not silently WIDEN it to group-readable (the old 0640 here was
# a real install-vs-restore contract mismatch, corrected in v0.3.19).
_MODE = {"ccc.db": 0o600, ".env": 0o600, "config.json": 0o640}
_DB_SIDECARS = ("ccc.db-wal", "ccc.db-shm")
_CHECKPOINT_FILES = ("ccc.db", "ccc.db-wal", "ccc.db-shm", ".env", "config.json")

# Keys we will accept FROM a backup's env.subset. Anything else in env.subset is
# ignored on restore. This independently enforces the locked policy "never
# restore SESSION_SECRET / CF_API_TOKEN / TLS_CERT_PATH / TLS_KEY_PATH" rather
# than trusting the collector's redaction (defense-in-depth at the restore
# boundary). Live values of the excluded keys are preserved untouched.
_ENV_RESTORE_ALLOWLIST = frozenset({
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD_HASH",
    "APP_PORT",
    "LOG_LEVEL",
    "SECURE_COOKIES",
    "CF_ZONE_NAME",
    "CF_RECORD_NAME",
})


class RestoreError(Exception):
    """A restore-apply or post-apply validation failure. Generic; no secrets."""


@dataclass
class RestoreResult:
    status: str                                  # restored | rolled_back | rollback_failed
    restored_items: list = field(default_factory=list)
    restart_required: bool = False
    message: str = ""


# --------------------------- validation ---------------------------
def _validate_manifest(manifest: dict) -> None:
    if manifest.get("format") != FORMAT:
        raise BackupArchiveError("unexpected backup format")
    mv = manifest.get("manifest_version")
    if not isinstance(mv, int) or isinstance(mv, bool) or mv > MANIFEST_VERSION:
        raise BackupArchiveError("backup is from a newer version of CCC")


def _validate_item_names(names) -> None:
    allowed = set(_REQUIRED_ITEMS) | set(_OPTIONAL_ITEMS)
    for n in names:
        if n not in allowed:
            raise BackupArchiveError("backup contains an unexpected item")
    for n in _REQUIRED_ITEMS:
        if n not in names:
            raise BackupArchiveError("backup is missing a required item")


# --------------------------- atomic + fs helpers ---------------------------
def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _try_chown(path: str) -> None:
    try:
        shutil.chown(path, "conduit-cc", "conduit-cc")
    except (LookupError, PermissionError, OSError):
        pass  # best-effort: account absent (tests) or insufficient privilege


def _atomic_write(path: str, data: bytes, mode: int) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".ccc-tmp-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        _try_chown(tmp)
        os.replace(tmp, path)
        _fsync_dir(d)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _merge_env(target_text: str, subset_text: str) -> str:
    """Overwrite only the allowlisted keys present in the backup's env.subset;
    preserve every other target key (SESSION_SECRET / CF_API_TOKEN / TLS_*).
    Any non-allowlisted key in env.subset is ignored, so a crafted/older backup
    can never inject or overwrite a forbidden secret. Generate a fresh
    SESSION_SECRET only if none is present afterward."""
    sub = {}
    for line in subset_text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            k = k.strip()
            if k in _ENV_RESTORE_ALLOWLIST:      # drop forbidden/unknown keys
                sub[k] = v
    out, applied = [], set()
    for line in target_text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in sub:
                out.append(k + "=" + sub[k])
                applied.add(k)
                continue
        out.append(line)
    for k, v in sub.items():
        if k not in applied:
            out.append(k + "=" + v)
    has_session_secret = any(
        ln.strip().split("=", 1)[0].strip() == "SESSION_SECRET"
        for ln in out if "=" in ln and not ln.strip().startswith("#")
    )
    if not has_session_secret:
        out.append("SESSION_SECRET=" + secrets.token_hex(32))
    return "\n".join(out) + "\n"


# --------------------------- checkpoint / apply / rollback ---------------------------
def _make_checkpoint(ccc_dir: str):
    """Checkpoint the replaceable config set. A-.env hardening:
    * every member is lstat-gated -- a symlink or non-regular object is SKIPPED
      exactly like an absent file (never followed; a symlinked .env can no
      longer make root read an arbitrary host file into the checkpoint);
    * .env itself is snapshotted IN MEMORY through the canonical reader
      (backend/env_file.py) -- canonical bytes, no pathname copy at all."""
    from backend import env_file as _envf
    ckpt = tempfile.mkdtemp(prefix=".ccc-restore-ckpt-", dir=ccc_dir)
    os.chmod(ckpt, 0o700)
    captured = {}
    for fname in _CHECKPOINT_FILES:
        src = os.path.join(ccc_dir, fname)
        try:
            st = os.lstat(src)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue                        # symlink/foreign object: never followed
        if fname == ".env":
            captured[fname] = ("__env_text__", _envf.read_env_text(src))
            continue
        dst = os.path.join(ckpt, fname)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o600)
        captured[fname] = dst
    return ckpt, captured


def _cleanup(ckpt: str) -> None:
    shutil.rmtree(ckpt, ignore_errors=True)


def _apply(ccc_dir: str, items: dict) -> list:
    restored = []
    if "config.json" in items:
        _atomic_write(os.path.join(ccc_dir, "config.json"), items["config.json"], _MODE["config.json"])
        restored.append("config.json")
    _atomic_write(os.path.join(ccc_dir, "ccc.db"), items["ccc.db"], _MODE["ccc.db"])
    for side in _DB_SIDECARS:                    # drop stale WAL/SHM of the old DB
        try:
            os.unlink(os.path.join(ccc_dir, side))
        except FileNotFoundError:
            pass
    restored.append("ccc.db")
    env_path = os.path.join(ccc_dir, ".env")
    # Epic-1 A3: ALL .env I/O goes through the single canonical implementation
    # (backend/env_file.py). The read refuses live/dangling symlinks and any
    # non-regular object BEFORE opening (a service-created symlink can no longer
    # make the root restore helper read an arbitrary host file); the write is
    # the canonical atomic 0600 exact-ownership writer. Merge policy unchanged.
    from backend import env_file as _envf
    target_text = _envf.read_env_text(env_path)
    merged = _merge_env(target_text, items["env.subset"].decode("utf-8", "replace"))
    _envf.write_env_text(env_path, merged)
    restored.append(".env")
    return restored


def _post_validate(ccc_db_path: str) -> None:
    """Confirm the restored DB opens and is structurally sound. The only hard
    invariant is integrity: the bytes must be an uncorrupted SQLite database.
    We deliberately do NOT require any specific application table -- the schema
    is additive-only and startup create_tables() recreates missing core tables,
    so asserting a particular table here would needlessly reject valid older or
    future backups."""
    con = sqlite3.connect(f"file:{ccc_db_path}?immutable=1", uri=True)  # read-only, no sidecars
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RestoreError("restored database failed its integrity check")
    finally:
        con.close()


def _rollback(ccc_dir: str, captured: dict) -> None:
    from backend import env_file as _envf
    for fname, ckpt_path in captured.items():
        if isinstance(ckpt_path, tuple) and ckpt_path[0] == "__env_text__":
            # .env restore goes exclusively through the canonical atomic writer:
            # exact conduit-cc:conduit-cc 0600, symlink-refusing, byte-preserving
            # (in-memory snapshot -- a root-owned checkpoint copy can no longer
            # leave .env root-owned/unreadable to the service).
            _envf.write_env_text(os.path.join(ccc_dir, fname), ckpt_path[1])
            continue
        os.replace(ckpt_path, os.path.join(ccc_dir, fname))
    for side in _DB_SIDECARS:
        if side not in captured:                 # original had none -> ensure none linger
            try:
                os.unlink(os.path.join(ccc_dir, side))
            except FileNotFoundError:
                pass


# --------------------------- public primitive ---------------------------
def restore_backup(opened: OpenedBackup, ccc_dir: str = CCC_DIR) -> RestoreResult:
    """Restore CCC state from an OpenedBackup. Pre-apply validation failures raise
    (KeyExclusionError / BackupArchiveError) with nothing changed; apply-phase
    failures roll back and return a RestoreResult status."""
    _validate_manifest(opened.manifest)
    items = {it.name: it.data for it in opened.staging.items}
    _validate_item_names(set(items))
    for data in items.values():                  # re-scan before any disk write
        scan_content(data)
    for name in items:                           # path-guard computed targets
        if name not in _TARGET_NAME:             # non-disk item (e.g. conduit_settings.json)
            continue
        assert_path_allowed(os.path.join(ccc_dir, _TARGET_NAME[name]))

    ckpt, captured = _make_checkpoint(ccc_dir)
    try:
        restored = _apply(ccc_dir, items)
        _post_validate(os.path.join(ccc_dir, "ccc.db"))
    except BaseException:
        try:
            _rollback(ccc_dir, captured)
        except BaseException:
            _cleanup(ckpt)
            return RestoreResult(
                status="rollback_failed",
                restart_required=False,
                message="restore failed and automatic rollback did not fully succeed; manual recovery may be required",
            )
        _cleanup(ckpt)
        return RestoreResult(
            status="rolled_back",
            restart_required=False,
            message="restore failed; the previous state was restored",
        )
    _cleanup(ckpt)
    return RestoreResult(
        status="restored",
        restored_items=restored,
        restart_required=True,
        message="restore complete; restart conduit-cc to load the restored state",
    )
