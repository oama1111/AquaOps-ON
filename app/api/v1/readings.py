"""Readings and anomaly endpoints (M2), `docs/api-spec.md` §2."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AnomalyAlert, ChlorineReading
from app.schemas.enums import AlertModel, AlertSeverity, ReadingSource, ReasonCode
from app.schemas.readings import (
    AlertAck,
    AlertContext,
    AlertOut,
    DetectReport,
    ReadingOut,
)
from app.services.detect import acknowledge, open_alerts, run_detection

router = APIRouter()


def _alert_out(row: AnomalyAlert) -> AlertOut:
    codes = [ReasonCode(code) for code in row.reason_codes.split(",") if code]
    return AlertOut(
        id=row.id,
        sampling_point_id=row.sampling_point_id,
        raised_at=row.raised_at,
        model=AlertModel(row.model),
        severity=AlertSeverity(row.severity),
        context=AlertContext(
            model=AlertModel(row.model),
            ewma_z=row.ewma_z,
            iforest_score=row.iforest_score,
            last_reading_mg_l=row.last_reading_mg_l,
            threshold_mg_l=row.threshold_mg_l,
            reason_codes=codes or [ReasonCode.TREND_TO_LIMIT],
        ),
        acknowledged_by=row.acknowledged_by,
        acknowledged_at=row.acknowledged_at,
    )


@router.get("/readings", response_model=list[ReadingOut])
def list_readings(
    point: str,
    limit: int = Query(default=200, le=500),
    session: Session = Depends(get_session),
) -> list[ReadingOut]:
    rows = session.execute(
        select(ChlorineReading)
        .where(ChlorineReading.sampling_point_id == point)
        .order_by(ChlorineReading.measured_at)
        .limit(limit)
    ).scalars()
    return [
        ReadingOut(
            id=str(row.id),
            sampling_point_id=row.sampling_point_id,
            measured_at=row.measured_at,
            free_chlorine_mg_l=row.free_chlorine_mg_l,
            source=ReadingSource(row.source),
        )
        for row in rows
    ]


@router.post("/readings", response_model=ReadingOut, status_code=status.HTTP_201_CREATED)
def add_reading(
    sampling_point_id: str,
    measured_at: datetime,
    free_chlorine_mg_l: float,
    session: Session = Depends(get_session),
) -> ReadingOut:
    """Record a single reading taken by the operator in the field.

    The (sampling point, measured time) pair is the identity of a reading, and
    the CSV ingest path already treats a repeat of it as a duplicate rather than
    an error. Unit 6 integration testing found that this endpoint did not: a
    second POST for the same point and minute — the field app retrying after a
    dropped connection, or an operator typing a value the laboratory had already
    reported — reached the database and returned a 500 from the resulting
    ``IntegrityError``. A crash on a foreseeable input is a defect, so the
    conflict is now translated into an explicit 409 that names the key, which
    keeps the API's error behaviour as truthful as its success behaviour.
    """
    row = ChlorineReading(
        sampling_point_id=sampling_point_id,
        measured_at=measured_at,
        free_chlorine_mg_l=free_chlorine_mg_l,
        source=ReadingSource.MANUAL.value,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"a reading for point {sampling_point_id} at "
                f"{measured_at.isoformat()} already exists; readings are keyed on "
                "(sampling point, measured time) so re-sending cannot duplicate a "
                "compliance record"
            ),
        ) from error
    return ReadingOut(
        id=str(row.id),
        sampling_point_id=row.sampling_point_id,
        measured_at=row.measured_at,
        free_chlorine_mg_l=row.free_chlorine_mg_l,
        source=ReadingSource(row.source),
    )


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    system_id: str = "maple-creek", session: Session = Depends(get_session)
) -> list[AlertOut]:
    return [_alert_out(row) for row in open_alerts(session, system_id)]


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
def ack_alert(
    alert_id: str, body: AlertAck, session: Session = Depends(get_session)
) -> AlertOut:
    row = acknowledge(session, alert_id, body.operator_id, body.note)
    if row is None:
        raise HTTPException(
            status_code=404, detail="alert not found, or already acknowledged"
        )
    return _alert_out(row)


@router.post("/detect/run", response_model=DetectReport)
def run_detection_now(
    point: str = Query(description="sampling point id, e.g. SP15"),
    session: Session = Depends(get_session),
) -> DetectReport:
    """On-demand M2 pass over one point's stored series (demos and M6 replays)."""
    return run_detection(session, point)
