"""M4 — O. Reg. 170/03 rule engine: versioned YAML → required tasks (ADR-2).

The safety property this module owns: **a rule that has not been verified
against the consolidated regulation is inert.** It loads, it is visible in the
catalogue, and it generates nothing. An unverified guess must never become a
compliance schedule, because the operator's licence — not the software's — is
on the line when a window is missed.

`include_unverified=True` exists for demonstrations and tests: it produces
clearly-labelled draft tasks so the mechanism can be shown end to end while the
clause-by-clause check against CanLII is still open (repository issue #4). The
default path never consults it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.schemas.enums import TaskStatus
from app.schemas.registry import SamplingPointOut
from app.schemas.rules import RuleOut, TaskBatch, TaskOut

RULES_PATH = Path(__file__).resolve().parents[1] / "rules" / "oreg170.yaml"

#: Namespace for deterministic task ids: regenerating the same week twice
#: produces the same ids, so the uniqueness constraint catches a double-run
#: instead of silently duplicating the operator's duty list.
TASK_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def load_rules(yaml_path: Path = RULES_PATH) -> list[RuleOut]:
    """Flatten the rule catalogue; `verified: false` entries load as inert."""
    document: dict[str, Any] = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}
    rules: list[RuleOut] = []
    for rule_set in document.get("rule_sets", []):
        set_id = rule_set.get("id", "unnamed")
        for entry in rule_set.get("rules", []):
            verified = bool(entry.get("verified", False))
            rules.append(
                RuleOut(
                    id=entry["id"],
                    rule_set_id=set_id,
                    parameter=str(entry.get("parameter", "")),
                    location=str(entry.get("location", "")),
                    frequency=str(entry.get("frequency", "")),
                    source_section=str(entry.get("source_section", "")),
                    hard_constraint=bool(entry.get("hard_constraint", False)),
                    verified=verified,
                    inert=not verified,
                )
            )
    return rules


def week_bounds(week: str) -> tuple[datetime, datetime]:
    """The regulatory window for a weekly duty: Monday 00:00 to next Monday 00:00.

    An ISO week is the horizon the regulation itself talks in ("each week"), so
    the window is stated that way rather than inventing business hours the
    regulation does not contain.
    """
    year, week_number = week.split("-W")
    monday = date.fromisocalendar(int(year), int(week_number), 1)
    start = datetime.combine(monday, time.min)
    return start, start + timedelta(days=7)


def hard_constraint_window(rule_id: str, week: str) -> tuple[str, str]:
    """Regulatory window for a zero-gap rule; a miss is a violation."""
    start, end = week_bounds(week)
    return start.isoformat(), end.isoformat()


def generate_tasks(
    system_id: str,
    week: str,
    rules: list[RuleOut],
    sampling_points: list[SamplingPointOut],
    *,
    include_unverified: bool = False,
) -> TaskBatch:
    """Expand the schedulable rules into one task per (rule, sampling point).

    A task is raised only for a `hard_constraint` rule, because those are the
    duties with a regulatory window a miss can be written up for. Advisory
    rules drive the alert workflow instead (see the `adverse_result_reporting`
    set), not the duty list.
    """
    start, end = week_bounds(week)
    tasks: list[TaskOut] = []
    skipped_inert = 0

    for rule in rules:
        if rule.inert:
            skipped_inert += 1
            if not include_unverified:
                continue
        if not rule.hard_constraint:
            continue
        for point in sampling_points:
            tasks.append(
                TaskOut(
                    id=str(uuid.uuid5(TASK_NAMESPACE, f"{system_id}:{point.id}:{rule.id}:{week}")),
                    sampling_point_id=point.id,
                    rule_id=rule.id,
                    window_start=start,
                    window_end=end,
                    status=TaskStatus.PENDING,
                )
            )

    return TaskBatch(
        week=week,
        generated=len(tasks),
        inert_rules_skipped=skipped_inert,
        tasks=tasks,
    )
