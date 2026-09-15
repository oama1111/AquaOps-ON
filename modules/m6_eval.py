"""M6 — evaluation harness: replay ground truth, assert the frozen metrics."""

from __future__ import annotations

from pathlib import Path

from app.schemas.evaluation import MetricOut

THRESHOLD_BASELINE_MG_L = 0.05
MILEAGE_SAVING_TARGET = 0.15
MIN_COVERAGE = 0.70


def run_evaluation(dataset: str, git_sha: str) -> list[MetricOut]:
    """Replay a dataset through M2/M3 and evaluate the frozen criteria C1–C6."""
    raise NotImplementedError


def f1_vs_threshold(dataset: str) -> float:
    """Anomaly F1 against the fixed-threshold baseline (criterion C1)."""
    raise NotImplementedError


def median_lead_time_cycles(dataset: str) -> float:
    """Median warning lead time in sampling cycles (criterion C2)."""
    raise NotImplementedError


def missed_windows(dataset: str) -> int:
    """Regulatory windows missed on the case system (criterion C3)."""
    raise NotImplementedError


def mileage_vs_greedy(dataset: str) -> float:
    """Fractional mileage saving over naive greedy (criterion C4)."""
    raise NotImplementedError


def solver_ms(dataset: str) -> int:
    """Wall-clock solve time for the 18-point instance (criterion C4)."""
    raise NotImplementedError


def api_p95_ms(dataset: str) -> float:
    """p95 API latency on the case dataset (criterion C5)."""
    raise NotImplementedError


def report_path(git_sha: str) -> Path:
    """Where a run's metric report is written under docs/evidence/."""
    raise NotImplementedError
