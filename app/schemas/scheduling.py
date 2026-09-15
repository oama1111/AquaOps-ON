"""M3 scheduling contract (docs/api-spec.md §4)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.enums import PlanStatus


class PlanRequest(BaseModel):
    week_start: date
    solver: str = "greedy2opt"
    vehicle_ids: list[str] = Field(min_length=1)


class RouteStopOut(BaseModel):
    seq: int
    required_task_id: str
    sampling_point_id: str
    vehicle_id: str
    eta: datetime
    travel_km: float


class RoutePlanOut(BaseModel):
    """Carries solver_ms and total_km so M6 can assert C4–C5 on every run."""

    id: str
    system_id: str
    week_start: date
    status: PlanStatus
    solver_ms: int
    total_km: float
    stops: list[RouteStopOut]


class InfeasibleDetail(BaseModel):
    """Reported explicitly — an actionable signal, not a bare 422."""

    vehicle_id: str
    blocking_task_ids: list[str]
    window_start: datetime
    window_end: datetime
    reason: str
