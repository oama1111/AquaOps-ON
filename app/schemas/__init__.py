"""Pydantic contract shared by the REST API, the modules, and M6 (docs/api-spec.md §7)."""

from app.schemas.enums import (
    AlertModel,
    AlertSeverity,
    IngestStatus,
    LocationType,
    PlanStatus,
    ReadingSource,
    ReasonCode,
    TaskStatus,
)
from app.schemas.evaluation import EvalRunOut, MetricOut
from app.schemas.readings import (
    AlertAck,
    AlertContext,
    AlertOut,
    DetectReport,
    ReadingOut,
)
from app.schemas.registry import (
    IngestReceipt,
    IngestStatusOut,
    RowError,
    SamplingPointOut,
    SystemOut,
)
from app.schemas.rules import RuleOut, TaskBatch, TaskGenerationRequest, TaskOut
from app.schemas.scheduling import (
    InfeasibleDetail,
    PlanRequest,
    RoutePlanOut,
    RouteStopOut,
)

__all__ = [
    "AlertAck",
    "AlertContext",
    "AlertModel",
    "AlertOut",
    "AlertSeverity",
    "DetectReport",
    "EvalRunOut",
    "IngestReceipt",
    "IngestStatus",
    "IngestStatusOut",
    "InfeasibleDetail",
    "LocationType",
    "MetricOut",
    "PlanRequest",
    "PlanStatus",
    "ReadingOut",
    "ReadingSource",
    "ReasonCode",
    "RoutePlanOut",
    "RouteStopOut",
    "RowError",
    "RuleOut",
    "SamplingPointOut",
    "SystemOut",
    "TaskBatch",
    "TaskGenerationRequest",
    "TaskOut",
    "TaskStatus",
]
