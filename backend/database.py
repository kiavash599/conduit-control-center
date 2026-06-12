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
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from backend.traffic.schema import apply_traffic_schema

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


# ---------------------------------------------------------------------------
# File permissions
# ---------------------------------------------------------------------------
# The database holds session identifiers and the audit log, so its files must
# not be readable by group or other. In WAL mode (enabled below) SQLite also
# creates -wal and -shm sidecar files that contain or expose the same row data;
# all three must be 0600.
#
# Defence in depth:
#   - Going forward, the systemd unit sets UMask=0077, so any file the service
#     creates (including sidecars SQLite recreates on its own) is born 0600.
#   - The explicit chmod below additionally tightens a database file that
#     already exists with looser permissions (e.g. a 0644 ccc.db created before
#     this change on an already-running install).

_DB_FILE_MODE = 0o600


def _restrict_db_file_permissions(db_path: Path) -> None:
    """
    Set the SQLite database and its WAL sidecar files to owner-only (0600).

    The main database file is mandatory: if its permissions cannot be set, this
    raises ``OSError`` so that startup fails. A database whose permissions
    cannot be secured must not be served.

    The ``-wal`` and ``-shm`` sidecars are best effort: they may legitimately
    be absent, and a failure to chmod a sidecar is logged at WARNING only and
    never aborts startup (UMask=0077 on the service unit is the primary
    guarantee for sidecars SQLite recreates on its own schedule).
    """
    # Main database file -- mandatory. A chmod failure here propagates and
    # aborts startup by design.
    os.chmod(db_path, _DB_FILE_MODE)

    # WAL sidecars -- optional, best effort.
    for suffix in ("-wal", "-shm"):
        target = db_path.with_name(db_path.name + suffix)
        try:
            if target.exists():
                os.chmod(target, _DB_FILE_MODE)
        except OSError as exc:  # noqa: PERF203 - explicit per-file handling
            logger.warning("Could not set permissions on %s: %s", target, exc)


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
        # P0 traffic persistence schema (Step 1) -- landed dormant; no collector
        # runs until the traffic_collector_enabled flag is set in a later step.
        await apply_traffic_schema(db)
        await db.commit()
    # Tighten permissions after the file (and any sidecars) exist on disk.
    _restrict_db_file_permissions(db_path)
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
