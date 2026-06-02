# SPDX-License-Identifier: MIT
"""
Shared fixtures for integration tests.

Integration tests run the full FastAPI application using the
httpx AsyncClient test transport. The Conduit adapter is mocked
so no real Conduit installation is required.

All integration tests must pass in CI. They must not depend on:
- A running Conduit service
- Real Cloudflare API credentials
- A real Raspberry Pi

Implementation: Issue #37.
"""
