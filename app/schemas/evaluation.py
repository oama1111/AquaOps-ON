"""M6 evaluation contract (docs/api-spec.md §6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MetricOut(BaseModel):
    """One frozen criterion from the Unit 2 proposal, with its verdict."""

    name: str
    value: float
    target: str
    passed: bool


class EvalRunOut(BaseModel):
    id: str
    git_sha: str
    dataset: str
    started_at: datetime
    metrics: list[MetricOut]
