"""Unit tests for M2 — the chlorine anomaly detector (`modules/m2_detect.py`).

Deliberately *unit* tests, not integration tests: every case here calls the
detector directly with a synthetic series and asserts one property, so a
failure names the module and the rule that broke rather than "the pipeline".

Test type is declared per test, because M2 contains both kinds of design:

``@pytest.mark.unit_blackbox``
    The test treats M2 as a black box: it supplies a chlorine series through the
    public ``detect`` / ``analyse`` contract and checks the observable verdict
    (alert raised or not, severity, reason code). It knows nothing about EWMA
    constants, the sigma estimator, or the Isolation Forest.

``@pytest.mark.unit_whitebox``
    The test deliberately reaches inside: it calls ``ewma_z_scores``,
    ``process_sigma``, ``slope_towards_limit`` and ``reason_codes`` in isolation
    and pins the boundary behaviour that the black-box cases cannot isolate.

Run just one kind with ``pytest -m unit_whitebox``.
"""

from __future__ import annotations

import pytest

from app.schemas.enums import AlertSeverity, ReasonCode
from modules.m2_detect import (
    EWMA_Z_LIMIT,
    MIN_READINGS,
    WATCH_LEVEL_MG_L,
    analyse,
    detect,
    ewma_z_scores,
    process_sigma,
    reason_codes,
    slope_towards_limit,
)


def series(values: list[float], start_day: int = 1) -> list[tuple[str, float]]:
    """Attach daily ISO timestamps to a list of readings."""
    return [(f"2026-09-{start_day + index:02d}T08:00:00", value) for index, value in enumerate(values)]


#: Slow, monotone decay of the kind a dead-end spur produces.
DECAY = [0.95, 0.88, 0.79, 0.68, 0.57, 0.46, 0.33, 0.31]

#: The same process with daily noise only — nothing to report.
STEADY = [0.95, 0.96, 0.94, 0.95, 0.93, 0.95, 0.94, 0.95]


# ------------------------------------------------------- black-box behaviour ---


@pytest.mark.unit_blackbox
def test_decaying_series_raises_exactly_one_explainable_alert() -> None:
    """One point, one verdict, and the verdict says why it fired."""
    alerts = detect("SP15", series(DECAY))

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.sampling_point_id == "SP15"
    assert alert.context.reason_codes, "an alert without a reason is not explainable"
    assert ReasonCode.EWMA_SHIFT_UP in alert.context.reason_codes
    assert alert.context.last_reading_mg_l == pytest.approx(0.31)


@pytest.mark.unit_blackbox
def test_severity_is_watch_above_the_compliance_floor() -> None:
    """0.31 mg/L is decaying but still legal, so the operator is warned, not paged."""
    alert = detect("SP15", series(DECAY))[0]
    assert alert.severity is AlertSeverity.WATCH


@pytest.mark.unit_blackbox
def test_severity_escalates_to_action_at_or_below_the_floor() -> None:
    """A reading on the 0.05 mg/L limit is a regulatory event, not a trend."""
    series_at_floor = series([0.95, 0.88, 0.79, 0.68, 0.57, 0.30, 0.12, 0.05])
    alert = detect("SP15", series_at_floor)[0]
    assert alert.severity is AlertSeverity.ACTION
    assert alert.context.threshold_mg_l == pytest.approx(0.05)


@pytest.mark.unit_blackbox
def test_steady_series_is_quiet() -> None:
    """No false alarm on a healthy point: the detector's silence is the product."""
    assert detect("SP01", series(STEADY)) == []


@pytest.mark.unit_blackbox
@pytest.mark.parametrize("length", [0, 1, MIN_READINGS - 1])
def test_short_series_never_alarms(length: int) -> None:
    """Below the minimum sample count there is no verdict to give, not a guess."""
    assert detect("SP01", series(DECAY[:length])) == []


@pytest.mark.unit_blackbox
def test_minimum_series_length_is_the_first_series_that_can_be_judged() -> None:
    codes, _z, _isolation = analyse(DECAY[:MIN_READINGS])
    assert isinstance(codes, list)


@pytest.mark.unit_blackbox
def test_detection_is_deterministic() -> None:
    """Replaying the same readings must give the same verdict.

    Isolation Forest samples randomly; the fixed ``random_state`` is what makes
    an operator's plan defensible after the fact and makes this test possible.
    """
    first = detect("SP15", series(DECAY))[0]
    second = detect("SP15", series(DECAY))[0]
    assert first.context.iforest_score == pytest.approx(second.context.iforest_score)
    assert first.context.reason_codes == second.context.reason_codes


# --------------------------------------------------------- white-box internals ---


@pytest.mark.unit_whitebox
def test_sigma_estimator_ignores_the_drift_it_must_detect() -> None:
    """The moving-range sigma must not be inflated by the trend itself.

    This is the bug the design notes call out: a standard deviation of residuals
    grows with the decay, cancels the signal, and lets chlorine decay slip
    through. The moving-range estimate sees only reading-to-reading noise.
    """
    drifting = process_sigma(DECAY)
    assert drifting == pytest.approx(0.0811, abs=1e-3)
    assert drifting > process_sigma([0.95, 0.96, 0.94, 0.95, 0.93])
    assert process_sigma([0.9]) == 0.0, "one reading has no moving range"


@pytest.mark.unit_whitebox
def test_ewma_z_score_is_negative_exactly_when_the_series_is_decaying() -> None:
    z_scores = ewma_z_scores(DECAY)
    assert len(z_scores) == len(DECAY)
    assert z_scores[0] == 0.0, "the first reading seeds the average"
    assert z_scores[-1] < -EWMA_Z_LIMIT, "the tail of a decay crosses the control limit"
    assert all(z == 0.0 for z in ewma_z_scores([0.95] * 6)), "a constant series has no shift"


@pytest.mark.unit_whitebox
def test_longer_span_makes_a_sustained_shift_stand_out() -> None:
    """The span is the lag/sensitivity knob, and the direction is not the obvious one.

    A short span tracks the series so closely that the current reading barely
    differs from the smoothed mean, so a slow decay looks unremarkable. A long
    span lags the decay, so the reading sits far below the mean and the shift is
    flagged. That is the signal the dead-end use case needs, so the assertion
    pins the measured values and a future tuning change becomes visible.
    """
    decaying = [1.0 - 0.06 * index for index in range(12)]

    short_span = ewma_z_scores(decaying, span=3)[-1]
    long_span = ewma_z_scores(decaying, span=30)[-1]

    assert short_span == pytest.approx(-1.127, abs=1e-3)
    assert long_span == pytest.approx(-8.502, abs=1e-3)
    assert long_span < short_span, "the long span must amplify the sustained shift"


@pytest.mark.unit_whitebox
@pytest.mark.parametrize(
    ("values", "expected_sign"),
    [([0.9, 0.8, 0.7, 0.6, 0.5], -1), ([0.5, 0.6, 0.7, 0.8, 0.9], 1), ([0.7] * 5, 0)],
)
def test_slope_sign_follows_the_trend(values: list[float], expected_sign: int) -> None:
    slope = slope_towards_limit(values)
    assert (slope > 0) - (slope < 0) == expected_sign
    assert slope_towards_limit([0.7]) == 0.0, "one point has no slope"


@pytest.mark.unit_whitebox
def test_trend_to_limit_needs_both_a_negative_slope_and_an_imminent_reading() -> None:
    """The watch band must be entered before the trend code is emitted."""
    near_limit = reason_codes(None, None, -0.05, last_reading_mg_l=0.40, outlier_cut=None)
    assert ReasonCode.TREND_TO_LIMIT in near_limit

    still_healthy = reason_codes(None, None, -0.05, last_reading_mg_l=0.80, outlier_cut=None)
    assert ReasonCode.TREND_TO_LIMIT not in still_healthy

    rising = reason_codes(None, None, 0.05, last_reading_mg_l=0.40, outlier_cut=None)
    assert ReasonCode.TREND_TO_LIMIT not in rising


@pytest.mark.unit_whitebox
def test_reason_codes_union_both_detectors() -> None:
    """An observation may break both rules at once; the operator sees both codes."""
    codes = reason_codes(
        -3.0, 0.5, -0.02, last_reading_mg_l=WATCH_LEVEL_MG_L - 0.05, outlier_cut=0.1
    )
    assert codes == [
        ReasonCode.EWMA_SHIFT_UP,
        ReasonCode.IFOREST_OUTLIER,
        ReasonCode.TREND_TO_LIMIT,
    ]


@pytest.mark.unit_whitebox
def test_outlier_cut_is_ignored_when_no_score_is_supplied() -> None:
    """``None`` means "that detector did not run", which must not raise."""
    assert reason_codes(None, None, None) == []
