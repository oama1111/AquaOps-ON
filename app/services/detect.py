"""Detection service (M2): turn stored readings into explainable alerts.

The alert payload is not summarised on the way to the database. Reason codes,
model scores, the last reading, and the threshold are all persisted, because the
API specification makes it a requirement that the operator interface renders
them verbatim — an alert an operator cannot interrogate is an alert they will
eventually learn to ignore.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnomalyAlert, ChlorineReading, SamplingPoint
from app.schemas.enums import AlertSeverity
from app.schemas.readings import DetectReport
from modules.m2_detect import DEFAULT_THRESHOLD_MG_L, detect


def series_for_point(session: Session, point_id: str) -> list[tuple[str, float]]:
    rows = session.execute(
        select(ChlorineReading.measured_at, ChlorineReading.free_chlorine_mg_l)
        .where(ChlorineReading.sampling_point_id == point_id)
        .order_by(ChlorineReading.measured_at)
    ).all()
    return [(measured_at.isoformat(), value) for measured_at, value in rows]


def run_detection(
    session: Session,
    sampling_point_id: str,
    *,
    threshold_mg_l: float = DEFAULT_THRESHOLD_MG_L,
) -> DetectReport:
    """Run one M2 pass over a point's stored series and persist any alert."""
    series = series_for_point(session, sampling_point_id)
    alerts = detect(
        sampling_point_id,
        series,
        threshold_mg_l=threshold_mg_l,
    )

    # One open alert per point, refreshed rather than duplicated.
    #
    # Unit 6 integration testing found that a second pass over an unchanged
    # series inserted a second identical row: `POST /detect/run` twice, or the
    # nightly batch overlapping a manual pass, filled the operator's inbox with
    # copies of one condition. That is not cosmetic. The C2 budget is "at most
    # two false alarms per point per week", and an inbox that multiplies every
    # alarm by the number of times the detector ran would breach that budget
    # without a single extra false positive. The alert is therefore keyed on the
    # point while it is unacknowledged: a new verdict updates the existing row
    # with the latest scores and reason codes and leaves `raised_at` alone, so
    # the operator still sees when the condition first appeared.
    open_by_point = {
        row.sampling_point_id: row
        for row in session.execute(
            select(AnomalyAlert).where(
                AnomalyAlert.sampling_point_id == sampling_point_id,
                AnomalyAlert.acknowledged_at.is_(None),
            )
        ).scalars()
    }

    raised = 0
    refreshed = 0
    for alert in alerts:
        existing = open_by_point.get(alert.sampling_point_id)
        if existing is not None:
            existing.model = alert.model.value
            existing.severity = alert.severity.value
            existing.last_reading_mg_l = alert.context.last_reading_mg_l
            existing.threshold_mg_l = alert.context.threshold_mg_l
            existing.ewma_z = alert.context.ewma_z
            existing.iforest_score = alert.context.iforest_score
            existing.reason_codes = ",".join(
                code.value for code in alert.context.reason_codes
            )
            refreshed += 1
            continue
        session.add(
            AnomalyAlert(
                id=alert.id,
                sampling_point_id=alert.sampling_point_id,
                raised_at=alert.raised_at,
                model=alert.model.value,
                severity=alert.severity.value,
                last_reading_mg_l=alert.context.last_reading_mg_l,
                threshold_mg_l=alert.context.threshold_mg_l,
                ewma_z=alert.context.ewma_z,
                iforest_score=alert.context.iforest_score,
                reason_codes=",".join(code.value for code in alert.context.reason_codes),
            )
        )
        raised += 1
    session.commit()

    first = datetime.fromisoformat(series[0][0]) if series else datetime.min
    last = datetime.fromisoformat(series[-1][0]) if series else datetime.min
    return DetectReport(
        sampling_point_id=sampling_point_id,
        from_at=first,
        to_at=last,
        readings_evaluated=len(series),
        alerts_raised=raised,
        alerts_refreshed=refreshed,
    )


def run_detection_all(session: Session, system_id: str) -> list[DetectReport]:
    """Nightly-batch behaviour: one pass over every active point in the system."""
    points = session.execute(
        select(SamplingPoint.id).where(SamplingPoint.system_id == system_id)
    ).scalars()
    return [run_detection(session, point_id) for point_id in points]


def open_alerts(session: Session, system_id: str) -> list[AnomalyAlert]:
    rows = session.execute(
        select(AnomalyAlert)
        .join(SamplingPoint, SamplingPoint.id == AnomalyAlert.sampling_point_id)
        .where(SamplingPoint.system_id == system_id, AnomalyAlert.acknowledged_at.is_(None))
        .order_by(AnomalyAlert.raised_at.desc())
    ).scalars()
    return list(rows)


def alerts_by_point(session: Session, system_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for alert in open_alerts(session, system_id):
        counts[alert.sampling_point_id] = counts.get(alert.sampling_point_id, 0) + 1
    return counts


def acknowledge(
    session: Session, alert_id: str, operator_id: str, note: str | None = None
) -> AnomalyAlert | None:
    """Record the operator's explicit acknowledgement; never destructive."""
    alert = session.get(AnomalyAlert, alert_id)
    if alert is None or alert.acknowledged_at is not None:
        return None
    alert.acknowledged_by = operator_id
    alert.acknowledged_at = datetime.utcnow()
    # The note gets its own column. Appending it to `reason_codes` would corrupt
    # the explainability payload, which the API parses back into the ReasonCode
    # enumeration and the operator interface is required to render verbatim.
    alert.ack_note = note
    session.commit()
    return alert


def alert_count(session: Session, system_id: str) -> int:
    return len(open_alerts(session, system_id))


def severity_label(alert: AnomalyAlert) -> str:
    return AlertSeverity(alert.severity).value


def new_alert_id() -> str:
    return str(uuid.uuid4())
