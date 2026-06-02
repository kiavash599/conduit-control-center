"""
backend/main.py
---------------
FastAPI application factory for Conduit Control Center.

Startup sequence
----------------
1. Load settings (config.py)
2. Configure logging
3. Create database tables (database.py)
4. Register all API routers
5. Mount static files and Jinja2 templates
6. Register global exception handler (JSON errors only — no stack traces)

Run locally
-----------
    uvicorn backend.main:app --reload

Production (managed by systemd)
--------------------------------
    /opt/conduit-cc/venv/bin/uvicorn backend.main:app \
        --host 127.0.0.1 --port 8000 --workers 1
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.config import get_app_config, get_settings
from backend.database import create_tables

# API routers
from backend.api import (
    auth_router,
    conduit_router,
    ddns_router,
    health_router,
    logs_router,
    metrics_router,
    settings_router,
    status_router,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application metadata
# ---------------------------------------------------------------------------

APP_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent
_STATIC_DIR = _PROJECT_ROOT / "frontend" / "static"
_TEMPLATES_DIR = _PROJECT_ROOT / "frontend" / "templates"

# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs startup tasks before the server accepts requests."""
    logger.info("Conduit Control Center v%s starting up", APP_VERSION)

    # Initialise the database (creates tables if they don't exist)
    await create_tables()

    # Store startup time so /api/health can report uptime
    app.state.started_at = time.time()

    logger.info("Startup complete — listening on port %d", get_app_config().port)
    yield
    logger.info("Conduit Control Center shutting down")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Conduit Control Center",
    version=APP_VERSION,
    description="Dashboard for managing a Psiphon Conduit node on Linux / Raspberry Pi.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------
# Never expose Python stack traces to the client.  All unhandled exceptions
# become a generic JSON 500 response.  The real error is logged server-side.


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Check server logs for details."},
    )


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------

app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")
app.include_router(status_router, prefix="/api")
app.include_router(conduit_router, prefix="/api/conduit")
app.include_router(metrics_router, prefix="/api/metrics")
app.include_router(logs_router, prefix="/api")
app.include_router(settings_router, prefix="/api/settings")
app.include_router(ddns_router, prefix="/api/ddns")

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
# Guard: only mount if the directory exists so the app starts cleanly on a
# fresh checkout before the frontend is built.

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    logger.debug("Static files mounted from %s", _STATIC_DIR)
else:
    logger.warning(
        "Static directory not found at %s — /static will return 404. "
        "Create frontend/static/ to enable static file serving.",
        _STATIC_DIR,
    )

# ---------------------------------------------------------------------------
# Template engine
# ---------------------------------------------------------------------------
# Exposed as app.state.templates so route handlers can access it via request.app.state.

if _TEMPLATES_DIR.exists():
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    logger.debug("Templates loaded from %s", _TEMPLATES_DIR)
else:
    app.state.templates = None
    logger.warning(
        "Templates directory not found at %s — HTML routes will not render pages. "
        "Create frontend/templates/ to enable template rendering.",
        _TEMPLATES_DIR,
    )
