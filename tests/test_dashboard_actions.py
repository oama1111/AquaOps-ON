"""Operator actions on the dashboard (M5): upload, acknowledge, plan, trend.

Each action is exercised twice where it matters: as an HTMX request, which must
return the fragment it changed, and as a plain form post, which must still work
when the HTMX script never loaded. The trend geometry is also tested directly,
because a chart that draws the reference lines in the wrong place is wrong
without raising anything.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modules.m5_dashboard import TREND_HEIGHT, TREND_WIDTH, trend_geometry

DEMO_CSV = Path(__file__).resolve().parents[1] / "examples" / "demo_lab_results.csv"
HX = {"HX-Request": "true"}
WEEK = "2026-W38"


def _upload(client: TestClient, *, htmx: bool = True, content: bytes | None = None):
    data = content if content is not None else DEMO_CSV.read_bytes()
    return client.post(
        "/dash/upload",
        files={"file": ("demo_lab_results.csv", data, "text/csv")},
        headers=HX if htmx else None,
    )


def _raise_sp15_alert(client: TestClient) -> str:
    _upload(client)
    client.post("/api/v1/detect/run", params={"point": "SP15"})
    alerts = client.get("/api/v1/alerts").json()
    assert alerts, "the demo series at SP15 should raise an alert"
    return alerts[0]["id"]


# ------------------------------------------------------------------ upload


def test_upload_receipt_reports_stored_and_refused_rows(client: TestClient) -> None:
    response = _upload(client)
    assert response.status_code == 200
    assert "12 stored" in response.text
    assert "2 refused" in response.text
    assert "point SP99 is not registered" in response.text
    assert response.headers["HX-Trigger"] == "readings-changed"


def test_upload_replay_stores_nothing_and_says_why(client: TestClient) -> None:
    _upload(client)
    replay = _upload(client)
    assert "0 stored" in replay.text
    assert "12 already on record" in replay.text
    assert "HX-Trigger" not in replay.headers


def test_upload_without_htmx_returns_the_whole_page(client: TestClient) -> None:
    response = _upload(client, htmx=False)
    assert response.status_code == 200
    assert "<html" in response.text
    assert "12 stored" in response.text


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"sampling_point_id,measured_at,free_chlorine_mg_l\n", "no data rows"),
        (b"\xff\xfe\x00binary", "not a UTF-8 text CSV"),
    ],
)
def test_upload_problems_are_explained_not_raised(
    client: TestClient, content: bytes, message: str
) -> None:
    response = _upload(client, content=content)
    assert response.status_code == 200
    assert message in response.text


# ------------------------------------------------------------------ acknowledge


def test_acknowledge_removes_the_alert_from_the_inbox(client: TestClient) -> None:
    alert_id = _raise_sp15_alert(client)
    response = client.post(
        f"/dash/alerts/{alert_id}/ack",
        data={"operator_id": "op-17", "note": "resampled SP15"},
        headers=HX,
    )
    assert response.status_code == 200
    assert "Alert inbox — 0 open" in response.text
    assert response.headers["HX-Trigger"] == "alerts-changed"
    assert client.get("/api/v1/alerts").json() == []


def test_acknowledge_requires_an_operator_id(client: TestClient) -> None:
    alert_id = _raise_sp15_alert(client)
    response = client.post(
        f"/dash/alerts/{alert_id}/ack", data={"operator_id": "  "}, headers=HX
    )
    assert "Enter your operator ID" in response.text
    assert "Alert inbox — 1 open" in response.text
    assert len(client.get("/api/v1/alerts").json()) == 1


def test_acknowledge_twice_is_reported_not_repeated(client: TestClient) -> None:
    alert_id = _raise_sp15_alert(client)
    client.post(f"/dash/alerts/{alert_id}/ack", data={"operator_id": "op-17"}, headers=HX)
    again = client.post(
        f"/dash/alerts/{alert_id}/ack", data={"operator_id": "op-17"}, headers=HX
    )
    assert "already acknowledged" in again.text


def test_acknowledge_without_htmx_redirects_to_the_inbox(client: TestClient) -> None:
    alert_id = _raise_sp15_alert(client)
    response = client.post(
        f"/dash/alerts/{alert_id}/ack",
        data={"operator_id": "op-17"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/#alert-inbox"


# ------------------------------------------------------------------ plan


def test_plan_button_generates_duties_and_a_draft_plan(client: TestClient) -> None:
    response = client.post("/dash/week/plan", data={"week": WEEK}, headers=HX)
    assert response.status_code == 200
    assert "Planned stops<strong>36</strong>" in response.text
    assert "Confirm plan" in response.text
    plan_id = re.search(r'name="plan_id" value="([^"]+)"', response.text).group(1)
    assert f"/api/v1/plans/{plan_id}/export.csv" in response.text
    assert f"/dash/export/week.csv?week={WEEK}" in response.text
    sheet = client.get(f"/api/v1/plans/{plan_id}/export.csv")
    assert sheet.status_code == 200
    assert sheet.text.startswith("seq,sampling_point,vehicle,eta,leg_km")


def test_confirming_a_plan_removes_the_confirm_button(client: TestClient) -> None:
    planned = client.post("/dash/week/plan", data={"week": WEEK}, headers=HX)
    plan_id = re.search(r'name="plan_id" value="([^"]+)"', planned.text).group(1)
    confirmed = client.post(
        "/dash/week/confirm", data={"plan_id": plan_id, "week": WEEK}, headers=HX
    )
    assert "Status<strong>confirmed</strong>" in confirmed.text
    assert "Confirm plan" not in confirmed.text
    assert client.get(f"/api/v1/plans/{plan_id}").json()["status"] == "confirmed"


def test_plan_rejects_a_malformed_week(client: TestClient) -> None:
    response = client.post("/dash/week/plan", data={"week": "next week"}, headers=HX)
    assert "is not an ISO week" in response.text


# ------------------------------------------------------------------ trend


def test_trend_defaults_to_the_point_with_an_open_alert(client: TestClient) -> None:
    _raise_sp15_alert(client)
    page = client.get("/").text
    assert "Chlorine trend — SP15" in page
    assert "<polyline" in page
    assert "alert threshold 0.05 mg/L" in page


def test_trend_for_a_point_without_readings_says_so(client: TestClient) -> None:
    response = client.get("/dash/trend", params={"point": "SP02"})
    assert "No readings are stored for SP02" in response.text


def test_dashboard_still_loads_nothing_from_another_origin(client: TestClient) -> None:
    _raise_sp15_alert(client)
    page = client.get("/").text
    assert not re.findall(r'(?:src|href|action)="https?://', page)


@pytest.mark.unit_blackbox
def test_trend_geometry_stays_inside_the_canvas_and_keeps_time_order() -> None:
    start = datetime(2026, 9, 14, 8)
    series = [(start + timedelta(hours=24 * i), 1.0 - 0.1 * i) for i in range(7)]
    chart = trend_geometry("SP15", list(reversed(series)), threshold_mg_l=0.05, watch_mg_l=0.5)
    assert chart is not None
    xs = [m.x for m in chart.markers]
    assert xs == sorted(xs), "markers are drawn in time order even if given out of order"
    assert all(0 <= m.x <= TREND_WIDTH and 0 <= m.y <= TREND_HEIGHT for m in chart.markers)
    # Lower concentrations sit lower on the chart: SVG y grows downwards.
    assert chart.threshold_y > chart.watch_y > chart.markers[0].y


@pytest.mark.unit_whitebox
def test_trend_geometry_edge_cases() -> None:
    assert trend_geometry("SP01", [], threshold_mg_l=0.05, watch_mg_l=0.5) is None
    single = trend_geometry(
        "SP01", [(datetime(2026, 9, 14, 8), 0.9)], threshold_mg_l=0.05, watch_mg_l=0.5
    )
    assert single is not None
    assert single.markers[0].x == pytest.approx((single.plot_left + single.plot_right) / 2)
    # The y axis always reaches the watch level, so a low series is not stretched.
    low = trend_geometry(
        "SP01",
        [(datetime(2026, 9, 14, 8), 0.10), (datetime(2026, 9, 15, 8), 0.09)],
        threshold_mg_l=0.05,
        watch_mg_l=0.5,
    )
    assert low is not None
    assert float(low.y_ticks[-1][1]) >= 0.5
