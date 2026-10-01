"""Unit test for the C4 route-efficiency margin on the committed case registry.

This is the margin the proposal froze: the planner must beat the naive
nearest-neighbour plan by at least 15 % on the 18-point Maple Creek system. The
test reads the committed registry and the committed depot coordinate (the
treatment plant, node ``TP``, at 150 m East / 500 m North) rather than a
hand-built toy grid, because the toy grid used by the other M3 tests is
deliberately too small for a sweep to show its advantage — see the reflection in
the Unit 5 report.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.services.case import CASE_SYSTEM_ID
from modules.m1_ingest import load_sampling_points
from modules.m3_schedule import (
    Stop,
    VehicleSpec,
    nearest_neighbour_km,
    plan_routes,
)
from modules.m6_eval import MILEAGE_SAVING_TARGET

POINTS_CSV = Path(__file__).resolve().parents[1] / "data" / "networks" / "maple_creek_points.csv"

#: The case system's depot: the treatment plant, read from the EPANET model.
DEPOT_X_M = 150.0
DEPOT_Y_M = 500.0


@pytest.mark.unit_blackbox
def test_planner_clears_the_frozen_c4_margin_on_the_case_registry() -> None:
    week_start = date(2026, 9, 14)
    window_start = datetime.combine(week_start, datetime.min.time())
    points = load_sampling_points(CASE_SYSTEM_ID, POINTS_CSV)

    stops = [
        Stop(
            task_id=f"T{index:02d}",
            point_id=point.id,
            x_m=point.x_m,
            y_m=point.y_m,
            window_start=window_start,
            window_end=window_start + timedelta(days=7),
        )
        for index, point in enumerate(points)
    ]
    vehicles = [
        VehicleSpec("V1", DEPOT_X_M, DEPOT_Y_M, 420),
        VehicleSpec("V2", DEPOT_X_M, DEPOT_Y_M, 420),
    ]

    plan = plan_routes(CASE_SYSTEM_ID, week_start, stops, vehicles)
    baseline_km = nearest_neighbour_km(stops, vehicles)
    saving = (baseline_km - plan.total_km) / baseline_km

    assert len(plan.stops) == 18
    assert saving >= MILEAGE_SAVING_TARGET, (
        f"planner saved {saving:.1%} against the frozen 15% target "
        f"({plan.total_km} km against {baseline_km:.2f} km)"
    )
    assert plan.solver_ms < 5_000
