"""Enumerations shared by the API contract and the data model."""

from __future__ import annotations

from enum import StrEnum


class LocationType(StrEnum):
    LOOPED = "looped"
    DEAD_END = "dead_end"


class ReadingSource(StrEnum):
    LAB_CSV = "lab_csv"
    API = "api"
    MANUAL = "manual"
    SIMULATION = "simulation"


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNED = "planned"
    DONE = "done"
    MISSED = "missed"


class AlertModel(StrEnum):
    EWMA = "ewma"
    IFOREST = "iforest"
    ENSEMBLE = "ensemble"


class AlertSeverity(StrEnum):
    WATCH = "watch"
    ACTION = "action"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class ReasonCode(StrEnum):
    """Explainability codes the UI is required to render verbatim."""

    EWMA_SHIFT_UP = "ewma_shift_up"
    IFOREST_OUTLIER = "iforest_outlier"
    TREND_TO_LIMIT = "trend_to_limit"


class IngestStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    REJECTED = "rejected"
