"""Planning service (M4 + M3): rules become duties, duties become a driven week.

The two safety properties live here at the boundary:

* Unverified rules are persisted but never expanded into tasks unless a caller
  explicitly asks for a draft preview.
* A plan is written only after the planner reports success; infeasibility is
  raised as an actionable message naming the tasks and vehicles responsible.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RegRule, RequiredTask, RoutePlan, RouteStop, SamplingPoint, Vehicle
from app.schemas.enums import LocationType
from app.schemas.registry import SamplingPointOut
from app.schemas.rules import TaskBatch
from modules.m3_schedule import Stop, VehicleSpec, nearest_neighbour_km, plan_routes
from modules.m4_rules import generate_tasks, load_rules, week_bounds


def sync_rules(session: Session) -> int:
    """Load `rules/oreg170.yaml` into the catalogue, preserving verification state."""
    rules = load_rules()
    for rule in rules:
        row = session.get(RegRule, rule.id)
        if row is None:
            session.add(
                RegRule(
                    id=rule.id,
                    rule_set_id=rule.rule_set_id,
                    parameter=rule.parameter,
                    location=rule.location,
                    frequency=rule.frequency,
                    source_section=rule.source_section,
                    hard_constraint=rule.hard_constraint,
                    verified=rule.verified,
                )
            )
        else:
            row.verified = rule.verified
            row.source_section = rule.source_section
            row.frequency = rule.frequency
    session.commit()
    return len(rules)


def catalogue(session: Session, *, verified_only: bool = False) -> list[RegRule]:
    statement = select(RegRule).order_by(RegRule.id)
    if verified_only:
        statement = statement.where(RegRule.verified.is_(True))
    return list(session.execute(statement).scalars())


def generate_week_tasks(
    session: Session,
    system_id: str,
    week: str,
    *,
    include_draft: bool = False,
) -> TaskBatch:
    """Expand the rules into required tasks and persist the ones that are new."""
    rules = load_rules()
    points = [
        SamplingPointOut(
            id=row.id,
            system_id=row.system_id,
            node_id=row.node_id,
            x_m=row.x_m,
            y_m=row.y_m,
            elevation_m=row.elevation_m,
            location_type=LocationType(row.location_type),
            active=row.active,
        )
        for row in session.execute(
            select(SamplingPoint).where(
                SamplingPoint.system_id == system_id, SamplingPoint.active.is_(True)
            )
        ).scalars()
    ]

    batch = generate_tasks(system_id, week, rules, points, include_unverified=include_draft)

    start, end = week_bounds(week)
    existing = {
        task_id
        for task_id in session.execute(
            select(RequiredTask.id).where(RequiredTask.week == week)
        ).scalars()
    }
    created = 0
    for task in batch.tasks:
        if task.id in existing:
            continue
        session.add(
            RequiredTask(
                id=task.id,
                system_id=system_id,
                sampling_point_id=task.sampling_point_id,
                rule_id=task.rule_id,
                week=week,
                window_start=start,
                window_end=end,
                status="pending",
                draft=include_draft,
            )
        )
        created += 1
    session.commit()
    return TaskBatch(
        week=week,
        generated=created,
        inert_rules_skipped=batch.inert_rules_skipped,
        tasks=batch.tasks,
    )


def week_tasks(session: Session, system_id: str, week: str) -> list[RequiredTask]:
    return list(
        session.execute(
            select(RequiredTask)
            .where(RequiredTask.system_id == system_id, RequiredTask.week == week)
            .order_by(RequiredTask.sampling_point_id, RequiredTask.rule_id)
        ).scalars()
    )


def _stops_and_vehicles(
    session: Session, system_id: str, tasks: list[RequiredTask]
) -> tuple[list[Stop], list[VehicleSpec]]:
    points = {
        point.id: point
        for point in session.execute(
            select(SamplingPoint).where(SamplingPoint.system_id == system_id)
        ).scalars()
    }
    stops = [
        Stop(
            task_id=task.id,
            point_id=task.sampling_point_id,
            x_m=points[task.sampling_point_id].x_m,
            y_m=points[task.sampling_point_id].y_m,
            window_start=task.window_start,
            window_end=task.window_end,
        )
        for task in tasks
        if task.sampling_point_id in points
    ]
    vehicles = [
        VehicleSpec(
            id=vehicle.id,
            depot_x_m=vehicle.depot_x_m,
            depot_y_m=vehicle.depot_y_m,
            shift_minutes=vehicle.shift_minutes,
        )
        for vehicle in session.execute(
            select(Vehicle).where(Vehicle.system_id == system_id).order_by(Vehicle.id)
        ).scalars()
    ]
    return stops, vehicles


def build_week_plan(
    session: Session, system_id: str, week: str, *, solver: str = "greedy2opt"
) -> tuple[RoutePlan, float]:
    """Plan the week's duties and persist the result. Returns the plan and baseline km."""
    tasks = week_tasks(session, system_id, week)
    stops, vehicles = _stops_and_vehicles(session, system_id, tasks)
    if not stops:
        raise ValueError(
            f"no required tasks exist for week {week}; generate tasks before planning"
        )

    plan = plan_routes(system_id, tasks[0].window_start.date(), stops, vehicles, solver=solver)
    baseline_km = nearest_neighbour_km(stops, vehicles)

    row = RoutePlan(
        id=plan.id,
        system_id=system_id,
        week_start=plan.week_start,
        status=plan.status.value if hasattr(plan.status, "value") else str(plan.status),
        solver_ms=plan.solver_ms,
        total_km=plan.total_km,
    )
    session.add(row)
    for stop in plan.stops:
        session.add(
            RouteStop(
                plan_id=plan.id,
                seq=stop.seq,
                required_task_id=stop.required_task_id,
                sampling_point_id=stop.sampling_point_id,
                vehicle_id=stop.vehicle_id,
                eta=stop.eta,
                travel_km=stop.travel_km,
            )
        )
        task = session.get(RequiredTask, stop.required_task_id)
        if task is not None:
            task.status = "planned"
    session.commit()
    return row, baseline_km


def preview_plan(
    session: Session, system_id: str, week: str, *, solver: str = "greedy2opt"
) -> tuple[object, float]:
    """Plan the week without writing anything.

    The evaluation harness needs the mileage numbers, not another stored plan,
    so it uses this path and leaves the operator's plan history untouched.
    """
    tasks = week_tasks(session, system_id, week)
    stops, vehicles = _stops_and_vehicles(session, system_id, tasks)
    if not stops:
        raise ValueError(f"no required tasks exist for week {week}")
    plan = plan_routes(system_id, tasks[0].window_start.date(), stops, vehicles, solver=solver)
    return plan, nearest_neighbour_km(stops, vehicles)


def latest_plan(session: Session, system_id: str) -> RoutePlan | None:
    return session.execute(
        select(RoutePlan)
        .where(RoutePlan.system_id == system_id)
        .order_by(RoutePlan.created_at.desc())
        .limit(1)
    ).scalars().first()


def plan_stops(session: Session, plan_id: str) -> list[RouteStop]:
    return list(
        session.execute(
            select(RouteStop).where(RouteStop.plan_id == plan_id).order_by(RouteStop.seq)
        ).scalars()
    )
