# AquaOps ON — System Architecture

Status: draft for Unit 3 design review · Last updated: 2026-09-11
Case system: fictional Township of Maple Creek, ON (pop. ~4,800, 18 sampling
points, 2 vehicles, 1.5 operator FTE) — see `data/networks/maple_creek.inp`.

## 1. Context

AquaOps ON is a decision-support web platform for operators of small Ontario
drinking-water systems. It combines three jobs that are today done on paper,
spreadsheets, and memory:

1. **Compliance scheduling** — turning O. Reg. 170/03 sampling duties into a
   feasible weekly route for a tiny field crew.
2. **Chlorine early warning** — flagging decaying free-chlorine residuals at
   least one sampling cycle before they become a reportable event.
3. **Evidence-keeping** — keeping an auditable trail of what was sampled,
   when, and what the system recommended.

It is explicitly **decision support, not automated compliance determination**:
every alert and schedule is advisory and traceable to a rule or model output.

## 2. Module map (M1–M6)

| ID | Module | Responsibility | Key tech |
|----|--------|----------------|----------|
| M1 | Ingestion & registry | Load network model, sampling-point registry, lab results, DWSP reference data | pandas, Pydantic |
| M2 | Chlorine anomaly detection | EWMA control chart + Isolation Forest ensemble per sampling point; explainable alert payload | scikit-learn, numpy |
| M3 | Route & schedule optimizer | Angular-sweep construction + 2-opt improvement over regulatory time windows; vehicle/crew constraints | Python, network topology |
| M4 | O. Reg. 170/03 rule engine | YAML-encoded sampling rules → required-task generator with effective-date versioning | YAML, Pydantic |
| M5 | Dashboard & reporting | Jinja2 + HTMX operator dashboard; weekly compliance summary export | FastAPI, Jinja2, HTMX |
| M6 | Evaluation harness | Frozen-metric evaluation runs (F1 vs baseline, lead time, mileage, runtime) and report generation | pytest, pandas |

Cross-cutting: FastAPI application core, SQLAlchemy persistence, APScheduler
nightly batch (ingest → detect → reschedule).

## 3. Container view

```
operator browser ──HTTP──> FastAPI app (Uvicorn)
                              ├─ M5 dashboard (Jinja2/HTMX)
                              ├─ REST API (/api/v1/...)
                              ├─ M4 rule engine ── reads ── rules/oreg170.yaml
                              ├─ M3 scheduler
                              ├─ M2 anomaly service
                              └─ SQLAlchemy ──> SQLite (dev) / PostgreSQL (prod profile)
nightly batch (APScheduler): ingest lab CSVs → M2 detect → M4 duty diff → M3 reschedule → alert outbox
offline: WNTR/EPANET simulation (examples/, data/networks/) feeds synthetic ground truth to M6
```

Single deployable unit (Docker container) — a deliberate choice: small-system
operators do not have IT staff; one `docker run` must be enough.

## 4. Key data flows

**F1 — Weekly planning.** M4 expands rules into required sampling tasks for
the horizon → M3 assigns tasks to days/routes under vehicle and shift
constraints → operator confirms in M5 → confirmed plan versioned in DB.

**F2 — Chlorine watch.** Lab/SCADA chlorine results land via CSV upload or
API → M2 updates per-point EWMA state and Isolation Forest score → alerts
with reason codes (`ewma_shift_up`, `iforest_outlier`, `trend_to_limit`)
appear on the dashboard within the nightly batch, or on demand.

**F3 — Evaluation.** M6 replays recorded/synthetic series (e.g. the 48 h
WNTR ground truth in `data/networks/maple_creek_chlorine_48h.csv`) through
M2 and M3 and computes the frozen metrics from the Unit 2 proposal.

## 5. Architectural decisions (ADR log, condensed)

- **ADR-1 Ensemble over single model (M2).** EWMA is interpretable and
  sensitive to sustained shifts; Isolation Forest catches point anomalies
  EWMA ignores. Union of alarms, each with a reason code, keeps alerts
  explainable — a hard requirement from the literature review.
- **ADR-2 Rules as YAML, not code (M4).** O. Reg. 170/03 amendments then
  become data changes with effective dates, not redeploys. Every rule entry
  carries `verified:` + CanLII reference; unverified rules are inert.
- **ADR-3 Constructive sweep + 2-opt, not metaheuristics (M3).** 18 points, 2
  vehicles — the instance is small; a transparent heuristic that runs in
  milliseconds beats a black-box solver for operator trust and debuggability.
  *Refined in Unit 4:* the construction was originally plain cheapest-insertion,
  which measured 11.34 km against the frozen C4 baseline while naive
  nearest-neighbour routing measured 11.61 km — the planner was barely ahead and
  the margin came from luck, not design. Seeding the construction with an
  angular sweep (cluster stops by bearing, route each sector by nearest
  neighbour, then 2-opt) measures 9.52 km, an 18 % saving. The frozen metric is
  what exposed the weakness.
- **ADR-4 Server-rendered HTMX, not SPA (M5).** Minimal operational surface:
  no build step, no frontend deploy, works on the town's old laptop.
- **ADR-5 SQLite-first persistence.** Single-file backups match how small
  municipalities actually handle data; SQLAlchemy keeps a PostgreSQL path
  open without code change.

## 6. Non-functional targets (frozen in Unit 2 — do not regress)

- Anomaly F1 strictly better than a fixed 0.05 mg/L threshold baseline
- ≥ 1 sampling cycle median warning lead time; ≤ 2 false alarms/week/point
- Regulatory window misses = 0 on the Maple Creek case
- Route mileage ≥ 15% below naive greedy baseline; solve < 5 s for 18 points
- API p95 < 300 ms on the case dataset; test coverage ≥ 70%; SUS ≥ 68

## 7. Repository map

```
aquaops-on/
├── app/            # FastAPI core + M5 routes          (Unit 4+)
├── modules/        # m1_ingest … m6_eval               (Units 4–6)
├── rules/          # oreg170.yaml (TODO: CanLII verification, issue #4)
├── data/networks/  # maple_creek.inp + registries (committed)
├── examples/       # build/validate scripts for case data
├── docs/           # this file, data-model.md, api-spec.md, evidence/
└── tests/          # pytest suite (Unit 4+)
```
