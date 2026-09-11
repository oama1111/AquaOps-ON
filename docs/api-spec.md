# AquaOps ON — REST API Specification (v1 draft)

Status: draft for Unit 3 design review · Last updated: 2026-09-11
Base URL: `/api/v1` · JSON everywhere · errors as RFC 9457 problem+json.
Performance target: p95 < 300 ms per endpoint on the Maple Creek dataset
(frozen metric, Unit 2). All list endpoints are paginated
(`?limit` ≤ 500, `?cursor`) and filterable by time range.

## 1. Registry (M1)

| Method & path | Purpose | Success | Key errors |
|---|---|---|---|
| `GET /systems/{id}` | System profile + network stats | 200 SystemOut | 404 |
| `GET /systems/{id}/sampling-points` | 18-point registry w/ location type | 200 [SamplingPointOut] | 404 |
| `POST /systems/{id}/ingest/lab-csv` | Multipart CSV upload of lab results | 202 IngestReceipt | 400 bad schema, 409 duplicate batch |
| `GET /ingest/{upload_id}` | Ingest status: rows accepted/rejected w/ row errors | 200 IngestStatus | 404 |

`IngestReceipt`: `{upload_id, rows_total, rows_accepted, rows_rejected}`
— acceptance is asynchronous; duplicates collapse on the
`(sampling_point_id, measured_at)` unique key, never error.

## 2. Chlorine readings & anomaly detection (M2)

| Method & path | Purpose | Success | Key errors |
|---|---|---|---|
| `GET /readings?point=SP07&from=…&to=…` | Time series for charts | 200 [ReadingOut] | 400 bad range |
| `POST /readings` | Single manual/API reading | 201 ReadingOut | 422 out of sanity range |
| `GET /alerts?status=open` | Alert inbox | 200 [AlertOut] | — |
| `POST /alerts/{id}/ack` | Acknowledge w/ operator note | 200 AlertOut | 404, 409 already acked |
| `POST /detect/run` | On-demand M2 pass over a point/range (for demos & M6) | 200 DetectReport | 400 |

`AlertOut.context` always carries the explainability payload:
`{model, ewma_z, iforest_score, last_reading_mg_l, threshold_mg_l,
reason_codes[]}` — the UI is required to render it verbatim.

## 3. Rules & required tasks (M4)

| Method & path | Purpose | Success | Key errors |
|---|---|---|---|
| `GET /rules?verified=true` | Rule catalogue loaded from YAML | 200 [RuleOut] | — |
| `POST /tasks/generate?week=2026-W38` | Expand rules → required tasks | 201 TaskBatch | 409 already generated |
| `GET /tasks?week=…&status=pending` | Duty list | 200 [TaskOut] | — |

Unverified rules (`verified: false` in YAML, pending issue #4 CanLII check)
are returned with `inert: true` and never generate tasks.

## 4. Scheduling (M3)

| Method & path | Purpose | Success | Key errors |
|---|---|---|---|
| `POST /plans?solver=greedy2opt` | Build draft plan for a week | 201 RoutePlanOut | 422 infeasible (details in body) |
| `GET /plans/{id}` | Plan with ordered stops, ETAs, km | 200 RoutePlanOut | 404 |
| `POST /plans/{id}/confirm` | Freeze plan (immutable afterwards) | 200 | 409 already confirmed |
| `GET /plans/{id}/export.csv` | Print-friendly route sheet | 200 text/csv | 404 |

`RoutePlanOut` includes `solver_ms` and `total_km` so the M6 harness can
assert the frozen targets (< 5 s solve, ≥ 15 % under naive-greedy mileage)
on every run.

## 5. Dashboard fragments (M5, HTMX)

Server-rendered partials, `text/html`, consumed by HTMX — not public API,
versioned with the app:

| Path | Fragment |
|---|---|
| `GET /dash/choropleth` | Network map with per-point status chips |
| `GET /dash/alerts` | Open-alert list (poll 30 s) |
| `GET /dash/week` | This week's plan vs. regulatory windows |
| `GET /dash/export/weekly.pdf` | Weekly compliance summary |

## 6. Evaluation (M6)

| Method & path | Purpose |
|---|---|
| `POST /eval/run?dataset=maple-creek-48h` | Replay ground truth through M2/M3, store EvalRun |
| `GET /eval/runs/{id}` | All frozen metrics, pass/fail vs. Unit 2 targets |

## 7. Conventions

- Auth: single-operator token (case scope); role model deferred post-capstone.
- Validation: Pydantic schemas in `app/schemas/`; 422 bodies list every
  failing field.
- Idempotency: `Idempotency-Key` header honoured on all POST ingest/plan
  endpoints.
- Versioning: path prefix; breaking changes ship `/api/v2`, never mutate v1.
