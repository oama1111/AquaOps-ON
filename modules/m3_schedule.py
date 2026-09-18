"""M3 — route & schedule optimizer: greedy insertion + 2-opt (ADR-3)."""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.scheduling import RoutePlanOut

SOLVE_BUDGET_MS = 5_000


def build_plan(
    system_id: str, week_start: str, vehicle_ids: Sequence[str]
) -> RoutePlanOut:
    """Construct a feasible plan, then improve it until 2-opt stops helping."""
    raise NotImplementedError


def greedy_insert(
    task_ids: Sequence[str], vehicle_ids: Sequence[str]
) -> dict[str, list[str]]:
    """Place each task at its cheapest feasible position in the open routes."""
    raise NotImplementedError


def two_opt(route: Sequence[str]) -> list[str]:
    """Reverse route segments until no improving swap remains."""
    raise NotImplementedError


def infeasibility_detail(task_ids: Sequence[str], vehicle_ids: Sequence[str]) -> list[str]:
    """Name the tasks and windows that make a week infeasible, if it is."""
    raise NotImplementedError
