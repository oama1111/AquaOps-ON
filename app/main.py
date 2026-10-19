"""FastAPI application factory — the single deployable unit (docs/architecture.md §3).

Startup does three things and nothing else: create any missing tables, register
the case system if it is not there yet, and mount the routers. Seeding is
idempotent, so `uvicorn app.main:app` on a fresh clone yields a demonstrable
system with the committed case data already loaded.

Operator authentication (`app/auth.py`) is read from the environment when the
application is built. With ``AQUAOPS_OPERATOR_TOKENS`` unset the system is open,
as it always was on loopback, and startup says so in the log.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Security
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.api.v1.dashboard import router as dashboard_router
from app.auth import TOKENS_ENV, OperatorAuthMiddleware, OperatorTokens, load_tokens
from app.auth import router as auth_router
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


#: Declares the bearer scheme in the OpenAPI document so /docs offers an
#: "Authorize" button. It enforces nothing itself (auto_error=False): the
#: middleware is the single place that decides who gets in.
_bearer_scheme = HTTPBearer(
    auto_error=False,
    description=f"An operator token from {TOKENS_ENV}. Required when that variable is set.",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare persistence and seed the case system before serving requests."""
    from app.services.case import ensure_case_system
    from app.services.plan import sync_rules

    tokens: OperatorTokens = app.state.operator_tokens
    log = logging.getLogger("aquaops.auth")
    if tokens.enabled:
        log.info("operator authentication on for %d operator(s)", len(tokens.digests))
    else:
        log.warning(
            "%s is not set: the API and dashboard are open to anyone who can reach "
            "this port. Acceptable on loopback only; set it before serving off-host.",
            TOKENS_ENV,
        )

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

    # Read once, at build time: a malformed variable fails here, before the
    # server binds, rather than on the first request.
    tokens = load_tokens()
    app.state.operator_tokens = tokens
    app.add_middleware(OperatorAuthMiddleware, tokens=tokens)

    app.include_router(api_router, dependencies=[Security(_bearer_scheme)])
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Open even when authentication is on: the container HEALTHCHECK carries no
    # credentials, and the answer reveals nothing beyond the version.
    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": APP_VERSION}

    return app


app = create_app()
