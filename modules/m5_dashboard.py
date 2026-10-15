"""M5 — operator dashboard & reporting: server-rendered HTMX fragments (ADR-4).

ADR-4 chose server rendering over a single-page application because the users
run aging municipal laptops and the platform must not need a Node build step
merely to display a duty list. Each function here returns an HTML fragment that
HTMX swaps into the page; the full page shell lives in `app/templates/`.

The `render_alert_inbox` fragment is required by the API specification to show
the M2 explainability payload verbatim — model, scores, last reading, threshold,
reason codes — so that an operator always sees *why* an alarm fired.

The operator actions (upload, acknowledge, plan, confirm) are ordinary HTML
forms. HTMX upgrades them to in-place updates when it loads; when it does not,
the browser submits them as full-page posts and they still work.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render(template: str, **context: Any) -> str:
    return _env.get_template(template).render(**context)


def render_shell(
    *,
    system_name: str,
    network_html: str,
    alerts_html: str,
    week_html: str,
    trend_html: str = "",
    upload_html: str = "",
) -> str:
    """The dashboard shell.

    Every fragment is rendered server-side into the first response and HTMX
    only refreshes them afterwards, so the page still shows the duty list and
    the alert inbox if the HTMX script never loads — which matters on the aging
    municipal laptops ADR-4 was written for.
    """
    return _render(
        "dashboard.html",
        system_name=system_name,
        network_html=network_html,
        alerts_html=alerts_html,
        week_html=week_html,
        trend_html=trend_html,
        upload_html=upload_html,
    )


def render_network_status(points: Iterable[Any], alerts_by_point: dict[str, int]) -> str:
    """Network map fragment with a per-point status chip. (`GET /dash/choropleth`)"""
    return _render("_network_status.html", points=list(points), alerts_by_point=alerts_by_point)


def render_alert_inbox(alerts: Iterable[Any], notice: str | None = None) -> str:
    """Open-alert fragment with an acknowledge form per alert. (`GET /dash/alerts`)

    ``notice`` reports an acknowledgement that could not be recorded, in the
    place the operator is already looking.
    """
    return _render("_alert_inbox.html", alerts=list(alerts), notice=notice)


def render_week_view(
    tasks: Sequence[Any],
    plan: Any | None = None,
    *,
    week: str | None = None,
    notice: str | None = None,
) -> str:
    """This week's plan against its regulatory windows. (`GET /dash/week`)

    ``week`` is carried into the plan form and the export link so that both act
    on the week being displayed; ``notice`` explains a planning request that
    produced no plan.
    """
    stops_by_task: dict[str, Any] = {}
    if plan is not None:
        stops_by_task = {stop.required_task_id: stop for stop in getattr(plan, "stops", [])}
    return _render(
        "_week_view.html",
        tasks=list(tasks),
        plan=plan,
        stops_by_task=stops_by_task,
        week=week,
        notice=notice,
    )


def render_upload_receipt(
    *,
    filename: str,
    rows_total: int = 0,
    rows_accepted: int = 0,
    errors: Sequence[Any] = (),
    problem: str | None = None,
) -> str:
    """The receipt for a laboratory file uploaded from the dashboard. (`POST /dash/upload`)

    Rows that were neither stored nor refused were already on record; the
    receipt names that number instead of leaving the operator to wonder why a
    fourteen-row file stored nothing on its second upload.
    """
    duplicates = max(rows_total - rows_accepted - len(errors), 0)
    return _render(
        "_upload_receipt.html",
        filename=filename,
        rows_total=rows_total,
        rows_accepted=rows_accepted,
        duplicates=duplicates,
        errors=list(errors),
        problem=problem,
    )


# --------------------------------------------------------------------------- trend
#
# The trend is drawn as inline SVG computed on the server rather than with a
# charting script. The dashboard loads nothing from another origin (see
# app/static/README.md) and must stay readable with JavaScript disabled; a
# server-side drawing satisfies both, adds no dependency, and leaves the
# geometry below as a pure function the test suite can check point by point.

TREND_WIDTH = 640
TREND_HEIGHT = 220
_LEFT, _RIGHT, _TOP, _BOTTOM = 48, 16, 14, 30


@dataclass(frozen=True)
class TrendMarker:
    x: float
    y: float
    measured_at: datetime
    value: float


@dataclass(frozen=True)
class TrendChart:
    """Pixel geometry for one sampling point's chlorine series."""

    point_id: str
    markers: list[TrendMarker]
    polyline: str
    threshold_mg_l: float
    threshold_y: float
    watch_mg_l: float
    watch_y: float
    y_ticks: list[tuple[float, str]]
    width: int = TREND_WIDTH
    height: int = TREND_HEIGHT
    plot_left: int = _LEFT
    plot_right: int = TREND_WIDTH - _RIGHT
    plot_bottom: int = TREND_HEIGHT - _BOTTOM

    @property
    def first(self) -> TrendMarker:
        return self.markers[0]

    @property
    def last(self) -> TrendMarker:
        return self.markers[-1]


def trend_geometry(
    point_id: str,
    series: Sequence[tuple[datetime, float]],
    *,
    threshold_mg_l: float,
    watch_mg_l: float,
) -> TrendChart | None:
    """Map a time-ordered series onto the chart area.

    The x axis is time, not sample index, so an irregular sampling interval
    shows as an irregular spacing rather than being hidden. The y axis always
    starts at zero and always includes the watch level, so the two reference
    lines sit in the same place relative to the axis on every point's chart and
    a low series is never stretched to look like a steep fall.
    """
    if not series:
        return None
    ordered = sorted(series, key=lambda item: item[0])
    t0, t1 = ordered[0][0], ordered[-1][0]
    span = (t1 - t0).total_seconds()
    y_max = max(max(value for _, value in ordered), watch_mg_l) * 1.15
    plot_w = TREND_WIDTH - _LEFT - _RIGHT
    plot_h = TREND_HEIGHT - _TOP - _BOTTOM

    def x_of(moment: datetime) -> float:
        if span == 0:
            return _LEFT + plot_w / 2
        return _LEFT + plot_w * (moment - t0).total_seconds() / span

    def y_of(value: float) -> float:
        return _TOP + plot_h * (1 - value / y_max)

    markers = [
        TrendMarker(round(x_of(moment), 1), round(y_of(value), 1), moment, value)
        for moment, value in ordered
    ]
    ticks = [(round(y_of(y_max * k / 4), 1), f"{y_max * k / 4:.2f}") for k in range(5)]
    return TrendChart(
        point_id=point_id,
        markers=markers,
        polyline=" ".join(f"{m.x},{m.y}" for m in markers),
        threshold_mg_l=threshold_mg_l,
        threshold_y=round(y_of(threshold_mg_l), 1),
        watch_mg_l=watch_mg_l,
        watch_y=round(y_of(watch_mg_l), 1),
        y_ticks=ticks,
    )


def render_trend(
    point_ids: Sequence[str], selected: str | None, chart: TrendChart | None
) -> str:
    """Chlorine trend for one point, with a point selector. (`GET /dash/trend`)"""
    return _render("_trend.html", point_ids=list(point_ids), selected=selected, chart=chart)


def export_weekly_summary(
    out_path: Path | TextIO, tasks: Sequence[Any], plan: Any | None = None
) -> Path | TextIO:
    """Write the weekly compliance summary for municipal records.

    CSV rather than PDF for the initial version: a spreadsheet opens on every
    municipal desktop without extra tooling, and the appendix to the design
    report already commits to the print-friendly route sheet as a later
    deliverable.

    Accepts either a filesystem path (for a scheduled job writing evidence) or
    an already-open text stream (for the HTTP response that streams it to the
    operator), so the same serialisation serves both callers.
    """
    stops_by_task: dict[str, Any] = {}
    if plan is not None:
        stops_by_task = {stop.required_task_id: stop for stop in getattr(plan, "stops", [])}

    if hasattr(out_path, "write"):
        _write_summary(out_path, tasks, stops_by_task)  # type: ignore[arg-type]
        return out_path

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        _write_summary(handle, tasks, stops_by_task)
    return target


def _write_summary(
    handle: TextIO, tasks: Sequence[Any], stops_by_task: dict[str, Any]
) -> None:
    writer = csv.writer(handle)
    writer.writerow(
        [
            "task_id",
            "sampling_point_id",
            "rule_id",
            "window_start",
            "window_end",
            "status",
            "scheduled_vehicle",
            "scheduled_eta",
        ]
    )
    for task in tasks:
        stop = stops_by_task.get(task.id)
        writer.writerow(
            [
                task.id,
                task.sampling_point_id,
                task.rule_id,
                task.window_start.isoformat(),
                task.window_end.isoformat(),
                task.status,
                getattr(stop, "vehicle_id", ""),
                getattr(stop, "eta", ""),
            ]
        )
