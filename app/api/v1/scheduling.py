"""Scheduling endpoints (M3), `docs/api-spec.md` §4."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import RoutePlan
from app.schemas.enums import PlanStatus
from app.schemas.scheduling import RoutePlanOut, RouteStopOut
from app.services.plan import build_week_plan, plan_stops

router = APIRouter()


def _plan_out(session: Session, row: RoutePlan) -> RoutePlanOut:
    stops = plan_stops(session, row.id)
    return RoutePlanOut(
        id=row.id,
        system_id=row.system_id,
        week_start=row.week_start,
        status=PlanStatus(row.status),
        solver_ms=row.solver_ms,
        total_km=row.total_km,
        stops=[
            RouteStopOut(
                seq=stop.seq,
                required_task_id=stop.required_task_id,
                sampling_point_id=stop.sampling_point_id,
                vehicle_id=stop.vehicle_id,
                eta=stop.eta,
                travel_km=stop.travel_km,
            )
            for stop in stops
        ],
    )


@router.post("/plans", response_model=RoutePlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(
    week: str = Query(pattern=r"^\d{4}-W\d{2}$"),
    system_id: str = "maple-creek",
    solver: str = Query(default="greedy2opt"),
    session: Session = Depends(get_session),
) -> RoutePlanOut:
    """Build the week's draft plan.

    Infeasibility is reported as a 422 whose body names the tasks and vehicles
    responsible, because "no plan" without a reason is not actionable.
    """
    try:
        row, _baseline = build_week_plan(session, system_id, week, solver=solver)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _plan_out(session, row)


@router.get("/plans/{plan_id}", response_model=RoutePlanOut)
def get_plan(plan_id: str, session: Session = Depends(get_session)) -> RoutePlanOut:
    row = session.get(RoutePlan, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown plan: {plan_id}")
    return _plan_out(session, row)


@router.post("/plans/{plan_id}/confirm", response_model=RoutePlanOut)
def confirm_plan(plan_id: str, session: Session = Depends(get_session)) -> RoutePlanOut:
    row = session.get(RoutePlan, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown plan: {plan_id}")
    if row.status == PlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=409, detail="plan is already confirmed")
    row.status = PlanStatus.CONFIRMED.value
    session.commit()
    return _plan_out(session, row)


@router.get("/plans/{plan_id}/export.csv")
def export_plan(plan_id: str, session: Session = Depends(get_session)) -> Response:
    """Print-friendly route sheet for the cab of the truck."""
    row = session.get(RoutePlan, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown plan: {plan_id}")

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["seq", "sampling_point", "vehicle", "eta", "leg_km"])
    for stop in plan_stops(session, row.id):
        writer.writerow(
            [
                stop.seq,
                stop.sampling_point_id,
                stop.vehicle_id,
                stop.eta.isoformat(),
                stop.travel_km,
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="plan-{plan_id[:8]}.csv"'},
    )
