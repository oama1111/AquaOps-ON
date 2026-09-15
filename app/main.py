"""FastAPI application factory — the single deployable unit (docs/architecture.md §3)."""

from __future__ import annotations

from fastapi import FastAPI

APP_VERSION = "0.1.0"

DISCLAIMER = (
    "Decision support only. Output is not legal proof of compliance; "
    "licensed operator judgment prevails."
)


def create_app() -> FastAPI:
    """Build the application; module routers mount here in Unit 4."""
    app = FastAPI(
        title="AquaOps ON",
        version=APP_VERSION,
        summary=(
            "Compliance scheduling and chlorine early warning for small "
            "Ontario drinking water systems."
        ),
        description=DISCLAIMER,
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": APP_VERSION}

    return app


app = create_app()
