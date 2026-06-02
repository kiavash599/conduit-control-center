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
