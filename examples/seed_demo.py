"""One command from a fresh clone to a demonstrable system.

Used as the opening move in the Unit 4 system demonstration:

    python examples/seed_demo.py

It seeds the case system, ingests the bundled laboratory export twice (the
second run proves the ingest is idempotent), runs the chlorine detector,
generates the week's duties, and plans the routes — printing what each step
produced so the output itself is the evidence.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import init_db, session_scope  # noqa: E402
from app.services.case import CASE_SYSTEM_ID, ensure_case_system  # noqa: E402
from app.services.detect import open_alerts, run_detection  # noqa: E402
from app.services.ingest import ingest_lab_csv  # noqa: E402
from app.services.plan import (  # noqa: E402
    build_week_plan,
    generate_week_tasks,
    sync_rules,
)

DEMO_CSV = ROOT / "examples" / "demo_lab_results.csv"
DEMO_POINT = "SP15"


def current_week() -> str:
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


def main() -> int:
    week = current_week()
    print(f"AquaOps ON — demo seed for week {week}\n")

    init_db()
    with session_scope() as session:
        system = ensure_case_system(session)
        print(f"[M1] case system registered: {system.name} "
              f"(population {system.population:,})")

        rules = sync_rules(session)
        print(f"[M4] rule catalogue synced: {rules} rules loaded from rules/oreg170.yaml")

        content = DEMO_CSV.read_bytes()

        receipt, parsed, inserted = ingest_lab_csv(
            session, CASE_SYSTEM_ID, DEMO_CSV.name, content
        )
        print(f"[M1] first ingest  : {parsed.rows_total} rows read, "
              f"{inserted} stored, {parsed.rows_rejected} refused")

        replay, parsed_replay, inserted_again = ingest_lab_csv(
            session, CASE_SYSTEM_ID, DEMO_CSV.name, content
        )
        print(f"[M1] replay ingest : {parsed_replay.rows_total} rows read, "
              f"{inserted_again} stored  <- idempotent, nothing duplicated")

        for error in parsed.errors:
            print(f"      refused row: {error.field} — {error.message}")

        report = run_detection(session, DEMO_POINT)
        print(f"[M2] detection on {DEMO_POINT}: {report.readings_evaluated} readings "
              f"evaluated, {report.alerts_raised} alert(s) raised")

        for alert in open_alerts(session, CASE_SYSTEM_ID):
            print(f"      {alert.sampling_point_id} {alert.severity} "
                  f"[{alert.reason_codes}] last={alert.last_reading_mg_l} mg/L "
                  f"threshold={alert.threshold_mg_l} mg/L")

        batch = generate_week_tasks(session, CASE_SYSTEM_ID, week, include_draft=True)
        print(f"[M4] tasks for {week}: {batch.generated} generated, "
              f"{batch.inert_rules_skipped} rule(s) inert (pending legal verification)")

        plan, baseline_km = build_week_plan(session, CASE_SYSTEM_ID, week)
        saving = (baseline_km - plan.total_km) / baseline_km if baseline_km else 0.0
        print(f"[M3] plan {plan.id[:8]}: {len(plan.stops) if hasattr(plan, 'stops') else '?'} "
              f"stops across the fleet")
        print(f"      total {plan.total_km} km vs nearest-neighbour {baseline_km:.2f} km "
              f"({saving:.1%} saving)")
        print(f"      solver time {plan.solver_ms} ms")

    print("\nDemo system ready. Start the server with:")
    print("    ./.venv/bin/uvicorn app.main:app --reload")
    print("Then open http://127.0.0.1:8000/ for the dashboard "
          "and http://127.0.0.1:8000/docs for the API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
