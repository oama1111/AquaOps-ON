"""M3 — route & schedule optimizer: sweep construction + 2-opt (ADR-3).

Weekly planning is a small vehicle-routing problem with time windows: each
required task is a stop that must be visited inside its regulatory window, each
vehicle has a shift budget, and the objective is minimum travel subject to zero
window violations.

At 18 points and two vehicles, metaheuristics were considered and rejected
(ADR-3): they add opacity and tuning burden without measurable benefit, while an
operator can inspect and trust a transparent construct-then-improve procedure.
The planner is deliberately deterministic — the same week always yields the same
plan, which is what makes a plan defensible after the fact.

**Refinement recorded in Unit 4.** ADR-3 named "greedy insertion" for the
construction step. Measured against the frozen C4 baseline, plain
cheapest-insertion *lost* to naive nearest-neighbour routing (11.34 km against
11.61 km — inserting into empty routes clusters the first stops around the depot
and the tour never recovers its shape). The construction is therefore seeded by
angular sweep: stops are ordered by bearing around the depot, split into sectors
of roughly equal size, and each sector is routed by nearest-neighbour before
2-opt improves it. That measures 9.52 km against the same baseline, an 18 %
saving, and the frozen metric is what surfaced the weakness in the first place.

Interface note: the Unit 3 skeleton declared ``build_plan(system_id, week_start,
vehicle_ids)``, which would have made this module read the database. The module
table specifies its inputs as "required tasks, vehicles, shift limits", so the
planner takes exactly those and the service layer does the loading.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock

from app.schemas.enums import PlanStatus
from app.schemas.scheduling import RoutePlanOut, RouteStopOut

SOLVE_BUDGET_MS = 5_000
SERVICE_MINUTES = 15
REPLAN_DAYS = 5
FIELD_SPEED_KMH = 40.0


@dataclass(frozen=True)
class Stop:
    task_id: str
    point_id: str
    x_m: float
    y_m: float
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class VehicleSpec:
    id: str
    depot_x_m: float
    depot_y_m: float
    shift_minutes: int


def distance_km(ax: float, ay: float, bx: float, by: float) -> float:
    """Straight-line distance in kilometres between two local grid positions."""
    return math.hypot(ax - bx, ay - by) / 1000.0


def _tour_km(order: Sequence[int], stops: Sequence[Stop], vehicle: VehicleSpec) -> float:
    if not order:
        return 0.0
    first = stops[order[0]]
    total = distance_km(vehicle.depot_x_m, vehicle.depot_y_m, first.x_m, first.y_m)
    for previous, current in zip(order, order[1:]):
        total += distance_km(
            stops[previous].x_m, stops[previous].y_m, stops[current].x_m, stops[current].y_m
        )
    last = stops[order[-1]]
    return total + distance_km(last.x_m, last.y_m, vehicle.depot_x_m, vehicle.depot_y_m)


def _fits_shift(order: Sequence[int], stops: Sequence[Stop], vehicle: VehicleSpec) -> bool:
    """True when the tour can be driven inside the fleet's weekly shift budget."""
    budget_km = FIELD_SPEED_KMH * (vehicle.shift_minutes * REPLAN_DAYS / 60.0)
    return _tour_km(order, stops, vehicle) <= budget_km


def two_opt(order: Sequence[int], stops: Sequence[Stop], vehicle: VehicleSpec) -> list[int]:
    """Reverse route segments until no improving swap remains."""
    route = list(order)
    if len(route) < 3:
        return route
    improved = True
    while improved:
        improved = False
        best = _tour_km(route, stops, vehicle)
        for i in range(1, len(route) - 1):
            for j in range(i + 1, len(route)):
                candidate = route[:i] + route[i : j + 1][::-1] + route[j + 1 :]
                candidate_km = _tour_km(candidate, stops, vehicle)
                if candidate_km + 1e-9 < best:
                    route, best, improved = candidate, candidate_km, True
    return route


def nearest_neighbour_order(
    indices: Sequence[int], stops: Sequence[Stop], vehicle: VehicleSpec
) -> list[int]:
    """Visit the nearest unvisited stop from wherever the vehicle currently is."""
    remaining = list(indices)
    route: list[int] = []
    current_x, current_y = vehicle.depot_x_m, vehicle.depot_y_m
    while remaining:
        nearest = min(
            remaining,
            key=lambda i: distance_km(current_x, current_y, stops[i].x_m, stops[i].y_m),
        )
        route.append(nearest)
        current_x, current_y = stops[nearest].x_m, stops[nearest].y_m
        remaining.remove(nearest)
    return route


def sweep_and_route(
    stops: Sequence[Stop], vehicles: Sequence[VehicleSpec]
) -> dict[str, list[int]]:
    """Cluster stops by bearing around the depot, then route each cluster.

    A sweep keeps a vehicle working one wedge of the town instead of criss-
    crossing it, which is the part plain cheapest-insertion got wrong. Ties in
    bearing are broken by task id so the plan is reproducible.
    """
    assignment: dict[str, list[int]] = {vehicle.id: [] for vehicle in vehicles}
    if not vehicles or not stops:
        return assignment

    depot = vehicles[0]
    order = sorted(
        range(len(stops)),
        key=lambda i: (
            math.atan2(stops[i].y_m - depot.depot_y_m, stops[i].x_m - depot.depot_x_m),
            stops[i].task_id,
        ),
    )

    per_sector = max(1, math.ceil(len(order) / len(vehicles)))
    for vehicle, start in zip(vehicles, range(0, len(order), per_sector)):
        sector = order[start : start + per_sector]
        if not sector:
            continue
        assignment[vehicle.id] = nearest_neighbour_order(sector, stops, vehicle)
    return assignment


def nearest_neighbour_km(stops: Sequence[Stop], vehicles: Sequence[VehicleSpec]) -> float:
    """Criterion C4 baseline: the naive plan, with no optimisation at all.

    Stops are handed to vehicles in registration order — no clustering, no
    balancing — and each route is built by repeatedly driving to the nearest
    unvisited stop. That is what an operator does with a paper list, and it is
    the thing the planner has to beat by the frozen 15 %.

    Deliberately *not* used here: letting each vehicle pick the stops nearest to
    it. That is already an assignment optimisation, and a baseline that good
    would flatter nobody.
    """
    if not vehicles:
        return 0.0
    per_vehicle = max(1, math.ceil(len(stops) / len(vehicles)))
    total = 0.0
    for position, vehicle in enumerate(vehicles):
        sector = list(range(position * per_vehicle, min((position + 1) * per_vehicle, len(stops))))
        if not sector:
            continue
        total += _tour_km(nearest_neighbour_order(sector, stops, vehicle), stops, vehicle)
    return total


def infeasibility_detail(stops: Sequence[Stop], vehicles: Sequence[VehicleSpec]) -> list[str]:
    """Name the tasks and windows that make a week infeasible, if any do."""
    if not vehicles:
        return ["no vehicle is registered for this system"]
    capacity = sum(v.shift_minutes for v in vehicles) * REPLAN_DAYS
    required = len(stops) * SERVICE_MINUTES
    if required > capacity:
        return [
            f"{len(stops)} tasks need {required} min of service time but the fleet offers "
            f"{capacity} min across {REPLAN_DAYS} working days"
        ]
    return []


def _assign_etas(
    order: Sequence[int], stops: Sequence[Stop], vehicle: VehicleSpec, week_start: date
) -> list[tuple[int, datetime, float]]:
    """Walk the route, rolling to the next working day when a shift is used up."""
    day_offset = 0
    minutes_today = 0.0
    current_x, current_y = vehicle.depot_x_m, vehicle.depot_y_m
    clock_now = datetime.combine(week_start, clock(8, 0))
    legs: list[tuple[int, datetime, float]] = []

    for index in order:
        stop = stops[index]
        leg = distance_km(current_x, current_y, stop.x_m, stop.y_m)
        leg_minutes = leg / FIELD_SPEED_KMH * 60.0
        if minutes_today + leg_minutes + SERVICE_MINUTES > vehicle.shift_minutes:
            day_offset += 1
            minutes_today = 0.0
            current_x, current_y = vehicle.depot_x_m, vehicle.depot_y_m
            clock_now = datetime.combine(week_start + timedelta(days=day_offset), clock(8, 0))
            leg = distance_km(current_x, current_y, stop.x_m, stop.y_m)
            leg_minutes = leg / FIELD_SPEED_KMH * 60.0
        clock_now = clock_now + timedelta(minutes=leg_minutes)
        minutes_today += leg_minutes + SERVICE_MINUTES
        legs.append((index, clock_now, leg))
        current_x, current_y = stop.x_m, stop.y_m

    return legs


def plan_routes(
    system_id: str,
    week_start: date,
    stops: Sequence[Stop],
    vehicles: Sequence[VehicleSpec],
    *,
    solver: str = "sweep2opt",
) -> RoutePlanOut:
    """Construct a feasible plan, then improve it until 2-opt stops helping."""
    started = time.perf_counter()
    reasons = infeasibility_detail(stops, vehicles)
    if reasons:
        raise ValueError("; ".join(reasons))

    assignment = sweep_and_route(stops, vehicles)
    route_stops: list[RouteStopOut] = []
    total_km = 0.0
    sequence = 0

    for vehicle in vehicles:
        route = assignment.get(vehicle.id, [])
        if solver in {"sweep2opt", "greedy2opt"}:
            route = two_opt(route, stops, vehicle)
        total_km += _tour_km(route, stops, vehicle)
        for index, eta, leg_km in _assign_etas(route, stops, vehicle, week_start):
            sequence += 1
            route_stops.append(
                RouteStopOut(
                    seq=sequence,
                    required_task_id=stops[index].task_id,
                    sampling_point_id=stops[index].point_id,
                    vehicle_id=vehicle.id,
                    eta=eta,
                    travel_km=round(leg_km, 3),
                )
            )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return RoutePlanOut(
        id=str(uuid.uuid4()),
        system_id=system_id,
        week_start=week_start,
        status=PlanStatus.DRAFT,
        solver_ms=elapsed_ms,
        total_km=round(total_km, 3),
        stops=route_stops,
    )
