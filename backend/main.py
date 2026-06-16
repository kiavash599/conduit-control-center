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
6. Register global exception handler (JSON errors only -- no stack traces)

Run locally
-----------
    uvicorn backend.main:app --reload

Production (managed by systemd)
--------------------------------
    /opt/conduit-cc/venv/bin/uvicorn backend.main:app \
        --host 127.0.0.1 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.config import get_app_config, get_settings
from backend.database import create_tables
from backend.auth.sessions import (
    _purge_loop,
    purge_expired_sessions,
)
from backend._version import APP_VERSION
from backend.dependencies import AuthRedirect
from backend.pages import router as pages_router
from backend.traffic.collector import TrafficCollector
from backend.api import (
    advisor_router,
    auth_router,
    conduit_router,
    ddns_router,
    health_router,
    logs_router,
    metrics_router,
    settings_router,
    status_router,
    traffic_router,
)
from backend.api.advisor import ensure_advisor_state
from backend.api.conduit import ensure_conduit_apply_lock
from backend.conduit.adapter import helper_is_safe as conduit_helper_is_safe

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
# Path helpers
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent
_STATIC_DIR = _PROJECT_ROOT / "frontend" / "static"
_TEMPLATES_DIR = _PROJECT_ROOT / "frontend" / "templates"


# ---------------------------------------------------------------------------
# Static asset cache-busting
# ---------------------------------------------------------------------------
# static_url("css/base.css") -> "/static/css/base.css?v=<mtime>"
#
# The query token is the file's modification time, so the URL changes only when
# the file changes. A new URL is a fresh cache key at the browser and at
# Cloudflare (Standard caching keys the full query string), so frontend deploys
# no longer require a manual CDN purge. Registered as a Jinja2 global and used in
# templates via {{ static_url('js/app.js') }}.
#
# Uncached on purpose: a stat() per asset (~10/page) is negligible on a Pi and
# guarantees the token reflects the on-disk file even if only static files
# changed without a service restart. Falls back to APP_VERSION if the file is
# missing (e.g. a mistyped path) so the page still renders.

def static_url(path: str) -> str:
    """Return a cache-busting URL for a file under /static."""
    rel = path.lstrip("/")
    try:
        token = str(int((_STATIC_DIR / rel).stat().st_mtime))
    except OSError:
        token = APP_VERSION
    return f"/static/{rel}?v={token}"

# ---------------------------------------------------------------------------
# Traffic persistence collector wiring (P0 Step 3c)
# ---------------------------------------------------------------------------
# Ship-dark: the collector starts only when traffic_collector_enabled is true in
# config.json. The wiring is factored into small helpers so it can be unit
# tested without driving the full application lifespan.

# How long to wait for a graceful collector stop before cancelling the task.
# Slightly above the collector's own bounded final-snapshot budget (5 s).
_COLLECTOR_SHUTDOWN_TIMEOUT_S: float = 8.0


def _maybe_start_traffic_collector(app: FastAPI) -> None:
    """Start the traffic collector as a background task, if enabled."""
    cfg = get_app_config()
    app.state.traffic_collector = None
    app.state.traffic_collector_task = None
    if not cfg.traffic_collector_enabled:
        logger.info("Traffic collector disabled by config (ship-dark default)")
        return
    collector = TrafficCollector(
        interval_seconds=cfg.traffic_collect_interval_seconds,
        gap_threshold_seconds=cfg.traffic_gap_threshold_seconds,
        snapshot_retention_days=cfg.traffic_snapshot_retention_days,
        delta_retention_days=cfg.traffic_delta_retention_days,
        hourly_retention_days=cfg.traffic_hourly_retention_days,
    )
    app.state.traffic_collector = collector
    app.state.traffic_collector_task = asyncio.create_task(
        collector.run(), name="traffic-collector"
    )
    logger.info("Traffic collector started (holder=%s)", collector.holder_id)


async def _stop_traffic_collector(app: FastAPI) -> None:
    """Request a graceful stop, await within budget, then cancel if needed."""
    task = getattr(app.state, "traffic_collector_task", None)
    collector = getattr(app.state, "traffic_collector", None)
    if task is None:
        return
    if collector is not None:
        collector.request_stop()
    try:
        await asyncio.wait_for(task, timeout=_COLLECTOR_SHUTDOWN_TIMEOUT_S)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        pass
    logger.info("Traffic collector stopped")


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs startup tasks before the server accepts requests."""
    logger.info("Conduit Control Center v%s starting up", APP_VERSION)

    # Initialise database tables
    await create_tables()

    # Purge any sessions left over from the previous server run
    startup_purged = await purge_expired_sessions()
    logger.info(
        "Startup session purge: %d expired session(s) removed",
        startup_purged,
    )

    # Start the hourly background purge task
    purge_task = asyncio.create_task(
        _purge_loop(), name="session-purge"
    )

    # Start the traffic collector (no-op unless explicitly enabled)
    _maybe_start_traffic_collector(app)

    # Initialise Contribution Advisor in-memory state (the asyncio.Lock is bound
    # to this running loop). In-memory + per-process -- valid under --workers 1,
    # the same single-worker invariant the collector relies on. The endpoint also
    # calls ensure_advisor_state() defensively.
    ensure_advisor_state(app)

    # M2 config write: per-process apply-lock (bound to this loop). The apply
    # endpoint also ensures it defensively. Log whether the privileged config
    # helper is present + safe; the apply endpoint refuses to run if it is not.
    ensure_conduit_apply_lock(app)
    if not conduit_helper_is_safe():
        logger.warning(
            "Conduit config write helper missing or unsafe -- config writes are "
            "disabled (the apply endpoint will return 503)."
        )

    app.state.started_at = time.time()
    logger.info(
        "Startup complete -- listening on port %d",
        get_app_config().port,
    )

    yield  # application runs

    # ---------------------------------------------------------------------------
    # Shutdown: cancel the purge task and await it to prevent
    # "Task was destroyed but it is pending" warnings.
    # ---------------------------------------------------------------------------
    logger.info("Conduit Control Center shutting down")

    # Stop the traffic collector first (graceful, with a bounded final snapshot)
    await _stop_traffic_collector(app)

    purge_task.cancel()
    try:
        await purge_task
    except asyncio.CancelledError:
        pass  # expected -- task exited cleanly via cancellation
    logger.info("Session purge task stopped")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Conduit Control Center",
    version=APP_VERSION,
    description=(
        "Dashboard for managing a Psiphon Conduit node on Linux / Raspberry Pi."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------
# Never expose Python stack traces to the client.




@app.exception_handler(AuthRedirect)
async def _auth_redirect_handler(request: Request, exc: AuthRedirect) -> RedirectResponse:
    """Convert AuthRedirect (raised by require_auth_html) to a 302 browser redirect."""
    return RedirectResponse(url=exc.redirect_url, status_code=302)

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Check server logs for details."
        },
    )


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------

app.include_router(pages_router)                          # HTML pages — no /api prefix
app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")
app.include_router(status_router, prefix="/api")
app.include_router(conduit_router, prefix="/api/conduit")
app.include_router(metrics_router, prefix="/api/metrics")
app.include_router(logs_router, prefix="/api")
app.include_router(settings_router, prefix="/api/settings")
app.include_router(ddns_router, prefix="/api/ddns")
app.include_router(traffic_router, prefix="/api/traffic")
app.include_router(advisor_router, prefix="/api/advisor")

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    logger.debug("Static files mounted from %s", _STATIC_DIR)
else:
    logger.warning(
        "Static directory not found at %s -- /static will return 404. "
        "Create frontend/static/ to enable static file serving.",
        _STATIC_DIR,
    )

# ---------------------------------------------------------------------------
# Template engine
# ---------------------------------------------------------------------------

if _TEMPLATES_DIR.exists():
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    # Expose the cache-busting helper to all templates: {{ static_url('...') }}
    app.state.templates.env.globals["static_url"] = static_url
    logger.debug("Templates loaded from %s", _TEMPLATES_DIR)
else:
    app.state.templates = None
    logger.warning(
        "Templates directory not found at %s -- HTML routes will not render pages. "
        "Create frontend/templates/ to enable template rendering.",
        _TEMPLATES_DIR,
    )
