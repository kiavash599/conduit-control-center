"""
backend/database.py
-------------------
Async SQLite connection helper using aiosqlite.

The database file lives at:
  - Production:   /etc/conduit-cc/ccc.db
    (created by install.sh with correct permissions)
  - Development:  <project-root>/ccc.db

Tables are created at application startup via create_tables().
Each module that needs storage adds its CREATE TABLE statements to _TABLE_DDL below.

Usage
-----
    async with get_db() as db:
        await db.execute("SELECT ...")

Design notes
------------
- One connection is opened per request via context manager. aiosqlite connections
  are lightweight; a connection pool is not needed for this workload.
- WAL mode is enabled so reads and writes do not block each other.
- Foreign key enforcement is turned on per connection.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database path resolution
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEV_DB_PATH = _PROJECT_ROOT / "ccc.db"
_PROD_DB_PATH = Path("/etc/conduit-cc/ccc.db")


def get_db_path() -> Path:
    """Return the database path appropriate for the current environment."""
    if _PROD_DB_PATH.parent.exists():
        return _PROD_DB_PATH
    return _DEV_DB_PATH


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Tables are created with IF NOT EXISTS so this is safe to call on every
# startup (idempotent). Each feature issue adds its DDL here.

_TABLE_DDL: list[str] = [
    # Sessions (Issue #13)
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id          TEXT     PRIMARY KEY,
        user_id     TEXT     NOT NULL,
        created_at  DATETIME NOT NULL,
        last_active DATETIME NOT NULL,
        expires_at  DATETIME NOT NULL
    )
    """,
    # Login lockout (Issue #15)
    """
    CREATE TABLE IF NOT EXISTS failed_attempts (
        username      TEXT     PRIMARY KEY,
        count         INTEGER  NOT NULL DEFAULT 0,
        locked_until  DATETIME
    )
    """,
    # Audit log (Issues #15, #19)
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id         INTEGER  PRIMARY KEY AUTOINCREMENT,
        timestamp  DATETIME NOT NULL,
        event_type TEXT     NOT NULL,
        username   TEXT,
        detail     TEXT
    )
    """,
]


async def create_tables() -> None:
    """
    Create all application tables if they do not exist.
    Called once during FastAPI startup.
    """
    db_path = get_db_path()
    logger.info("Initialising database at %s", db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        for ddl in _TABLE_DDL:
            await db.execute(ddl)
        await db.commit()
    logger.info("Database tables ready")


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Async context manager that yields a configured aiosqlite connection.

    Each connection has WAL mode and foreign key enforcement enabled.
    The connection is automatically closed when the context exits.

    Example::

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
    """
    db_path = get_db_path()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db
