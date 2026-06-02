"""
backend/api/__init__.py
-----------------------
Exports all API routers so main.py can import them from a single location.

Router registration map (main.py prefixes)
------------------------------------------
health_router    → /api/health
auth_router      → /api/auth
status_router    → /api/status
conduit_router   → /api/conduit
metrics_router   → /api/metrics
logs_router      → /api/logs
settings_router  → /api/settings
ddns_router      → /api/ddns
"""

from backend.api.health import router as health_router
from backend.api.auth import router as auth_router
from backend.api.status import router as status_router
from backend.api.conduit import router as conduit_router
from backend.api.metrics import router as metrics_router
from backend.api.logs import router as logs_router
from backend.api.settings import router as settings_router
from backend.api.ddns import router as ddns_router

__all__ = [
    "health_router",
    "auth_router",
    "status_router",
    "conduit_router",
    "metrics_router",
    "logs_router",
    "settings_router",
    "ddns_router",
]
