"""Independent benchmark: the unmodified M2 detector on real, externally labelled data.

The Unit 6 feedback was that C1/C2 rest on labels the author injected. This
script answers it with data the author neither generated nor labelled: the
GECCO 2018 Industrial Challenge set (Moritz et al., 2018; CC BY 4.0), 139,566
one-minute readings from a German water utility (Thüringer Fernwasserversorgung)
with anomaly events labelled by the organisers.

Rules fixed before the first run, so the result cannot be tuned into a pass:

* The detector is ``modules/m2_detect.py`` exactly as tagged ``u8``; only the
  ``Cl`` column is used, because M2 is a univariate chlorine detector.
* Readings are averaged into 10-minute cycles (the shortest event lasts 10
  minutes) and replayed walk-forward over a trailing one-day window
  (144 cycles), so every verdict uses only data available at that moment.
* Two configurations: ``frozen`` (as shipped, Ontario calibration) and
  ``site-scaled``, where the absolute watch level is rescaled once by the site's
  median chlorine (0.50 x median / 1.12). No other parameter is touched.
* An event counts as detected if any alert falls inside it or in the 30 minutes
  before it. An event is "chlorine-visible" if, inside the event, Cl departs from
  the median of the preceding hour by at least 0.02 mg/L (two sensor steps).

Usage: python examples/unit8_gecco_benchmark.py <aquaops-src> <gecco2018.csv> <out-dir>

The data set is not committed (10 MB, CC BY 4.0); download it from
https://zenodo.org/records/3884398 and pass an export of tag u8 as <aquaops-src>.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

CYCLE = "10min"
WINDOW = 144
PRE_EVENT_CYCLES = 3
VISIBLE_DELTA = 0.02
ONTARIO_MEAN_MG_L = 1.12


def _verdicts(args: tuple[str, list[float], int, int, float]) -> list[tuple[str, ...]]:
    src, values, lo, hi, watch = args
    sys.path.insert(0, src)
    import modules.m2_detect as m2

    m2.WATCH_LEVEL_MG_L = watch
    out = []
    for i in range(lo, hi):
        window = values[max(0, i + 1 - WINDOW) : i + 1]
        codes, _, _ = m2.analyse(window)
        out.append(tuple(code.value for code in codes))
    return out


def replay(src: str, values: list[float], watch: float, workers: int = 8) -> list[tuple[str, ...]]:
    step = -(-len(values) // (workers * 4))
    jobs = [(src, values, lo, min(lo + step, len(values)), watch) for lo in range(0, len(values), step)]
    with ProcessPoolExecutor(workers) as pool:
        return [v for chunk in pool.map(_verdicts, jobs) for v in chunk]


def f1(pred: list[bool], truth: list[bool]) -> tuple[float, float, float]:
    tp = sum(p and t for p, t in zip(pred, truth))
    fp = sum(p and not t for p, t in zip(pred, truth))
    fn = sum(t and not p for p, t in zip(pred, truth))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, score


def runs(flags: list[bool]) -> list[tuple[int, int]]:
    spans, start = [], None
    for i, flag in enumerate(flags + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            spans.append((start, i - 1))
            start = None
    return spans


def score(name: str, pred: list[bool], truth: list[bool], events: list[dict], weeks: float) -> dict:
    precision, recall, point_f1 = f1(pred, truth)
    alert_runs = runs(pred)
    detected, leads = [], []
    for event in events:
        lo, hi = event["lo"], event["hi"]
        hits = [i for i in range(max(0, lo - PRE_EVENT_CYCLES), hi + 1) if pred[i]]
        event["detected_" + name] = bool(hits)
        if hits:
            detected.append(event)
            leads.append(lo - hits[0])
    false_runs = [
        r for r in alert_runs
        if not any(r[0] <= e["hi"] and r[1] >= e["lo"] - PRE_EVENT_CYCLES for e in events)
    ]
    visible = [e for e in events if e["visible"]]
    # Event "hits" expected by chance if the same number of alert cycles were
    # scattered at random: guards against reading a dense alarm stream as recall.
    density = sum(pred) / len(pred)
    chance = sum(1 - (1 - density) ** (e["hi"] - e["lo"] + 1 + PRE_EVENT_CYCLES) for e in events)
    return {
        "alert_cycle_share": round(density, 4),
        "events_expected_by_chance": round(chance, 1),
        "config": name,
        "point_precision": round(precision, 4),
        "point_recall": round(recall, 4),
        "point_f1": round(point_f1, 4),
        "events_detected": len(detected),
        "events_total": len(events),
        "visible_detected": sum(e["detected_" + name] for e in visible),
        "visible_total": len(visible),
        "alert_episodes": len(alert_runs),
        "false_alarm_episodes": len(false_runs),
        "false_alarms_per_week": round(len(false_runs) / weeks, 2),
        "alert_episode_precision": round(1 - len(false_runs) / len(alert_runs), 4) if alert_runs else 0.0,
        "median_lead_cycles": statistics.median(leads) if leads else None,
    }


def main() -> None:
    src, csv_path, out_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(csv_path, index_col=0, parse_dates=["Time"]).set_index("Time")
    raw["EVENT"] = raw["EVENT"].astype(str).str.upper().eq("TRUE")
    cycles = raw.resample(CYCLE).agg({"Cl": "mean", "EVENT": "max"}).dropna(subset=["Cl"])
    values = [round(float(v), 4) for v in cycles["Cl"]]
    truth = [bool(v) for v in cycles["EVENT"]]
    weeks = (cycles.index[-1] - cycles.index[0]).total_seconds() / (7 * 86400)

    events = []
    for lo, hi in runs(truth):
        before = values[max(0, lo - 6) : lo] or values[lo : lo + 1]
        ref = statistics.median(before)
        events.append({
            "lo": lo, "hi": hi,
            "start": str(cycles.index[lo]), "end": str(cycles.index[hi]),
            "visible": max(abs(v - ref) for v in values[lo : hi + 1]) >= VISIBLE_DELTA,
        })

    site_median = statistics.median(values)
    configs = {"frozen": 0.50, "site-scaled": round(0.50 * site_median / ONTARIO_MEAN_MG_L, 4)}
    results, started = [], time.perf_counter()
    for name, watch in configs.items():
        codes = replay(src, values, watch)
        pred = [bool(c) for c in codes]
        fired = [c for c in codes if c]
        share = {k: round(sum(k in c for c in fired) / len(fired), 4) for k in sorted({k for c in fired for k in c})}
        results.append(score(name, pred, truth, events, weeks) | {"watch_level_mg_l": watch, "reason_share": share})

    baselines = {
        "threshold <0.05 mg/L": [v < 0.05 for v in values],
        "rolling 3-sigma": [
            i >= 6 and abs(values[i] - statistics.mean(values[max(0, i - WINDOW) : i]))
            > 3 * (statistics.pstdev(values[max(0, i - WINDOW) : i]) or 1e9)
            for i in range(len(values))
        ],
    }
    for name, pred in baselines.items():
        results.append(score(name, pred, truth, events, weeks))

    summary = {
        "dataset": "GECCO 2018 Industrial Challenge (Moritz et al., 2018), doi:10.5281/zenodo.3884398",
        "raw_rows": len(raw), "cycles": len(values), "cycle": CYCLE, "window_cycles": WINDOW,
        "span": [str(cycles.index[0]), str(cycles.index[-1])], "weeks": round(weeks, 2),
        "labelled_cycles": sum(truth), "site_median_cl_mg_l": site_median,
        "runtime_s": round(time.perf_counter() - started, 1),
        "results": results, "events": events,
    }
    (out_dir / "unit8-gecco-benchmark.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"GECCO 2018: {len(raw)} rows -> {len(values)} cycles over {weeks:.1f} weeks, "
          f"{len(events)} labelled events ({sum(e['visible'] for e in events)} chlorine-visible)")
    for r in results:
        print(f"{r['config']:<22} F1={r['point_f1']:.4f} P={r['point_precision']:.4f} R={r['point_recall']:.4f} "
              f"events {r['events_detected']}/{r['events_total']} visible {r['visible_detected']}/{r['visible_total']} "
              f"false-alarm episodes/wk={r['false_alarms_per_week']} lead={r['median_lead_cycles']} "
              f"chance={r['events_expected_by_chance']} alert-share={r['alert_cycle_share']} {r.get('reason_share', '')}")
    print(f"runtime {summary['runtime_s']} s")


if __name__ == "__main__":
    main()
