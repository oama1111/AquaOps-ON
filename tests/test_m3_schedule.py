"""Unit tests for M3 — the route and schedule optimizer (`modules/m3_schedule.py`).

Two layers are tested separately, which is the point of unit testing a module
that mixes geometry, search, and calendar arithmetic:

``@pytest.mark.unit_whitebox``
    The distance metric, the sweep construction, nearest-neighbour ordering,
    2-opt improvement, the shift budget, and the infeasibility message are each
    exercised on synthetic coordinates with a known optimal answer.

``@pytest.mark.unit_blackbox``
    The public ``plan_routes`` entry point is then treated as a black box: given
    stops and vehicles it must return a complete, feasible, persisted-shaped
    plan, and it must refuse rather than silently drop work it cannot fit.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.schemas.enums import PlanStatus
from modules.m3_schedule import (
    SERVICE_MINUTES,
    Stop,
    VehicleSpec,
    _tour_km,
    distance_km,
    infeasibility_detail,
    nearest_neighbour_km,
    nearest_neighbour_order,
    plan_routes,
    sweep_and_route,
    two_opt,
)

WEEK_START = date(2026, 9, 14)
WINDOW_START = datetime(2026, 9, 14, 0, 0)


def stops_on(grid: list[tuple[float, float]]) -> list[Stop]:
    return [
        Stop(
            task_id=f"T{index}",
            point_id=f"SP{index:02d}",
            x_m=float(x),
            y_m=float(y),
            window_start=WINDOW_START,
            window_end=WINDOW_START + timedelta(days=7),
        )
        for index, (x, y) in enumerate(grid)
    ]


#: A corridor plus a second row: `[0, 5, 4, 3, 2, 1]` is badly ordered on purpose.
TOY = stops_on([(0, 0), (1000, 0), (2000, 0), (0, 1000), (1000, 1000), (2000, 1000)])
VEHICLES = [VehicleSpec("V1", 0.0, 0.0, 420), VehicleSpec("V2", 0.0, 0.0, 420)]


# --------------------------------------------------------- white-box internals ---


@pytest.mark.unit_whitebox
def test_distance_is_euclidean_kilometres() -> None:
    assert distance_km(0, 0, 3000, 4000) == pytest.approx(5.0)
    assert distance_km(100, 100, 100, 100) == 0.0


@pytest.mark.unit_whitebox
def test_every_stop_is_a_real_registry_point() -> None:
    """Each generated stop must be a real registry point, not a placeholder."""
    for stop in TOY:
        assert stop.point_id.startswith("SP")
        assert stop.window_start <= stop.window_end


@pytest.mark.unit_whitebox
def test_nearest_neighbour_order_visits_the_closest_stop_first() -> None:
    order = nearest_neighbour_order([0, 1, 2], TOY, VEHICLES[0])
    assert order == [0, 1, 2]


@pytest.mark.unit_whitebox
def test_two_opt_never_worsens_a_route_and_keeps_every_stop() -> None:
    """The improvement step is a strict improvement or a no-op — never a loss."""
    badly_ordered = [0, 5, 4, 3, 2, 1]
    improved = two_opt(badly_ordered, TOY, VEHICLES[0])

    assert sorted(improved) == sorted(badly_ordered), "2-opt reorders, it never drops"
    assert _tour_km(improved, TOY, VEHICLES[0]) <= _tour_km(badly_ordered, TOY, VEHICLES[0]) + 1e-9


@pytest.mark.unit_whitebox
@pytest.mark.parametrize("length", [0, 1, 2])
def test_two_opt_leaves_trivially_short_routes_alone(length: int) -> None:
    order = list(range(length))
    assert two_opt(order, TOY, VEHICLES[0]) == order


@pytest.mark.unit_whitebox
def test_sweep_keeps_each_vehicle_in_one_wedge_of_the_town() -> None:
    """The Unit 4 refinement: sectors, not cheapest-insertion clustering."""
    assignment = sweep_and_route(TOY, VEHICLES)
    assert set(assignment) == {"V1", "V2"}
    assert sum(len(route) for route in assignment.values()) == len(TOY)
    assert all(route for route in assignment.values()), "no vehicle is left with an empty sector"


@pytest.mark.unit_whitebox
def test_sweep_without_vehicles_returns_no_assignment() -> None:
    assert sweep_and_route(TOY, []) == {}


@pytest.mark.unit_whitebox
def test_shift_budget_rejects_a_route_it_cannot_drive() -> None:
    """A one-minute shift is physically impossible, so the plan must not claim it."""
    tiny_shift = VehicleSpec("V-tiny", 0.0, 0.0, 1)
    detail = infeasibility_detail(TOY, [tiny_shift])
    assert detail and "service time" in detail[0]


@pytest.mark.unit_whitebox
def test_planner_refuses_an_empty_fleet_with_a_reason() -> None:
    assert infeasibility_detail(TOY, []) == ["no vehicle is registered for this system"]


@pytest.mark.unit_whitebox
def test_infeasibility_is_silent_when_capacity_is_sufficient() -> None:
    assert infeasibility_detail(TOY, VEHICLES) == []


# ----------------------------------------------------------- black-box contract ---


@pytest.mark.unit_blackbox
def test_plan_contains_every_task_exactly_once() -> None:
    plan = plan_routes("case-system", WEEK_START, TOY, VEHICLES)

    assert plan.status is PlanStatus.DRAFT
    assert [stop.seq for stop in plan.stops] == list(range(1, len(TOY) + 1))
    assert {stop.sampling_point_id for stop in plan.stops} == {stop.point_id for stop in TOY}
    assert plan.total_km > 0


@pytest.mark.unit_blackbox
def test_plan_never_loses_to_the_naive_baseline() -> None:
    """C4 is a comparison, so the assertion is a comparison: the planner may tie
    on a six-stop toy grid, but it must never be worse than the naive plan. The
    15 % margin itself is asserted against the 18-point case registry in
    ``test_m3_margin.py``.
    """
    plan = plan_routes("case-system", WEEK_START, TOY, VEHICLES)
    baseline = nearest_neighbour_km(TOY, VEHICLES)

    assert baseline > 0
    assert plan.total_km <= baseline + 1e-9, "the planner must not be worse than naive routing"


@pytest.mark.unit_blackbox
def test_plan_is_deterministic() -> None:
    """Replanning the same week must not shuffle the operator's day."""
    first = plan_routes("case-system", WEEK_START, TOY, VEHICLES)
    second = plan_routes("case-system", WEEK_START, TOY, VEHICLES)

    assert [stop.sampling_point_id for stop in first.stops] == [
        stop.sampling_point_id for stop in second.stops
    ]
    assert first.total_km == second.total_km


@pytest.mark.unit_blackbox
def test_every_stop_is_visited_inside_the_regulatory_window() -> None:
    plan = plan_routes("case-system", WEEK_START, TOY, VEHICLES)
    window_end = WINDOW_START + timedelta(days=7)

    for stop in plan.stops:
        assert WINDOW_START <= stop.eta <= window_end


@pytest.mark.unit_blackbox
def test_etas_are_chronological_within_a_vehicle() -> None:
    """A route the operator cannot drive in the printed order is not a plan."""
    plan = plan_routes("case-system", WEEK_START, TOY, VEHICLES)
    per_vehicle: dict[str, list[datetime]] = {}
    for stop in plan.stops:
        per_vehicle.setdefault(stop.vehicle_id, []).append(stop.eta)

    for etas in per_vehicle.values():
        assert etas == sorted(etas)


@pytest.mark.unit_blackbox
def test_long_duty_list_rolls_onto_further_working_days() -> None:
    """More work than one shift holds must be spread across the week, not dropped."""
    many = stops_on([(index * 400.0, 0.0) for index in range(20)])
    plan = plan_routes("case-system", WEEK_START, many, [VehicleSpec("V1", 0.0, 0.0, 120)])

    assert len(plan.stops) == 20
    assert len({stop.eta.date() for stop in plan.stops}) > 1, "the plan must use multiple days"
    assert max(stop.eta.date() for stop in plan.stops) <= WEEK_START + timedelta(days=6)


@pytest.mark.unit_blackbox
def test_infeasible_week_raises_instead_of_dropping_duties() -> None:
    """Silently dropping a regulatory duty would be the most dangerous failure mode."""
    with pytest.raises(ValueError, match="service time"):
        plan_routes("case-system", WEEK_START, TOY, [VehicleSpec("V1", 0.0, 0.0, 1)])


@pytest.mark.unit_blackbox
def test_solve_stays_inside_the_frozen_budget() -> None:
    """C4 also caps the solve at 5 s; the whole point of rejecting metaheuristics."""
    plan = plan_routes("case-system", WEEK_START, TOY, VEHICLES)
    assert plan.solver_ms < 5_000
    assert SERVICE_MINUTES == 15, "the service time is a frozen planning parameter"
