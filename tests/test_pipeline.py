"""End-to-end tests for the Unit 4 vertical slice: ingest, detect, plan, evaluate.

These are the tests that make the system demonstration reproducible. Each one
asserts a property the video claims out loud:

* ingest is idempotent, so replaying a laboratory file changes nothing;
* a rule that has not been legally verified generates no duty;
* the ensemble beats the fixed-threshold baseline and says why it fired;
* the planner assigns every duty and stays inside the solve budget.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.case import CASE_SYSTEM_ID
from app.services.detect import open_alerts, run_detection
from app.services.evaluate import run as run_eval
from app.services.ingest import ingest_lab_csv
from app.services.plan import build_week_plan, generate_week_tasks, week_tasks
from modules.m1_ingest import load_epanet_network, load_sampling_points, parse_lab_csv
from modules.m2_detect import detect
from modules.m3_schedule import Stop, VehicleSpec, nearest_neighbour_km, plan_routes, two_opt
from modules.m4_rules import generate_tasks, load_rules
from modules.m6_eval import evaluate_detection, inject_decay, mileage_saving

DEMO_CSV = Path(__file__).resolve().parents[1] / "examples" / "demo_lab_results.csv"
POINTS_CSV = (
    Path(__file__).resolve().parents[1] / "data" / "networks" / "maple_creek_points.csv"
)
NETWORK_INP = Path(__file__).resolve().parents[1] / "data" / "networks" / "maple_creek.inp"

DECAYING = [
    ("2026-09-15T08:00:00", 0.95),
    ("2026-09-16T08:00:00", 0.88),
    ("2026-09-17T08:00:00", 0.79),
    ("2026-09-18T08:00:00", 0.68),
    ("2026-09-19T08:00:00", 0.57),
    ("2026-09-20T08:00:00", 0.46),
    ("2026-09-21T08:00:00", 0.33),
    ("2026-09-22T08:00:00", 0.31),
]

HEALTHY = [(f"2026-09-{day:02d}T08:00:00", 0.95 + (day % 3) * 0.02) for day in range(10, 20)]


def current_week() -> str:
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


# --------------------------------------------------------------------- M1 ---


def test_registry_loads_the_eighteen_committed_points() -> None:
    points = load_sampling_points(CASE_SYSTEM_ID, POINTS_CSV)
    assert len(points) == 18
    assert {point.location_type.value for point in points} == {"looped", "dead_end"}


def test_network_summary_matches_the_report() -> None:
    summary = load_epanet_network(NETWORK_INP)
    assert (summary.junctions, summary.pipes) == (25, 30)


def test_lab_parser_separates_valid_rows_from_refused_ones() -> None:
    """Parsing alone refuses one row; the registry check refuses the other.

    Two different layers reject rows, and the counts have to stay honest at
    both: the parser catches the 9.90 mg/L reading as out of range, and the
    ingest service catches SP99 because the registry does not know it.
    """
    parsed = parse_lab_csv(DEMO_CSV)
    assert parsed.rows_total == 14
    assert parsed.rows_accepted == 13
    assert parsed.rows_rejected == 1
    assert parsed.errors[0].field == "free_chlorine_mg_l"


def test_ingest_is_idempotent_on_replay(seeded: Session) -> None:
    content = DEMO_CSV.read_bytes()

    _upload, parsed, inserted = ingest_lab_csv(seeded, CASE_SYSTEM_ID, "demo.csv", content)
    assert inserted == 12
    assert parsed.rows_accepted == 12, "the registry refusal must adjust the counts"
    assert parsed.rows_rejected == 2
    assert parsed.rows_accepted + parsed.rows_rejected == parsed.rows_total

    _replay, _parsed_replay, inserted_again = ingest_lab_csv(
        seeded, CASE_SYSTEM_ID, "demo.csv", content
    )
    assert inserted_again == 0, "a replayed file must not insert a single row"


def test_ingest_refuses_an_unregistered_point(seeded: Session) -> None:
    body = b"sampling_point_id,measured_at,free_chlorine_mg_l\nZZ99,2026-09-15T08:00:00Z,0.8\n"
    _upload, parsed, inserted = ingest_lab_csv(seeded, CASE_SYSTEM_ID, "bad.csv", body)
    assert inserted == 0
    assert parsed.rows_rejected == 1


# --------------------------------------------------------------------- M2 ---


def test_detector_flags_a_decaying_series_with_reasons() -> None:
    alerts = detect("SP15", DECAYING)
    assert len(alerts) == 1
    codes = {code.value for code in alerts[0].context.reason_codes}
    assert "ewma_shift_up" in codes
    assert alerts[0].context.last_reading_mg_l == pytest.approx(0.31)
    assert alerts[0].severity.value == "watch", "0.31 mg/L is above the 0.05 compliance floor"


def test_detector_stays_quiet_on_a_healthy_series() -> None:
    assert detect("SP01", HEALTHY) == []


def test_detector_returns_nothing_below_the_minimum_series_length() -> None:
    assert detect("SP01", DECAYING[:3]) == []


def test_alert_payload_survives_the_round_trip_to_the_database(seeded: Session) -> None:
    ingest_lab_csv(seeded, CASE_SYSTEM_ID, "demo.csv", DEMO_CSV.read_bytes())
    report = run_detection(seeded, "SP15")
    assert report.alerts_raised == 1
    alerts = open_alerts(seeded, CASE_SYSTEM_ID)
    assert alerts and alerts[0].reason_codes
    assert alerts[0].acknowledged_at is None


# --------------------------------------------------------------------- M4 ---


def test_shipped_rule_catalogue_is_verified_and_traceable() -> None:
    """Unit 6 closed issue #4: no shipped rule may still be a placeholder.

    The catalogue was authored as draft data with `verified: false` and `TODO`
    source sections. It has now been checked clause by clause against the
    Ontario e-Laws consolidation, so this test fails if a future edit
    reintroduces an unverified or untraceable rule.
    """
    rules = load_rules()
    assert rules, "the rule catalogue should not be empty"
    assert all(rule.verified for rule in rules), "every shipped rule must be verified"
    assert all(not rule.inert for rule in rules), "a verified rule is not inert"
    for rule in rules:
        assert "TODO" not in rule.source_section, f"{rule.id} has no real citation"
        assert "O. Reg. 170/03" in rule.source_section or "Safe Drinking Water Act" in rule.source_section
        assert "TODO" not in rule.frequency, f"{rule.id} has no real frequency"

    # Two schedulable duties per point plus one workflow rule is what the case
    # system's band implies, so the count is asserted rather than assumed.
    schedulable = [rule for rule in rules if rule.hard_constraint]
    assert len(schedulable) == 2
    assert {rule.id for rule in schedulable} == {
        "micro_dist_weekly",
        "chlorine_residual_frequency",
    }


def test_unverified_rules_are_inert(tmp_path: Path) -> None:
    """The inert-until-verified safety property survives the verification work.

    Issue #4 is closed, so the property is now pinned against a synthetic draft
    catalogue: if a future amendment arrives before it has been read against the
    regulation, it must load, be visible, and generate nothing.
    """
    draft = tmp_path / "draft.yaml"
    draft.write_text(
        "version: test\n"
        "rule_sets:\n"
        "  - id: pending_amendment\n"
        "    rules:\n"
        "      - id: unverified_rule\n"
        "        parameter: free chlorine residual\n"
        "        location: distribution system\n"
        "        frequency: TODO - verify against regulation\n"
        "        source_section: TODO\n"
        "        hard_constraint: true\n"
        "        verified: false\n",
        encoding="utf-8",
    )

    rules = load_rules(draft)
    assert len(rules) == 1
    assert rules[0].inert and not rules[0].verified

    points = load_sampling_points(CASE_SYSTEM_ID, POINTS_CSV)
    strict = generate_tasks(CASE_SYSTEM_ID, "2026-W38", rules, points)
    assert strict.generated == 0
    assert strict.inert_rules_skipped == 1

    preview = generate_tasks(
        CASE_SYSTEM_ID, "2026-W38", rules, points, include_unverified=True
    )
    assert preview.generated == len(points)


# --------------------------------------------------------------------- M3 ---


def _toy_stops() -> list[Stop]:
    from datetime import datetime, timedelta

    start = datetime(2026, 9, 14, 0, 0)
    return [
        Stop(
            task_id=f"T{i}",
            point_id=f"SP{i:02d}",
            x_m=float(x),
            y_m=float(y),
            window_start=start,
            window_end=start + timedelta(days=7),
        )
        for i, (x, y) in enumerate(
            [(0, 0), (1000, 0), (2000, 0), (0, 1000), (1000, 1000), (2000, 1000)], start=1
        )
    ]


def test_planner_assigns_every_duty_and_respects_the_solve_budget() -> None:
    stops = _toy_stops()
    vehicles = [
        VehicleSpec("V1", 0.0, 0.0, 420),
        VehicleSpec("V2", 0.0, 0.0, 420),
    ]
    plan = plan_routes(CASE_SYSTEM_ID, date(2026, 9, 14), stops, vehicles)
    assert len(plan.stops) == len(stops)
    assert plan.solver_ms < 5_000
    assert plan.total_km > 0


def test_two_opt_never_makes_a_route_worse() -> None:
    stops = _toy_stops()
    vehicle = VehicleSpec("V1", 0.0, 0.0, 420)
    naive = [5, 0, 3, 1, 4, 2]
    improved = two_opt(naive, stops, vehicle)
    assert set(improved) == set(naive)
    from modules.m3_schedule import _tour_km

    assert _tour_km(improved, stops, vehicle) <= _tour_km(naive, stops, vehicle) + 1e-9


def test_planner_reports_infeasibility_instead_of_dropping_work() -> None:
    stops = _toy_stops()
    with pytest.raises(ValueError):
        plan_routes(CASE_SYSTEM_ID, date(2026, 9, 14), stops, [])


def test_week_plan_is_persisted_with_stops(seeded: Session) -> None:
    week = current_week()
    batch = generate_week_tasks(seeded, CASE_SYSTEM_ID, week, include_draft=True)
    assert batch.generated == 36, "two hard-constraint rules x 18 points"

    plan, baseline_km = build_week_plan(seeded, CASE_SYSTEM_ID, week)
    stops = week_tasks(seeded, CASE_SYSTEM_ID, week)
    assert plan.total_km > 0
    assert baseline_km > 0
    assert all(task.status == "planned" for task in stops)


def test_generating_the_same_week_twice_does_not_duplicate_duties(seeded: Session) -> None:
    week = current_week()
    first = generate_week_tasks(seeded, CASE_SYSTEM_ID, week, include_draft=True)
    second = generate_week_tasks(seeded, CASE_SYSTEM_ID, week, include_draft=True)
    assert first.generated == 36
    assert second.generated == 0, "deterministic task ids make regeneration a no-op"
    assert len(week_tasks(seeded, CASE_SYSTEM_ID, week)) == 36


# --------------------------------------------------------------------- M6 ---


def test_ensemble_beats_the_fixed_threshold_baseline() -> None:
    from app.services.evaluate import ground_truth_series

    series, labels = inject_decay(ground_truth_series(), start=10, end=30, drop=0.55)
    quality = evaluate_detection(series, labels)
    assert quality.f1_ensemble > quality.f1_baseline
    assert quality.median_lead_cycles >= 1, "the alert must precede the labelled event"


def test_mileage_saving_is_a_fraction() -> None:
    assert mileage_saving(85.0, 100.0) == pytest.approx(0.15)
    assert mileage_saving(10.0, 0.0) == 0.0


def test_evaluation_run_records_every_frozen_criterion(seeded: Session) -> None:
    week = current_week()
    generate_week_tasks(seeded, CASE_SYSTEM_ID, week, include_draft=True)
    run = run_eval(seeded, CASE_SYSTEM_ID, week, "test-sha", coverage=0.90)
    assert run.git_sha == "test-sha"

    from app.services.evaluate import metrics_for

    metrics = metrics_for(seeded, run.id)
    names = {metric.name for metric in metrics}
    assert len(metrics) == 6
    assert any(name.startswith("C1") for name in names)

    coverage_metric = next(m for m in metrics if m.name.startswith("C6"))
    assert coverage_metric.passed, "0.90 coverage clears the 0.70 floor"


# -------------------------------------------------------------------- API ---


def test_upload_endpoint_reports_duplicates_on_replay(client: TestClient) -> None:
    with DEMO_CSV.open("rb") as handle:
        first = client.post(
            f"/api/v1/systems/{CASE_SYSTEM_ID}/ingest/lab-csv",
            files={"file": ("demo.csv", handle, "text/csv")},
        )
    assert first.status_code == 202
    assert first.json()["rows_accepted"] == 12
    assert first.json()["rows_rejected"] == 2

    with DEMO_CSV.open("rb") as handle:
        second = client.post(
            f"/api/v1/systems/{CASE_SYSTEM_ID}/ingest/lab-csv",
            files={"file": ("demo.csv", handle, "text/csv")},
        )
    assert second.json()["rows_accepted"] == 0


def test_detect_then_acknowledge_over_http(client: TestClient) -> None:
    with DEMO_CSV.open("rb") as handle:
        client.post(
            f"/api/v1/systems/{CASE_SYSTEM_ID}/ingest/lab-csv",
            files={"file": ("demo.csv", handle, "text/csv")},
        )
    report = client.post("/api/v1/detect/run", params={"point": "SP15"})
    assert report.status_code == 200
    assert report.json()["alerts_raised"] == 1

    alerts = client.get("/api/v1/alerts").json()
    assert len(alerts) == 1
    codes = alerts[0]["context"]["reason_codes"]
    assert codes

    acked = client.post(
        f"/api/v1/alerts/{alerts[0]['id']}/ack",
        json={"operator_id": "op-1", "note": "flushing scheduled"},
    )
    assert acked.status_code == 200
    assert acked.json()["acknowledged_by"] == "op-1"
    assert client.get("/api/v1/alerts").json() == []
    assert client.post(
        f"/api/v1/alerts/{alerts[0]['id']}/ack", json={"operator_id": "op-1"}
    ).status_code == 404


def test_task_generation_expands_the_verified_catalogue(client: TestClient) -> None:
    """The verified catalogue now produces duties on the default path.

    Unit 5 asserted the opposite, because every rule was still a draft
    placeholder. Unit 6 verified the clauses, so the strict path is the one that
    generates: two schedulable rules over the 18 registered points.
    """
    week = current_week()
    strict = client.post("/api/v1/tasks/generate", params={"week": week})
    assert strict.status_code == 201
    assert strict.json()["generated"] == 36
    assert strict.json()["inert_rules_skipped"] == 0
    assert len(client.get("/api/v1/tasks", params={"week": week}).json()) == 36

    # A second call is idempotent: the deterministic task ids already exist.
    again = client.post("/api/v1/tasks/generate", params={"week": week})
    assert again.json()["generated"] == 0
    assert len(client.get("/api/v1/tasks", params={"week": week}).json()) == 36


def test_plan_creation_export_and_confirmation(client: TestClient) -> None:
    week = current_week()
    client.post("/api/v1/tasks/generate", params={"week": week, "include_draft": True})

    created = client.post("/api/v1/plans", params={"week": week})
    assert created.status_code == 201
    plan = created.json()
    assert len(plan["stops"]) == 36
    assert plan["solver_ms"] < 5_000

    csv_response = client.get(f"/api/v1/plans/{plan['id']}/export.csv")
    assert csv_response.status_code == 200
    assert csv_response.text.splitlines()[0].startswith("seq,sampling_point")

    confirmed = client.post(f"/api/v1/plans/{plan['id']}/confirm")
    assert confirmed.json()["status"] == "confirmed"
    assert client.post(f"/api/v1/plans/{plan['id']}/confirm").status_code == 409


def test_planning_before_generating_tasks_is_a_422(client: TestClient) -> None:
    response = client.post("/api/v1/plans", params={"week": "2026-W52"})
    assert response.status_code == 422
    assert "no required tasks" in response.json()["detail"]


def test_registry_and_evaluation_endpoints(client: TestClient) -> None:
    system = client.get(f"/api/v1/systems/{CASE_SYSTEM_ID}").json()
    assert system["point_count"] == 18
    assert system["vehicle_count"] == 2

    points = client.get(f"/api/v1/systems/{CASE_SYSTEM_ID}/sampling-points").json()
    assert len(points) == 18

    rules = client.get("/api/v1/rules").json()
    assert rules and all(rule["verified"] for rule in rules)
    assert all(not rule["inert"] for rule in rules)
    verified_only = client.get("/api/v1/rules", params={"verified": True}).json()
    assert verified_only == rules

    assert client.get("/api/v1/systems/nope").status_code == 404
    assert client.get("/api/v1/ingest/nope").status_code == 404
    assert client.get("/api/v1/plans/nope").status_code == 404
    assert client.get("/api/v1/eval/runs/nope").status_code == 404


def test_evaluation_endpoint_and_compliance_export(client: TestClient) -> None:
    week = current_week()
    client.post("/api/v1/tasks/generate", params={"week": week, "include_draft": True})

    run = client.post(
        "/api/v1/eval/run",
        params={"week": week, "git_sha": "abcdef1", "coverage": 0.9},
    )
    assert run.status_code == 201
    body = run.json()
    assert len(body["metrics"]) == 6
    assert client.get(f"/api/v1/eval/runs/{body['id']}").status_code == 200

    export = client.get("/dash/export/week.csv", params={"week": week})
    assert export.status_code == 200
    assert "task_id,sampling_point_id" in export.text


def test_dashboard_fragments_render(client: TestClient) -> None:
    assert "Network status" in client.get("/dash/network").text
    assert "Alert inbox" in client.get("/dash/alerts").text
    assert "duty list" in client.get("/dash/week").text


def test_manual_reading_round_trip(client: TestClient) -> None:
    created = client.post(
        "/api/v1/readings",
        params={
            "sampling_point_id": "SP01",
            "measured_at": "2026-09-18T09:30:00",
            "free_chlorine_mg_l": 0.87,
        },
    )
    assert created.status_code == 201
    readings = client.get("/api/v1/readings", params={"point": "SP01"}).json()
    assert readings[-1]["free_chlorine_mg_l"] == pytest.approx(0.87)


def test_ingest_status_explains_refused_rows(client: TestClient) -> None:
    with DEMO_CSV.open("rb") as handle:
        receipt = client.post(
            f"/api/v1/systems/{CASE_SYSTEM_ID}/ingest/lab-csv",
            files={"file": ("demo.csv", handle, "text/csv")},
        ).json()

    status = client.get(f"/api/v1/ingest/{receipt['upload_id']}").json()
    assert status["status"] == "partial"
    assert status["rows_rejected"] == 2
    assert status["row_errors"], "the refusal reason must survive the round trip"


def test_nearest_neighbour_baseline_is_usable() -> None:
    stops = _toy_stops()
    vehicles = [VehicleSpec("V1", 0.0, 0.0, 420), VehicleSpec("V2", 0.0, 0.0, 420)]
    assert nearest_neighbour_km(stops, vehicles) > 0


def test_week_bounds_span_monday_to_monday() -> None:
    from modules.m4_rules import week_bounds

    start, end = week_bounds("2026-W38")
    assert start.weekday() == 0
    assert (end - start).days == 7
