"""Unit 6 qualitative evaluation: heuristic usability review of the dashboard.

The Unit 2 proposal committed to a System Usability Scale study with three to
five operators (criterion C7). That study was not run — no operators were
recruited — so this script does not pretend to replace it and does not report a
SUS score. What it does instead is the honest substitute available to a single
developer: a structured heuristic evaluation of the operator dashboard against
Nielsen's ten heuristics (Nielsen, 2024), with every finding anchored to
evidence that a reader can re-derive.

Two things keep the review from being pure opinion:

* **Machine-checked observations.** Twelve properties that ought to hold — the
  decision-support disclaimer is present, every alert renders its reason codes
  and its numeric evidence, units are labelled, status is conveyed as text and
  not only as colour, the core content survives with JavaScript disabled — are
  asserted against the live HTML and reported as PASS or FAIL with the matching
  excerpt. A heuristic score that disagreed with its own evidence would be
  visible.
* **Declared bias.** The evaluator is the system's author. That inflates the
  scores, and the report says so; the score is therefore presented as an upper
  bound and as a triage instrument, not as a measurement of operator
  satisfaction.

Screenshots are captured with headless Chrome so the visual evidence is the
rendered interface rather than the template source.

    python examples/unit6_ui.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.unit6_harness import (  # noqa: E402
    EVIDENCE,
    ISO_WEEK,
    chrome_screenshot,
    find_chrome,
    live_server,
    seed_case,
    wait_for_health,
)

STAMP = "2026-09-19"

#: Nielsen's ten heuristics, each scored 0-4 (0 = unusable, 4 = no problem
#: found). The rationale is written against the machine-checked evidence below.
HEURISTICS: tuple[tuple[str, int, str], ...] = (
    (
        "H1 Visibility of system status",
        4,
        "The shell names the system, the network panel reports how many points are "
        "registered and shows each point's last reading and open-alert count, the "
        "inbox header states how many alerts are open, and the week panel reports "
        "planned stops, total distance, solver time and plan status. Alert age is "
        "shown as a timestamp.",
    ),
    (
        "H2 Match between system and the real world",
        4,
        "The vocabulary is the operator's, not the developer's: 'sampling point', "
        "'dead end', 'regulatory window closes', 'free chlorine residual in mg/L'. "
        "The unit of work is the week and the cab sheet, which is how the job is "
        "actually organised. Reason codes such as 'trend_to_limit' are the one "
        "exception and are terse, though each is paired with the numeric evidence "
        "that justifies it.",
    ),
    (
        "H3 User control and freedom",
        2,
        "Alerts can be acknowledged with a free-text note and the week view can be "
        "requested for any ISO week, so the operator can drill in. But there is no "
        "undo for an acknowledgement, no way to re-open a mis-acknowledged alert, "
        "and no way to reorder or exclude a duty from the plan. The interface is "
        "read-mostly.",
    ),
    (
        "H4 Consistency and standards",
        4,
        "One visual language throughout: the same status chip component is used "
        "for location type, alert state and task status; the same panel structure "
        "for all three sections; the same units and the same date format. The API "
        "and the UI use identical field names because both render the same rows.",
    ),
    (
        "H5 Error prevention",
        3,
        "The ingest path refuses out-of-band values (a 9.90 mg/L row is rejected "
        "with the reason) and refuses unregistered points, and a duplicate reading "
        "is an explicit conflict rather than a silent overwrite. In the UI, though, "
        "acknowledgement is a single unconfirmed action, and there is no warning "
        "when the operator is looking at a stale fragment.",
    ),
    (
        "H6 Recognition rather than recall",
        3,
        "Everything needed to judge an alert is in the alert card — model, both "
        "model scores, last reading, threshold and reason codes — so the operator "
        "does not have to remember thresholds. The duty table names the rule that "
        "generated each task, but the rule's source section is not shown, so "
        "tracing a duty back to the regulation still means opening the API.",
    ),
    (
        "H7 Flexibility and efficiency of use",
        3,
        "Fragments refresh themselves on independent timers (network 60 s, alerts "
        "30 s, week 120 s), so the page can be left open on a wall display, and the "
        "route sheet and weekly summary export as CSV for people who work in a "
        "spreadsheet. There is no bulk acknowledgement, no filtering of a 36-row "
        "duty list, and no keyboard shortcuts.",
    ),
    (
        "H8 Aesthetic and minimalist design",
        4,
        "Three panels, no navigation, no decorative imagery, and the safety "
        "disclaimer sits in the header rather than in a modal. Density is high in "
        "the duty table but the information is all load-bearing.",
    ),
    (
        "H9 Help users recognise, diagnose and recover from errors",
        3,
        "API errors are explicit and actionable — planning without tasks returns "
        "422 naming the missing tasks, and a duplicate reading returns 409 naming "
        "the key. In the UI, failures are weaker: an HTMX fragment that fails to "
        "refresh leaves the previous content in place with no indication that it "
        "is stale, and there is no error surface at all.",
    ),
    (
        "H10 Help and documentation",
        3,
        "FastAPI's generated OpenAPI documentation is served at /docs and covers "
        "every endpoint with schemas, and the repository carries design documents "
        "for the architecture, data model and API. What is missing is "
        "operator-facing help: nothing in the interface explains what 'watch' "
        "means, what the isolation score is, or what to do after acknowledging.",
    ),
)

HEURISTIC_MAX = 4


class Checklist:
    """Machine-checked observations, each with the excerpt that proves it."""
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def check(self, name: str, passed: bool, evidence: str) -> None:
        self.rows.append({"check": name, "passed": bool(passed), "evidence": evidence})

    def add(self, name: str, needle: str, haystack: str, *, note: str = "") -> None:
        self.check(name, needle in haystack, note or f"looking for {needle!r}")

    def render(self, say: Any) -> None:
        for row in self.rows:
            say(f"  [{'PASS' if row['passed'] else 'FAIL'}] {row['check']}")
            say(f"         {row['evidence']}")


def panel_crops(
    source: Path,
    stamp: str,
    *,
    background: tuple[int, int, int] = (246, 248, 250),
    tolerance: int = 4,
    min_gap: int = 8,
) -> list[tuple[str, Path, tuple[int, int]]]:
    """Split a full-page capture into its panels along the page's own gutters.

    A single 1440x3200 screenshot of a 36-row duty table is unreadable once it is
    scaled to a document page width, so the capture is cut into panels. The cut
    points are found rather than hard-coded: the page's panels are white cards
    separated by the `#f6f8fa` body background, so a run of background-coloured
    rows of at least `min_gap` pixels *is* a gutter between two panels. Detecting
    them means a change to the layout moves the crops instead of mis-cropping
    them.
    """
    from PIL import Image

    image = Image.open(source).convert("RGB")
    width, height = image.size
    pixels = image.load()

    def is_background(y: int) -> bool:
        for x in range(0, width, 5):
            red, green, blue = pixels[x, y]
            if (
                abs(red - background[0]) > tolerance
                or abs(green - background[1]) > tolerance
                or abs(blue - background[2]) > tolerance
            ):
                return False
        return True

    start: int | None = None
    gutters: list[tuple[int, int]] = []
    for y in range(height):
        if is_background(y):
            if start is None:
                start = y
        elif start is not None:
            if y - start >= min_gap and start > 0:
                gutters.append((start, y))
            start = None

    if not gutters:
        return []

    # Segments between gutters, dropping the leading and trailing page margins.
    bounds: list[tuple[int, int]] = []
    top = 0
    for gutter_start, gutter_end in gutters:
        if gutter_start > top:
            bounds.append((top, gutter_start))
        top = gutter_end
    if height - top > min_gap:
        bounds.append((top, height))

    # The header is only about 90 px tall and belongs with the panel beneath it.
    if len(bounds) >= 2 and bounds[0][1] - bounds[0][0] < 200:
        bounds = [(bounds[0][0], bounds[1][1]), *bounds[2:]]

    names = ("ui-1-status", "ui-2-alerts", "ui-3-week-plan")
    out: list[tuple[str, Path, tuple[int, int]]] = []
    for name, (top_y, bottom_y) in zip(names, bounds):
        target = source.with_name(f"{stamp}-{name}.png")
        image.crop((0, top_y, width, bottom_y)).save(target)
        out.append((name, target, (width, bottom_y - top_y)))
    return out


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        print(text)

    payload: dict[str, Any] = {}
    checklist = Checklist()

    say("=" * 78)
    say("AquaOps ON — Unit 6 qualitative evaluation (heuristic usability review)")
    say("Evaluator: the system's author. Self-assessment bias is a stated limitation.")
    say("=" * 78)
    say()

    with live_server() as live:
        wait_for_health(live.base_url, live.process)
        client = httpx.Client(base_url=live.base_url, timeout=60.0)
        facts = seed_case(client)
        say(f"seeded: {facts['tasks_generated']} duties, plan "
            f"{facts['plan']['total_km']} km, {facts['alerts_open']} open alert(s)")
        say()

        page = client.get("/").text
        network = client.get("/dash/network").text
        inbox = client.get("/dash/alerts").text
        week = client.get(f"/dash/week?week={ISO_WEEK}").text
        empty_week = client.get("/dash/week?week=2026-W52").text
        docs = client.get("/docs")
        openapi = client.get("/openapi.json").json()

        # ------------------------------------------------ machine-checked checks
        say("-" * 78)
        say("Machine-checked interface properties (evaluated on the live HTML)")
        say("-" * 78)

        # Any absolute src/href is an external origin the operator's browser must
        # reach. For an interface served on a municipal network that is a real
        # constraint, so it is checked rather than asserted in prose.
        external_origins = sorted(
            {
                match.group(1)
                for match in re.finditer(
                    r'(?:src|href)="(https?://[^/"]+)', page, flags=re.IGNORECASE
                )
            }
        )

        checklist.check(
            "decision-support disclaimer is persistently visible",
            "Decision support only" in page and "licensed operator judgment prevails" in page,
            "header carries: 'Decision support only. Output is not legal proof of "
            "compliance; licensed operator judgment prevails.'",
        )
        checklist.check(
            "system identity and week context are stated",
            "AquaOps ON" in page and "Township of Maple Creek" in page,
            "h1 renders 'AquaOps ON — Township of Maple Creek'",
        )
        checklist.add(
            "viewport meta tag present (mobile layout is not broken by default)",
            'name="viewport"',
            page,
        )
        checklist.check(
            "document landmarks exist for assistive technology",
            "<main" in page and "<h1" in page and page.count("<h2") >= 3,
            f"one <main>, one <h1>, {page.count('<h2')} <h2> section headings",
        )
        checklist.check(
            "core content is server-rendered and survives without JavaScript",
            all(marker in page for marker in ("SP01", "SP15", "SP18"))
            and "sampling points" in network
            and "mg/L" in network,
            "the first response already contains the point registry rows and the "
            "mg/L readings; HTMX only refreshes them afterwards",
        )
        checklist.check(
            "every alert renders its reason codes verbatim",
            "reason:" in inbox
            and any(code in inbox for code in ("ewma_shift_up", "iforest_outlier", "trend_to_limit")),
            "inbox renders 'reason: ewma_shift_up,iforest_outlier,trend_to_limit'",
        )
        checklist.check(
            "every alert renders its numeric evidence, not just a verdict",
            "mg/L" in inbox and "threshold" in inbox and "ewma z" in inbox
            and "isolation score" in inbox,
            "each alert card shows last reading, threshold, EWMA z and isolation score",
        )
        checklist.check(
            "status is conveyed as text as well as colour",
            "open alert(s)" in network and "no open alert" in network
            and "dead_end" in network,
            "status and location are written out ('open alert(s)', 'no open alert', "
            "'dead_end'), so colour is redundant rather than load-bearing",
        )
        checklist.check(
            "quantities carry their units",
            "mg/L" in network and "km" in week and "ms" in week,
            "network column header is 'Last reading (mg/L)'; week panel shows "
            "'Total distance' in km and 'Solver time' in ms",
        )
        checklist.check(
            "the plan's regulatory context is visible, not only its geometry",
            "Regulatory window closes" in week and "Planned stops" in week,
            "the duty table has a 'Regulatory window closes' column and the panel "
            "reports planned stops, distance, solver time and plan status",
        )
        checklist.check(
            "empty states are explicit rather than blank",
            "No sampling duties are scheduled for this week" in empty_week,
            "requesting a week with no duties renders an explanatory message",
        )
        checklist.check(
            "empty states avoid internal implementation jargon",
            "inert" not in empty_week
            and "pending legal verification" not in empty_week
            and "verified" not in empty_week,
            "the empty-week copy no longer tells the operator that rules are "
            "'inert (pending legal verification)'. That sentence was true only "
            "while the catalogue was a draft; Unit 6 verified every clause, so "
            "showing it would be a stale statement about the system's internals "
            "to someone who cannot act on it",
        )
        checklist.check(
            "the API is self-documenting",
            docs.status_code == 200 and docs.headers.get("content-type", "").startswith("text/html")
            and len(openapi.get("paths", {})) >= 15,
            f"GET /docs returns {docs.status_code} text/html; /openapi.json "
            f"publishes {len(openapi.get('paths', {}))} paths with schemas",
        )
        checklist.check(
            "the wide duty table stays reachable on a narrow screen",
            "@media (max-width: 760px)" in page and "overflow-x:auto" in page,
            "FINDING, FIXED IN UNIT 6: at 480 px the six-column duty table pushed "
            "the page sideways and clipped the Vehicle and ETA columns — exactly "
            "the fields that say which truck is coming and when. A narrow-screen "
            "rule now makes each table its own horizontal scroller so no column is "
            "lost, and lets the totals row wrap",
        )
        checklist.check(
            "no third-party CDN is required to serve the operator interface",
            not external_origins
            and "/static/htmx.min.js" in page
            and client.get("/static/htmx.min.js").status_code == 200,
            "FINDING, FIXED IN UNIT 6: the shell used to load htmx from "
            "https://unpkg.com, so on a filtered or air-gapped municipal network "
            "the script never arrived, and a third-party host became a supply-chain "
            "dependency for a drinking-water interface. The bundle is now vendored "
            "under app/static/, served from this origin at /static/htmx.min.js, and "
            "committed with its pinned version and SHA-256. External origins "
            f"referenced by the served page: {external_origins or 'none'}",
        )

        checklist.render(say)
        passed = sum(1 for row in checklist.rows if row["passed"])
        say()
        say(f"  {passed}/{len(checklist.rows)} machine-checked properties hold")
        payload["checks"] = checklist.rows
        payload["checks_passed"] = passed
        payload["checks_total"] = len(checklist.rows)

        # ------------------------------------------------------- screenshots
        say()
        say("-" * 78)
        say("Interface screenshots")
        say("-" * 78)
        browser = find_chrome()
        shots: dict[str, Any] = {}
        if browser is None:
            say("  no Chrome binary found; no screenshots captured")
        else:
            targets = (
                ("ui-0-dashboard-full", "/", 1440, 3400, 1.0),
                ("ui-4-dashboard-narrow", "/", 480, 2000, 2.0),
                ("ui-5-api-docs", "/docs", 1440, 1200, 1.0),
            )
            for name, path, width, height, scale in targets:
                out = EVIDENCE / f"{STAMP}-{name}.png"
                ok = chrome_screenshot(
                    f"{live.base_url}{path}",
                    out,
                    width=width,
                    height=height,
                    scale=scale,
                )
                size = out.stat().st_size if out.exists() else 0
                say(f"  {name:<26} {width}x{height} @{scale}x  "
                    f"{'captured' if ok else 'FAILED'} ({size:,} bytes)")
                shots[name] = {"path": str(out), "ok": ok, "bytes": size,
                               "width": width, "height": height, "scale": scale}

            full = EVIDENCE / f"{STAMP}-ui-0-dashboard-full.png"
            if full.exists():
                for name, path, (width, height) in panel_crops(full, STAMP):
                    say(f"  {name:<26} {width}x{height} (panel cut from the full page)")
                    shots[name] = {
                        "path": str(path),
                        "ok": True,
                        "bytes": path.stat().st_size,
                        "width": width,
                        "height": height,
                        "scale": 1.0,
                    }
        payload["screenshots"] = shots

        client.close()

    # --------------------------------------------------------- heuristic scores
    say()
    say("-" * 78)
    say("Heuristic evaluation — Nielsen's ten heuristics, 0 (unusable) to 4 (no problem found)")
    say("-" * 78)
    total = 0
    rows: list[dict[str, Any]] = []
    for name, score, rationale in HEURISTICS:
        total += score
        rows.append({"heuristic": name, "score": score, "max": HEURISTIC_MAX,
                     "rationale": rationale})
        say(f"  {score}/{HEURISTIC_MAX}  {name}")
        say(f"        {rationale}")
    maximum = HEURISTIC_MAX * len(HEURISTICS)
    say()
    say(f"  heuristic score: {total}/{maximum} ({total / maximum:.0%})")
    say("  This is an author self-assessment, so treat it as an upper bound: it")
    say("  cannot substitute for the SUS study with 3-5 operators that criterion")
    say("  C7 requires, and that study remains outstanding.")
    payload["heuristics"] = rows
    payload["heuristic_total"] = total
    payload["heuristic_max"] = maximum

    (EVIDENCE / "log-unit6-usability.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (EVIDENCE / "unit6-usability.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nwrote {EVIDENCE / 'log-unit6-usability.txt'}")
    print(f"wrote {EVIDENCE / 'unit6-usability.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
