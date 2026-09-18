"""Regression tests for the committed Maple Creek case network.

The Unit 3 report quotes specific numbers about this dataset (network size,
sampling-point count, dead-end count) and the evaluation harness depends on the
files being present and well formed. These tests turn those quoted numbers into
executable assertions so that the documentation cannot drift away from the data
in either direction.

They need no third-party package beyond pytest, so they run in the fast job;
the slower WNTR re-simulation and plausibility validation runs separately.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest

NETWORKS = Path(__file__).resolve().parents[1] / "data" / "networks"
INP = NETWORKS / "maple_creek.inp"
POINTS = NETWORKS / "maple_creek_points.csv"
CHLORINE = NETWORKS / "maple_creek_chlorine_48h.csv"

# Figures quoted in the Unit 3 technical report and data/README.md.
EXPECTED_JUNCTIONS = 25
EXPECTED_PIPES = 30
EXPECTED_POINTS = 18
EXPECTED_DEAD_END_POINTS = 6


def _sections(path: Path) -> dict[str, list[str]]:
    """Parse an EPANET .inp file into {SECTION: [data lines]}."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].upper()
            sections[current] = []
        elif current and line and not line.startswith(";"):
            sections[current].append(line)
    return sections


@pytest.fixture(scope="module")
def sections() -> dict[str, list[str]]:
    assert INP.is_file(), f"case network missing: {INP}"
    return _sections(INP)


@pytest.fixture(scope="module")
def points() -> list[dict[str, str]]:
    with POINTS.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_network_scale_matches_the_report(sections: dict[str, list[str]]) -> None:
    assert len(sections["JUNCTIONS"]) == EXPECTED_JUNCTIONS
    assert len(sections["PIPES"]) == EXPECTED_PIPES


def test_sampling_point_registry_counts(points: list[dict[str, str]]) -> None:
    assert len(points) == EXPECTED_POINTS
    ids = [row["sampling_point_id"] for row in points]
    assert ids == [f"SP{i:02d}" for i in range(1, EXPECTED_POINTS + 1)]
    assert len(set(ids)) == EXPECTED_POINTS, "duplicate sampling-point id"


def test_dead_end_count_matches_the_documentation(points: list[dict[str, str]]) -> None:
    """`dead_end` marks a point on a dead-end spur (high water age).

    It is a water-age classification, not a topological one: B3, D2 and E2 are
    intermediate junctions on spurs rather than degree-1 terminals. The report
    and data/README.md quote this number, so assert it here.
    """
    kinds = Counter(row["location_type"] for row in points)
    assert kinds["dead_end"] == EXPECTED_DEAD_END_POINTS
    assert kinds["looped"] == EXPECTED_POINTS - EXPECTED_DEAD_END_POINTS
    assert set(kinds) == {"dead_end", "looped"}


def test_every_sampling_point_is_a_real_junction(
    points: list[dict[str, str]], sections: dict[str, list[str]]
) -> None:
    junctions = {line.split()[0] for line in sections["JUNCTIONS"]}
    unknown = [row["node_id"] for row in points if row["node_id"] not in junctions]
    assert not unknown, f"registry references unknown junctions: {unknown}"


def test_chlorine_ground_truth_shape(points: list[dict[str, str]]) -> None:
    with CHLORINE.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 49, "expected 49 hourly steps for the 48-hour horizon"
    columns = [c for c in rows[0] if c != "time"]
    assert columns == [row["sampling_point_id"] for row in points]
