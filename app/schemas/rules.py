"""M4 rules & required-task contract (docs/api-spec.md §3)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.enums import TaskStatus


class RuleOut(BaseModel):
    """One entry of rules/oreg170.yaml after load."""

    id: str
    rule_set_id: str
    parameter: str
    location: str
    frequency: str
    source_section: str
    hard_constraint: bool
    verified: bool
    inert: bool


class TaskOut(BaseModel):
    id: str
    sampling_point_id: str
    rule_id: str
    window_start: datetime
    window_end: datetime
    status: TaskStatus


class TaskGenerationRequest(BaseModel):
    week: str = Field(pattern=r"^\d{4}-W\d{2}$")


class TaskBatch(BaseModel):
    week: str
    generated: int
    inert_rules_skipped: int
    tasks: list[TaskOut]
