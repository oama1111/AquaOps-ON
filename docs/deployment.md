# AquaOps ON — deployment and configuration

This document is the deployment plan and the operator-facing setup record for
Unit 6. It states the deployment options that were considered, the one that was
chosen and why, what configuration must be supplied, and the exact commands that
build and run the system.

Nothing here is aspirational: every command below was executed, and the measured
results are in `Unit6/evidence/`.

---

## 1. Deployment options considered

| Option | How it would work here | Why it was or was not chosen |
| --- | --- | --- |
| **Single-host container** (chosen) | One `docker compose up` on a municipal server or a small cloud VM; the SQLite database on a named volume; TLS terminated by a reverse proxy in front | Fits a 1.5-FTE operation with no IT staff, keeps the compliance record on hardware the municipality controls, one image to version, and the whole system is one artefact to back up. Blue-green and canary strategies described by AWS (2021) and Sato (2014) assume a fleet and a load balancer that make a release reversible; with one server and one operator, a health-probed restart plus a verified backup is the credible version of the same idea |
| **Platform as a service** (documented alternative) | Push the image to a managed container host; the platform supplies TLS, the domain and the health checks | Lowest operational burden and it is how the Unit 2 proposal expected to demonstrate a public URL. Not chosen as the primary target because the compliance record would sit in a third party's storage, and because a managed free tier sleeps, which is unacceptable for a tool an operator opens at 6 a.m. |
| **On-premise virtual machine without containers** | `pip install -r requirements-runtime.txt` in a virtualenv, `uvicorn` under `systemd` | Viable, and the fallback if the municipality forbids containers. Rejected as the default because it makes the operating system part of the release: the same code behaves differently on the host's Python and library set, which is the problem Boettiger (2015) documents |
| **Kubernetes** | Deploy the same image with a Deployment and a rolling-update strategy | Rejected outright. A rolling update (Kubernetes Authors, n.d.) needs at least two replicas and SQLite permits one writer; the operational surface of a cluster is far larger than the system it would run |
| **Shadow and canary releases** | Mirror production traffic to a second instance before promoting | Not applicable at this scale. They are the right tools when a release can be compared against live traffic; a single-town deployment has no live traffic to shadow |

### The selected approach

**A single Docker image, run with Docker Compose, serving one municipal system,
with PostgreSQL as the documented scale-out path.** The reasons, in the order
they mattered:

1. **One artefact.** `app/`, `modules/`, `rules/` and `data/networks/` are copied
   into one image. The YAML rule catalogue travels with the code that reads it,
   so a rule amendment and the engine that interprets it can never be deployed
   apart — the failure mode the Unit 3 review called out as the reason to keep
   regulation as versioned data rather than as a separate service.
2. **A reversible release.** The image is tagged (`aquaops-on:u6`), the health
   probe is declared *in the image*, and the database lives on a volume outside
   it. Rolling back is `docker run` with the previous tag and the same volume,
   which is the practical single-host equivalent of the blue-green idea.
3. **The data stays where the accountability is.** The compliance record is a
   file the municipality can copy, inspect and hand to an inspector.
4. **The runtime image is smaller and simpler than the development environment.**
   It installs `requirements-runtime.txt`, which excludes `wntr` and `pandas`.
   The Unit 6 benchmark measured why that matters: importing the application
   costs about **76 MiB** of peak resident memory, and importing the offline
   simulation stack as well costs about **190 MiB**. The simulation stack is only
   needed to *build* the case data, which is committed, so it is not in the
   serving image. `tests/test_runtime_dependencies.py` enforces that separation
   by importing `app.main` in a subprocess with `pandas`, `wntr` and `matplotlib`
   blocked.

---

## 2. System prerequisites

### To run the deployed image

| Requirement | Minimum | Notes |
| --- | --- | --- |
| Docker Engine | 20.10+ | `docker compose` v2 syntax is used |
| Docker Compose | v2 | Bundled with modern Docker Desktop and Engine |
| CPU | 1 core | The solver ran 36 duties in 1 ms; the benchmark peaked at 1,130 req/s |
| Memory | 512 MiB | Measured peak resident set was about 200 MiB under load; the compose file caps the container at 512 MiB |
| Disk | 1 GiB + data | The image is small; the SQLite database grows by roughly 100 bytes per reading |
| Network | Loopback only | The dashboard loads no external assets: htmx is vendored in `app/static/`. Egress is not required at run time |
| TLS termination | Required off-host | Put nginx, Caddy or the municipality's existing proxy in front; the container serves plain HTTP on 8000 |

### To run from source (development)

| Requirement | Version | Notes |
| --- | --- | --- |
| Operating system | macOS, Linux, or Windows with WSL2 | Developed and verified on macOS 15 (Darwin 24) |
| Python | 3.12 (3.11+ works) | `python3.12 --version` was `3.12.14` for every measurement in this report |
| pip / venv | Bundled with Python | No Poetry or PDM dependency |
| Git | Any recent version | Only needed to clone and to read the evidence history |
| Google Chrome | Any recent version | **Optional.** Only `examples/unit6_ui.py` uses it, to render dashboard screenshots; set `AQUAOPS_CHROME` to another Chromium binary if Chrome is elsewhere |
| Network | Only for the first `pip install` | After that, every script is offline |

### Python dependencies

Two files, deliberately:

- `requirements-runtime.txt` — what the deployment image installs. Includes
  `scikit-learn` (M2's Isolation Forest) and `psycopg2-binary` (the PostgreSQL
  profile).
- `requirements.txt` — the full development set, adding `pandas` and `wntr`
  (build the case network), `pytest`, `pytest-cov`, `httpx`, `ruff` and `mypy`.

---

## 3. Environment variables

All configuration is environment-supplied; the image bakes in defaults that work
for one town and nothing that differs between installations. This is the
twelve-factor config rule (Wiggins, 2017), and the reason it matters here is
concrete: on 19 September 2026 the rule catalogue was corrected because the case
system turned out to be in a different population band than assumed. If the
frequency had been compiled into the code, that correction would have been a
release; because it is data in `rules/oreg170.yaml`, it was a reviewed edit.

| Variable | Default in the image | Purpose | When to change it |
| --- | --- | --- | --- |
| `AQUAOPS_DB_URL` | `sqlite:////data/aquaops.db` | SQLAlchemy connection string. The only thing that differs between the SQLite and PostgreSQL profiles | Set to `postgresql+psycopg2://…` for multi-town, or to a different file to run two towns side by side |
| `AQUAOPS_EVIDENCE_DIR` | `/data/evidence` | Where M6 evaluation reports are written | Point at a backed-up path if evaluation reports are part of the compliance record |
| `AQUAOPS_PORT` | `8000` | Port uvicorn binds inside the container | Only if 8000 collides with something on the host network |
| `AQUAOPS_WORKERS` | `1` | uvicorn worker processes | **Only after moving to PostgreSQL.** SQLite permits a single writer, so extra workers serialise and buy nothing |
| `AQUAOPS_LOG_LEVEL` | `info` | uvicorn log level | `warning` reduces log volume on a small disk; `debug` while diagnosing |
| `AQUAOPS_CHROME` | unset | Path to a Chromium binary for `examples/unit6_ui.py` | Non-macOS machines, or Chrome installed elsewhere |
| `MPLCONFIGDIR` | `/tmp/mplcache` | Matplotlib's font cache | Must point somewhere writable. A read-only `HOME` makes the first `import wntr` rebuild the font cache and the process appears to hang — a defect found in Unit 5 and carried into this image as a default |
| `AQUAOPS_AUTH_TOKEN` | unset | Single-operator API token | **Required before serving anything off-host.** Unset means the API is open, which is acceptable only on loopback |

`AQUAOPS_DB_PASSWORD` is referenced by the commented PostgreSQL block in
`docker-compose.yml` and must be supplied from a secret store, never committed.

---

## 4. Install, build, and run

### 4.1 Deploy with Docker (the selected approach)

```bash
git clone https://github.com/oama1111/AquaOps-ON.git
cd AquaOps-ON
git checkout u6                      # the evaluated milestone

docker compose build                 # build the image from the Dockerfile
docker compose up -d                 # start it detached
docker compose ps                    # wait for "healthy"
```

Verify the deployment:

```bash
curl -fsS http://127.0.0.1:8000/health
# {"status":"ok","version":"0.4.0"}

curl -fsS http://127.0.0.1:8000/api/v1/systems/maple-creek
# the case system is seeded during application startup, so a fresh clone is
# immediately demonstrable

xdg-open http://127.0.0.1:8000/       # the operator dashboard
xdg-open http://127.0.0.1:8000/docs   # the generated API documentation
```

Operate it:

```bash
docker compose logs -f aquaops        # follow the log
docker compose restart aquaops        # restart after a configuration change
docker compose down                   # stop; the named volume keeps the data
```

Back up and restore the compliance record — the one procedure an operator must
be able to perform:

```bash
docker run --rm -v aquaops-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/aquaops-$(date +%F).tar.gz -C /data .
```

### 4.2 Run from source

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt      # full development set

# Build the case data offline (needs wntr and pandas)
./.venv/bin/python examples/build_maple_creek.py
./.venv/bin/python examples/validate_maple_creek.py

# Serve
MPLCONFIGDIR=/tmp/mplcache ./.venv/bin/uvicorn app.main:app --reload
```

### 4.3 Build checks that CI runs

```bash
./.venv/bin/ruff check .
./.venv/bin/mypy app modules --ignore-missing-imports
MPLCONFIGDIR=/tmp/mplcache ./.venv/bin/python -m pytest \
  --cov=app --cov=modules --cov-report=term-missing --cov-fail-under=70
```

### 4.4 Reproduce the Unit 6 evidence

Three scripts produce everything quoted in the report. Each one is hermetic: it
boots the deployable unit on a loopback port against a throwaway SQLite file, so
the numbers are reproducible from a clean clone and cannot be an artefact of
leftover state.

```bash
./.venv/bin/python examples/unit6_integration.py   # end-to-end feature evidence
./.venv/bin/python examples/unit6_perf.py          # latency, throughput, memory, C1-C6
./.venv/bin/python examples/unit6_ui.py            # heuristic review + screenshots
```

Outputs land in `Unit6/evidence/` (transcripts, JSON measurements, screenshots).

---

## 5. How configuration management contributes to reliability

Three mechanisms, each with a failure it prevents.

**The rule catalogue is configuration, not code, and it is inert until
verified.** `rules/oreg170.yaml` carries a `verified` flag. A rule with
`verified: false` loads into the catalogue, appears in the API, and generates no
duty. This is why the Unit 6 clause-by-clause verification could change what the
system schedules without touching a line of Python — and why the discovery that
the case system sits in the *large* band (Schedule 10 and Schedule 7 s. 7-2(3),
not the small-band clauses the draft assumed) was a data fix. Had the
frequencies been hard-coded, the same discovery would have been a release with
its own regression risk. Configuration errors are a leading cause of outages
precisely because they are changes that bypass the code review path (Yin et al.,
2011; Xu & Zhou, 2015); keeping the regulation in versioned data puts it back on
that path.

**The environment, not the image, says where the data lives and how many workers
run.** `AQUAOPS_DB_URL` and `AQUAOPS_WORKERS` are the two settings that must
change together: the image defaults to one worker because SQLite permits one
writer, and raising the worker count without moving to PostgreSQL would produce
intermittent `database is locked` errors under exactly the load — several
fragments refreshing at once — that the dashboard generates. Making both values
explicit and adjacent in `docker-compose.yml` turns a subtle scaling mistake into
a visible one.

**The health probe is declared in the image and asserted by the test suite.**
`HEALTHCHECK` calls the same `GET /health` that
`examples/unit6_integration.py` asserts as its first step, so "healthy" means the
same thing to the orchestrator, to the benchmark and to a reviewer. A deployment
that reports healthy is therefore one whose lifespan startup — create tables,
seed the case system, sync the rule catalogue — actually completed.

Two smaller practices support the same goal. The vendored `htmx.min.js` is
committed with its pinned version and SHA-256 in `app/static/README.md`, so the
served page references no external origin and the asset cannot change silently.
And the case network plus the 48-hour ground truth are committed under
`data/networks/`, which is why `docker compose up` on a fresh clone produces a
demonstrable system with no data download step.

---

## 6. What is deliberately not in the image

| Excluded | Reason |
| --- | --- |
| `wntr`, `pandas`, `matplotlib` | Only used to build the case data offline; measured at roughly 114 MiB of avoidable resident memory |
| `tests/`, `examples/` | Development artefacts; `examples/` would re-introduce the simulation stack |
| `data/raw/` (DWSP, BattLeDIM) | Hundreds of megabytes of public source data; the derived, committed case data is what the application reads |
| The developer's `*.db` | Deployment state, not an image input |
| Any credential | `AQUAOPS_AUTH_TOKEN` and `AQUAOPS_DB_PASSWORD` come from the deployment's secret store |

---

## 7. Known limitations of this plan

Recorded here rather than omitted, because a reader deploying this should know
them:

1. **The image was not built in the author's environment.** Docker is not
   installed on the development machine and the container registry was not
   reachable, so the `Dockerfile` and `docker-compose.yml` are reviewed artefacts
   rather than a run image. What *was* executed is the same application run in
   production mode on loopback, the runtime-dependency guard, and the health
   probe the image declares. Building the image and running the compose profile
   on a machine with Docker is the first item of remaining work.
2. **There is no authentication in the running configuration.** The Unit 3
   specification defers the role model, and `AQUAOPS_AUTH_TOKEN` is declared but
   not yet enforced by a middleware. Until it is, the deployment must stay bound
   to loopback or sit behind a proxy that authenticates.
3. **SQLite means one writer.** This is a deliberate single-town trade, not an
   oversight, and PostgreSQL is the documented exit.
4. **There is no alerting on the container itself.** Docker's own log rotation is
   configured, but nothing pages anyone if the health check starts failing. For a
   system whose whole purpose is to warn, that is the most important gap on this
   list.
