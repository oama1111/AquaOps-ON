"""M1 registry & ingest contract (docs/api-spec.md §1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.enums import IngestStatus, LocationType


class SystemOut(BaseModel):
    id: str
    name: str
    population: int
    network_inp: str
    point_count: int
    vehicle_count: int


class SamplingPointOut(BaseModel):
    id: str
    system_id: str
    node_id: str
    x_m: float
    y_m: float
    elevation_m: float
    location_type: LocationType
    active: bool


class IngestReceipt(BaseModel):
    """Returned immediately; acceptance is asynchronous and idempotent."""

    upload_id: str
    rows_total: int
    rows_accepted: int
    rows_rejected: int


class RowError(BaseModel):
    row_number: int
    field: str
    message: str


class IngestStatusOut(BaseModel):
    upload_id: str
    status: IngestStatus
    rows_accepted: int
    rows_rejected: int
    row_errors: list[RowError] = Field(default_factory=list)
