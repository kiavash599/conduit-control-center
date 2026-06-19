# SPDX-License-Identifier: MIT
"""R2b: tests for the Ryve claim API + single-slot RAM store (backend/api/ryve.py).

The adapter is monkeypatched (the real `ryve-claim` binary is not in CI). Covers
auth/CSRF gating, the POST/GET/DELETE contract, no-store headers, new-claim
invalidation, TTL expiry, and bytearray zero-on-evict.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.ryve as ryve_api
from backend.conduit.errors import ConduitPermissionError, RyveClaimError
from backend.conduit.ryve import RyveClaim
from backend.dependencies import AuthenticatedUser, get_current_user, require_csrf_token

_PNG = b"\x89PNG\r\n\x1a\nIMAGE-BYTES"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ryve_api.router, prefix="/api/conduit")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="admin")
    app.dependency_overrides[require_csrf_token] = lambda: None
    return TestClient(app)


def _patch_generate(monkeypatch, *, claim=None, exc=None):
    async def _fake():
        if exc is not None:
            raise exc
        return claim
    monkeypatch.setattr(ryve_api, "generate_ryve_claim", _fake)


def _claim(station="alirpi4", proxy="AbC123", png=_PNG):
    return RyveClaim(station_name=station, proxy_id=proxy, png=png)


# ------------------------------- POST -------------------------------
def test_post_returns_handle_and_metadata_only(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    r = client.post("/api/conduit/ryve/claim")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"claim_id", "station_name", "proxy_id"}
    assert body["station_name"] == "alirpi4" and body["proxy_id"] == "AbC123"
    assert "png" not in body and "image" not in body and "base64" not in body
    assert r.headers["cache-control"] == "no-store"


def test_post_requires_auth(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    client.app.dependency_overrides.pop(get_current_user, None)
    assert client.post("/api/conduit/ryve/claim").status_code == 401


def test_post_requires_csrf(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    client.app.dependency_overrides.pop(require_csrf_token, None)
    assert client.post("/api/conduit/ryve/claim").status_code == 403


def test_post_permission_error_503(client, monkeypatch):
    _patch_generate(monkeypatch, exc=ConduitPermissionError("nope"))
    assert client.post("/api/conduit/ryve/claim").status_code == 503


def test_post_generic_error_503(client, monkeypatch):
    _patch_generate(monkeypatch, exc=RyveClaimError("boom"))
    assert client.post("/api/conduit/ryve/claim").status_code == 503


# ------------------------------- GET image -------------------------------
def test_get_image_returns_png_no_store(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    cid = client.post("/api/conduit/ryve/claim").json()["claim_id"]
    r = client.get(f"/api/conduit/ryve/claim/image/{cid}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == "inline"
    assert r.content == _PNG


def test_get_image_requires_auth(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    cid = client.post("/api/conduit/ryve/claim").json()["claim_id"]
    client.app.dependency_overrides.pop(get_current_user, None)
    assert client.get(f"/api/conduit/ryve/claim/image/{cid}").status_code == 401


def test_get_image_unknown_id_404(client):
    assert client.get("/api/conduit/ryve/claim/image/bogus").status_code == 404


def test_new_post_invalidates_previous(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    id1 = client.post("/api/conduit/ryve/claim").json()["claim_id"]
    id2 = client.post("/api/conduit/ryve/claim").json()["claim_id"]
    assert id1 != id2
    assert client.get(f"/api/conduit/ryve/claim/image/{id1}").status_code == 404
    assert client.get(f"/api/conduit/ryve/claim/image/{id2}").status_code == 200


# ------------------------------- DELETE -------------------------------
def test_delete_evicts_then_404(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    cid = client.post("/api/conduit/ryve/claim").json()["claim_id"]
    d = client.delete(f"/api/conduit/ryve/claim/{cid}")
    assert d.status_code == 204
    assert client.get(f"/api/conduit/ryve/claim/image/{cid}").status_code == 404


def test_delete_idempotent(client):
    assert client.delete("/api/conduit/ryve/claim/bogus").status_code == 204


def test_delete_requires_csrf(client, monkeypatch):
    _patch_generate(monkeypatch, claim=_claim())
    cid = client.post("/api/conduit/ryve/claim").json()["claim_id"]
    client.app.dependency_overrides.pop(require_csrf_token, None)
    assert client.delete(f"/api/conduit/ryve/claim/{cid}").status_code == 403


# ------------------------------- store unit -------------------------------
async def test_store_zeroes_bytes_on_delete():
    store = ryve_api.RyveClaimStore()
    cid = await store.put(_PNG)
    ba = store._png                       # capture the live bytearray
    assert await store.get_png(cid) == _PNG
    await store.delete(cid)
    assert bytes(ba) == b"\x00" * len(_PNG)   # zeroed in place
    assert await store.get_png(cid) is None


async def test_store_new_put_zeroes_previous():
    store = ryve_api.RyveClaimStore()
    await store.put(_PNG)
    ba1 = store._png
    await store.put(b"\x89PNG\r\n\x1a\nSECOND")
    assert bytes(ba1) == b"\x00" * len(_PNG)


async def test_store_ttl_expiry():
    store = ryve_api.RyveClaimStore()
    cid = await store.put(_PNG)
    store._expires_at = time.monotonic() - 1.0   # force expired
    assert await store.get_png(cid) is None
