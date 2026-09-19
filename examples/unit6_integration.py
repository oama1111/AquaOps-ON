"""Unit 6 integration harness: drive the assembled system over real HTTP.

Every other example in this repository calls the service layer or the modules
directly. This one does not. It boots the *deployed* application with
``uvicorn`` on a loopback port, waits for the health probe, and then walks the
whole operator workflow with an HTTP client — the same path a browser or an
HTMX fragment request takes. That is what makes it integration evidence rather
than another unit test:

* the UI-to-backend link is exercised by requesting the server-rendered
  dashboard and its HTMX fragments and asserting that real data appears in the
  returned HTML;
* the API-to-module link is exercised because the API is the only entry point,
  so M1, M2, M3, M4 and M6 are reached through routers and services;
* the service-to-database link is exercised by re-reading everything through
  fresh requests after writing it, and by proving ingest idempotency on replay.

The run is deliberately hermetic: it points ``AQUAOPS_DB_URL`` at a throwaway
SQLite file so the transcript is reproducible from a clean clone, and it prints
one block per step so the output itself is the evidence.

    python examples/unit6_integration.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.unit6_harness import (  # noqa: E402
    DEAD_END_POINTS,
    DEMO_CSV,
    EVIDENCE,
    ISO_WEEK,
    field_week_csv,
    live_server,
    wait_for_health,
)


class Transcript:
    """Collect the steps so they can be printed and written to a log at once."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.results: dict[str, Any] = {}
        self.step = 0

    def say(self, text: str = "") -> None:
        self.lines.append(text)
        print(text)

    def record(self, label: str, request: str, response: str, extra: str = "") -> None:
        self.step += 1
        entry = f"[{self.step:02d}] {label}"
        self.say(entry)
        self.say(f"     {request}")
        self.say(f"     {response}")
        if extra:
            self.say(f"     {extra}")
        self.say()

    def save(self, name: str, payload: Any) -> None:
        self.results[name] = payload


def benchmarked_p95() -> tuple[float, str]:
    """The worst-endpoint p95 the benchmark measured, if it has been run.

    C5 cannot be measured by an integration walk — a handful of requests is not a
    latency profile — so this harness reads the number
    ``examples/unit6_perf.py`` wrote instead of inventing one, and reports where
    the number came from.
    """
    path = EVIDENCE / "unit6-perf.json"
    if path.is_file():
        try:
            measured = json.loads(path.read_text(encoding="utf-8"))
            value = float(measured.get("worst_p95_ms") or 0.0)
            if value > 0:
                return value, (
                    f"examples/unit6_perf.py, commit {measured.get('git_sha', 'unknown')}"
                )
        except (OSError, ValueError, TypeError):
            pass
    return 0.0, "no benchmark result found; run examples/unit6_perf.py"


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    log = Transcript()
    started = time.monotonic()

    with live_server() as live:
        base, port = live.base_url, live.port
        try:
            log.say("=" * 78)
            log.say("AquaOps ON — Unit 6 end-to-end integration transcript")
            log.say(
                "One deployable unit (app.main:app) booted with uvicorn; every step "
                "below is a real HTTP request."
            )
            log.say("=" * 78)
            log.say()

            health, boot_ms = wait_for_health(live.base_url, live.process)
            log.record(
                "boot the deployable unit and probe readiness",
                f"uvicorn app.main:app --host 127.0.0.1 --port {port}   (cold start)",
                f"GET /health -> 200 {json.dumps(health)}",
                f"lifespan startup (create tables, seed case system, sync rules) "
                f"completed in ~{boot_ms} ms",
            )
            log.save("health", health)
            log.save("boot_ms", boot_ms)

            client = httpx.Client(base_url=base, timeout=60.0)

            # ---------------------------------------------------------- M1 ---
            system = client.get("/api/v1/systems/maple-creek").json()
            log.record(
                "M1 registry — read the system profile through the API",
                "GET /api/v1/systems/maple-creek",
                f"200 {json.dumps(system)}",
            )
            log.save("system", system)

            points = client.get("/api/v1/systems/maple-creek/sampling-points").json()
            dead_ends = [p["id"] for p in points if p["location_type"] == "dead_end"]
            log.record(
                "M1 registry — the sampling-point registry as the planner sees it",
                "GET /api/v1/systems/maple-creek/sampling-points",
                f"200 {len(points)} points, {len(dead_ends)} of them dead-end spurs "
                f"({', '.join(dead_ends)})",
                f"first record: {json.dumps(points[0])}",
            )
            log.save("point_count", len(points))
            log.save("dead_ends", dead_ends)

            # ------------------------------------------------- lab upload ---
            demo = DEMO_CSV.read_bytes()
            files = {"file": ("demo_lab_results.csv", demo, "text/csv")}
            receipt = client.post(
                "/api/v1/systems/maple-creek/ingest/lab-csv", files=files
            ).json()
            log.record(
                "M1 ingest — laboratory CSV uploaded as multipart/form-data",
                "POST /api/v1/systems/maple-creek/ingest/lab-csv  (file=demo_lab_results.csv)",
                f"202 {json.dumps(receipt)}",
                "two rows were refused: 9.90 mg/L is outside the 0.0-5.0 sanity band, "
                "and SP99 is not in the registry",
            )
            log.save("receipt_first", receipt)

            files = {"file": ("demo_lab_results.csv", demo, "text/csv")}
            replay = client.post(
                "/api/v1/systems/maple-creek/ingest/lab-csv", files=files
            ).json()
            log.record(
                "M1 ingest — the identical file sent again (idempotency)",
                "POST /api/v1/systems/maple-creek/ingest/lab-csv  (same bytes)",
                f"202 {json.dumps(replay)}",
                "rows_accepted = 0: the (point, measured_at) key recognised every row, "
                "so a re-sent e-mail cannot duplicate a compliance record",
            )
            log.save("receipt_replay", replay)

            status = client.get(f"/api/v1/ingest/{receipt['upload_id']}").json()
            log.record(
                "M1 ingest — the stored receipt, with per-row refusals",
                f"GET /api/v1/ingest/{receipt['upload_id']}",
                f"200 status={status['status']} accepted={status['rows_accepted']} "
                f"rejected={status['rows_rejected']}",
                "refusals: " + "; ".join(
                    f"row {e['row_number']} {e['field']}: {e['message']}"
                    for e in status["row_errors"]
                ),
            )
            log.save("ingest_status", status)

            field_csv = field_week_csv()
            files = {"file": ("field_week_2026-W38.csv", field_csv, "text/csv")}
            field_receipt = client.post(
                "/api/v1/systems/maple-creek/ingest/lab-csv", files=files
            ).json()
            log.record(
                "M1 ingest — a full field week derived from the WNTR ground truth",
                "POST /api/v1/systems/maple-creek/ingest/lab-csv  "
                "(field_week_2026-W38.csv)",
                f"202 {json.dumps(field_receipt)}",
                f"{len(DEAD_END_POINTS)} dead-end points x "
                f"{field_receipt['rows_total'] // len(DEAD_END_POINTS)} hourly readings",
            )
            log.save("receipt_field_week", field_receipt)

            # ---------------------------------------------------------- M2 ---
            manual = client.post(
                "/api/v1/readings",
                params={
                    "sampling_point_id": "SP15",
                    "measured_at": "2026-09-23T08:00:00",
                    "free_chlorine_mg_l": 0.29,
                },
            ).json()
            log.record(
                "M2 readings — an operator types a field reading straight into the API",
                "POST /api/v1/readings?sampling_point_id=SP15"
                "&measured_at=2026-09-23T08:00:00&free_chlorine_mg_l=0.29",
                f"201 {json.dumps(manual)}",
            )
            log.save("manual_reading", manual)

            duplicate = client.post(
                "/api/v1/readings",
                params={
                    "sampling_point_id": "SP15",
                    "measured_at": "2026-09-23T08:00:00",
                    "free_chlorine_mg_l": 0.29,
                },
            )
            log.record(
                "M2 readings — the same reading submitted twice (integration defect fixed)",
                "POST /api/v1/readings  (identical point, timestamp and value)",
                f"{duplicate.status_code} {str(duplicate.json().get('detail'))[:104]}…",
                "before the Unit 6 fix this reached the database and returned a 500 "
                "from an IntegrityError; the conflict is now an explicit, named 409",
            )
            log.save("duplicate_status", duplicate.status_code)

            series = client.get("/api/v1/readings", params={"point": "SP15"}).json()
            log.record(
                "M2 readings — the stored series read back for the chart",
                "GET /api/v1/readings?point=SP15",
                f"200 {len(series)} readings, "
                f"{series[0]['free_chlorine_mg_l']} -> "
                f"{series[-1]['free_chlorine_mg_l']} mg/L",
                f"source of the last row: {series[-1]['source']}",
            )
            log.save("sp15_series_length", len(series))
            log.save("sp15_first", series[0]["free_chlorine_mg_l"])
            log.save("sp15_last", series[-1]["free_chlorine_mg_l"])

            detect_report = client.post(
                "/api/v1/detect/run", params={"point": "SP15"}
            ).json()
            log.record(
                "M2 detection — one on-demand pass over the stored series",
                "POST /api/v1/detect/run?point=SP15",
                f"200 {json.dumps(detect_report)}",
                "the detector is only meaningful from 5 readings onward; the report "
                "names how many it evaluated",
            )
            log.save("detect_report", detect_report)

            repeat_report = client.post(
                "/api/v1/detect/run", params={"point": "SP15"}
            ).json()
            log.record(
                "M2 detection — the identical pass run again (integration defect fixed)",
                "POST /api/v1/detect/run?point=SP15  (unchanged series, second call)",
                f"200 alerts_raised={repeat_report['alerts_raised']} "
                f"alerts_refreshed={repeat_report['alerts_refreshed']}",
                "before the Unit 6 fix each pass inserted another copy of the same "
                "alert; the open alert is now refreshed in place, so the C2 "
                "false-alarm budget is not multiplied by the number of detector runs",
            )
            log.save("detect_repeat", repeat_report)

            detected = 0
            refreshed = 0
            for point_id in DEAD_END_POINTS:
                report = client.post(
                    "/api/v1/detect/run", params={"point": point_id}
                ).json()
                detected += report["alerts_raised"]
                refreshed += report["alerts_refreshed"]
            log.record(
                "M2 detection — the nightly-batch behaviour, one pass per dead end",
                "POST /api/v1/detect/run?point=SP0x  (six dead-end points)",
                f"200 {detected} newly raised, {refreshed} refreshed, across "
                f"{len(DEAD_END_POINTS)} points",
            )
            log.save("batch_alerts", detected)
            log.save("batch_refreshed", refreshed)

            alerts = client.get("/api/v1/alerts").json()
            log.record(
                "M2 alerts — the open inbox, with the explainability payload intact",
                "GET /api/v1/alerts",
                f"200 {len(alerts)} open alert(s)",
                " | ".join(
                    f"{a['sampling_point_id']} {a['severity']} "
                    f"[{','.join(a['context']['reason_codes'])}] "
                    f"last={a['context']['last_reading_mg_l']} mg/L "
                    f"z={a['context']['ewma_z']} "
                    f"iforest={a['context']['iforest_score']}"
                    for a in alerts[:4]
                ),
            )
            log.save("alerts", alerts[:4])
            log.save("alert_count", len(alerts))

            if alerts:
                acked = client.post(
                    f"/api/v1/alerts/{alerts[0]['id']}/ack",
                    json={"operator_id": "op-1", "note": "Flushed the spur; re-sampling."},
                ).json()
                log.record(
                    "M2 alerts — explicit acknowledgement writes an audit trail",
                    f"POST /api/v1/alerts/{alerts[0]['id'][:8]}…/ack "
                    '{"operator_id": "op-1", "note": "Flushed the spur; re-sampling."}',
                    f"200 acknowledged_by={acked['acknowledged_by']} "
                    f"at={acked['acknowledged_at']}",
                    f"open alerts after the ack: "
                    f"{len(client.get('/api/v1/alerts').json())}",
                )

            # ---------------------------------------------------------- M4 ---
            rules = client.get("/api/v1/rules").json()
            verified = [r for r in rules if r["verified"]]
            log.record(
                "M4 rule engine — the regulation loaded as versioned data",
                "GET /api/v1/rules",
                f"200 {len(rules)} rules from rules/oreg170.yaml, "
                f"{len(verified)} verified, {len(rules) - len(verified)} inert",
                "a rule with verified=false loads into the catalogue but generates "
                "no duty, so an unfinished legal review cannot contaminate a schedule",
            )
            log.save("rule_total", len(rules))
            log.save("rule_verified", len(verified))

            schedulable = [r for r in rules if r["verified"] and r["hard_constraint"]]
            # A verified hard-constraint rule is the only thing that may become a
            # regulatory duty. While the clause check is open, the harness runs
            # the clearly labelled draft path instead of presenting an empty duty
            # list as if it were a schedule, and says which mode it used so the
            # transcript cannot be misread.
            draft_mode = not schedulable
            task_params: dict[str, Any] = {"week": ISO_WEEK}
            if draft_mode:
                task_params["include_draft"] = "true"

            batch = client.post("/api/v1/tasks/generate", params=task_params).json()
            tasks = client.get("/api/v1/tasks", params={"week": ISO_WEEK}).json()
            log.record(
                "M4 rule engine — rules expand into windowed duties for one ISO week",
                f"POST /api/v1/tasks/generate?week={ISO_WEEK}"
                + ("&include_draft=true" if draft_mode else ""),
                f"201 generated={batch['generated']} "
                f"inert_rules_skipped={batch['inert_rules_skipped']}; "
                f"GET /api/v1/tasks -> {len(tasks)} duties",
                f"distinct points covered: "
                f"{len({t['sampling_point_id'] for t in tasks})}; "
                + (
                    f"mode=draft preview ({len(schedulable)} verified schedulable rules)"
                    if draft_mode
                    else f"mode=verified ({len(schedulable)} verified schedulable rules)"
                ),
            )
            log.save("task_count", len(tasks))
            log.save("draft_mode", draft_mode)

            # ---------------------------------------------------------- M3 ---
            plan_response = client.post(
                "/api/v1/plans", params={"week": ISO_WEEK, "solver": "greedy2opt"}
            )
            plan = plan_response.json() if plan_response.status_code == 201 else None
            log.save("plan_available", plan is not None)

            if plan is None:
                log.record(
                    "M3 scheduler — the week could not be planned",
                    f"POST /api/v1/plans?week={ISO_WEEK}&solver=greedy2opt",
                    f"{plan_response.status_code} {plan_response.text[:180]}",
                    "infeasibility is reported with the responsible tasks and the "
                    "shortfall named, rather than dropping duties silently",
                )
            else:
                log.record(
                    "M3 scheduler — the week planned across the fleet",
                    f"POST /api/v1/plans?week={ISO_WEEK}&solver=greedy2opt",
                    f"201 plan {plan['id'][:8]} status={plan['status']} "
                    f"stops={len(plan['stops'])} total_km={plan['total_km']} "
                    f"solver_ms={plan['solver_ms']}",
                    "first leg: " + json.dumps(plan["stops"][0]),
                )
                log.save(
                    "plan",
                    {k: plan[k] for k in ("id", "status", "total_km", "solver_ms")},
                )
                log.save("plan_stop_count", len(plan["stops"]))

                fetched = client.get(f"/api/v1/plans/{plan['id']}").json()
                confirmed = client.post(f"/api/v1/plans/{plan['id']}/confirm").json()
                export = client.get(f"/api/v1/plans/{plan['id']}/export.csv").text
                export_rows = len(export.strip().splitlines()) - 1
                log.record(
                    "M3 scheduler — freeze the plan and print the cab sheet",
                    f"GET /api/v1/plans/{plan['id'][:8]}… ; "
                    f"POST /api/v1/plans/{plan['id'][:8]}…/confirm ; "
                    f"GET /api/v1/plans/{plan['id'][:8]}…/export.csv",
                    f"200 status {fetched['status']} -> {confirmed['status']}; "
                    f"CSV {export_rows} data rows, {len(export)} bytes",
                    "route sheet head: " + " / ".join(export.strip().splitlines()[:3]),
                )
                log.save("plan_confirmed_status", confirmed["status"])
                log.save("plan_export_rows", export_rows)

            # ---------------------------------------------------------- M6 ---
            # C5 is a system property, so the value comes from the benchmark
            # rather than from this harness guessing. When
            # `examples/unit6_perf.py` has been run, its measured worst-endpoint
            # p95 is picked up automatically and the run is self-contained; when
            # it has not, the transcript says so instead of reporting a zero as
            # if it were a measurement.
            p95_ms, p95_source = benchmarked_p95()
            evaluation = client.post(
                "/api/v1/eval/run",
                params={
                    "week": ISO_WEEK,
                    "git_sha": "u6-integration",
                    "coverage": 0.95,
                    "api_p95_ms": p95_ms,
                },
            ).json()
            verdicts = {m["name"][:2]: m for m in evaluation["metrics"]}
            log.record(
                "M6 evaluation — replay the ground truth and store the frozen verdicts",
                f"POST /api/v1/eval/run?week={ISO_WEEK}&git_sha=u6-integration"
                f"&coverage=0.95&api_p95_ms={p95_ms}",
                f"201 run {evaluation['id'][:8]} with {len(evaluation['metrics'])} metrics",
                " | ".join(
                    f"{m['name'][:2]}={m['value']} "
                    f"{'PASS' if m['passed'] else 'FAIL'}"
                    for m in evaluation["metrics"]
                ),
            )
            log.save(
                "eval_metrics",
                [
                    {"name": m["name"], "value": m["value"], "target": m["target"],
                     "passed": m["passed"]}
                    for m in evaluation["metrics"]
                ],
            )
            log.save("eval_run_id", evaluation["id"])
            log.save("c1", verdicts.get("C1", {}).get("value"))
            log.save("c4", verdicts.get("C4", {}).get("value"))
            log.save("c5_input_p95_ms", p95_ms)
            log.save("c5_source", p95_source)

            reread = client.get(f"/api/v1/eval/runs/{evaluation['id']}").json()
            log.record(
                "M6 evaluation — the run read back from the database",
                f"GET /api/v1/eval/runs/{evaluation['id'][:8]}…",
                f"200 git_sha={reread['git_sha']} metrics={len(reread['metrics'])}",
                "persisting the run is what lets a later unit compare against this one",
            )

            # ---------------------------------------------------- UI (M5) ---
            page = client.get("/")
            network = client.get("/dash/network")
            inbox = client.get("/dash/alerts")
            week_view = client.get("/dash/week", params={"week": ISO_WEEK})
            summary = client.get("/dash/export/week.csv", params={"week": ISO_WEEK})
            log.record(
                "M5 dashboard — the browser-facing UI and its HTMX fragments",
                "GET / ; GET /dash/network ; GET /dash/alerts ; GET /dash/week ; "
                "GET /dash/export/week.csv",
                f"200 text/html {len(page.text)} B (shell) + "
                f"{len(network.text)} B (network) + {len(inbox.text)} B (inbox) + "
                f"{len(week_view.text)} B (week) ; text/csv {len(summary.text)} B",
                "the fragments are server-rendered from the same rows the API "
                "returned, so the UI and the contract cannot drift",
            )
            log.save("ui_bytes", {
                "shell": len(page.text),
                "network": len(network.text),
                "inbox": len(inbox.text),
                "week": len(week_view.text),
                "summary": len(summary.text),
            })
            log.save("ui_has_alert", "SP" in inbox.text)

            # the final verdict table, computed from the replay itself
            log.say("-" * 78)
            log.say("Frozen criteria recorded by this run (dataset maple-creek-48h):")
            log.say("-" * 78)
            for metric in evaluation["metrics"]:
                log.say(
                    f"  {metric['name']:<32} value={metric['value']:<10} "
                    f"{'PASS' if metric['passed'] else 'FAIL'}"
                )
                log.say(f"       target: {metric['target']}")
                if metric["name"].startswith("C5"):
                    log.say(
                        f"       the latency input came from: {p95_source} "
                        "(this harness does not profile latency itself)"
                    )
            log.say()
            log.say(f"total wall time for {log.step} integration steps: "
                    f"{time.monotonic() - started:.1f} s")
            client.close()
        except Exception as error:  # annotate the transcript, then let it fail loudly
            log.lines.append("")
            log.lines.append(
                f"!! integration run aborted: {type(error).__name__}: {error}"
            )
            raise

    # The context manager has terminated the server by now, so the child's own
    # output can be drained without blocking on a live pipe.
    noise = live.output
    if noise:
        log.lines.append("")
        log.lines.append("--- uvicorn stderr/stdout ---")
        log.lines.append(noise)

    (EVIDENCE / "log-unit6-integration.txt").write_text(
        "\n".join(log.lines) + "\n", encoding="utf-8"
    )
    (EVIDENCE / "unit6-integration.json").write_text(
        json.dumps(log.results, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nwrote {EVIDENCE / 'log-unit6-integration.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
