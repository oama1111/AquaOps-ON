"""M1 — ingestion & registry (`docs/architecture.md` §2, `docs/data-model.md`).

Pure domain logic: parsing, validation, and network summarising. Persistence is
the service layer's job (`app/services/ingest.py`), which keeps this module
testable without a database.

The integrity rule this module owns is idempotency: a laboratory file is keyed
on (sampling point, measured time), so replaying the same export is a no-op
rather than a duplicate or an overwrite. The parser therefore reports what it
read; the service decides what is new.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.schemas.enums import LocationType, ReadingSource
from app.schemas.readings import CHLORINE_SANITY_MAX, CHLORINE_SANITY_MIN
from app.schemas.registry import RowError, SamplingPointOut


class NetworkSummary:
    """Node/pipe/tank counts read back from a committed EPANET model."""

    def __init__(self, junctions: int, pipes: int, tanks: int, reservoirs: int) -> None:
        self.junctions = junctions
        self.pipes = pipes
        self.tanks = tanks
        self.reservoirs = reservoirs


@dataclass(frozen=True)
class ParsedReading:
    sampling_point_id: str
    measured_at: datetime
    free_chlorine_mg_l: float
    source: ReadingSource = ReadingSource.LAB_CSV


@dataclass
class LabParseResult:
    """What a laboratory CSV contained, before any database work."""

    rows_total: int = 0
    readings: list[ParsedReading] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    def reject(self, reading: ParsedReading, error: RowError) -> None:
        """Refuse a reading that parsed cleanly but the registry does not know.

        The counts are derived from the two lists, so a later-stage refusal has
        to move the reading out of ``readings`` as well as record the reason —
        otherwise ``rows_accepted`` keeps claiming a row that was never stored.
        """
        if reading in self.readings:
            self.readings.remove(reading)
        self.errors.append(error)

    @property
    def rows_rejected(self) -> int:
        return len(self.errors)

    @property
    def rows_accepted(self) -> int:
        return len(self.readings)


def load_sampling_points(system_id: str, csv_path: Path) -> list[SamplingPointOut]:
    """Read the committed point registry into validated point records."""
    if not Path(csv_path).is_file():
        raise FileNotFoundError(f"sampling-point registry not found: {csv_path}")

    points: list[SamplingPointOut] = []
    with Path(csv_path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            points.append(
                SamplingPointOut(
                    id=row["sampling_point_id"],
                    system_id=system_id,
                    node_id=row["node_id"],
                    x_m=float(row["x_m"]),
                    y_m=float(row["y_m"]),
                    elevation_m=float(row["elevation_m"]),
                    location_type=LocationType(row["location_type"]),
                    active=True,
                )
            )
    if not points:
        raise ValueError(f"registry {csv_path} contained no sampling points")
    return points


def parse_lab_csv(csv_path: Path) -> LabParseResult:
    """Parse and sanity-check a laboratory result CSV.

    Every rejected row carries the field and a reason, so the ingest status
    endpoint can show an operator exactly which rows were refused and why.
    """
    result = LabParseResult()
    with Path(csv_path).open(newline="", encoding="utf-8") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):  # 1 = header
            result.rows_total += 1

            point_id = (row.get("sampling_point_id") or "").strip()
            if not point_id:
                result.errors.append(
                    RowError(
                        row_number=number, field="sampling_point_id", message="missing point id"
                    )
                )
                continue

            raw_time = (row.get("measured_at") or "").strip()
            try:
                measured_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except ValueError:
                result.errors.append(
                    RowError(
                        row_number=number,
                        field="measured_at",
                        message=f"unparseable timestamp: {raw_time!r}",
                    )
                )
                continue

            raw_value = (row.get("free_chlorine_mg_l") or "").strip()
            try:
                value = float(raw_value)
            except ValueError:
                result.errors.append(
                    RowError(
                        row_number=number,
                        field="free_chlorine_mg_l",
                        message=f"not a number: {raw_value!r}",
                    )
                )
                continue

            if not (CHLORINE_SANITY_MIN <= value <= CHLORINE_SANITY_MAX):
                result.errors.append(
                    RowError(
                        row_number=number,
                        field="free_chlorine_mg_l",
                        message=(
                            f"{value} outside the accepted sanity range "
                            f"{CHLORINE_SANITY_MIN}-{CHLORINE_SANITY_MAX} mg/L"
                        ),
                    )
                )
                continue

            result.readings.append(
                ParsedReading(
                    sampling_point_id=point_id,
                    measured_at=measured_at,
                    free_chlorine_mg_l=value,
                )
            )
    return result


def load_epanet_network(inp_path: Path) -> NetworkSummary:
    """Summarise a committed EPANET model without running a simulation.

    Used to prove the shipped network matches the scale the design report
    quotes, without paying for a 48-hour hydraulic solve.
    """
    sections: dict[str, int] = {}
    current: str | None = None
    for raw in Path(inp_path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].upper()
            sections[current] = 0
        elif current and line and not line.startswith(";"):
            sections[current] += 1
    return NetworkSummary(
        junctions=sections.get("JUNCTIONS", 0),
        pipes=sections.get("PIPES", 0),
        tanks=sections.get("TANKS", 0),
        reservoirs=sections.get("RESERVOIRS", 0),
    )
