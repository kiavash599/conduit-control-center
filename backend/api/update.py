"""
backend/api/update.py
---------------------
One-click CCC update API (Feature 2, Batch 3) -- backend only.

Routes (registered under /api/update):
  GET  /api/update/check    -- current vs latest stable release (24h cached)
  POST /api/update/install  -- auth + CSRF; stream the verified release tarball
                               to ccc-update-apply; 202 on ack
  GET  /api/update/status   -- read the helper's outcome file; reconcile stale

Source of truth: GitHub Releases "latest". GitHub's /releases/latest excludes
drafts and prereleases, so only STABLE releases are ever surfaced. This module
NEVER uses origin/main, a branch, an arbitrary ref, or any user-supplied
URL/ref/path. The install path uses ONLY the cached latest release resolved
server-side; the privileged helper (ccc-update-apply) re-validates the payload.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import select
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend._version import APP_VERSION
from backend.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_csrf_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["update"])

# --------------------------------------------------------------------------- #
#  Hardcoded constants (no user input ever reaches a URL/ref/path)            #
# --------------------------------------------------------------------------- #
_REPO = "kiavash599/conduit-control-center"
_GH_LATEST = f"https://api.github.com/repos/{_REPO}/releases/latest"
# ADR-0003: the update payload is the publisher-produced SIGNED asset set
# {manifest, signature, content-addressed artifact}; GitHub auto-generated source
# archives (tarball_url) are NOT part of the update trust model.
_FRAME_MAGIC = b"CCCU\x01"   # payload frame header (MUST match ccc-update-apply)
_ALLOWED_DL_HOSTS = {
    "api.github.com", "codeload.github.com",
    "github.com", "objects.githubusercontent.com",
}
_HELPER_ARGV = ["sudo", "/opt/conduit-cc/bin/ccc-update-apply", "apply"]

_STATE_DIR = "/var/lib/conduit-cc"
_CHECK_CACHE = f"{_STATE_DIR}/update-check.json"
_STATUS_PATH = f"{_STATE_DIR}/update-status.json"
_INSTALLED_CORE = "/opt/conduit/version"

_CACHE_TTL_S = 24 * 3600
_HTTP_TIMEOUT_S = 10
_MAX_TARBALL = 200 * 1024 * 1024
_MAX_NOTES_LINES = 8
_ACK_DEADLINE_S = 30
_UA = "conduit-control-center-update-check"

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_CORE_VER_RE = re.compile(r'CONDUIT_VERSION="(\d+\.\d+\.\d+)"')

# ccc-update-apply pre-detach exit codes
_EXIT_VALIDATION = 2
_EXIT_FS = 3


# --------------------------------------------------------------------------- #
#  Small pure helpers                                                          #
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _semver(s: str | None) -> tuple[int, int, int] | None:
    if not s:
        return None
    m = _TAG_RE.match(s.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _read_installed_core() -> str | None:
    try:
        with open(_INSTALLED_CORE, encoding="utf-8") as fh:
            v = fh.read().strip()
        return v or None
    except OSError:
        return None


def _sanitize_notes(body: str) -> list[str]:
    """Plain-text bullet preview. Strips HTML tags and markdown markers; never
    returns raw HTML or scriptable content."""
    out: list[str] = []
    for raw in (body or "").splitlines():
        line = re.sub(r"<[^>]*>", "", raw)          # strip any HTML tags
        line = re.sub(r"^\s*[\-\*\+]\s+", "", line)  # bullet marker
        line = re.sub(r"^\s*#{1,6}\s+", "", line)    # heading marker
        line = line.replace("`", "").strip()
        if not line:
            continue
        if len(line) > 140:
            line = line[:137] + "..."
        out.append(line)
        if len(out) >= _MAX_NOTES_LINES:
            break
    return out


# --------------------------------------------------------------------------- #
#  GitHub access (stdlib urllib; all URLs server-built/cache-resolved)        #
# --------------------------------------------------------------------------- #
def _gh_get_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310 (https only)
        return json.loads(resp.read().decode("utf-8"))


def _gh_get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def _gh_download(url: str) -> bytes:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not url.startswith("https://") or host not in _ALLOWED_DL_HOSTS:
        raise RuntimeError(f"refusing download from disallowed host: {host!r}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310
        data = resp.read(_MAX_TARBALL + 1)
    if len(data) > _MAX_TARBALL:
        raise RuntimeError("release tarball exceeds size limit")
    return data


def _release_assets(data: dict, version: str) -> dict:
    """Resolve the canonical SIGNED release asset URLs by name (host-allow-listed).
    Raises if any of the three assets is absent (the release is not signed)."""
    want = {
        "manifest_url": f"ccc-{version}.manifest.json",
        "signature_url": f"ccc-{version}.manifest.json.sig",
        "artifact_url": f"ccc-{version}.tar.gz",
    }
    by_name: dict = {}
    for asset in data.get("assets") or []:
        url = asset.get("browser_download_url") or ""
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if url.startswith("https://") and host in _ALLOWED_DL_HOSTS:
            by_name[asset.get("name")] = url
    resolved = {}
    for key, name in want.items():
        if name not in by_name:
            raise RuntimeError(f"release is missing signed asset: {name}")
        resolved[key] = by_name[name]
    return resolved


def _recommended_core_from_manifest(manifest_url: str) -> str | None:
    """Best-effort advisory: read the recommended Conduit Core version from the
    (signed) manifest asset. NOT authoritative here — compatibility authority is
    established at install AFTER verification. Any failure returns None and must
    NOT fail the check. Replaces the former unauthenticated raw update.sh read."""
    try:
        raw = _gh_download(manifest_url)
        obj = json.loads(raw.decode("utf-8"))
        return (obj.get("compatibility") or {}).get("recommended_conduit_core")
    except Exception:  # noqa: BLE001 - best effort
        return None


def _fetch_latest() -> dict:
    """Resolve the latest STABLE release. Raises on any failure (caller falls
    back to cache)."""
    data = _gh_get_json(_GH_LATEST)
    if data.get("draft") or data.get("prerelease"):
        raise RuntimeError("latest release is a draft/prerelease")
    tag = (data.get("tag_name") or "").strip()
    if not _TAG_RE.match(tag):
        raise RuntimeError(f"unexpected tag format: {tag!r}")
    version = re.sub(r"^v", "", tag)
    assets = _release_assets(data, version)
    return {
        "checked_at_epoch": time.time(),
        "checked_at": _now(),
        "latest": version,
        "tag": tag,
        "manifest_url": assets["manifest_url"],
        "signature_url": assets["signature_url"],
        "artifact_url": assets["artifact_url"],
        "html_url": data.get("html_url"),
        "published_at": data.get("published_at"),
        "notes_preview": _sanitize_notes(data.get("body") or ""),
        "recommended_core": _recommended_core_from_manifest(assets["manifest_url"]),
    }


# --------------------------------------------------------------------------- #
#  Cache (24h)                                                                 #
# --------------------------------------------------------------------------- #
def _load_cache() -> dict | None:
    try:
        with open(_CHECK_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_cache(doc: dict) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = f"{_CHECK_CACHE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.replace(tmp, _CHECK_CACHE)
    except OSError as exc:  # cache is best-effort
        logger.warning("could not write update-check cache: %s", exc)


def _present(doc: dict, installed_core: str | None, reachable: bool) -> dict:
    """Build the stable response shape from a cache/fetch doc."""
    latest = doc.get("latest")
    cur_v, lat_v = _semver(APP_VERSION), _semver(latest)
    update_available = bool(cur_v and lat_v and lat_v > cur_v)
    rec_core = doc.get("recommended_core")
    rc_v, ic_v = _semver(rec_core), _semver(installed_core)
    core_warning = bool(rc_v and ic_v and rc_v > ic_v)
    return {
        "current": APP_VERSION,
        "latest": latest,
        "update_available": update_available,
        "notes_preview": doc.get("notes_preview", []),
        "html_url": doc.get("html_url"),
        "published_at": doc.get("published_at"),
        "recommended_core": rec_core,
        "installed_core": installed_core,
        "core_warning": core_warning,
        "last_checked": doc.get("checked_at"),
        "reachable": reachable,
    }


# --------------------------------------------------------------------------- #
#  Status file (mirror restore: outcome file + PID reconcile)                  #
# --------------------------------------------------------------------------- #
def _update_worker_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            return b"ccc-update-apply" in fh.read().replace(b"\x00", b" ")
    except (FileNotFoundError, PermissionError, OSError):
        return True


def _read_status() -> dict:
    base = {
        "state": "idle", "id": None, "from_version": None, "to_version": None,
        "message": "No update has been run.", "started_at": None,
        "finished_at": None, "updated_at": None,
    }
    try:
        with open(_STATUS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return base
    except (OSError, ValueError):
        return {**base, "state": "unknown", "message": "Update status is unavailable."}
    out = {**base, **{k: data.get(k) for k in base if k in data}}
    out["state"] = data.get("state", "unknown")
    # Reconcile a stale in_progress (worker died before writing an outcome).
    if out["state"] == "in_progress" and not _update_worker_alive(data.get("pid")):
        out["state"] = "unknown"
        out["message"] = "A previous update did not complete (interrupted)."
    return out


# --------------------------------------------------------------------------- #
#  Helper invocation (mirror restore: Popen + writer thread + ack read)       #
# --------------------------------------------------------------------------- #
def _frame_payload(manifest: bytes, signature: bytes, artifact: bytes) -> bytes:
    """Frame the signed asset set for the helper's stdin (MUST match
    ccc-update-apply's decoder): magic header + three length-prefixed records in
    the order manifest, signature, artifact."""
    out = bytearray(_FRAME_MAGIC)
    for part in (manifest, signature, artifact):
        out += struct.pack(">Q", len(part))
        out += part
    return bytes(out)


def _invoke_helper(payload: bytes):
    """Stream the framed payload to ccc-update-apply on stdin; return on the ack.
    Returns ("ack", line) | ("exit", rc) | ("timeout", None) | ("mismatch", line)."""
    proc = subprocess.Popen(
        list(_HELPER_ARGV),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, shell=False,
    )

    def _write():
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    threading.Thread(target=_write, daemon=True).start()

    deadline = time.monotonic() + _ACK_DEADLINE_S
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            try:
                proc.kill()
            finally:
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            return ("timeout", None)
        rlist, _, _ = select.select([proc.stdout], [], [], remaining)
        if not rlist:
            continue
        line = proc.stdout.readline()
        break

    text = (line or b"").decode("ascii", "replace").strip()
    if text == "":
        try:
            rc = proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            rc = -1
        return ("exit", rc)
    threading.Thread(target=proc.wait, daemon=True).start()
    return ("ack", text) if text.startswith("accepted ") else ("mismatch", text)


# --------------------------------------------------------------------------- #
#  Routes                                                                      #
# --------------------------------------------------------------------------- #
class InstallRequest(BaseModel):
    version: str = Field(..., min_length=1, max_length=32,
                         description="Must equal the cached latest stable release")


@router.get("/check", summary="Check for a newer stable CCC release")
async def check(
    force: bool = False,
    _user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    cache = _load_cache()
    installed_core = _read_installed_core()
    fresh = bool(
        cache and not force
        and (time.time() - float(cache.get("checked_at_epoch", 0)) < _CACHE_TTL_S)
    )
    if fresh:
        return _present(cache, installed_core, reachable=True)
    try:
        doc = _fetch_latest()
        _save_cache(doc)
        return _present(doc, installed_core, reachable=True)
    except Exception as exc:  # noqa: BLE001 - GitHub failure must never break the dashboard
        logger.warning("update check failed: %s", exc)
        if cache:
            return _present(cache, installed_core, reachable=False)
        return {
            "current": APP_VERSION, "latest": None, "update_available": False,
            "notes_preview": [], "html_url": None, "published_at": None,
            "recommended_core": None, "installed_core": installed_core,
            "core_warning": False, "last_checked": None, "reachable": False,
        }


@router.post("/install", status_code=status.HTTP_202_ACCEPTED,
             summary="Install the latest stable CCC release")
async def install(
    body: InstallRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_token),
) -> dict:
    cache = _load_cache()
    if not cache or not cache.get("latest"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="No update information available. Check for updates first.")
    latest = cache["latest"]
    if body.version != latest:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Requested version is not the current latest release. Re-check for updates.")
    cur_v, lat_v = _semver(APP_VERSION), _semver(latest)
    if not (cur_v and lat_v and lat_v > cur_v):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="No newer stable release is available.")
    st = _read_status()
    if st.get("state") == "in_progress":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="An update is already in progress.")

    try:
        manifest = _gh_download(cache.get("manifest_url") or "")
        signature = _gh_download(cache.get("signature_url") or "")
        artifact = _gh_download(cache.get("artifact_url") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("release download failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Could not download the release.")

    # ADR-0003: stream the SIGNED asset set; the privileged helper verifies the
    # signature + content digest against the on-device trust store BEFORE it does
    # anything else. No structural/gzip pre-check here (untrusted transport).
    kind, info = _invoke_helper(_frame_payload(manifest, signature, artifact))
    if kind == "ack":
        update_id = info.split(" ", 1)[1] if " " in info else None
        return {"status": "accepted", "id": update_id,
                "from_version": APP_VERSION, "to_version": latest}
    if kind == "exit":
        if info == _EXIT_FS:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Update is not available on this server.")
        if info == _EXIT_VALIDATION:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Update rejected (already running or invalid release).")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Could not start the update.")


@router.get("/status", summary="Current update status")
async def get_status(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    return _read_status()
