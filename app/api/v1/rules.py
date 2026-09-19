"""Rules and required-task endpoints (M4), `docs/api-spec.md` §3."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.enums import TaskStatus
from app.schemas.rules import RuleOut, TaskBatch, TaskOut
from app.services.plan import (
    catalogue,
    generate_week_tasks,
    sync_rules,
    week_tasks,
)
from modules.m4_rules import load_rules

router = APIRouter()


@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    verified_only: bool = Query(default=False, alias="verified"),
) -> list[RuleOut]:
    """The rule catalogue as loaded from the versioned YAML.

    Rules whose `verified` flag is false are returned with `inert: true`: they
    are visible so a reviewer can see what is pending, and they never generate a
    duty until the clause has been checked against the consolidated regulation.
    """
    rules = load_rules()
    return [rule for rule in rules if rule.verified] if verified_only else rules


@router.post(
    "/tasks/generate", response_model=TaskBatch, status_code=status.HTTP_201_CREATED
)
def generate_tasks_for_week(
    week: str = Query(pattern=r"^\d{4}-W\d{2}$", description="ISO week, e.g. 2026-W38"),
    system_id: str = "maple-creek",
    include_draft: bool = Query(
        default=False,
        description="also expand rules that are not yet legally verified (labelled draft)",
    ),
    session: Session = Depends(get_session),
) -> TaskBatch:
    sync_rules(session)
    return generate_week_tasks(session, system_id, week, include_draft=include_draft)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    week: str = Query(pattern=r"^\d{4}-W\d{2}$"),
    system_id: str = "maple-creek",
    session: Session = Depends(get_session),
) -> list[TaskOut]:
    return [
        TaskOut(
            id=row.id,
            sampling_point_id=row.sampling_point_id,
            rule_id=row.rule_id,
            window_start=row.window_start,
            window_end=row.window_end,
            status=TaskStatus(row.status),
        )
        for row in week_tasks(session, system_id, week)
    ]


@router.get("/rules/verified", response_model=list[RuleOut])
def verified_catalogue(session: Session = Depends(get_session)) -> list[RuleOut]:
    """Catalogue rows as persisted, showing which have passed legal verification."""
    return [
        RuleOut(
            id=row.id,
            rule_set_id=row.rule_set_id,
            parameter=row.parameter,
            location=row.location,
            frequency=row.frequency,
            source_section=row.source_section,
            hard_constraint=row.hard_constraint,
            verified=row.verified,
            inert=not row.verified,
        )
        for row in catalogue(session)
    ]
