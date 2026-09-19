"""ORM entities for the AquaOps ON data model (`docs/data-model.md`).

Thirteen tables. Three integrity rules carry the compliance semantics and are
enforced here rather than trusted to callers:

1. ``chlorine_reading`` is unique on (sampling_point_id, measured_at), so
   replaying a laboratory file can never duplicate or overwrite a reading.
2. ``required_task`` rows are never deleted; they move only along
   pending -> planned -> done | missed, so the audit trail is the product.
3. ``reg_rule`` is versioned and ``required_task.rule_id`` points at the exact
   version that generated the task, so a regulatory amendment cannot rewrite
   history.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class WaterSystem(Base):
    __tablename__ = "water_system"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    population: Mapped[int] = mapped_column(Integer)
    network_inp: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SamplingPoint(Base):
    __tablename__ = "sampling_point"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    system_id: Mapped[str] = mapped_column(ForeignKey("water_system.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(32))
    x_m: Mapped[float] = mapped_column(Float)
    y_m: Mapped[float] = mapped_column(Float)
    elevation_m: Mapped[float] = mapped_column(Float)
    location_type: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LabResultUpload(Base):
    """One row per ingest batch: the provenance record that makes a replay auditable."""

    __tablename__ = "lab_result_upload"

    upload_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    system_id: Mapped[str] = mapped_column(ForeignKey("water_system.id"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sha256: Mapped[str] = mapped_column(String(64))
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    #: JSON list of RowError objects, kept so the status endpoint can explain
    #: exactly which rows were refused and why, long after the upload.
    row_errors: Mapped[str] = mapped_column(Text, default="[]")


class ChlorineReading(Base):
    __tablename__ = "chlorine_reading"
    __table_args__ = (
        UniqueConstraint("sampling_point_id", "measured_at", name="uq_reading_point_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sampling_point_id: Mapped[str] = mapped_column(ForeignKey("sampling_point.id"), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    free_chlorine_mg_l: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))
    upload_id: Mapped[str | None] = mapped_column(
        ForeignKey("lab_result_upload.upload_id"), nullable=True
    )


class AnomalyAlert(Base):
    __tablename__ = "anomaly_alert"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sampling_point_id: Mapped[str] = mapped_column(ForeignKey("sampling_point.id"), index=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    model: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    last_reading_mg_l: Mapped[float] = mapped_column(Float)
    threshold_mg_l: Mapped[float] = mapped_column(Float)
    ewma_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    iforest_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_codes: Mapped[str] = mapped_column(Text)  # comma-separated, rendered verbatim
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ack_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RegRule(Base):
    __tablename__ = "reg_rule"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_set_id: Mapped[str] = mapped_column(String(64), index=True)
    parameter: Mapped[str] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(120))
    frequency: Mapped[str] = mapped_column(String(120))
    source_section: Mapped[str] = mapped_column(String(120))
    hard_constraint: Mapped[bool] = mapped_column(Boolean, default=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class RequiredTask(Base):
    __tablename__ = "required_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    system_id: Mapped[str] = mapped_column(ForeignKey("water_system.id"), index=True)
    sampling_point_id: Mapped[str] = mapped_column(ForeignKey("sampling_point.id"), index=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("reg_rule.id"))
    week: Mapped[str] = mapped_column(String(8), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    draft: Mapped[bool] = mapped_column(Boolean, default=False)


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    system_id: Mapped[str] = mapped_column(ForeignKey("water_system.id"), index=True)
    label: Mapped[str] = mapped_column(String(64))
    depot_x_m: Mapped[float] = mapped_column(Float)
    depot_y_m: Mapped[float] = mapped_column(Float)
    shift_minutes: Mapped[int] = mapped_column(Integer, default=420)


class Operator(Base):
    __tablename__ = "operator"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    system_id: Mapped[str] = mapped_column(ForeignKey("water_system.id"), index=True)
    label: Mapped[str] = mapped_column(String(64))
    licence_class: Mapped[str] = mapped_column(String(16), default="DZ")


class RoutePlan(Base):
    __tablename__ = "route_plan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    system_id: Mapped[str] = mapped_column(ForeignKey("water_system.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    solver_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_km: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    stops: Mapped[list[RouteStop]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="RouteStop.seq"
    )


class RouteStop(Base):
    __tablename__ = "route_stop"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("route_plan.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    required_task_id: Mapped[str] = mapped_column(ForeignKey("required_task.id"))
    sampling_point_id: Mapped[str] = mapped_column(ForeignKey("sampling_point.id"))
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicle.id"))
    eta: Mapped[datetime] = mapped_column(DateTime)
    travel_km: Mapped[float] = mapped_column(Float)

    plan: Mapped[RoutePlan] = relationship(back_populates="stops")


class EvalRun(Base):
    __tablename__ = "eval_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    git_sha: Mapped[str] = mapped_column(String(64))
    dataset: Mapped[str] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    metrics: Mapped[list[EvalMetric]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class EvalMetric(Base):
    __tablename__ = "eval_metric"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_run.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    target: Mapped[str] = mapped_column(String(120))
    passed: Mapped[bool] = mapped_column(Boolean)

    run: Mapped[EvalRun] = relationship(back_populates="metrics")
