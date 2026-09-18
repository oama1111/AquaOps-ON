"""Dashboard fragments (M5, HTMX) — server-rendered HTML, not public API.

`docs/api-spec.md` §5 keeps these outside `/api/v1` because they are consumed by
HTMX and versioned with the application rather than with the contract.
"""

from __future__ import annotations

import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ChlorineReading, SamplingPoint, WaterSystem
from app.services.case import CASE_SYSTEM_ID
from app.services.detect import alerts_by_point, open_alerts
from app.services.plan import latest_plan, week_tasks
from modules.m4_rules import week_bounds
from modules.m5_dashboard import (
    export_weekly_summary,
    render_alert_inbox,
    render_network_status,
    render_shell,
    render_week_view,
)

router = APIRouter(include_in_schema=False)


class _PointView:
    """A point plus its latest reading, shaped for the template."""

    def __init__(self, row: SamplingPoint, last_reading: float | None) -> None:
        self.id = row.id
        self.node_id = row.node_id
        self.location_type = row.location_type
        self.last_reading = last_reading


def _latest_readings(session: Session) -> dict[str, float]:
    latest: dict[str, float] = {}
    rows = session.execute(
        select(
            ChlorineReading.sampling_point_id,
            ChlorineReading.measured_at,
            ChlorineReading.free_chlorine_mg_l,
        ).order_by(ChlorineReading.measured_at)
    ).all()
    for point_id, _measured_at, value in rows:
        latest[point_id] = value
    return latest


def _point_views(session: Session, system_id: str) -> list[_PointView]:
    latest = _latest_readings(session)
    points = session.execute(
        select(SamplingPoint)
        .where(SamplingPoint.system_id == system_id)
        .order_by(SamplingPoint.id)
    ).scalars()
    return [_PointView(row, latest.get(row.id)) for row in points]


def current_week() -> str:
    today = date.today()
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


@router.get("/", response_class=Response)
def dashboard(session: Session = Depends(get_session)) -> Response:
    system = session.get(WaterSystem, CASE_SYSTEM_ID)
    if system is None:
        raise HTTPException(status_code=503, detail="case system is not seeded")
    week = current_week()
    plan = latest_plan(session, CASE_SYSTEM_ID)
    tasks = week_tasks(session, CASE_SYSTEM_ID, week)
    html = render_shell(
        system_name=system.name,
        network_html=render_network_status(
            _point_views(session, CASE_SYSTEM_ID), alerts_by_point(session, CASE_SYSTEM_ID)
        ),
        alerts_html=render_alert_inbox(open_alerts(session, CASE_SYSTEM_ID)),
        week_html=render_week_view(tasks, plan),
    )
    return Response(content=html, media_type="text/html")


@router.get("/dash/network", response_class=Response)
def dash_network(session: Session = Depends(get_session)) -> Response:
    html = render_network_status(
        _point_views(session, CASE_SYSTEM_ID), alerts_by_point(session, CASE_SYSTEM_ID)
    )
    return Response(content=html, media_type="text/html")


@router.get("/dash/alerts", response_class=Response)
def dash_alerts(session: Session = Depends(get_session)) -> Response:
    html = render_alert_inbox(open_alerts(session, CASE_SYSTEM_ID))
    return Response(content=html, media_type="text/html")


@router.get("/dash/week", response_class=Response)
def dash_week(
    week: str = Query(default="", description="ISO week; defaults to the current one"),
    session: Session = Depends(get_session),
) -> Response:
    week = week or current_week()
    html = render_week_view(week_tasks(session, CASE_SYSTEM_ID, week), latest_plan(session, CASE_SYSTEM_ID))
    return Response(content=html, media_type="text/html")


@router.get("/dash/export/week.csv")
def dash_export(
    week: str = Query(default=""),
    session: Session = Depends(get_session),
) -> Response:
    """Weekly compliance summary for municipal records."""
    week = week or current_week()
    tasks = week_tasks(session, CASE_SYSTEM_ID, week)
    plan = latest_plan(session, CASE_SYSTEM_ID)
    buffer = io.StringIO()
    export_weekly_summary(buffer, tasks, plan)
    start, _end = week_bounds(week)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="compliance-{start.date()}.csv"'
        },
    )
