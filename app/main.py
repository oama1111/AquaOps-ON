"""FastAPI application factory — the single deployable unit (docs/architecture.md §3).

Startup does three things and nothing else: create any missing tables, register
the case system if it is not there yet, and mount the routers. Seeding is
idempotent, so `uvicorn app.main:app` on a fresh clone yields a demonstrable
system with the committed case data already loaded.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.api.v1.dashboard import router as dashboard_router
from app.db import init_db, session_scope

APP_VERSION = "0.4.0"

#: First-party static assets. The only one today is the vendored htmx bundle:
#: the dashboard must not depend on a public CDN, because a municipal network may
#: be filtered or air-gapped and a third-party script host is a supply-chain
#: dependency the operator cannot audit. Serving it from the same origin also
#: removes a mixed-content and a certificate-trust question.
STATIC_DIR = Path(__file__).resolve().parent / "static"

DISCLAIMER = (
    "Decision support only. Output is not legal proof of compliance; "
    "licensed operator judgment prevails."
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Prepare persistence and seed the case system before serving requests."""
    from app.services.case import ensure_case_system
    from app.services.plan import sync_rules

    init_db()
    with session_scope() as session:
        ensure_case_system(session)
        sync_rules(session)
    yield


def create_app() -> FastAPI:
    """Build the application: API contract first, dashboard second."""
    app = FastAPI(
        title="AquaOps ON",
        version=APP_VERSION,
        summary=(
            "Compliance scheduling and chlorine early warning for small "
            "Ontario drinking water systems."
        ),
        description=DISCLAIMER,
        lifespan=lifespan,
    )

    app.include_router(api_router)
    app.include_router(dashboard_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": APP_VERSION}

    return app


app = create_app()
