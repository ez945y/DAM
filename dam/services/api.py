"""DAM API Server — FastAPI app combining REST + WebSocket + static UI.

Usage::

    from dam.services.api import create_app
    app = create_app(telemetry_svc, risk_log_svc, boundary_svc, control_svc)

    # Run with:
    # uvicorn dam.services.api:app --host 0.0.0.0 --port 8080

Endpoints
---------
    GET  /                           → serves UI (index.html)
    GET  /static/*                   → serves static assets (app.js, style.css)

    GET  /api/telemetry/history      → last N cycle events
    WS   /ws/telemetry               → real-time cycle stream

    GET  /api/risk-log               → query risk events
    GET  /api/risk-log/{id}          → single event
    GET  /api/risk-log/export/json   → JSON export
    GET  /api/risk-log/export/csv    → CSV export
    GET  /api/risk-log/stats         → summary statistics

    GET  /api/boundaries             → list boundary configs
    GET  /api/boundaries/{name}      → single boundary config
    POST /api/boundaries             → create boundary config
    PUT  /api/boundaries/{name}      → update boundary config
    DELETE /api/boundaries/{name}    → delete boundary config

    GET  /api/control/status         → runtime state
    POST /api/control/start          → start runtime
    POST /api/control/pause          → pause runtime
    POST /api/control/resume         → resume runtime
    POST /api/control/stop           → stop runtime
    POST /api/control/estop          → emergency stop
    POST /api/control/reset          → reset to IDLE
    POST /api/control/restart        → restart process (reads from disk)

    POST /api/system/save-config     → write YAML to .dam_stackfile.yaml
    POST /api/system/restart         → save YAML (if provided) + restart process
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from dam.services.service_container import ServiceContainer

try:
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    _FASTAPI = True
except ImportError:
    _FASTAPI = False

_UI_DIR = Path(__file__).parent / "ui"


def create_app(services: ServiceContainer | None = None) -> Any:
    """Create and return the FastAPI application.

    Pass a ``ServiceContainer`` with the services to wire in.  Any field that
    is ``None`` causes the corresponding routes to return 503 Service Unavailable.
    """
    if services is None:
        services = ServiceContainer()
    if not _FASTAPI:
        raise ImportError("FastAPI is not installed. Run: pip install 'dam[services]'")

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    from dam.services.routers import (
        create_boundaries_router,
        create_control_router,
        create_mcap_router,
        create_ood_router,
        create_risk_log_router,
        create_system_router,
        create_telemetry_router,
    )

    app = FastAPI(
        title="DAM Console API",
        description="Detachable Action Monitor — Runtime REST + WebSocket API",
        version="0.3.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Lifecycle ────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def _startup() -> None:
        from dam.boundary.builtin_callbacks import register_all as reg_callbacks
        from dam.guard.builtin import register_all as reg_guards

        reg_callbacks()
        reg_guards()

        if services.telemetry is not None:
            services.telemetry.attach_loop(asyncio.get_event_loop())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if services.control is not None:
            logger.info("API: shutting down runtime control service...")
            services.control.stop()
            if (
                hasattr(services.control, "_runtime")
                and services.control._runtime is not None
                and hasattr(services.control._runtime, "shutdown")
            ):
                services.control._runtime.shutdown()

    # ── Static UI ────────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def root() -> Any:
        index = _UI_DIR / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text())
        return HTMLResponse("<h1>DAM Console</h1><p>UI not found. Run from project root.</p>")

    if _UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_UI_DIR)), name="static")

    # ── Domain routers ───────────────────────────────────────────────────────
    app.include_router(create_telemetry_router(services.telemetry))
    app.include_router(create_risk_log_router(services.risk_log))
    app.include_router(create_boundaries_router(services.boundary))
    app.include_router(create_control_router(services.control))
    app.include_router(create_system_router(services.control))
    app.include_router(create_ood_router(services.ood_trainer))
    app.include_router(create_mcap_router(services.mcap_sessions, services.control))

    return app
