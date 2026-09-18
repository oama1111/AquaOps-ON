"""Version 1 of the AquaOps ON HTTP API (`docs/api-spec.md`).

The router groups mirror the modules so that a reader can move between the
design document, the module, and the endpoint without a translation step.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import evaluation, readings, registry, rules, scheduling

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(registry.router, tags=["registry (M1)"])
api_router.include_router(readings.router, tags=["readings & detection (M2)"])
api_router.include_router(rules.router, tags=["rules & tasks (M4)"])
api_router.include_router(scheduling.router, tags=["scheduling (M3)"])
api_router.include_router(evaluation.router, tags=["evaluation (M6)"])

__all__ = ["api_router"]
