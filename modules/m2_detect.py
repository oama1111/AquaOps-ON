"""M2 — chlorine anomaly detection: EWMA ∪ Isolation Forest (ADR-1)."""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.readings import AlertOut

DEFAULT_THRESHOLD_MG_L = 0.05
DEFAULT_EWMA_SPAN = 14


def detect(
    sampling_point_id: str,
    series: Sequence[tuple[str, float]],
    *,
    threshold_mg_l: float = DEFAULT_THRESHOLD_MG_L,
    ewma_span: int = DEFAULT_EWMA_SPAN,
) -> list[AlertOut]:
    """Run the ensemble over one point's chlorine series; union the alarms."""
    raise NotImplementedError


def ewma_z_scores(series: Sequence[float], span: int = DEFAULT_EWMA_SPAN) -> list[float]:
    """Geometrically weighted moving-average z-scores (Roberts, 1959)."""
    raise NotImplementedError


def iforest_scores(series: Sequence[float], n_estimators: int = 100) -> list[float]:
    """Isolation depth per reading (Liu et al., 2008); no distribution assumed."""
    raise NotImplementedError


def reason_codes(
    ewma_z: float | None, iforest_score: float | None, trend: float | None
) -> list[str]:
    """Map model outputs to the codes the UI must render verbatim."""
    raise NotImplementedError
