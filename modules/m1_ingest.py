"""M1 — ingestion & registry (docs/architecture.md §2)."""

from __future__ import annotations

from pathlib import Path

from app.schemas.registry import IngestReceipt, SamplingPointOut


class NetworkSummary:
    """Node/pipe/tank counts read back from a committed EPANET model."""

    def __init__(self, junctions: int, pipes: int, tanks: int, reservoirs: int) -> None:
        self.junctions = junctions
        self.pipes = pipes
        self.tanks = tanks
        self.reservoirs = reservoirs


def load_sampling_points(system_id: str, csv_path: Path) -> list[SamplingPointOut]:
    """Read the point registry CSV into validated point records."""
    raise NotImplementedError


def load_lab_csv(system_id: str, csv_path: Path) -> IngestReceipt:
    """Parse a laboratory result CSV; upsert idempotently on (point, measured_at)."""
    raise NotImplementedError


def load_epanet_network(inp_path: Path) -> NetworkSummary:
    """Summarize a committed EPANET model without running a simulation."""
    raise NotImplementedError
