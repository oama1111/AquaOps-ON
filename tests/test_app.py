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

DEMO_CSV = Path(__file__).resolve().parents[1] / "examples" / "demo_lab_results.csv"


# The `client` fixture comes from conftest.py: it points the application at a
# fresh temporary database per test, so these tests never depend on whatever
# developer database happens to be sitting in the working tree (and never fail
# because that file predates a schema change).


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


def test_api_surface_matches_the_design_document(client: TestClient) -> None:
    """Every endpoint group named in docs/api-spec.md must actually be mounted."""
    paths = set(client.get("/openapi.json").json()["paths"])
    for expected in [
        "/api/v1/systems/{system_id}",
        "/api/v1/systems/{system_id}/sampling-points",
        "/api/v1/systems/{system_id}/ingest/lab-csv",
        "/api/v1/readings",
        "/api/v1/alerts",
        "/api/v1/alerts/{alert_id}/ack",
        "/api/v1/detect/run",
        "/api/v1/rules",
        "/api/v1/tasks/generate",
        "/api/v1/tasks",
        "/api/v1/plans",
        "/api/v1/plans/{plan_id}",
        "/api/v1/plans/{plan_id}/confirm",
        "/api/v1/eval/run",
    ]:
        assert expected in paths, f"{expected} is specified but not mounted"


def test_dashboard_renders_the_decision_support_disclaimer(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "not legal proof of compliance" in response.text
    assert "sampling points" in response.text


def test_demo_lab_export_is_present_and_well_formed() -> None:
    """The demonstration dataset is part of the Unit 4 deliverable."""
    assert DEMO_CSV.is_file()
    header = DEMO_CSV.read_text(encoding="utf-8").splitlines()[0]
    assert header == "sampling_point_id,measured_at,free_chlorine_mg_l"
