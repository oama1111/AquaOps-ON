"""M2 — chlorine anomaly detection: EWMA ∪ Isolation Forest (ADR-1).

Two detection philosophies are unioned rather than averaged, because they fail
differently:

* The **EWMA control chart** (Roberts, 1959) is sensitive to a small *sustained*
  shift in the process mean — exactly the signature of chlorine decaying along a
  dead-end spur over several days.
* **Isolation Forest** (Liu et al., 2008) isolates points by random partitioning
  and needs no distributional assumption, so it catches the single freak reading
  the smoothed chart deliberately ignores.

Every alert carries the reason it fired. An operator who is told "watch, but not
why" cannot exercise professional judgement, and a black-box alarm on a
public-health decision is an ethical failure, not only a usability one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from app.schemas.enums import AlertModel, AlertSeverity, ReasonCode
from app.schemas.readings import AlertContext, AlertOut

DEFAULT_THRESHOLD_MG_L = 0.05
DEFAULT_EWMA_SPAN = 14

#: The Unit 2 data profile puts the Ontario free-chlorine mean at 1.12 mg/L
#: (3,460 numeric DWSP records). A reading below half the provincial mean is
#: where operator attention starts paying off, so `trend_to_limit` watches that
#: band instead of waiting for the 0.05 mg/L compliance floor to be breached.
WATCH_LEVEL_MG_L = 0.50

EWMA_Z_LIMIT = 2.0
TREND_WINDOW = 5
MIN_READINGS = 5
OUTLIER_QUANTILE = 0.90


def ewma_z_scores(series: Sequence[float], span: int = DEFAULT_EWMA_SPAN) -> list[float]:
    """Geometrically weighted moving-average z-scores (Roberts, 1959)."""
    if not series:
        return []
    alpha = 2.0 / (span + 1.0)
    ewma = float(series[0])
    residuals: list[float] = [0.0]
    for value in series[1:]:
        ewma = alpha * float(value) + (1.0 - alpha) * ewma
        residuals.append(float(value) - ewma)
    sigma = process_sigma(series)
    if sigma == 0.0:
        return [0.0 for _ in residuals]
    return [r / sigma for r in residuals]


def process_sigma(series: Sequence[float]) -> float:
    """Estimate short-term process noise from the mean moving range.

    The obvious estimator — the standard deviation of the residuals — is biased
    in exactly the case that matters. A series trending downward has large
    residuals *because of the trend*, so the inflated sigma cancels the signal
    we are looking for and chlorine decay slips through. The mean moving range
    sees only reading-to-reading variation, so it estimates the noise floor
    rather than the drift. The 1.128 divisor is the standard Shewhart d2
    constant for a moving range of two observations.
    """
    if len(series) < 2:
        return 0.0
    ranges = [abs(float(series[i]) - float(series[i - 1])) for i in range(1, len(series))]
    mean_range = sum(ranges) / len(ranges)
    return mean_range / 1.128


def iforest_scores(series: Sequence[float], n_estimators: int = 100) -> list[float]:
    """Isolation depth per reading (Liu et al., 2008); no distribution assumed."""
    if len(series) < MIN_READINGS:
        return [0.0 for _ in series]
    import numpy as np
    from sklearn.ensemble import IsolationForest

    matrix = np.asarray(series, dtype=float).reshape(-1, 1)
    forest = IsolationForest(
        n_estimators=n_estimators,
        contamination="auto",
        random_state=42,  # deterministic: the same series must score identically
    )
    forest.fit(matrix)
    # decision_function is positive for inliers; negate so larger means odder.
    return [float(-score) for score in forest.decision_function(matrix)]


def slope_towards_limit(series: Sequence[float]) -> float:
    """Least-squares slope over the tail of the series, in mg/L per reading."""
    tail = list(series[-TREND_WINDOW:])
    if len(tail) < 2:
        return 0.0
    n = len(tail)
    mean_x = (n - 1) / 2.0
    mean_y = sum(tail) / n
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if denominator == 0.0:
        return 0.0
    return sum((i - mean_x) * (y - mean_y) for i, y in enumerate(tail)) / denominator


def reason_codes(
    ewma_z: float | None,
    iforest_score: float | None,
    trend: float | None,
    *,
    last_reading_mg_l: float | None = None,
    outlier_cut: float | None = None,
) -> list[ReasonCode]:
    """Map model outputs to the codes the UI must render verbatim."""
    codes: list[ReasonCode] = []
    if ewma_z is not None and ewma_z <= -EWMA_Z_LIMIT:
        codes.append(ReasonCode.EWMA_SHIFT_UP)
    if iforest_score is not None and outlier_cut is not None and iforest_score > outlier_cut:
        codes.append(ReasonCode.IFOREST_OUTLIER)
    if (
        trend is not None
        and trend < 0
        and last_reading_mg_l is not None
        and last_reading_mg_l < WATCH_LEVEL_MG_L
    ):
        codes.append(ReasonCode.TREND_TO_LIMIT)
    return codes


def analyse(
    series: Sequence[float],
    *,
    threshold_mg_l: float = DEFAULT_THRESHOLD_MG_L,
    ewma_span: int = DEFAULT_EWMA_SPAN,
) -> tuple[list[ReasonCode], float | None, float | None]:
    """Union the two detectors at the latest reading and explain the verdict."""
    if len(series) < MIN_READINGS:
        return [], None, None

    z_scores = ewma_z_scores(series, span=ewma_span)
    isolation = iforest_scores(series)
    ordered = sorted(isolation)
    cut_index = max(0, min(len(ordered) - 1, int(len(ordered) * OUTLIER_QUANTILE) - 1))

    codes = reason_codes(
        z_scores[-1],
        isolation[-1],
        slope_towards_limit(series),
        last_reading_mg_l=float(series[-1]),
        outlier_cut=ordered[cut_index],
    )
    return codes, z_scores[-1], isolation[-1]


def detect(
    sampling_point_id: str,
    series: Sequence[tuple[str, float]],
    *,
    threshold_mg_l: float = DEFAULT_THRESHOLD_MG_L,
    ewma_span: int = DEFAULT_EWMA_SPAN,
) -> list[AlertOut]:
    """Run the ensemble over one point's chlorine series; union the alarms."""
    values = [float(value) for _, value in series]
    codes, last_z, last_isolation = analyse(
        values, threshold_mg_l=threshold_mg_l, ewma_span=ewma_span
    )
    if not codes:
        return []

    if len(codes) > 1:
        model = AlertModel.ENSEMBLE
    elif codes[0] is ReasonCode.EWMA_SHIFT_UP:
        model = AlertModel.EWMA
    elif codes[0] is ReasonCode.IFOREST_OUTLIER:
        model = AlertModel.IFOREST
    else:
        model = AlertModel.ENSEMBLE

    last_reading = values[-1]
    severity = AlertSeverity.ACTION if last_reading <= threshold_mg_l else AlertSeverity.WATCH

    return [
        AlertOut(
            id=str(uuid.uuid4()),
            sampling_point_id=sampling_point_id,
            raised_at=datetime.now(UTC).replace(tzinfo=None),
            model=model,
            severity=severity,
            context=AlertContext(
                model=model,
                ewma_z=last_z,
                iforest_score=last_isolation,
                last_reading_mg_l=last_reading,
                threshold_mg_l=threshold_mg_l,
                reason_codes=codes,
            ),
            acknowledged_by=None,
            acknowledged_at=None,
        )
    ]
