"""Evaluation endpoints (M6), `docs/api-spec.md` §6."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import EvalRun
from app.schemas.evaluation import EvalRunOut, MetricOut
from app.services.evaluate import metrics_for, run

router = APIRouter()


def _run_out(session: Session, row: EvalRun) -> EvalRunOut:
    return EvalRunOut(
        id=row.id,
        git_sha=row.git_sha,
        dataset=row.dataset,
        started_at=row.started_at,
        metrics=[
            MetricOut(name=m.name, value=m.value, target=m.target, passed=m.passed)
            for m in metrics_for(session, row.id)
        ],
    )


@router.post("/eval/run", response_model=EvalRunOut, status_code=status.HTTP_201_CREATED)
def run_evaluation_now(
    week: str = Query(pattern=r"^\d{4}-W\d{2}$"),
    system_id: str = "maple-creek",
    git_sha: str = Query(default="unknown"),
    coverage: float = Query(default=0.0, ge=0.0, le=1.0),
    api_p95_ms: float = Query(
        default=0.0,
        ge=0.0,
        description=(
            "measured p95 API latency in ms, supplied by the Unit 6 benchmark so "
            "that C5 is evaluated from a real measurement rather than assumed"
        ),
    ),
    session: Session = Depends(get_session),
) -> EvalRunOut:
    """Replay the case dataset through M2/M3 and store the frozen verdicts."""
    row = run(
        session, system_id, week, git_sha, coverage=coverage, api_p95_ms=api_p95_ms
    )
    return _run_out(session, row)


@router.get("/eval/runs/{run_id}", response_model=EvalRunOut)
def get_run(run_id: str, session: Session = Depends(get_session)) -> EvalRunOut:
    row = session.get(EvalRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    return _run_out(session, row)
