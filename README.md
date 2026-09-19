# AquaOps ON

[![CI](https://github.com/oama1111/AquaOps-ON/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/oama1111/AquaOps-ON/actions/workflows/ci.yml)

A compliance-scheduling and water-quality early-warning platform for operators of
small Ontario drinking water systems (fewer than 10,000 people served).

Small systems are typically run by one or two certified operators who must satisfy
the sampling and monitoring duties of **O. Reg. 170/03** while also doing all other
field work. Sampling windows get missed, and decaying free-chlorine residuals are
often discovered only when laboratory results return days later. AquaOps ON turns
regulatory obligations and chlorine trends into an executable daily work plan.

> **Decision support only.** Every output is advisory, explainable, and subordinate
> to licensed operator judgment. The platform does not determine compliance and is
> not legal proof of compliance.

## Status

| Stage | State |
| --- | --- |
| Unit 1 — literature survey and problem definition | complete |
| Unit 2 — proposal, scope, frozen success criteria | complete |
| Unit 3 — architecture, data model, API spec, evaluation baseline | complete |
| Unit 4 — implemented vertical slice, dashboard, CI pipeline | complete |
| Unit 5 — unit testing of the critical modules, 95 % coverage | complete |
| Units 6–8 — deployment, system testing, evaluation report, final demo | in progress |

All six modules in `modules/` are implemented and exercised by the test suite:
M1 ingestion and registry, M2 the EWMA ∪ Isolation Forest detector, M3 the
sweep + 2-opt planner, M4 the O. Reg. 170/03 rule engine, M5 the dashboard, and
M6 the evaluation harness. `pytest` reports **87 passed** and **95 % statement
coverage** against the 70 % floor that criterion C6 sets. Two unit-test
categories are declared in `pytest.ini` and can be run separately:

```bash
pytest -m unit_whitebox   # 21 tests against named internal helpers and branches
pytest -m unit_blackbox   # 18 tests against the public module contracts
```

The FastAPI core in `app/main.py` serves `GET /health` and the `/api/v1` contract.

## Repository layout

| Path | Contents |
| --- | --- |
| `app/` | FastAPI application core and the `/api/v1` Pydantic contract (`app/schemas/`) |
| `modules/` | M1–M6: ingestion, anomaly detection, scheduler, rule engine, dashboard, evaluation harness |
| `tests/` | pytest suite, populated alongside each module |
| `docs/` | living design documents: `architecture.md`, `data-model.md`, `api-spec.md` |
| `docs/diagrams/` | architecture and entity-relationship diagrams (script-generated) |
| `docs/evidence/` | dated, reproducible evidence: simulation logs, validation runs, smoke tests |
| `data/networks/` | committed EPANET case network, sampling-point registry, 48 h chlorine ground truth |
| `rules/` | `oreg170.yaml` — regulatory sampling rules as versioned data |
| `examples/` | reproducible scripts: build the case network, validate it, render the diagrams |

Large raw datasets are deliberately not committed; see `data/README.md` for
provenance and download instructions.

## Branching model

| Branch | Role |
| --- | --- |
| `main` | Last reviewed design milestone. Only receives reviewed pull requests. |
| `development` | Long-lived integration branch for ongoing work. |
| `feature/*` | Short-lived branches for one unit of work, merged by pull request. |

Pull requests are the review record: [#6](https://github.com/oama1111/AquaOps-ON/pull/6)
merged the Unit 2–3 design documents into `main`;
[#7](https://github.com/oama1111/AquaOps-ON/pull/7) merged the implementation
baseline into `development`. Commit messages follow a `type(scope):` convention
(`docs(u3):`, `feat(app):`, `data:`, `chore:`) and state both what changed and
why, so the history reads as a project narrative. Course milestones are
snapshotted with git tags `u1` … `u8`; evaluation runs record the git SHA they
were produced from.

## Continuous integration

Every push and pull request runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml),
split into two jobs by feedback speed:

| Job | What it does | Why it is separate |
| --- | --- | --- |
| `quality` | `ruff` lint, `mypy` type check, and `pytest` with a **70% coverage floor** | Fast enough to run on every commit; needs no simulation stack |
| `case-data` | Rebuilds the Maple Creek network and re-runs the plausibility validator, which exits non-zero on violation, then uploads the log as a build artefact | Slow, so it must not gate the fast feedback loop |

The coverage floor is not arbitrary: **C6** in the project's frozen success criteria
is "test coverage ≥ 70% and a reproducible deployment", so the pipeline turns that
criterion into a gate that a machine enforces rather than an intention a developer
remembers.

## Reproducing the case data

The case system is a fictional but parameter-realistic Ontario town — Township of
Maple Creek, population 4,800, 25 nodes, 18 sampling points (6 of them on dead-end spurs),
two vehicles, 1.5 operator FTE.

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# Build the network and export 48 h of chlorine ground truth
./.venv/bin/python examples/build_maple_creek.py

# Validate it against physical plausibility bands (exits non-zero on violation)
./.venv/bin/python examples/validate_maple_creek.py

# Re-render docs/diagrams/architecture.png and erd.png
./.venv/bin/python examples/render_design_diagrams.py
```

Ground truth is produced offline with WNTR/EPANET simulation. This is a design
decision, not a convenience: connecting a student project to live SCADA/PLC
infrastructure would widen the attack surface of critical infrastructure for no
benefit a one-way file import cannot provide.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — context, module map, container view, ADR log
- [`docs/data-model.md`](docs/data-model.md) — 13 entities, integrity rules, case-data mapping
- [`docs/api-spec.md`](docs/api-spec.md) — `/api/v1` endpoint contract
- [`docs/README.md`](docs/README.md) — index of design documents and evidence

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).
