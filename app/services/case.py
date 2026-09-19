"""Case-system registry: the fictional Township of Maple Creek.

Seeding is idempotent, so application startup can always call it and a fresh
clone becomes demonstrable in one step. The point registry is read from the
committed CSV rather than duplicated in code — the data is the single source of
truth, and `tests/test_case_data.py` already guards it.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Operator, SamplingPoint, Vehicle, WaterSystem
from modules.m1_ingest import load_epanet_network, load_sampling_points

CASE_SYSTEM_ID = "maple-creek"
CASE_SYSTEM_NAME = "Township of Maple Creek"
CASE_POPULATION = 4_800

REPO_ROOT = Path(__file__).resolve().parents[2]
NETWORKS = REPO_ROOT / "data" / "networks"
POINTS_CSV = NETWORKS / "maple_creek_points.csv"
NETWORK_INP = NETWORKS / "maple_creek.inp"
CHLORINE_CSV = NETWORKS / "maple_creek_chlorine_48h.csv"

DEFAULT_VEHICLES = (
    ("V1", 420),
    ("V2", 420),
)


def _coordinates(inp_path: Path, node_id: str) -> tuple[float, float]:
    """Read one node's coordinates out of the committed EPANET model."""
    in_coords = False
    for raw in inp_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_coords = line.upper().startswith("[COORDINATES")
            continue
        if in_coords and line and not line.startswith(";"):
            parts = line.split()
            if parts[0] == node_id and len(parts) >= 3:
                return float(parts[1]), float(parts[2])
    return 0.0, 0.0


def ensure_case_system(session: Session) -> WaterSystem:
    """Register the case system, its 18 points, its fleet, and its operator."""
    system = session.get(WaterSystem, CASE_SYSTEM_ID)
    if system is None:
        system = WaterSystem(
            id=CASE_SYSTEM_ID,
            name=CASE_SYSTEM_NAME,
            population=CASE_POPULATION,
            network_inp=str(NETWORK_INP.relative_to(REPO_ROOT)),
        )
        session.add(system)
        session.flush()

    if not session.execute(
        select(SamplingPoint).where(SamplingPoint.system_id == CASE_SYSTEM_ID).limit(1)
    ).scalars().first():
        for point in load_sampling_points(CASE_SYSTEM_ID, POINTS_CSV):
            session.add(
                SamplingPoint(
                    id=point.id,
                    system_id=point.system_id,
                    node_id=point.node_id,
                    x_m=point.x_m,
                    y_m=point.y_m,
                    elevation_m=point.elevation_m,
                    location_type=point.location_type.value,
                    active=point.active,
                )
            )

    depot_x, depot_y = _coordinates(NETWORK_INP, "TP")
    for vehicle_id, shift in DEFAULT_VEHICLES:
        if session.get(Vehicle, vehicle_id) is None:
            session.add(
                Vehicle(
                    id=vehicle_id,
                    system_id=CASE_SYSTEM_ID,
                    label=f"Field truck {vehicle_id}",
                    depot_x_m=depot_x,
                    depot_y_m=depot_y,
                    shift_minutes=shift,
                )
            )

    if session.get(Operator, "op-1") is None:
        session.add(
            Operator(
                id="op-1",
                system_id=CASE_SYSTEM_ID,
                label="Certified operator (case)",
                licence_class="DZ",
            )
        )

    session.commit()
    return system


def network_summary() -> object:
    """Scale of the committed network, without running a simulation."""
    return load_epanet_network(NETWORK_INP)
