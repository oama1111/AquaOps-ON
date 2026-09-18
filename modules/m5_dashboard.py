"""M5 — operator dashboard & reporting: server-rendered HTMX fragments (ADR-4).

ADR-4 chose server rendering over a single-page application because the users
run aging municipal laptops and the platform must not need a Node build step
merely to display a duty list. Each function here returns an HTML fragment that
HTMX swaps into the page; the full page shell lives in `app/templates/`.

The `render_alert_inbox` fragment is required by the API specification to show
the M2 explainability payload verbatim — model, scores, last reading, threshold,
reason codes — so that an operator always sees *why* an alarm fired.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
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
) -> str:
    """The dashboard shell.

    The three fragments are rendered server-side into the first response and
    HTMX only refreshes them afterwards, so the page still shows the duty list
    and the alert inbox if the HTMX script never loads — which matters on the
    aging municipal laptops ADR-4 was written for.
    """
    return _render(
        "dashboard.html",
        system_name=system_name,
        network_html=network_html,
        alerts_html=alerts_html,
        week_html=week_html,
    )


def render_network_status(points: Iterable[Any], alerts_by_point: dict[str, int]) -> str:
    """Network map fragment with a per-point status chip. (`GET /dash/choropleth`)"""
    return _render("_network_status.html", points=list(points), alerts_by_point=alerts_by_point)


def render_alert_inbox(alerts: Iterable[Any]) -> str:
    """Open-alert fragment, polled every 30 s. (`GET /dash/alerts`)"""
    return _render("_alert_inbox.html", alerts=list(alerts))


def render_week_view(tasks: Sequence[Any], plan: Any | None = None) -> str:
    """This week's plan against its regulatory windows. (`GET /dash/week`)"""
    stops_by_task: dict[str, Any] = {}
    if plan is not None:
        stops_by_task = {stop.required_task_id: stop for stop in getattr(plan, "stops", [])}
    return _render("_week_view.html", tasks=list(tasks), plan=plan, stops_by_task=stops_by_task)


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
