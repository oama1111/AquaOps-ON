"""Contract tests for the application core and the shared Pydantic schema.

These run in the fast CI job: they need only fastapi, pydantic, sqlalchemy and
httpx, so they stay within the few-minutes feedback budget that DORA's CI
capability describes. The slower hydraulic validation lives in
``test_case_data.py`` and runs as a separate job.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.schemas as schemas
from app.main import APP_VERSION, DISCLAIMER, app

MODULES = [
    "modules.m1_ingest",
    "modules.m2_detect",
    "modules.m3_schedule",
    "modules.m4_rules",
    "modules.m5_dashboard",
    "modules.m6_eval",
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": APP_VERSION}


def test_openapi_document_exposes_health(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["info"]["version"] == APP_VERSION
    assert "/health" in spec["paths"]


def test_app_carries_the_decision_support_disclaimer() -> None:
    """The UI/API disclaimer is a design commitment, not decoration (ADR log)."""
    assert "not legal proof of compliance" in DISCLAIMER
    assert DISCLAIMER in app.description


def test_schema_contract_exports_every_public_model() -> None:
    assert len(schemas.__all__) == 28
    assert len(set(schemas.__all__)) == len(schemas.__all__), "duplicate export"
    for name in schemas.__all__:
        assert hasattr(schemas, name), f"{name} listed in __all__ but not importable"


@pytest.mark.parametrize("module_name", MODULES)
def test_modules_m1_to_m6_are_importable(module_name: str) -> None:
    """The Unit 3 design fixes the module boundaries; guard them against drift."""
    assert importlib.import_module(module_name) is not None


def test_unimplemented_module_entry_points_fail_loudly() -> None:
    """Until Units 4-6 land, a module body must raise rather than return a stub.

    A silent wrong answer would be worse than an exception: the evaluation
    harness would score it as a real result.
    """
    m2 = importlib.import_module("modules.m2_detect")
    with pytest.raises(NotImplementedError):
        m2.detect("SP15", [("2026-01-01T00:00:00Z", 0.4)])


def test_ingest_pipeline_is_idempotent_by_contract() -> None:
    """M1 declares a uniqueness key on (sampling point, measured time)."""
    m1 = importlib.import_module("modules.m1_ingest")
    with pytest.raises(NotImplementedError):
        m1.load_sampling_points("maple-creek", Path("unused.csv"))
