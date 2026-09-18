"""M6 — evaluation harness: replay ground truth, assert the frozen metrics.

The metrics this module computes are the ones frozen in the Unit 2 proposal, so
that later units are judged against fixed targets rather than moving goalposts.
Two properties make the numbers trustworthy:

* **No lookahead.** Detection quality is scored by replaying the series and
  asking what the system would have said *at that moment*, using only readings
  available then. Scoring a detector on a full series it can see end-to-end
  would flatter it and, worse, would not correspond to anything an operator
  ever experiences.
* **Declared baseline.** Anomaly F1 is reported next to the fixed-threshold
  baseline (chlorine < 0.05 mg/L), never on its own. A number without a
  baseline is an advertisement, not an evaluation.

Interface note: the Unit 3 skeleton declared ``run_evaluation(dataset: str)``,
which would have made the harness read the database. It now takes an explicit
``EvaluationInputs`` so a run is reproducible from its arguments alone.
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.evaluation import MetricOut

#: The band the operator treats as actionable. Imported from M2 rather than
#: duplicated, so the detector and its evaluation can never disagree about what
#: "approaching the limit" means.
from modules.m2_detect import WATCH_LEVEL_MG_L

THRESHOLD_BASELINE_MG_L = 0.05
MILEAGE_SAVING_TARGET = 0.15
MIN_COVERAGE = 0.70
MAX_SOLVE_MS = 5_000
FALSE_ALARM_BUDGET_PER_WEEK = 2.0


@dataclass
class DetectionQuality:
    """Ensemble performance next to the baseline it must beat."""

    f1_ensemble: float
    f1_baseline: float
    precision: float
    recall: float
    median_lead_cycles: float
    alerts_raised: int
    false_alarms_per_week: float


@dataclass
class EvaluationInputs:
    """Everything a run needs, so the run is reproducible from its arguments."""

    dataset: str
    git_sha: str
    series: Sequence[float] = field(default_factory=list)
    labels: Sequence[bool] = field(default_factory=list)
    plan_km: float = 0.0
    baseline_km: float = 0.0
    solver_ms: int = 0
    missed_windows: int = 0
    coverage: float = 0.0
    api_p95_ms: float = 0.0


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    if true_positive == 0:
        return 0.0
    precision = true_positive / (true_positive + false_positive)
    recall = true_positive / (true_positive + false_negative)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def detection_timeline(
    series: Sequence[float],
    *,
    threshold_mg_l: float = THRESHOLD_BASELINE_MG_L,
    ewma_span: int = 14,
) -> list[bool]:
    """Replay the series and record what M2 would have said at each reading.

    Each prefix is evaluated on its own, so the answer at index *i* uses only
    readings 0..i. That is the operator's actual information set.
    """
    from modules.m2_detect import MIN_READINGS, analyse

    timeline: list[bool] = []
    for index in range(len(series)):
        prefix = list(series[: index + 1])
        if len(prefix) < MIN_READINGS:
            timeline.append(False)
            continue
        codes, _, _ = analyse(prefix, threshold_mg_l=threshold_mg_l, ewma_span=ewma_span)
        timeline.append(bool(codes))
    return timeline


def evaluate_detection(
    series: Sequence[float],
    labels: Sequence[bool],
    *,
    threshold_mg_l: float = THRESHOLD_BASELINE_MG_L,
) -> DetectionQuality:
    """Score the ensemble against injected ground truth and the fixed baseline."""
    if len(series) != len(labels):
        raise ValueError("series and labels must be the same length")

    timeline = detection_timeline(series, threshold_mg_l=threshold_mg_l)
    baseline = [value < threshold_mg_l for value in series]

    def confusion(predictions: Sequence[bool]) -> tuple[int, int, int]:
        tp = sum(1 for p, y in zip(predictions, labels) if p and y)
        fp = sum(1 for p, y in zip(predictions, labels) if p and not y)
        fn = sum(1 for p, y in zip(predictions, labels) if not p and y)
        return tp, fp, fn

    tp, fp, fn = confusion(timeline)
    base_tp, base_fp, base_fn = confusion(baseline)

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    leads: list[float] = []
    label_indices = [i for i, flag in enumerate(labels) if flag]
    alert_indices = [i for i, flag in enumerate(timeline) if flag]

    # Lead time is measured to the moment the reading actually crosses the
    # actionable band, not to the first injected sample. The injection labels
    # the whole decay episode, so measuring against its first sample would score
    # "detected at onset" as zero warning — when detecting at onset is exactly
    # what the criterion asks for. The search is restricted to the labelled
    # window on purpose: the simulated series still carries start-up transients
    # that briefly dip below the band, and an unrelated transient is not the
    # event the detector is being asked to pre-empt.
    event_index = next(
        (i for i in label_indices if series[i] < WATCH_LEVEL_MG_L),
        label_indices[0] if label_indices else None,
    )
    if event_index is not None and alert_indices:
        earlier = [event_index - i for i in alert_indices if i <= event_index]
        if earlier:
            # The *first* warning is what the operator experiences as lead time,
            # and because the list is measured backwards it is the largest
            # value, not the smallest.
            leads.append(float(max(earlier)))

    return DetectionQuality(
        f1_ensemble=_f1(tp, fp, fn),
        f1_baseline=_f1(base_tp, base_fp, base_fn),
        precision=precision,
        recall=recall,
        median_lead_cycles=statistics.median(leads) if leads else 0.0,
        alerts_raised=len(alert_indices),
        false_alarms_per_week=float(len([i for i in alert_indices if not labels[i]])),
    )


def mileage_saving(plan_km: float, baseline_km: float) -> float:
    """Fractional mileage saving over the naive nearest-neighbour baseline (C4)."""
    if baseline_km <= 0:
        return 0.0
    return (baseline_km - plan_km) / baseline_km


def inject_decay(
    series: Sequence[float], *, start: int, end: int, drop: float = 0.55
) -> tuple[list[float], list[bool]]:
    """Label a synthetic decay episode so C1/C2 can be scored deterministically.

    The Unit 2 data profile gives us a realistic baseline series but no labelled
    failures, and the literature is explicit that real-world training labels are
    scarce (Li et al., 2024). Injecting a known episode into a physically
    simulated series is the honest substitute: the label is exact by
    construction, and the same injection runs in CI every time.
    """
    values = list(series)
    labels = [False] * len(values)
    for index in range(start, min(end, len(values))):
        progress = (index - start) / max(1, end - start - 1)
        values[index] = round(values[index] * (1.0 - drop * progress), 4)
        labels[index] = True
    return values, labels


def build_metrics(inputs: EvaluationInputs) -> list[MetricOut]:
    """Turn one run's measurements into the frozen-criteria verdict list (C1-C6)."""
    quality = evaluate_detection(inputs.series, inputs.labels) if inputs.series else None
    saving = mileage_saving(inputs.plan_km, inputs.baseline_km)

    metrics = [
        MetricOut(
            name="C1 anomaly detection quality",
            value=round(quality.f1_ensemble, 4) if quality else 0.0,
            target=f"F1 strictly above the fixed-threshold baseline "
            f"(baseline F1 = {round(quality.f1_baseline, 4) if quality else 0.0})",
            passed=bool(quality and quality.f1_ensemble > quality.f1_baseline),
        ),
        MetricOut(
            name="C2 warning timeliness",
            value=round(quality.median_lead_cycles, 2) if quality else 0.0,
            target="median lead time >= 1 sampling cycle; <= 2 false alarms per week per point",
            passed=bool(
                quality
                and quality.median_lead_cycles >= 1
                and quality.false_alarms_per_week <= FALSE_ALARM_BUDGET_PER_WEEK
            ),
        ),
        MetricOut(
            name="C3 regulatory coverage",
            value=float(inputs.missed_windows),
            target="zero missed regulatory windows on the case system",
            passed=inputs.missed_windows == 0,
        ),
        MetricOut(
            name="C4 route efficiency",
            value=round(saving, 4),
            target=f"mileage >= {MILEAGE_SAVING_TARGET:.0%} below nearest-neighbour; solve < 5 s",
            passed=bool(saving >= MILEAGE_SAVING_TARGET and inputs.solver_ms < MAX_SOLVE_MS),
        ),
        MetricOut(
            name="C5 system performance",
            value=round(inputs.api_p95_ms, 2),
            target="API p95 latency < 300 ms on the case dataset",
            passed=bool(inputs.api_p95_ms and inputs.api_p95_ms < 300),
        ),
        MetricOut(
            name="C6 engineering quality",
            value=round(inputs.coverage, 4),
            target=f"test coverage >= {MIN_COVERAGE:.0%}; reproducible deployment",
            passed=bool(inputs.coverage >= MIN_COVERAGE),
        ),
    ]
    return metrics


def run_evaluation(inputs: EvaluationInputs) -> list[MetricOut]:
    """Evaluate the frozen criteria C1-C6 for one dataset replay."""
    return build_metrics(inputs)


def evidence_root() -> Path:
    """Directory that evaluation reports are written to.

    Overridable with ``AQUAOPS_EVIDENCE_DIR`` so the test suite writes its
    throwaway runs to a temporary directory instead of leaving synthetic
    reports lying in the committed evidence folder, where they would be
    indistinguishable from real ones.
    """
    override = os.environ.get("AQUAOPS_EVIDENCE_DIR")
    root = (
        Path(override)
        if override
        else Path(__file__).resolve().parents[1] / "docs" / "evidence"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def report_path(git_sha: str) -> Path:
    """Where a run's metric report is written under docs/evidence/."""
    return evidence_root() / f"eval-{git_sha[:7]}.md"


def render_report(inputs: EvaluationInputs, metrics: Sequence[MetricOut]) -> str:
    """A small markdown report suitable for committing as dated evidence."""
    lines = [
        f"# Evaluation run — {inputs.dataset}",
        "",
        f"- git SHA: `{inputs.git_sha}`",
        f"- plan km: {inputs.plan_km} (baseline {inputs.baseline_km})",
        f"- solver ms: {inputs.solver_ms}",
        f"- missed windows: {inputs.missed_windows}",
        f"- test coverage: {inputs.coverage:.0%}",
        "",
        "| criterion | value | target | verdict |",
        "| --- | --- | --- | --- |",
    ]
    for metric in metrics:
        lines.append(
            f"| {metric.name} | {metric.value} | {metric.target} | "
            f"{'PASS' if metric.passed else 'FAIL'} |"
        )
    return "\n".join(lines) + "\n"
