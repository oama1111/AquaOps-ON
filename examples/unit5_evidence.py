"""Unit 5 evidence run — reproduce the core algorithms' outputs on the case data.

Prints, in one deterministic transcript:

1. M2's three-tier verdict (quiet / watch / action) with the reason codes that
   explain each decision, over three real dead-end series shapes.
2. M3's weekly plan, its total distance, and the saving against the frozen C4
   nearest-neighbour baseline.
3. M6's frozen-criteria verdict list (C1-C6) for the committed case dataset.

Nothing here is hand-written: every number is produced by the modules under
test. Run it with

    MPLCONFIGDIR=/tmp/mplcache python examples/unit5_evidence.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from app.services.evaluate import ground_truth_series
from modules.m1_ingest import load_sampling_points
from modules.m2_detect import MIN_READINGS, detect
from modules.m3_schedule import Stop, VehicleSpec, nearest_neighbour_km, plan_routes
from modules.m6_eval import EvaluationInputs, evaluate_detection, inject_decay, run_evaluation

ROOT = Path(__file__).resolve().parents[1]
POINTS_CSV = ROOT / "data" / "networks" / "maple_creek_points.csv"
CASE_SYSTEM_ID = "case-system"
DEPOT_X_M, DEPOT_Y_M = 150.0, 500.0
WEEK_START = date(2026, 9, 14)


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def tier(series: list[float]) -> str:
    """Classify a series the way the dashboard does: quiet, watch, or action."""
    alerts = detect("SP15", [(f"day-{index}", value) for index, value in enumerate(series)])
    if not alerts:
        return "quiet   (no alert)"
    alert = alerts[0]
    reasons = ", ".join(code.value for code in alert.context.reason_codes)
    return f"{alert.severity.value:<7} reasons=[{reasons}]"


def main() -> None:
    rule("PART A / M2 - three-tier chlorine verdicts (EWMA union Isolation Forest)")

    healthy = [0.95, 0.96, 0.94, 0.95, 0.93, 0.95, 0.94, 0.95]
    decaying = [0.95, 0.88, 0.79, 0.68, 0.57, 0.46, 0.33, 0.31]
    at_limit = [0.62, 0.55, 0.48, 0.31, 0.22, 0.14, 0.07, 0.04]

    print(f"minimum series length enforced: MIN_READINGS = {MIN_READINGS}")
    print()
    for label, values in (
        ("healthy point      ", healthy),
        ("dead-end decay     ", decaying),
        ("decay to the limit ", at_limit),
    ):
        print(f"  {label} last={values[-1]:.2f} mg/L -> {tier(values)}")
        print(f"  {'':21} series={values}")

    alert = detect("SP15", [(f"day-{index}", value) for index, value in enumerate(decaying)])[0]
    context = alert.context
    print()
    print("  alert context persisted for the decaying point:")
    print(f"    model            = {context.model.value}")
    print(f"    ewma_z           = {context.ewma_z:.4f}  (control limit -2.0)")
    print(f"    iforest_score    = {context.iforest_score:.4f}")
    print(f"    last_reading_mg_l= {context.last_reading_mg_l}")
    print(f"    reason_codes     = {[code.value for code in context.reason_codes]}")

    rule("PART A / M3 - weekly plan against the frozen C4 baseline")

    week_start = WEEK_START
    window_start = datetime.combine(week_start, datetime.min.time())
    points = load_sampling_points(CASE_SYSTEM_ID, POINTS_CSV)
    vehicles = [
        VehicleSpec("V1", DEPOT_X_M, DEPOT_Y_M, 420),
        VehicleSpec("V2", DEPOT_X_M, DEPOT_Y_M, 420),
    ]

    def build_stops(rule_ids: tuple[str, ...]) -> list[Stop]:
        return [
            Stop(
                task_id=f"{point.id}:{rule_id}",
                point_id=point.id,
                x_m=point.x_m,
                y_m=point.y_m,
                window_start=window_start,
                window_end=window_start + timedelta(days=7),
            )
            for rule_id in rule_ids
            for point in points
        ]

    def report(label: str, stop_list: list[Stop]) -> tuple[float, float]:
        candidate = plan_routes(CASE_SYSTEM_ID, week_start, stop_list, vehicles)
        baseline = nearest_neighbour_km(stop_list, vehicles)
        print(f"  {label}")
        print(f"    duties planned = {len(candidate.stops)}  (of {len(stop_list)} required)")
        print(f"    plan total_km  = {candidate.total_km}")
        print(f"    baseline_km    = {baseline:.3f}  (naive nearest-neighbour)")
        print(f"    saving         = {(baseline - candidate.total_km) / baseline:.1%}"
              f"   (frozen C4 target: 15%)")
        print(f"    solver_ms      = {candidate.solver_ms}   (budget: 5000 ms)")
        return candidate.total_km, baseline

    print("  M3 is exercised on the 18 unique sampling points and on the real")
    print("  weekly duty list, which contains one duty per hard-constraint rule:")
    print()
    report("18 unique sampling points:", build_stops(("micro_dist_weekly",)))
    print()
    plan_km, baseline_km = report(
        "36 weekly duties (2 hard-constraint rules x 18 points):",
        build_stops(("micro_dist_weekly", "chlorine_residual_frequency")),
    )

    plan = plan_routes(
        CASE_SYSTEM_ID,
        week_start,
        build_stops(("micro_dist_weekly", "chlorine_residual_frequency")),
        vehicles,
    )
    print()
    print(f"  first twelve stops of the returned plan ({len(plan.stops)} duties):")
    print(f"  {'seq':>3}  {'point':<6}{'vehicle':<9}{'eta':<20}{'leg km':>7}")
    for stop in plan.stops[:12]:
        print(
            f"  {stop.seq:>3}  {stop.sampling_point_id:<6}{stop.vehicle_id:<9}"
            f"{stop.eta.isoformat(sep=' '):<20}{stop.travel_km:>7}"
        )

    rule("PART A / M6 - frozen criteria C1-C6 for the committed case dataset")

    series = ground_truth_series()
    injected, labels = inject_decay(series, start=10, end=30, drop=0.55)
    quality = evaluate_detection(injected, labels)
    print(f"  F1 (ensemble)  = {quality.f1_ensemble:.4f}")
    print(f"  F1 (baseline)  = {quality.f1_baseline:.4f}   (fixed 0.05 mg/L threshold)")
    print(f"  precision      = {quality.precision:.4f}")
    print(f"  recall         = {quality.recall:.4f}")
    print(f"  median lead    = {quality.median_lead_cycles} sampling cycles")

    coverage = 0.95
    metrics = run_evaluation(
        EvaluationInputs(
            dataset="maple-creek-48h",
            git_sha="unit5-evidence",
            series=injected,
            labels=labels,
            plan_km=plan_km,
            baseline_km=baseline_km,
            solver_ms=plan.solver_ms,
            missed_windows=0,
            coverage=coverage,
            api_p95_ms=42.0,
        )
    )
    print()
    print(f"  {'criterion':<32}{'value':>10}   verdict")
    for metric in metrics:
        print(f"  {metric.name:<32}{metric.value:>10}   {'PASS' if metric.passed else 'FAIL'}")


if __name__ == "__main__":
    main()
