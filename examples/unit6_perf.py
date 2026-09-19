"""Unit 6 performance benchmark: measure the assembled system, do not estimate it.

This script produces the quantitative half of the Unit 6 evaluation. It boots the
same deployable unit the integration harness uses, seeds it with the same data,
and then measures four things against the frozen targets:

* **API latency** — p50, p95 and p99 per endpoint, because C5 is stated as a p95
  and because the mean hides exactly the tail an operator experiences (Ewaschuk,
  2017; the four golden signals). The 300 ms C5 ceiling is drawn on the results.
* **Throughput under concurrency** — achieved requests per second and the p95 at
  each concurrency level, which is the only honest way to report throughput: a
  number without the latency it was bought at is not a capacity figure.
* **Memory footprint** — resident set size of the server process, the constraint
  that matters most on the modest municipal hardware the design targets.
* **The C1-C6 verdicts** — a final M6 evaluation run with the *measured* p95
  passed in, so C5 is evaluated from a real measurement rather than assumed.

Every figure is written to a JSON file next to the transcript so the report, the
charts and the log cannot disagree.

    python examples/unit6_perf.py
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.unit6_harness import (  # noqa: E402
    BENCHMARK_ENDPOINTS,
    EVIDENCE,
    ISO_WEEK,
    git_sha,
    interpreter_rss_kb,
    live_server,
    seed_case,
    wait_for_health,
)

#: Warm-up requests per endpoint. The first request to each route pays for
#: SQLAlchemy mapper configuration and Pydantic model construction; measuring it
#: would report start-up cost as steady-state latency.
WARMUP = 15

#: Timed requests per endpoint for the single-client latency profile.
SAMPLES = 150

#: The latency profile is repeated and the median round is reported. A single
#: round on a shared, unisolated developer machine is not reproducible: the first
#: Unit 6 run reported a worst-endpoint p95 of 33 ms and the second reported
#: 17 ms on the same code, purely from machine load.
ROUNDS = 3

#: The concurrency levels in the throughput sweep, and how long each runs.
CONCURRENCY_LEVELS = (1, 2, 4, 8, 16)
THROUGHPUT_WINDOW_S = 3.0

#: C5, frozen in the Unit 2 proposal.
P95_CEILING_MS = 300.0

#: C6's coverage floor, frozen in the Unit 2 proposal.
COVERAGE_FLOOR_PERCENT = 70.0


def measure_coverage() -> dict[str, Any]:
    """Run the suite and read the real statement coverage out of pytest-cov.

    C6 is a number that must be measured, not supplied. The first version of this
    script passed a literal coverage fraction to the evaluation endpoint, which is
    exactly the kind of input that silently stops being true: the report would
    have claimed one figure while the suite printed another. Running the same
    command CI runs, and parsing the TOTAL row, makes the verdict a measurement.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--cov=app",
            "--cov=modules",
            "--cov-report=term",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
        env={
            **os.environ,
            "MPLCONFIGDIR": os.environ.get("MPLCONFIGDIR", "/tmp/mplcache"),
        },
    )
    total = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", completed.stdout, flags=re.MULTILINE)
    passed = re.search(r"(\d+) passed", completed.stdout)
    if total is None:
        return {"fraction": 0.0, "percent": 0.0, "tests": 0, "measured": False}
    percent = float(total.group(1))
    return {
        "fraction": round(percent / 100.0, 4),
        "percent": percent,
        "tests": int(passed.group(1)) if passed else 0,
        "measured": True,
    }


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; no interpolation, so no value is invented."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def summarise(samples_ms: list[float]) -> dict[str, float]:
    return {
        "n": len(samples_ms),
        "mean_ms": round(statistics.fmean(samples_ms), 3) if samples_ms else 0.0,
        "p50_ms": round(percentile(samples_ms, 0.50), 3),
        "p95_ms": round(percentile(samples_ms, 0.95), 3),
        "p99_ms": round(percentile(samples_ms, 0.99), 3),
        "max_ms": round(max(samples_ms), 3) if samples_ms else 0.0,
    }


def time_request(client: httpx.Client, method: str, url: str) -> float:
    started = time.perf_counter()
    response = client.request(method, url)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if response.status_code >= 500:
        raise SystemExit(f"{url} returned {response.status_code}")
    return elapsed_ms


def seed(client: httpx.Client) -> dict[str, Any]:
    """Put the case data in place so the benchmark measures work, not emptiness.

    Kept as a thin alias for the shared seeding routine so the benchmark and the
    usability study can never measure differently seeded systems.
    """
    return seed_case(client)


def throughput_sweep(
    base_url: str, endpoint: str
) -> list[dict[str, Any]]:
    """Measure achieved throughput and its latency cost at each concurrency level."""
    results: list[dict[str, Any]] = []
    for level in CONCURRENCY_LEVELS:
        latencies: list[float] = []

        def worker() -> int:
            # Each thread owns a client, so connection setup cannot serialise
            # the measurement on a shared pool.
            local = httpx.Client(base_url=base_url, timeout=60.0)
            count = 0
            try:
                deadline = time.perf_counter() + THROUGHPUT_WINDOW_S
                while time.perf_counter() < deadline:
                    latencies.append(time_request(local, "GET", endpoint))
                    count += 1
            finally:
                local.close()
            return count

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=level) as pool:
            counts = list(pool.map(lambda _: worker(), range(level)))
        elapsed = time.perf_counter() - started
        completed = sum(counts)
        results.append(
            {
                "concurrency": level,
                "requests": completed,
                "window_s": round(elapsed, 3),
                "throughput_rps": round(completed / elapsed, 1),
                **summarise(latencies),
            }
        )
    return results


def rule_catalogue_timing(iterations: int = 200) -> dict[str, float]:
    """Time the rule catalogue with and without its cache, best of N.

    Best-of rather than mean on purpose: this measures how long the work takes,
    and the smallest sample is the one least contaminated by whatever else the
    machine was doing.
    """
    from modules import m4_rules

    def timed(call: Any) -> float:
        started = time.perf_counter()
        call()
        return (time.perf_counter() - started) * 1000.0

    m4_rules.load_rules()  # warm the module
    cached = min(timed(m4_rules.load_rules) for _ in range(iterations))

    def parse_from_scratch() -> None:
        m4_rules._cached_rules.cache_clear()
        m4_rules.load_rules()

    uncached = min(timed(parse_from_scratch) for _ in range(iterations))
    m4_rules.load_rules()  # leave the cache warm for anything that follows
    return {
        "cached_ms": round(cached, 4),
        "uncached_ms": round(uncached, 4),
        "speedup": round(uncached / cached, 1) if cached > 0 else 0.0,
    }


def update_p95_history(value: float, limit: int = 20) -> list[dict[str, Any]]:
    """Append this run's conservative p95 to a rolling history file."""
    path = EVIDENCE / "unit6-perf-history.json"
    history: list[dict[str, Any]] = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            history = loaded if isinstance(loaded, list) else []
        except (OSError, ValueError):
            history = []
    history.append(
        {
            "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "git_sha": git_sha(),
            "conservative_p95_ms": round(value, 3),
        }
    )
    path.write_text(json.dumps(history[-limit:], indent=2), encoding="utf-8")
    return history[-limit:]


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    payload: dict[str, Any] = {}

    def say(text: str = "") -> None:
        lines.append(text)
        print(text)

    sha = git_sha()
    say("=" * 78)
    say("AquaOps ON — Unit 6 quantitative performance evaluation")
    say(f"commit {sha} · single uvicorn worker · SQLite · loopback HTTP")
    say("=" * 78)
    say()

    with live_server() as live:
        health, boot_ms = wait_for_health(live.base_url, live.process)
        say(f"cold start: /health ready in {boot_ms} ms (version {health['version']})")
        say("(memory is reported after the run, from the reaped child's peak RSS)")
        say()
        payload["git_sha"] = sha
        payload["boot_ms"] = boot_ms

        client = httpx.Client(base_url=live.base_url, timeout=60.0)
        facts = seed(client)
        say("dataset in place before measuring:")
        for key, value in facts.items():
            say(f"  {key}: {value}")
        say()
        payload["dataset"] = facts

        # ------------------------------------------------ single-client latency
        say("-" * 78)
        say(
            f"Latency, single client: {WARMUP} warm-up + {SAMPLES} timed requests "
            f"each, repeated over {ROUNDS} rounds"
        )
        say("the reported figure is the median across rounds, not a lucky round")
        say("-" * 78)
        rounds: list[dict[str, dict[str, float]]] = []
        for round_index in range(ROUNDS):
            measured: dict[str, dict[str, float]] = {}
            for method, url in BENCHMARK_ENDPOINTS:
                if round_index == 0:
                    for _ in range(WARMUP):
                        time_request(client, method, url)
                samples = [time_request(client, method, url) for _ in range(SAMPLES)]
                measured[f"{method} {url}"] = summarise(samples)
            rounds.append(measured)
            worst_this_round = max(s["p95_ms"] for s in measured.values())
            say(f"  round {round_index + 1}: worst endpoint p95 = {worst_this_round:.2f} ms")

        statistic_names = ("n", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms")
        per_endpoint: dict[str, dict[str, float]] = {
            key: {
                name: round(
                    statistics.median([r[key][name] for r in rounds]), 3
                )
                for name in statistic_names
            }
            for key in rounds[0]
        }
        say()
        for key, stats in per_endpoint.items():
            url = key.split(" ", 1)[1]
            verdict = "PASS" if stats["p95_ms"] < P95_CEILING_MS else "FAIL"
            say(
                f"  {url:<52} p50={stats['p50_ms']:>7.2f}  "
                f"p95={stats['p95_ms']:>7.2f}  p99={stats['p99_ms']:>7.2f}  {verdict}"
            )
        payload["latency_by_endpoint"] = per_endpoint
        payload["latency_rounds"] = rounds

        all_samples = [
            value
            for stats in per_endpoint.values()
            for value in [stats["p50_ms"], stats["p95_ms"], stats["p99_ms"]]
        ]
        payload["all_endpoint_percentiles_ms"] = sorted(all_samples)
        worst_p95 = max(stats["p95_ms"] for stats in per_endpoint.values())
        slowest = max(per_endpoint.items(), key=lambda item: item[1]["p95_ms"])
        aggregate = summarise(
            [stats["p50_ms"] for stats in per_endpoint.values()]
        )

        # The pass/fail claim uses the worst p95 seen in ANY round, not the
        # median. On a shared, unisolated machine the same code has produced a
        # worst-endpoint p95 anywhere between 3 ms and 34 ms depending on what
        # else was running, so the median round is a fair description and the
        # worst round is the honest basis for saying "inside the budget". If the
        # worst case clears 300 ms by an order of magnitude, the claim does not
        # depend on the machine having been quiet.
        conservative_p95 = max(
            stats["p95_ms"] for round_stats in rounds for stats in round_stats.values()
        )
        round_worsts = [
            max(stats["p95_ms"] for stats in round_stats.values()) for round_stats in rounds
        ]
        say()
        say(
            f"  worst endpoint p95, median round = {worst_p95:.2f} ms "
            f"(worst round {conservative_p95:.2f} ms)"
        )
        say("  per-round worst p95: "
            + ", ".join(f"{value:.2f}" for value in round_worsts)
            + " ms")
        say(
            f"  C5 is evaluated at the conservative {conservative_p95:.2f} ms "
            f"against the {P95_CEILING_MS:.0f} ms ceiling"
        )
        say(f"  slowest route: {slowest[0].split(' ', 1)[1]}")
        payload["worst_p95_ms"] = worst_p95
        payload["conservative_p95_ms"] = conservative_p95
        payload["round_worst_p95_ms"] = round_worsts
        payload["slowest_endpoint"] = slowest[0]
        payload["median_endpoint_p50_ms"] = aggregate["p50_ms"]

        # ------------------------------------------------------- throughput
        hot_endpoint = "/api/v1/readings?point=SP15&limit=500"
        say("-" * 78)
        say(f"Throughput sweep against GET {hot_endpoint}")
        say(f"each level runs for a {THROUGHPUT_WINDOW_S:.0f} s window")
        say("-" * 78)
        sweep = throughput_sweep(live.base_url, hot_endpoint)
        for row in sweep:
            say(
                f"  concurrency {row['concurrency']:>2}  "
                f"{row['throughput_rps']:>8.1f} req/s  "
                f"p50={row['p50_ms']:>7.2f}  p95={row['p95_ms']:>7.2f}  "
                f"n={row['requests']}"
            )
        payload["throughput_sweep"] = sweep
        peak = max(row["throughput_rps"] for row in sweep)
        say()
        say(f"  peak observed throughput = {peak:.1f} req/s")
        payload["peak_throughput_rps"] = peak
        say()

        # ------------------------------------------------- C6, measured
        coverage = measure_coverage()
        payload["coverage"] = coverage
        say("-" * 78)
        say("C6 engineering quality, measured by running the suite CI runs")
        say("-" * 78)
        if coverage["measured"]:
            say(f"  {coverage['tests']} tests passing at {coverage['percent']:.0f} % "
                f"statement coverage against the {COVERAGE_FLOOR_PERCENT:.0f} % floor")
        else:
            say("  pytest-cov output could not be parsed; C6 will report a failure")
        say()

        # ------------------------------------------------------- M6 verdicts
        evaluation = client.post(
            "/api/v1/eval/run",
            params={
                "week": ISO_WEEK,
                "git_sha": sha,
                "coverage": coverage["fraction"],
                "api_p95_ms": conservative_p95,
            },
        ).json()
        say("-" * 78)
        say("Frozen criteria C1-C6, with C5 measured by this run rather than assumed")
        say("-" * 78)
        for metric in evaluation["metrics"]:
            say(
                f"  {metric['name']:<32} value={metric['value']:<12} "
                f"{'PASS' if metric['passed'] else 'FAIL'}"
            )
            say(f"       target: {metric['target']}")
        payload["eval_metrics"] = evaluation["metrics"]
        payload["eval_run_id"] = evaluation["id"]

        client.close()

    # The server has been reaped by now, so its peak resident size is available.
    service_peak = live.peak_rss_kb()
    layers = {
        "bare interpreter": interpreter_rss_kb(),
        "interpreter + app.main (the deployable unit)": interpreter_rss_kb("app.main"),
        "interpreter + offline simulation stack (wntr, pandas, sklearn)": interpreter_rss_kb(
            "wntr, pandas, sklearn"
        ),
    }
    payload["memory"] = {
        "service_peak_rss_kb": service_peak,
        "layer_peak_rss_kb": layers,
    }

    say()
    say("-" * 78)
    say("Memory footprint (peak resident set size, KiB)")
    say("-" * 78)
    say(f"  {'serving process, peak over the whole run':<62} {service_peak:,.0f}"
        if service_peak is not None
        else "  serving process peak: unavailable on this platform")
    for label, value in layers.items():
        shown = f"{value:,.0f}" if value is not None else "unavailable"
        say(f"  {label:<62} {shown}")

    # ------------------------------------------------- rule-catalogue timing
    # Measured in-process so the Unit 6 caching fix is evidenced reproducibly
    # rather than merely asserted. `load_rules()` used to re-read and re-parse
    # the YAML on every request, which made GET /api/v1/rules the slowest route
    # in the first benchmark run; it is now keyed on the file's mtime.
    catalog = rule_catalogue_timing()
    payload["rule_catalogue_ms"] = catalog
    say()
    say("-" * 78)
    say("Rule catalogue load, in-process (the Unit 6 caching fix)")
    say("-" * 78)
    say(f"  cached call (what a request now pays)    {catalog['cached_ms']:.4f} ms")
    say(f"  cache cleared (the old per-request cost) {catalog['uncached_ms']:.4f} ms")
    say(f"  reduction                                {catalog['speedup']:.0f}x")
    say("  the cache is keyed on the YAML's modification time, so an amended")
    say("  regulation is still picked up on the next request")

    # ------------------------------------------------ run-to-run history
    # One machine cannot report its own load sensitivity, so each run appends its
    # figure to a history file and the observed range is reported.
    history = update_p95_history(payload["conservative_p95_ms"])
    payload["p95_history"] = history
    if len(history) > 1:
        values = [entry["conservative_p95_ms"] for entry in history]
        say()
        say(f"  cross-run worst-endpoint p95 over {len(history)} recorded runs: "
            f"{min(values):.2f}-{max(values):.2f} ms")
        say("  the spread is the shared machine's load, which is why C5 is")
        say("  evaluated at the worst observed case rather than the best one")

    (EVIDENCE / "log-unit6-perf.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (EVIDENCE / "unit6-perf.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nwrote {EVIDENCE / 'log-unit6-perf.txt'}")
    print(f"wrote {EVIDENCE / 'unit6-perf.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
