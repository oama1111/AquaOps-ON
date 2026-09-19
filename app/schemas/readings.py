"""M2 readings & anomaly contract (docs/api-spec.md §2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.enums import AlertModel, AlertSeverity, ReadingSource, ReasonCode

CHLORINE_SANITY_MIN = 0.0
CHLORINE_SANITY_MAX = 5.0


class ReadingOut(BaseModel):
    id: str
    sampling_point_id: str
    measured_at: datetime
    free_chlorine_mg_l: float = Field(ge=CHLORINE_SANITY_MIN, le=CHLORINE_SANITY_MAX)
    source: ReadingSource


class AlertContext(BaseModel):
    """The explainability payload; never optional on an alert."""

    model: AlertModel
    ewma_z: float | None
    iforest_score: float | None
    last_reading_mg_l: float
    threshold_mg_l: float
    reason_codes: list[ReasonCode] = Field(min_length=1)


class AlertOut(BaseModel):
    id: str
    sampling_point_id: str
    raised_at: datetime
    model: AlertModel
    severity: AlertSeverity
    context: AlertContext
    acknowledged_by: str | None
    acknowledged_at: datetime | None


class AlertAck(BaseModel):
    operator_id: str
    note: str | None = None


class DetectReport(BaseModel):
    sampling_point_id: str
    from_at: datetime
    to_at: datetime
    readings_evaluated: int
    alerts_raised: int
    #: Alerts that already existed open for this point and were updated in place
    #: rather than duplicated. Added in Unit 6 so a repeated detection pass is
    #: auditable instead of silently multi-counting the operator's inbox.
    alerts_refreshed: int = 0
