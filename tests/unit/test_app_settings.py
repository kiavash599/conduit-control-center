# SPDX-License-Identifier: MIT
"""Unit tests for the app_settings key/value store (Personal Mode C1).

Covers table creation via create_tables(), DDL idempotency, get/set round-trip,
updating an existing key, and the storage contract: only key/value/updated_at
columns exist (no token/id columns), and the single known key is a benign label.

Fully inert: exercises only backend/database.py — no helper, API, Conduit, or
network calls.
"""
from __future__ import annotations

import aiosqlite
import pytest

import backend.database as database
from backend.database import (
    PERSONAL_COMPARTMENT_NAME_KEY,
    create_tables,
    get_setting,
    set_setting,
)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database module at a throwaway file DB for the test."""
    db_file = tmp_path / "ccc.db"
    monkeypatch.setattr(database, "get_db_path", lambda: db_file)
    return db_file


# --- DDL shape -------------------------------------------------------------


def test_app_settings_ddl_is_idempotent():
    ddl = [d for d in database._TABLE_DDL if "app_settings" in d]
    assert ddl, "app_settings DDL missing from _TABLE_DDL"
    assert "if not exists" in ddl[0].lower()


# --- table creation + idempotency -----------------------------------------


async def test_create_tables_creates_app_settings(temp_db):
    await create_tables()
    async with aiosqlite.connect(temp_db) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='app_settings'"
        )
        assert await cur.fetchone() is not None


async def test_create_tables_idempotent(temp_db):
    await create_tables()
    await create_tables()  # second run must not raise
    async with aiosqlite.connect(temp_db) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='app_settings'"
        )
        (count,) = await cur.fetchone()
    assert count == 1


# --- get/set round-trip ----------------------------------------------------


async def test_get_missing_returns_default(temp_db):
    await create_tables()
    assert await get_setting("nope") is None
    assert await get_setting("nope", "fallback") == "fallback"


async def test_set_then_get_round_trip(temp_db):
    await create_tables()
    await set_setting(PERSONAL_COMPARTMENT_NAME_KEY, "raspberrypi")
    assert await get_setting(PERSONAL_COMPARTMENT_NAME_KEY) == "raspberrypi"


async def test_update_existing_key_overwrites(temp_db):
    await create_tables()
    await set_setting(PERSONAL_COMPARTMENT_NAME_KEY, "first")
    await set_setting(PERSONAL_COMPARTMENT_NAME_KEY, "second")
    assert await get_setting(PERSONAL_COMPARTMENT_NAME_KEY) == "second"
    # Exactly one row per key (PRIMARY KEY enforced).
    async with aiosqlite.connect(temp_db) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM app_settings WHERE key = ?",
            (PERSONAL_COMPARTMENT_NAME_KEY,),
        )
        (count,) = await cur.fetchone()
    assert count == 1


# --- storage contract: no token/id columns; name key is a label ------------


async def test_schema_has_only_kv_columns(temp_db):
    await create_tables()
    async with aiosqlite.connect(temp_db) as db:
        cur = await db.execute("PRAGMA table_info(app_settings)")
        cols = {row[1] for row in await cur.fetchall()}
    assert cols == {"key", "value", "updated_at"}
    # No dedicated token/id storage exists by construction.
    assert not (cols & {"token", "pairing_token", "compartment_id", "id"})


def test_only_a_benign_name_key_is_exposed():
    # The single known key is a label; there is no token/id key constant.
    assert PERSONAL_COMPARTMENT_NAME_KEY == "personal_compartment_name"
    key_constants = {n for n in dir(database) if n.endswith("_KEY")}
    assert key_constants == {"PERSONAL_COMPARTMENT_NAME_KEY"}
