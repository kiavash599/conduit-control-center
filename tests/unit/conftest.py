# SPDX-License-Identifier: MIT
"""
Shared fixtures for unit tests.

Unit tests mock all external dependencies:
- subprocess calls (Conduit adapter / systemctl)
- psutil (system metrics)
- filesystem reads (log files, ddns.log)
- SQLite (use in-memory database)

No real Conduit installation or Raspberry Pi hardware is required
to run unit tests. They must pass in any CI environment.

Implementation: Issue #36.
"""
from __future__ import annotations

import pytest
import aiosqlite

from backend.database import _TABLE_DDL
from backend.config import get_app_config, get_settings


@pytest.fixture
async def db():
    """
    Yield an in-memory aiosqlite connection with the full application schema.

    Uses ':memory:' so no file is written to disk and each test starts
    with a clean, empty database. The connection is closed automatically
    when the test ends.

    All three tables are created:
      - sessions         (auth/sessions.py)
      - failed_attempts  (auth/lockout.py)
      - audit_log        (auth/lockout.py, api/logs.py)
    """
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        for ddl in _TABLE_DDL:
            await conn.execute(ddl)
        await conn.commit()
        yield conn


@pytest.fixture(autouse=True)
def clear_config_cache():
    """
    Clear the lru_cache on get_settings() and get_app_config() before and
    after each test so that monkeypatched environment variables and file
    paths take effect without leaking into subsequent tests.
    """
    get_settings.cache_clear()
    get_app_config.cache_clear()
    yield
    get_settings.cache_clear()
    get_app_config.cache_clear()
