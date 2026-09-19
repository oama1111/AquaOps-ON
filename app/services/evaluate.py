"""Evaluation service (M6): replay ground truth and record the frozen verdicts.

The dataset is the committed 48-hour Maple Creek simulation. Its first eight
hours are a hydraulic warm-up in which every point reads 0.0 mg/L — a numerical
start-up artefact, not water quality — so the replay starts once the network has
settled. Scoring the warm-up would be scoring the solver, not the detector.

Because the simulation contains no labelled failures, a decay episode is
injected deterministically into the settled series (`M6.inject_decay`). The
label is then exact by construction, and the identical episode runs in CI every
time, which is what makes the metric comparable across commits.
"""

from __future__ import annotations

import csv
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EvalMetric, EvalRun
from app.services.case import CHLORINE_CSV
from app.services.plan import preview_plan, week_tasks
from modules.m6_eval import (
    EvaluationInputs,
    inject_decay,
    render_report,
    report_path,
    run_evaluation,
)

EVAL_POINT = "SP18"  # dead-end spur: the point the simulation decays fastest
WARMUP_READINGS = 8
DECAY_START = 10
DECAY_END = 30
DECAY_DROP = 0.55


def ground_truth_series(column: str = EVAL_POINT) -> list[float]:
    with CHLORINE_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [float(row[column]) for row in rows][WARMUP_READINGS:]


def missed_window_count(session: Session, system_id: str, week: str, plan: object) -> int:
    """Count planned stops whose ETA falls outside their regulatory window."""
    windows = {
        task.id: (task.window_start, task.window_end)
        for task in week_tasks(session, system_id, week)
    }
    missed = 0
    for stop in getattr(plan, "stops", []):
        window = windows.get(stop.required_task_id)
        if window and not (window[0] <= stop.eta <= window[1]):
            missed += 1
    return missed


def run(
    session: Session,
    system_id: str,
    week: str,
    git_sha: str,
    *,
    coverage: float = 0.0,
    api_p95_ms: float = 0.0,
    dataset: str = "maple-creek-48h",
) -> EvalRun:
    """Replay the case dataset through M2/M3 and store the frozen metric verdicts."""
    settled = ground_truth_series()
    series, labels = inject_decay(
        settled, start=DECAY_START, end=DECAY_END, drop=DECAY_DROP
    )

    plan_km = baseline_km = 0.0
    solver_ms = 0
    missed = 0
    try:
        plan, baseline_km = preview_plan(session, system_id, week)
        plan_km = float(getattr(plan, "total_km", 0.0))
        solver_ms = int(getattr(plan, "solver_ms", 0))
        missed = missed_window_count(session, system_id, week, plan)
    except ValueError:
        # No plan is computable yet (no tasks generated). The remaining metrics
        # still run, and C3/C4 report the honest zero rather than a guess.
        pass

    inputs = EvaluationInputs(
        dataset=dataset,
        git_sha=git_sha,
        series=series,
        labels=labels,
        plan_km=plan_km,
        baseline_km=baseline_km,
        solver_ms=solver_ms,
        missed_windows=missed,
        coverage=coverage,
        # C5 is a system-level property, so its value comes from the benchmark
        # (`examples/unit6_perf.py`) instead of being guessed here. Unit 6
        # closed this integration gap: `EvaluationInputs` declared the field
        # from the start, but no caller supplied it, so C5 reported a hard zero
        # on every stored run and could never pass.
        api_p95_ms=api_p95_ms,
    )
    metrics = run_evaluation(inputs)

    row = EvalRun(
        id=str(uuid.uuid4()),
        git_sha=git_sha,
        dataset=dataset,
        started_at=datetime.utcnow(),
    )
    session.add(row)
    for metric in metrics:
        session.add(
            EvalMetric(
                run_id=row.id,
                name=metric.name,
                value=metric.value,
                target=metric.target,
                passed=metric.passed,
            )
        )
    session.commit()

    target = report_path(git_sha)
    target.write_text(render_report(inputs, metrics), encoding="utf-8")
    return row


def latest_run(session: Session) -> EvalRun | None:
    return session.execute(
        select(EvalRun).order_by(EvalRun.started_at.desc()).limit(1)
    ).scalars().first()


def metrics_for(session: Session, run_id: str) -> list[EvalMetric]:
    return list(
        session.execute(select(EvalMetric).where(EvalMetric.run_id == run_id)).scalars()
    )


def evidence_dir() -> Path:
    return report_path("0000000").parent
