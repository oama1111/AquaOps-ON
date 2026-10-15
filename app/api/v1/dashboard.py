"""Dashboard fragments (M5, HTMX) — server-rendered HTML, not public API.

`docs/api-spec.md` §5 keeps these outside `/api/v1` because they are consumed by
HTMX and versioned with the application rather than with the contract.

The POST routes are the operator's actions. Each answers an HTMX request with
the fragment it changed, and a plain form post (no JavaScript) with the whole
page or a redirect back to it, so every action works either way. They call the
same services as the `/api/v1` endpoints; nothing here has its own rules.
"""

from __future__ import annotations

import io
import re
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ChlorineReading, RoutePlan, SamplingPoint, WaterSystem
from app.schemas.enums import PlanStatus
from app.services.case import CASE_SYSTEM_ID
from app.services.detect import acknowledge, alerts_by_point, open_alerts
from app.services.ingest import ingest_lab_csv
from app.services.plan import (
    build_week_plan,
    generate_week_tasks,
    latest_plan,
    sync_rules,
    week_tasks,
)
from modules.m2_detect import DEFAULT_THRESHOLD_MG_L, WATCH_LEVEL_MG_L
from modules.m4_rules import week_bounds
from modules.m5_dashboard import (
    export_weekly_summary,
    render_alert_inbox,
    render_network_status,
    render_shell,
    render_trend,
    render_upload_receipt,
    render_week_view,
    trend_geometry,
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


_ISO_WEEK = re.compile(r"^\d{4}-W\d{2}$")


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _html(content: str, *, trigger: str | None = None) -> Response:
    headers = {"HX-Trigger": trigger} if trigger else None
    return Response(content=content, media_type="text/html", headers=headers)


def _week_html(session: Session, week: str, notice: str | None = None) -> str:
    return render_week_view(
        week_tasks(session, CASE_SYSTEM_ID, week),
        latest_plan(session, CASE_SYSTEM_ID),
        week=week,
        notice=notice,
    )


def _default_trend_point(session: Session, point_ids: list[str]) -> str | None:
    """The point with the newest open alert, else the first point with readings."""
    alerts = open_alerts(session, CASE_SYSTEM_ID)
    if alerts:
        return alerts[0].sampling_point_id
    with_readings = set(
        session.execute(select(ChlorineReading.sampling_point_id).distinct()).scalars()
    )
    for point_id in point_ids:
        if point_id in with_readings:
            return point_id
    return point_ids[0] if point_ids else None


def _trend_html(session: Session, point: str | None) -> str:
    point_ids = list(
        session.execute(
            select(SamplingPoint.id)
            .where(SamplingPoint.system_id == CASE_SYSTEM_ID)
            .order_by(SamplingPoint.id)
        ).scalars()
    )
    if point not in point_ids:
        point = _default_trend_point(session, point_ids)
    series = [
        (measured_at, value)
        for measured_at, value in session.execute(
            select(ChlorineReading.measured_at, ChlorineReading.free_chlorine_mg_l)
            .where(ChlorineReading.sampling_point_id == point)
            .order_by(ChlorineReading.measured_at)
        ).all()
    ]
    chart = (
        trend_geometry(
            point,
            series,
            threshold_mg_l=DEFAULT_THRESHOLD_MG_L,
            watch_mg_l=WATCH_LEVEL_MG_L,
        )
        if point
        else None
    )
    return render_trend(point_ids, point, chart)


def _page(session: Session, *, point: str | None = None, upload_html: str = "") -> Response:
    system = session.get(WaterSystem, CASE_SYSTEM_ID)
    if system is None:
        raise HTTPException(status_code=503, detail="case system is not seeded")
    html = render_shell(
        system_name=system.name,
        network_html=render_network_status(
            _point_views(session, CASE_SYSTEM_ID), alerts_by_point(session, CASE_SYSTEM_ID)
        ),
        alerts_html=render_alert_inbox(open_alerts(session, CASE_SYSTEM_ID)),
        week_html=_week_html(session, current_week()),
        trend_html=_trend_html(session, point),
        upload_html=upload_html,
    )
    return _html(html)


@router.get("/", response_class=Response)
def dashboard(
    point: str | None = Query(default=None, description="sampling point for the trend chart"),
    session: Session = Depends(get_session),
) -> Response:
    return _page(session, point=point)


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
    return _html(_week_html(session, week or current_week()))


@router.get("/dash/trend", response_class=Response)
def dash_trend(
    point: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _html(_trend_html(session, point))


@router.post("/dash/upload", response_class=Response)
async def dash_upload(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> Response:
    """Laboratory CSV from the dashboard: the same M1 ingest path as the API."""
    filename = file.filename or "upload.csv"
    content = await file.read()
    changed = False
    try:
        _upload, parsed, inserted = ingest_lab_csv(session, CASE_SYSTEM_ID, filename, content)
    except UnicodeDecodeError:
        receipt = render_upload_receipt(
            filename=filename,
            problem="This file is not a UTF-8 text CSV. Export the laboratory "
            "results as CSV (not a spreadsheet file) and upload that.",
        )
    else:
        if parsed.rows_total == 0:
            receipt = render_upload_receipt(
                filename=filename,
                problem="The file has no data rows. Check that it has a header row "
                "and at least one result below it.",
            )
        else:
            receipt = render_upload_receipt(
                filename=filename,
                rows_total=parsed.rows_total,
                rows_accepted=inserted,
                errors=parsed.errors,
            )
            changed = inserted > 0
    if _is_htmx(request):
        return _html(receipt, trigger="readings-changed" if changed else None)
    return _page(session, upload_html=receipt)


@router.post("/dash/alerts/{alert_id}/ack", response_class=Response)
def dash_acknowledge(
    alert_id: str,
    request: Request,
    operator_id: str = Form(default=""),
    note: str = Form(default=""),
    session: Session = Depends(get_session),
) -> Response:
    """Acknowledge from the inbox. Recorded with who and when; never a delete."""
    operator_id = operator_id.strip()
    notice = None
    if not operator_id:
        notice = "Enter your operator ID to acknowledge an alert."
    elif acknowledge(session, alert_id, operator_id, note.strip() or None) is None:
        notice = "That alert was already acknowledged, or no longer exists."
    if not _is_htmx(request):
        return RedirectResponse("/#alert-inbox", status_code=303)
    return _html(
        render_alert_inbox(open_alerts(session, CASE_SYSTEM_ID), notice=notice),
        trigger=None if notice else "alerts-changed",
    )


@router.post("/dash/week/plan", response_class=Response)
def dash_plan_week(
    request: Request,
    week: str = Form(default=""),
    session: Session = Depends(get_session),
) -> Response:
    """Generate the week's duties from the rule catalogue, then plan the routes."""
    week = week or current_week()
    notice = None
    if not _ISO_WEEK.match(week):
        notice = f"{week!r} is not an ISO week such as 2026-W40."
        week = current_week()
    else:
        sync_rules(session)
        generate_week_tasks(session, CASE_SYSTEM_ID, week)
        try:
            build_week_plan(session, CASE_SYSTEM_ID, week)
        except ValueError as error:
            notice = f"No plan was made: {error}."
    if not _is_htmx(request):
        return RedirectResponse("/#week-view", status_code=303)
    return _html(_week_html(session, week, notice))


@router.post("/dash/week/confirm", response_class=Response)
def dash_confirm_plan(
    request: Request,
    plan_id: str = Form(...),
    week: str = Form(default=""),
    session: Session = Depends(get_session),
) -> Response:
    """Confirm a draft plan, the operator's commitment to run it."""
    week = week if _ISO_WEEK.match(week) else current_week()
    notice = None
    row = session.get(RoutePlan, plan_id)
    if row is None:
        notice = "That plan no longer exists."
    elif row.status == PlanStatus.CONFIRMED.value:
        notice = "This plan is already confirmed."
    else:
        row.status = PlanStatus.CONFIRMED.value
        session.commit()
    if not _is_htmx(request):
        return RedirectResponse("/#week-view", status_code=303)
    return _html(_week_html(session, week, notice))


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
