"""backend/env_file.py -- THE canonical `.env` write contract (Epic 1, F7).

One contract, every writer:
  * owner/group : conduit-cc:conduit-cc (the service account; preserved from the
                  existing file when replacing, asserted by install/restore)
  * mode        : 0600 -- only the service account and root ever need access
  * object type : regular file only; a symlink or other object FAILS CLOSED
  * replacement : ATOMIC -- unpredictable mkstemp sibling, write, flush, fsync,
                  fchmod 0600, owner preserved, os.replace, parent-dir fsync.
                  A failure at any point leaves the previous complete `.env`
                  untouched and removes the temp file.

Consumers: backend/api/settings.py (password change), backend/backup/restore.py
(restore + checkpoint), install.sh/update.sh (shell side asserts the same
contract). stdlib-only.
"""
from __future__ import annotations

import os
import stat
import tempfile

ENV_FILE_MODE = 0o600
ENV_OWNER_NAME = "conduit-cc"          # canonical owner/group of /etc/conduit-cc/.env
MAX_ENV_BYTES = 256 * 1024             # bounded read/write


def _canonical_ids() -> "tuple[int, int] | None":
    """(uid, gid) of the canonical owner, or None when the account is absent
    (dev/test hosts). Under root execution with the account PRESENT, ownership
    is enforced exactly -- never best-effort."""
    try:
        import pwd
        import grp
        return (pwd.getpwnam(ENV_OWNER_NAME).pw_uid,
                grp.getgrnam(ENV_OWNER_NAME).gr_gid)
    except (ImportError, KeyError):
        return None


class EnvFileError(Exception):
    """Fail-closed .env contract violation (specific, testable)."""


def _fsync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_env_text(env_path: str) -> str:
    """Bounded, symlink-refusing read of the canonical `.env`.

    lstat BEFORE open: a symlink (live or dangling), or any non-regular object,
    fails closed -- a privileged reader can never be redirected to an arbitrary
    host file. Absent file reads as empty (callers create it)."""
    directory = os.path.dirname(env_path) or "."
    try:
        parent = os.lstat(directory)
    except OSError as exc:
        raise EnvFileError(f"parent of {env_path!r} unavailable: {exc}") from exc
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise EnvFileError(f"parent of {env_path!r} is not a real directory")
    try:
        st = os.lstat(env_path)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise EnvFileError(f"cannot stat {env_path!r}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise EnvFileError(f"{env_path!r} is not a regular file (refusing to read)")
    if st.st_size > MAX_ENV_BYTES:
        raise EnvFileError(f"{env_path!r} exceeds the bounded size")
    fd = os.open(env_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
            raise EnvFileError(f"{env_path!r} changed while being opened")
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise EnvFileError(f"{env_path!r} is not a single regular file")
        if stat.S_IMODE(opened.st_mode) != ENV_FILE_MODE:
            raise EnvFileError(f"{env_path!r} mode is not exact 0600")
        ids = _canonical_ids()
        if ids is not None and (opened.st_uid, opened.st_gid) != ids:
            raise EnvFileError(f"{env_path!r} ownership is not canonical")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(MAX_ENV_BYTES + 1)
        if len(data) > MAX_ENV_BYTES:
            raise EnvFileError(f"{env_path!r} exceeds the bounded size")
        return data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        # Do not echo undecodable content or codec position details through a
        # privileged caller. The exception class is sufficient evidence.
        raise EnvFileError(
            f"cannot read {env_path!r}: {exc.__class__.__name__}"
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)


def write_env_text(env_path: str, content: str) -> None:
    """Atomically replace `env_path` with `content` under the canonical contract.

    The destination, if present, must be a REGULAR file (lstat; a symlink is
    refused before any write). Ownership of an existing file is preserved on the
    replacement (the service rewrites its own 0600 file; root-driven writers keep
    conduit-cc ownership). Mode is forced to 0600. On any failure the previous
    complete file remains and the temp file is removed."""
    directory = os.path.dirname(env_path) or "."
    try:
        dst = os.lstat(directory)
    except OSError as exc:
        raise EnvFileError(f"parent of {env_path!r} unavailable: {exc}") from exc
    if stat.S_ISLNK(dst.st_mode) or not stat.S_ISDIR(dst.st_mode):
        raise EnvFileError(f"parent of {env_path!r} is not a real directory")
    if len(content.encode("utf-8")) > MAX_ENV_BYTES:
        raise EnvFileError("content exceeds the bounded size")
    try:
        prev = os.lstat(env_path)
    except FileNotFoundError:
        prev = None
    except OSError as exc:
        raise EnvFileError(f"cannot stat {env_path!r}: {exc}") from exc
    if prev is not None and (not stat.S_ISREG(prev.st_mode) or prev.st_nlink != 1):
        raise EnvFileError(
            f"{env_path!r} is not a single regular file (refusing to write)"
        )
    ids = _canonical_ids()
    if (prev is not None and ids is not None and os.geteuid() != 0
            and (prev.st_uid, prev.st_gid) != ids):
        raise EnvFileError(f"{env_path!r} ownership is not canonical")

    fd, tmp = tempfile.mkstemp(prefix=".env-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), ENV_FILE_MODE)
            if os.geteuid() == 0:
                # ROOT execution: exact canonical ownership, fail-closed
                # (never best-effort). Falls back to preserving the previous
                # owner only if the canonical account is absent (dev hosts).
                if ids is not None:
                    os.fchown(fh.fileno(), ids[0], ids[1])
                elif prev is not None:
                    os.fchown(fh.fileno(), prev.st_uid, prev.st_gid)
            elif prev is not None:
                try:
                    os.fchown(fh.fileno(), prev.st_uid, prev.st_gid)
                except PermissionError:
                    # Non-owner cannot chown; only acceptable when we ARE the
                    # previous owner already (self-rewrite keeps ownership).
                    if prev.st_uid != os.geteuid():
                        raise
        os.replace(tmp, env_path)
        _fsync_dir(directory)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_env_key(env_path: str, key: str, value_line: str) -> None:
    """Replace (or append) the single line for `key` and atomically rewrite the
    file under the canonical contract. `value_line` is the COMPLETE replacement
    line including the key and trailing newline; existing content, ordering and
    all other keys are preserved byte-for-byte."""
    if not value_line.startswith(key) or not value_line.endswith("\n"):
        raise EnvFileError("value_line must start with the key and end with a newline")
    existing = read_env_text(env_path)

    lines = existing.splitlines(keepends=True)
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(key):
            lines[i] = value_line
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(value_line)
    write_env_text(env_path, "".join(lines))
